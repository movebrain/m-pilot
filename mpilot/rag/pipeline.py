"""고객 안내·엔지니어 답변의 단계 조립.

역할:
    retriever와 generator를 순서대로 엮어, 신고 한 건 또는 질문 하나를 `Answer` 하나로 만든다.

처리 순서(spec §2.6): 근거 확보 → 생성 → 검증.
    - 고객 안내: 근거는 신고 코드의 `manual`만 검색한다(신고 설비 필터, 다른 코드 청크 제외).
      폴백(필터 없는 재검색)과 `history` 검색은 하지 않는다.
    - 엔지니어 답변: 근거는 `manual`·`history` 검색으로 얻는다. 시작값은 각 3건(`manual_k`, `history_k`).
      LangGraph 그래프로 질문 라우팅 → 검색 → 생성 → 검증(실패 시 재생성) → 보류 시 폴백을 잇는다.

책임:
    - 근거가 충분한지는 거리 임계값이 아니라 모델이 판정한다. 검색 결과를 생성기에 넘기고, 모델이
      `insufficient_evidence`를 내면 보류다. 보류는 예외가 아니라 조치 단계가 없는(`steps=[]`) `Answer`로 돌려준다.
    - 엔지니어 질문은 먼저 어느 설비를 묻는지 정한다(설비 이름·에러코드 규칙, 못 정하면 모델). 다른 설비를
      물으면 그 설비 필터로 찾아 답하고 `notice`로 알린다. 4종 밖 설비나 설비 언급이 없으면 신고 설비로 찾는다.
    - 엔지니어 답변이 보류되면 설비 필터 없이 4종 전체를 질문 원문으로 한 번 더 찾아 답한다(폴백).
      폴백 근거로 답하면 다른 설비 자료가 섞였음을 `notice`로 알린다.
    - 모델·검색 장애와 (재생성 뒤에도) 검증 실패는 `DependencyError`로 올린다.
    - 고객 안내의 대상 코드는 신고한 코드로 고정한다.
    - 결과를 DB에 저장하지 않는다. 저장은 `service`가 맡는다.

의존: `rag.retriever`, `rag.generator`, `models`, `langgraph`만 import한다(의존 규칙 §2.5). `db.rdb`는 부르지 않는다.
호출: `service`.
소유: RAG 파트
"""

from __future__ import annotations

import re
from functools import cache
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from mpilot.models import (
    Answer,
    CaseContext,
    ChatRequest,
    CitationKind,
    DependencyError,
    Equipment,
    SearchHit,
    SearchSettings,
    ValidationError,
    Verdict,
)
from mpilot.rag import generator, retriever

MAX_QUESTION_LEN = 2000  # 질문 최대 길이: 공백 제거 후 1~2,000자
# 신고 코드의 매뉴얼 검색은 `manual_k`건보다 이 배수만큼 가져와 다른 코드의 청크를 거른 뒤 `manual_k`건을 남긴다.
# 에러코드 절마다 구성이 비슷해 상위에 다른 코드의 청크가 많이 섞이기 때문이다.
MANUAL_FETCH_FACTOR = 5
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
    query = f"{error_code.code} {error_code.name}"
    hits = _search_manual_for_code(error_code.equipment, query, settings.manual_k, error_code.code)
    if not hits:  # 근거가 하나도 없으면 모델을 부르지 않는다
        return _make_hold_answer("매뉴얼에서 고객이 직접 조치할 수 있는 근거를 찾지 못해 엔지니어가 접수합니다.")

    answer = generator.generate_customer_guidance(context, hits)
    _verify_generated(answer, hits, error_code.equipment, "고객 안내")
    return answer


def _search_manual_for_code(equipment: Equipment, query: str, k: int, code: str) -> list[SearchHit]:
    """신고 코드의 매뉴얼 근거를 찾는다. 다른 에러코드 절의 청크는 뺀다.

    에러코드 절마다 구성(원인 후보 표, 안전 확보 절차 등)이 비슷해서, 같은 질문에 다른 코드의 청크가 상위에
    섞여 나온다. 그래서 `k`건보다 넉넉히 가져와 다른 코드의 문서(`SearchHit.code`가 신고 코드와 다른 매뉴얼 청크와
    코드 표 행)를 걸러 내고 앞에서 `k`건을 남긴다. 코드가 없는 청크(공통 절)는 그대로 둔다.

    Args:
        equipment: 검색 대상 설비.
        query: 검색 질의 문장.
        k: 돌려줄 상위 건수(`SearchSettings.manual_k`).
        code: 신고한 에러코드.

    Returns:
        list[SearchHit]: 가까운 순으로 최대 `k`건. 걸러내고 남은 것이 `k`건보다 적으면 그만큼만 돌려준다.

    Raises:
        ValidationError: 질의가 비었거나 `k`가 1보다 작을 때(`retriever.search`가 낸다).
        DependencyError: 임베딩 생성이나 저장소 검색에 실패했을 때(`retriever.search`가 낸다).
    """
    hits = retriever.search(CitationKind.MANUAL, equipment, query, k * MANUAL_FETCH_FACTOR)
    return [hit for hit in hits if hit.code in (None, code)][:k]


# --- 엔지니어 답변: LangGraph 흐름 ---------------------------------------------
#
#   route → retrieve → generate → verify ─┬─ answered ──────────────→ finalize → END
#                         ▲                ├─ 검증 실패(재시도 남음) → generate
#              fallback ──┘                ├─ 보류(1차) ─────────────→ fallback
#                                          └─ 보류(폴백) ────────────→ finalize → END

# 설비 이름·별칭. 영문은 앞뒤가 영문자가 아닐 때만, 한글은 그대로 찾는다(소문자 비교).
_EQUIPMENT_ALIASES: dict[Equipment, tuple[str, ...]] = {
    Equipment.MINI_LOADER: ("mini loader", "mini-loader", "miniloader", "미니로더", "미니 로더"),
    Equipment.SRM: ("srm", "스태커 크레인", "스태커크레인"),
    Equipment.AGV: ("agv",),
    Equipment.RTV: ("rtv",),
}
# 에러코드 첫 글자로 설비를 안다(`data/errorcodes.csv`: Mini Loader M, SRM S, AGV A, RTV R).
_CODE_PREFIX: dict[str, Equipment] = {
    "M": Equipment.MINI_LOADER, "S": Equipment.SRM, "A": Equipment.AGV, "R": Equipment.RTV,
}
_CODE_PATTERN = re.compile(r"(?<![A-Z0-9])([MSAR])-?(\d{3})(?!\d)")
# 인용 검증에 실패하면 같은 근거로 다시 생성하는 횟수. 모델이 가끔 출처 없는 단계를 붙이기 때문이다.
MAX_REGENERATIONS = 1
ROUTED_EQUIPMENT_NOTICE = (
    "질문한 설비의 매뉴얼·이력으로 답했습니다. 신고한 설비와 다르므로 출처의 설비를 확인한 뒤 적용하세요."
)


class _EngineerState(TypedDict, total=False):
    """엔지니어 답변 그래프의 상태. 노드는 바뀐 키만 돌려준다."""

    context: CaseContext
    request: ChatRequest
    settings: SearchSettings
    question: str  # 앞뒤 공백을 지운 질문
    target: Equipment  # 검색·인용 검증 대상 설비(라우팅 결과)
    target_code: str | None  # 다른 설비로 라우팅될 때 질문에 적힌 그 설비의 에러코드
    fallback: bool  # 설비 필터 없이 다시 찾은 근거인가
    hits: list[SearchHit]
    answer: Answer
    verified: bool
    regenerations: int


def run_engineer_answer(
    context: CaseContext, request: ChatRequest, settings: SearchSettings
) -> Answer:
    """엔지니어 답변 처리 흐름을 실행한다.

    LangGraph 그래프로 라우팅 → 검색 → 생성 → 검증을 잇는다.

    - 라우팅: 질문에 설비 이름·별칭이나 에러코드가 한 설비만 나오면 그 설비로 정한다. 아무것도 없으면 모델에
      묻고(`generator.route_question`), 모델 호출이 실패하거나 여러 설비가 나오면 신고 설비로 둔다.
    - 검색: 대상 설비 필터로 `manual`·`history`를 찾는다. 신고 설비면 신고 코드·이름을 붙여 찾고 다른 코드의
      문서를 뺀다. 다른 설비면 질문 원문으로 찾고, 질문에 그 설비의 에러코드가 있으면 그 코드로 거른다.
    - 검증: `answered`이면 인용이 근거에 있고 대상 설비와 일치하는지 본다. 실패하면 같은 근거로
      `MAX_REGENERATIONS`번 다시 생성하고, 그래도 실패하면 `DependencyError`다.
    - 폴백: 모델이 보류하면 설비 필터 없이 4종 전체를 질문 원문으로 한 번 더 찾아 다시 생성한다.
    - 안내: 폴백 근거로 답하면 `CROSS_EQUIPMENT_NOTICE`, 다른 설비로 라우팅해 답하면
      `ROUTED_EQUIPMENT_NOTICE`를 `notice`에 담는다.

    Args:
        context: 신고 맥락. 신고한 에러코드가 라우팅의 기본 설비와 1차 검색어를 정한다.
        request: 현재 질문과 저장된 대화 이력.
        settings: 검색 설정. `manual_k`, `history_k`.

    Returns:
        Answer: `answered`이면 출처가 붙은 단계(다른 설비 근거면 `notice`에 안내). 모델이 폴백까지 근거 부족으로
            판정하면 조치 없이 안내 문구만 있는 `insufficient_evidence`(`steps=[]`).

    Raises:
        ValidationError: 질문이 비었거나 설정 값이 허용 범위를 벗어났을 때.
        DependencyError: 모델·검색 장애, 제한 시간 초과, 재생성 뒤에도 검증 실패일 때.
    """
    question = _validate_engineer_input(request, settings)
    state = _engineer_graph().invoke(
        {"context": context, "request": request, "settings": settings, "question": question}
    )
    return state["answer"]


@cache
def _engineer_graph():
    """엔지니어 답변 그래프를 한 번만 만들어 둔다.

    Returns:
        CompiledStateGraph: `invoke(state)`로 실행하는 그래프.
    """
    graph = StateGraph(_EngineerState)
    graph.add_node("route", _route_node)
    graph.add_node("retrieve", _retrieve_node)
    graph.add_node("fallback", _fallback_node)
    graph.add_node("generate", _generate_node)
    graph.add_node("verify", _verify_node)
    graph.add_node("finalize", _finalize_node)
    graph.add_edge(START, "route")
    graph.add_edge("route", "retrieve")
    graph.add_edge("retrieve", "generate")
    graph.add_edge("fallback", "generate")
    graph.add_edge("generate", "verify")
    graph.add_conditional_edges("verify", _after_verify, ["generate", "fallback", "finalize"])
    graph.add_edge("finalize", END)
    return graph.compile()


def _route_node(state: _EngineerState) -> dict:
    """질문이 묻는 설비(`target`)와 그 설비의 에러코드(`target_code`)를 정한다."""
    reported = state["context"].error_code.equipment
    target, code = _route_by_rules(state["question"], reported)
    if target is None:
        try:
            target = generator.route_question(state["context"], state["question"])
        except DependencyError:
            target = reported  # 라우팅은 보조 단계라 모델이 실패해도 신고 설비로 계속한다
    return {
        "target": target,
        "target_code": code if target != reported else None,
        "fallback": False,
        "regenerations": 0,
    }


def _route_by_rules(question: str, reported: Equipment) -> tuple[Equipment | None, str | None]:
    """질문에 적힌 설비 이름·별칭과 에러코드로 설비를 정한다.

    Args:
        question: 앞뒤 공백을 지운 질문.
        reported: 신고 설비. 여러 설비가 나올 때의 기본값이다.

    Returns:
        tuple[Equipment | None, str | None]: (설비, 질문에 적힌 그 설비의 에러코드). 설비가 하나만 나오면 그 설비,
            여럿이면 신고 설비, 하나도 없으면 `None`(모델에 묻는다). 코드는 그 설비의 코드가 하나일 때만 담는다.
    """
    lowered = question.lower()
    mentioned: set[Equipment] = set()
    for equipment, aliases in _EQUIPMENT_ALIASES.items():
        for alias in aliases:
            pattern = rf"(?<![a-z]){re.escape(alias)}(?![a-z])" if alias.isascii() else re.escape(alias)
            if re.search(pattern, lowered):
                mentioned.add(equipment)
    codes: dict[Equipment, set[str]] = {}
    for prefix, digits in _CODE_PATTERN.findall(question.upper()):
        codes.setdefault(_CODE_PREFIX[prefix], set()).add(prefix + digits)
    mentioned |= codes.keys()
    if not mentioned:
        return None, None
    if len(mentioned) > 1:
        return reported, None
    target = mentioned.pop()
    target_codes = codes.get(target, set())
    return target, (next(iter(target_codes)) if len(target_codes) == 1 else None)


def _retrieve_node(state: _EngineerState) -> dict:
    """대상 설비 필터로 `manual`·`history` 근거를 찾는다."""
    error_code = state["context"].error_code
    settings, question, target = state["settings"], state["question"], state["target"]
    if target == error_code.equipment:
        query = f"{error_code.code} {error_code.name} {question}"
        manual_hits = _search_manual_for_code(target, query, settings.manual_k, error_code.code)
    elif state["target_code"]:
        query = question
        manual_hits = _search_manual_for_code(target, query, settings.manual_k, state["target_code"])
    else:
        query = question
        manual_hits = retriever.search(CitationKind.MANUAL, target, query, settings.manual_k)
    history_hits = retriever.search(CitationKind.HISTORY, target, query, settings.history_k)
    return {"hits": manual_hits + history_hits}


def _fallback_node(state: _EngineerState) -> dict:
    """설비 필터 없이 4종 전체를 질문 원문으로 다시 찾는다(폴백)."""
    settings, question = state["settings"], state["question"]
    manual_hits = retriever.search(CitationKind.MANUAL, None, question, settings.manual_k)
    history_hits = retriever.search(CitationKind.HISTORY, None, question, settings.history_k)
    return {"hits": manual_hits + history_hits, "fallback": True, "regenerations": 0}


def _generate_node(state: _EngineerState) -> dict:
    """근거로 답변을 생성한다. 근거가 신고 설비 자료가 아니면 다른 설비 근거 규칙을 붙인다."""
    other_equipment = state["fallback"] or state["target"] != state["context"].error_code.equipment
    answer = generator.generate_engineer_answer(
        state["context"], state["hits"], state["request"], cross_equipment=other_equipment
    )
    return {"answer": answer}


def _verify_node(state: _EngineerState) -> dict:
    """답변을 검증한다. 재생성 기회가 남았으면 실패를 `verified=False`로 남기고, 없으면 예외를 그대로 올린다."""
    try:
        _verify_generated(
            state["answer"], state["hits"], state["target"], "답변", allow_other_equipment=state["fallback"]
        )
    except DependencyError:
        if state["regenerations"] >= MAX_REGENERATIONS:
            raise
        return {"verified": False, "regenerations": state["regenerations"] + 1}
    return {"verified": True}


def _after_verify(state: _EngineerState) -> str:
    """검증 뒤에 갈 노드를 고른다."""
    if not state["verified"]:
        return "generate"
    if state["answer"].verdict == Verdict.ANSWERED or state["fallback"]:
        return "finalize"
    return "fallback"


def _finalize_node(state: _EngineerState) -> dict:
    """다른 설비 근거로 답했으면 고정 안내 문구를 붙인다."""
    answer = state["answer"]
    if answer.verdict != Verdict.ANSWERED:
        return {}
    if state["fallback"]:
        notice = CROSS_EQUIPMENT_NOTICE
    elif state["target"] != state["context"].error_code.equipment:
        notice = ROUTED_EQUIPMENT_NOTICE
    else:
        return {}
    return {"answer": Answer(verdict=answer.verdict, steps=answer.steps, notice=notice)}
