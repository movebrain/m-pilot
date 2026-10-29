"""고객 안내·엔지니어 답변의 단계 조립.

역할:
    retriever와 generator를 순서대로 엮어, 신고 한 건 또는 질문 하나를 `Answer` 하나로 만든다.

처리 순서(spec §2.6): 근거 확보 → 생성 → 검증.
    - 고객 안내: 근거는 허용 절차를 직접 조회한다(검색하지 않는다).
    - 엔지니어 답변: 근거는 `manual`·`history` 검색으로 얻는다. 시작값은 각 3건(`manual_k`, `history_k`).

책임:
    - 근거 부족은 예외가 아니라 조치 단계가 없는(`steps=[]`) `insufficient_evidence` `Answer`로 돌려준다.
      4종 밖 설비·다른 설비를 말한 질문도 먼저 신고 설비 자료를 검색하고, 근거가 없으면 같은 판정이다.
    - 신고 설비 자료만으로 근거가 부족하면 설비 필터 없이 4종 전체를 질문 원문으로 한 번 더 찾는다(폴백).
      폴백 근거로 답하면 다른 설비 자료가 섞였음을 `notice`로 알린다.
    - 모델·검색 장애와 검증 실패는 `DependencyError`로 올린다.
    - 대상 코드는 신고한 코드로 고정한다.
    - 결과를 DB에 저장하지 않는다. 저장은 `service`가 맡는다.

의존: `rag.retriever`, `rag.generator`, `models`만 import한다(의존 규칙 §2.5). `db.rdb`는 부르지 않는다.
호출: `service`.
소유: RAG 파트
"""

from __future__ import annotations

from mpilot.models import Answer, CaseContext, ChatRequest, DependencyError, SearchSettings, ValidationError, Verdict
from mpilot.rag import generator, retriever

MAX_QUESTION_LEN = 2000  # 질문 최대 길이: 공백 제거 후 1~2,000자
CROSS_EQUIPMENT_NOTICE = (
    "신고한 설비의 자료만으로는 근거가 부족해 다른 설비의 매뉴얼·이력을 포함해 답했습니다. "
    "설비가 다르므로 출처의 설비를 확인한 뒤 적용하세요."
)


def _make_hold_answer(notice: str) -> Answer:
    """보류 답변을 만든다. 보류는 예외가 아니라 조치 단계가 없는 Answer다.
     
    Args:
        notice: 화면에 보여줄 한국어 안내 문구.
         
    Returns:
        Answer: `steps=[]`인 근거 부족(`insufficient_evidence`) 답변.
    """
    return Answer(verdict=Verdict.INSUFFICIENT_EVIDENCE, steps=[], notice=notice)


def _is_valid_hold(answer: Answer) -> bool:
    """`answered`가 아닌 답변이 보류 모양(`steps=[]`, 비지 않은 `notice`)인지 확인한다."""
    return not answer.steps and bool(answer.notice and answer.notice.strip())


def _validate_engineer_input(request: ChatRequest, settings: SearchSettings) -> str:
    """질문과 검색 설정을 검사하고, 공백을 정리한 질문을 돌려준다.

    Args:
        request: 현재 질문과 저장된 대화 이력.
        settings: 검색 설정. `manual_k`, `history_k`, `evidence_threshold`.

    Returns:
        str: 앞뒤 공백을 지운 질문.

    Raises:
        ValidationError: 질문이 비었거나 `MAX_QUESTION_LEN`자를 넘을 때,
            또는 `manual_k`·`history_k`가 1보다 작을 때. `evidence_threshold`의 범위는 `SearchSettings`가 막는다.
    """
    question = request.question.strip()
    if not question or len(question) > MAX_QUESTION_LEN:
        raise ValidationError("질문은 공백 제외 1~2,000자여야 합니다.")
    if settings.manual_k < 1 or settings.history_k < 1:
        raise ValidationError("검색 설정 값이 허용 범위를 벗어났습니다.")
    return question
   

def run_customer_guidance(context: CaseContext) -> Answer:
    """고객 안내 처리 흐름을 실행한다.

    허용 절차 직접 조회 → 생성 → 검증 순으로 진행한다. `answered`이면 인용·안전 문구를 검증한다. 허용 절차가
    없거나 생성 결과의 `verdict`가 `answered`가 아니면(근거 부족) 추측하지 않고
    `passes_guidance_check`를 부르지 않은 채, 보류 모양(`steps=[]`, `notice` 있음)만 확인해 그 `Answer`를 돌려준다.

    Args:
        context: 신고 맥락. 대상 에러코드의 상세(허용 절차 ID, 필수 안전 문구 포함)와 증상 메모.

    Returns:
        Answer: `answered`이고 `passes_guidance_check`를 통과하면 출처가 붙은 단계별 안내. 허용 절차가
            없거나 생성 결과가 `answered`가 아니면 `steps=[]`인 보류 판정이며, `service`가 이를
            `verdict`만 보고 엔지니어 접수(`held`)로 바꾼다.

    Raises:
        DependencyError: 모델·검색 장애, 제한 시간 초과, 인용·설비·안전 수칙 검증 실패, 보류 판정 형식 오류일 때
            (`service`가 엔지니어 접수 `generation_failed`로 바꾼다).
    """
    error_code = context.error_code
    hits = retriever.fetch_customer_procedures(error_code)
    if not hits:
        return _make_hold_answer("고객이 직접 조치할 수 있는 절차가 없어 엔지니어가 접수합니다.")

    answer = generator.generate_customer_guidance(context, hits)
    if answer.verdict != Verdict.ANSWERED:
        if not _is_valid_hold(answer):
            raise DependencyError("고객 안내의 보류 판정 형식 검증에 실패했습니다.")
        return answer
    if not generator.passes_guidance_check(answer, hits, error_code):
        raise DependencyError("고객 안내의 인용 또는 안전 문구 검증에 실패했습니다.")
    return answer


def run_engineer_answer(
    context: CaseContext, request: ChatRequest, settings: SearchSettings
) -> Answer:
    """엔지니어 답변 처리 흐름을 실행한다.

    매뉴얼·이력 검색(신고 설비 필터) → 근거 충분성 판단 → 생성 → 검증 순으로 진행한다. 신고 설비 자료만으로
    근거가 부족하면 설비 필터 없이 질문 원문으로 다시 찾고(폴백), 그래도 부족하면 `insufficient_evidence`다.
    `answered`이면 인용을 검증하고, 아니면 보류 모양(`steps=[]`, `notice` 있음)을 확인한다.
    폴백 근거로 답한 `answered`는 인용의 설비가 신고 설비와 달라도 통과시키고 `notice`에 다른 설비 자료를
    포함했다는 고정 문구를 담는다.

    Args:
        context: 신고 맥락. 신고한 에러코드로 대상 설비와 코드를 고정한다.
        request: 현재 질문과 저장된 대화 이력.
        settings: 검색 설정. `manual_k`, `history_k`, `evidence_threshold`.

    Returns:
        Answer: `answered`이면 출처가 붙은 단계(폴백이면 `notice`에 다른 설비 자료 안내). 근거가 부족하면
            조치 없이 안내 문구만 있는 `insufficient_evidence`(`steps=[]`).

    Raises:
        ValidationError: 질문이 비었거나 설정 값이 허용 범위를 벗어났을 때.
        DependencyError: 모델·검색 장애, 제한 시간 초과, 인용 검증 실패, 보류 판정 형식 오류일 때.
    """
    question = _validate_engineer_input(request, settings)

    error_code = context.error_code
    equipment = error_code.equipment

    search_query = f"{error_code.code} {error_code.name} {question}"

    manual_hits = retriever.search_manual(
        equipment, search_query, settings.manual_k
    )
    history_hits = retriever.search_history(
        equipment, search_query, settings.history_k
    )


    hits = manual_hits + history_hits
    cross_equipment = False
    if not retriever.has_enough_evidence(hits, settings.evidence_threshold):
        # 신고 설비 자료만으로 부족하면 설비 필터 없이 4종 전체에서 질문 원문으로 한 번 더 찾는다.
        manual_hits = retriever.search_manual(None, question, settings.manual_k)
        history_hits = retriever.search_history(None, question, settings.history_k)
        hits = manual_hits + history_hits
        if not retriever.has_enough_evidence(hits, settings.evidence_threshold):
            return _make_hold_answer("신고한 설비의 매뉴얼·이력에서 관련 근거를 찾지 못했습니다.")
        cross_equipment = True

    answer = generator.generate_engineer_answer(context, hits, request, cross_equipment=cross_equipment)
    if answer.verdict != Verdict.ANSWERED:
        if not _is_valid_hold(answer):
            raise DependencyError("답변의 보류 판정 형식 검증에 실패했습니다.")
        return answer
    if not generator.passes_answer_check(answer, hits, equipment, allow_other_equipment=cross_equipment):
        raise DependencyError("답변의 인용 검증에 실패했습니다.")
    if cross_equipment:
        return Answer(verdict=answer.verdict, steps=answer.steps, notice=CROSS_EQUIPMENT_NOTICE)
    return answer
