"""고객 안내·엔지니어 답변의 단계 조립.

역할:
    retriever와 generator를 순서대로 엮어, 신고 한 건 또는 질문 하나를 `Answer` 하나로 만든다.

처리 순서(spec §2.6): 범위 확인 → 근거 확보 → 생성 → 검증.
    - 고객 안내: 근거는 허용 절차를 직접 조회한다(검색하지 않는다).
    - 엔지니어 답변: 근거는 `manual`·`history` 검색으로 얻는다. 시작값은 각 3건(`manual_k`, `history_k`).

책임:
    - 범위 밖·근거 부족은 예외가 아니라 조치 단계가 없는(`steps=[]`) `Answer`로 돌려준다.
      범위 밖 설비 질문은 `not_applicable`, 근거 없이 다른 주제·다른 코드를 묻는 질문은 `insufficient_evidence`다.
    - 모델·검색 장애와 검증 실패는 `DependencyError`로 올린다.
    - 범위 밖 키워드 상수 `OUT_OF_SCOPE_EQUIPMENT_KEYWORDS`와 4종 설비 별칭을 이 모듈에서 관리한다.
    - 대상 코드는 신고한 코드로 고정한다.
    - 결과를 DB에 저장하지 않는다. 저장은 `service`가 맡는다.

의존: `rag.retriever`, `rag.generator`, `models`만 import한다(의존 규칙 §2.5). `db.rdb`는 부르지 않는다.
호출: `service`.
소유: RAG 파트
"""

from __future__ import annotations

from mpilot.models import Answer, CaseContext, ChatRequest, SearchSettings, ValidationError, Verdict

#범위 밖 설비 키워드와 4종 설비 별칭(값은 예시, 팀 확정 필요)
OUT_OF_SCOPE_EQUIPMENT_KEYWORDS: tuple[str, ...] = ("컨베이어", "지게차", "엘리베이터")

# 4종 설비 별칭(소문자로 비교한다). 값은 예시이며 팀 확정이 필요하다.
EQUIPMENT_ALIASES: dict[str, tuple[str, ...]] = {
    "Mini Loader": ("mini loader", "미니로더", "미니 로더"),
    "SRM": ("srm", "스태커크레인", "스태커 크레인"),
    "AGV": ("agv", "무인운반차"),
    "RTV": ("rtv", "레일대차"),
}

MAX_QUESTION_LEN = 2000  # 질문 최대 길이: 공백 제거 후 1~2,000자

def _hold(verdict: Verdict, notice : str) -> Answer:
    """보류 답변을 만든다. 보류는 예외가 아니라 조치 단계가 없는 Answer다. """
    return Answer(verdict=verdict, steps=[], notice=notice)

def _mentions(text: str, words: tuple[str, ...]) -> bool:
    """글 안에 단어가 하나라도 들어 있는지 대소문자 구분 없이 확인한다."""
    lowered = text.lower()
    return any(word.lower() in lowered for word in words)


def _is_out_of_scope(question: str, equipment: str) -> bool:
    """질문이 신고 설비 밖을 묻는지 판단한다."""
    if _mentions(question, OUT_OF_SCOPE_EQUIPMENT_KEYWORDS):
        return True
    if _mentions(question, EQUIPMENT_ALIASES[equipment]):
        return False
    return any(
        _mentions(question, aliases)
        for name, aliases in EQUIPMENT_ALIASES.items()
        if name != equipment
    )


def _validate_engineer_input(request: ChatRequest, settings: SearchSettings) -> str:
    """질문과 검색 설정을 검사하고, 공백을 정리한 질문을 돌려준다."""
    question = request.question.strip()
    if not question or len(question) > MAX_QUESTION_LEN :
        raise ValidationError("질문은 공백 제외 1~2,000자여야 합니다.")
    if settings.manual_k < 1 or settings.history_k < 1 or settings.evidence_threshold < 0:
        raise ValidationError("검색 설정 값이 허용 범위를 벗어났습니다.")
    return question 



def run_customer_guidance(context: CaseContext) -> Answer:
    """고객 안내 처리 흐름을 실행한다.

    허용 절차 직접 조회 → 생성 → 인용·안전 문구 검증 순으로 진행한다. 허용 절차가 없거나 만들 수 없으면
    추측하지 않고 보류 `Answer`를 돌려준다.

    Args:
        context: 신고 맥락. 대상 에러코드의 상세(허용 절차 ID, 필수 안전 문구 포함)와 증상 메모.

    Returns:
        Answer: `answered`이면 출처가 붙은 단계별 안내. 만들 수 없으면 `steps=[]`인 보류 판정이며,
            `service`가 이를 엔지니어 접수(`held`)로 바꾼다.

    Raises:
        DependencyError: 모델·검색 장애, 제한 시간 초과, 인용·설비·안전 수칙 검증 실패일 때
            (`service`가 엔지니어 접수 `generation_failed`로 바꾼다).
    """
    raise NotImplementedError("PR3애서 구현")


def run_engineer_answer(
    context: CaseContext, request: ChatRequest, settings: SearchSettings
) -> Answer:
    """엔지니어 답변 처리 흐름을 실행한다.

    범위 확인(대상 설비 밖이면 `not_applicable`) → 매뉴얼·이력 검색 → 근거 충분성 판단(부족하면
    `insufficient_evidence`) → 생성 → 인용 검증 순으로 진행한다.

    Args:
        context: 신고 맥락. 신고한 에러코드로 대상 설비와 코드를 고정한다.
        request: 현재 질문과 저장된 대화 이력.
        settings: 검색 설정. `manual_k`, `history_k`, `evidence_threshold`.

    Returns:
        Answer: `answered`이면 출처가 붙은 단계. 범위 밖이거나 근거가 부족하면 조치 없이 안내 문구만 있는
            보류 판정(`steps=[]`).

    Raises:
        ValidationError: 질문이 비었거나 설정 값이 허용 범위를 벗어났을 때.
        DependencyError: 모델·검색 장애, 제한 시간 초과, 인용 검증 실패일 때.
    """
    question = _validate_engineer_input(request, settings)

    equipment = context.error_code.equipment  # 대상 설비·코드는 신고 코드로 고정
    if _is_out_of_scope(question, equipment):
        return _hold(Verdict.NOT_APPLICABLE, "대상 설비 밖의 질문이라 답할 수 없습니다.")

    raise NotImplementedError("PR2에서 구현: 근거 확보 → 생성 → 검증")
