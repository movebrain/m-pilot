"""고객 안내·엔지니어 답변의 단계 조립.

역할:
    retriever와 generator를 순서대로 엮어, 신고 한 건 또는 질문 하나를 `Answer` 하나로 만든다.

처리 순서(spec §2.6): 근거 확보 → 생성 → 검증.
    - 고객 안내: 근거는 신고 코드의 `manual`만 검색한다(신고 설비 필터, 다른 코드 청크 제외).
      폴백(필터 없는 재검색)과 `history` 검색은 하지 않는다.
    - 엔지니어 답변: 근거는 `manual`·`history` 검색으로 얻는다. 시작값은 각 3건(`manual_k`, `history_k`).

책임:
    - 근거가 충분한지는 거리 임계값이 아니라 모델이 판정한다. 검색 결과를 생성기에 넘기고, 모델이
      `insufficient_evidence`를 내면 보류다. 보류는 예외가 아니라 조치 단계가 없는(`steps=[]`) `Answer`로 돌려준다.
      4종 밖 설비·다른 설비를 말한 질문도 먼저 신고 설비 자료로 답을 시도한다.
    - 엔지니어 답변이 신고 설비 자료로 보류되면 설비 필터 없이 4종 전체를 질문 원문으로 한 번 더 찾아 답한다(폴백).
      폴백 근거로 답하면 다른 설비 자료가 섞였음을 `notice`로 알린다.
    - 모델·검색 장애와 검증 실패는 `DependencyError`로 올린다.
    - 대상 코드는 신고한 코드로 고정한다.
    - 결과를 DB에 저장하지 않는다. 저장은 `service`가 맡는다.

의존: `rag.retriever`, `rag.generator`, `models`만 import한다(의존 규칙 §2.5). `db.rdb`는 부르지 않는다.
호출: `service`.
소유: RAG 파트
"""

from __future__ import annotations

from mpilot.models import (
    Answer,
    CaseContext,
    ChatRequest,
    CitationKind,
    DependencyError,
    Equipment,
    ErrorCode,
    SearchHit,
    SearchSettings,
    ValidationError,
    Verdict,
)
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


def _verify_generated(
    answer: Answer, hits: list[SearchHit], equipment: Equipment, what: str, allow_other_equipment: bool = False
) -> None:
    """생성 결과를 검증한다. 문제가 없으면 아무것도 돌려주지 않고, 문제가 있으면 예외를 낸다.

    `answered`이면 `generator.passes_check`로 인용을 검증한다. 아니면 검증 함수를
    부르지 않고 보류 모양(`steps=[]`, 공백이 아닌 `notice`)만 확인한다.

    Args:
        answer: 생성 함수가 돌려준 답변.
        hits: 생성에 쓴 근거(인용의 기준).
        equipment: 신고 설비. 인용한 근거의 설비가 같아야 한다.
        what: 오류 문구에 쓸 결과 이름(`고객 안내`, `답변`).
        allow_other_equipment: 폴백 답변이면 `True`. 인용의 설비가 달라도 통과시킨다.

    Raises:
        DependencyError: 보류 모양이 틀렸거나 검증에 실패했을 때.
    """
    if answer.verdict == Verdict.ANSWERED:
        if not generator.passes_check(answer, hits, equipment, allow_other_equipment):
            raise DependencyError(f"{what}의 인용 검증에 실패했습니다.")
    elif answer.steps or not (answer.notice and answer.notice.strip()):
        raise DependencyError(f"{what}의 보류 판정 형식 검증에 실패했습니다.")


def _validate_engineer_input(request: ChatRequest, settings: SearchSettings) -> str:
    """질문과 검색 설정을 검사하고, 공백을 정리한 질문을 돌려준다.

    Args:
        request: 현재 질문과 저장된 대화 이력.
        settings: 검색 설정. `manual_k`, `history_k`.

    Returns:
        str: 앞뒤 공백을 지운 질문.

    Raises:
        ValidationError: 질문이 비었거나 `MAX_QUESTION_LEN`자를 넘을 때,
            또는 `manual_k`·`history_k`가 1보다 작을 때.
    """
    question = request.question.strip()
    if not question or len(question) > MAX_QUESTION_LEN:
        raise ValidationError("질문은 공백 제외 1~2,000자여야 합니다.")
    if settings.manual_k < 1 or settings.history_k < 1:
        raise ValidationError("검색 설정 값이 허용 범위를 벗어났습니다.")
    return question


def run_customer_guidance(context: CaseContext, settings: SearchSettings) -> Answer:
    """고객 안내 처리 흐름을 실행한다.

    신고 코드의 매뉴얼 검색(신고 설비 필터, 다른 코드 청크 제외) → 생성 → 검증 순으로 진행한다.
    엔지니어 답변과 달리 폴백(필터 없는 재검색)과 `history` 검색은 하지 않는다. 검색 결과가 없거나 생성 결과의
    `verdict`가 `answered`가 아니면 추측하지 않고 `passes_check`를 부르지 않은 채, 보류 모양
    (`steps=[]`, `notice` 있음)만 확인해 그 `Answer`를 돌려준다. `answered`이면 인용을 검증한다.

    Args:
        context: 신고 맥락. 대상 에러코드의 상세와 증상 메모.
        settings: 검색 설정. `manual_k`만 쓴다.

    Returns:
        Answer: `answered`이고 `passes_check`를 통과하면 출처가 붙은 단계별 안내. 검색 결과가 없거나
            생성 결과가 `answered`가 아니면 `steps=[]`인 보류 판정이며, `service`가 이를
            `verdict`만 보고 엔지니어 접수(`held`)로 바꾼다.

    Raises:
        ValidationError: `manual_k`가 1보다 작을 때(`service`가 엔지니어 접수 `generation_failed`로 바꾼다).
        DependencyError: 모델·검색 장애, 제한 시간 초과, 인용·설비 검증 실패, 보류 판정 형식 오류일 때
            (`service`가 엔지니어 접수 `generation_failed`로 바꾼다).
    """
    error_code = context.error_code
    hits = retriever.search(
        CitationKind.MANUAL, error_code.equipment, customer_query(error_code), settings.manual_k, error_code.code
    )
    if not hits:  # 근거가 하나도 없으면 모델을 부르지 않는다
        return _make_hold_answer("매뉴얼에서 고객이 직접 조치할 수 있는 근거를 찾지 못해 엔지니어가 접수합니다.")

    answer = generator.generate_customer_guidance(context, hits)
    _verify_generated(answer, hits, error_code.equipment, "고객 안내")
    return answer


def customer_query(error_code: ErrorCode) -> str:
    """고객 안내의 검색 질의. 평가가 운영과 같은 질의로 검색하도록 공개한다.

    Args:
        error_code: 신고한 에러코드.

    Returns:
        str: `<코드> <이름>`.
    """
    return f"{error_code.code} {error_code.name}"


def engineer_query(error_code: ErrorCode, question: str) -> str:
    """엔지니어 답변의 1차 검색 질의. 평가가 운영과 같은 질의로 검색하도록 공개한다. 폴백은 질문 원문을 쓴다.

    Args:
        error_code: 신고한 에러코드.
        question: 앞뒤 공백을 지운 엔지니어 질문.

    Returns:
        str: `<코드> <이름> <질문>`.
    """
    return f"{error_code.code} {error_code.name} {question}"


def run_engineer_answer(
    context: CaseContext, request: ChatRequest, settings: SearchSettings
) -> Answer:
    """엔지니어 답변 처리 흐름을 실행한다.

    매뉴얼·이력 검색(신고 설비 필터) → 생성 → 검증 순으로 진행한다. 매뉴얼은 신고 코드와 다른
    에러코드 절의 청크를 빼고 남긴다(`retriever.search`의 `code`). 모델이 `answered`가 아니면
    설비 필터 없이 질문 원문으로 한 번 더 찾아 다시 생성하고(폴백), 그래도 `answered`가 아니면 그 보류를 돌려준다.
    `answered`이면 인용을 검증하고, 아니면 보류 모양(`steps=[]`, `notice` 있음)을 확인한다.
    폴백 근거로 답한 `answered`는 인용의 설비가 신고 설비와 달라도 통과시키고 `notice`에 다른 설비 자료를
    포함했다는 고정 문구를 담는다.

    Args:
        context: 신고 맥락. 신고한 에러코드로 대상 설비와 코드를 고정한다.
        request: 현재 질문과 저장된 대화 이력.
        settings: 검색 설정. `manual_k`, `history_k`.

    Returns:
        Answer: `answered`이면 출처가 붙은 단계(폴백이면 `notice`에 다른 설비 자료 안내). 모델이 두 번 모두
            근거 부족으로 판정하면 조치 없이 안내 문구만 있는 `insufficient_evidence`(`steps=[]`).

    Raises:
        ValidationError: 질문이 비었거나 설정 값이 허용 범위를 벗어났을 때.
        DependencyError: 모델·검색 장애, 제한 시간 초과, 인용 검증 실패, 보류 판정 형식 오류일 때.
    """
    question = _validate_engineer_input(request, settings)

    error_code = context.error_code
    equipment = error_code.equipment
    search_query = engineer_query(error_code, question)

    manual_hits = retriever.search(CitationKind.MANUAL, equipment, search_query, settings.manual_k, error_code.code)
    history_hits = retriever.search(CitationKind.HISTORY, equipment, search_query, settings.history_k)
    answer = _generate_engineer_answer(context, manual_hits + history_hits, request, cross_equipment=False)
    if answer.verdict == Verdict.ANSWERED:
        return answer

    # 신고 설비 자료로 답하지 못했으면 설비 필터 없이 4종 전체에서 질문 원문으로 한 번 더 찾는다.
    manual_hits = retriever.search(CitationKind.MANUAL, None, question, settings.manual_k)
    history_hits = retriever.search(CitationKind.HISTORY, None, question, settings.history_k)
    answer = _generate_engineer_answer(context, manual_hits + history_hits, request, cross_equipment=True)
    if answer.verdict == Verdict.ANSWERED:
        # 폴백 근거로 답했으면 다른 설비 자료가 섞였다는 고정 문구를 붙인다.
        return Answer(verdict=answer.verdict, steps=answer.steps, notice=CROSS_EQUIPMENT_NOTICE)
    return answer


def _generate_engineer_answer(
    context: CaseContext, hits: list[SearchHit], request: ChatRequest, cross_equipment: bool
) -> Answer:
    """엔지니어 답변을 생성하고 검증한다.

    Args:
        context: 신고 맥락. 신고 설비로 인용의 설비를 확인한다.
        hits: 생성에 넘길 근거.
        request: 현재 질문과 저장된 대화 이력.
        cross_equipment: `True`이면 `hits`에 다른 설비 근거가 섞인 폴백이며, 인용의 설비가 달라도 통과시킨다.

    Returns:
        Answer: 검증을 통과한 답변 또는 보류 모양이 맞는 보류 판정.

    Raises:
        DependencyError: 모델 장애, 인용 검증 실패, 보류 판정 형식 오류일 때.
    """
    answer = generator.generate_engineer_answer(context, hits, request, cross_equipment=cross_equipment)
    _verify_generated(answer, hits, context.error_code.equipment, "답변", allow_other_equipment=cross_equipment)
    return answer