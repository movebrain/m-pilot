"""모델 호출과 답변 형식·인용 검증.

역할:
    LangChain 채팅 모델을 호출해 구조화된 `Answer`를 만들고, 만들어진 답이 보여 줄 수 있는지 검사한다.

책임:
    - 고객 안내와 엔지니어 답변을 프롬프트(`prompts/`)로 생성한다. 모든 단계(`AnswerStep`)에는 출처가 1개 이상 붙는다.
    - 엔지니어 질문이 어느 설비를 묻는지 모델로 판별한다(`route_question`, 규칙으로 못 정한 질문만).
    - 원문 밖의 원인·절차를 단정하지 않는다. 증상 메모는 확인된 사실과 구분한다(프롬프트 규칙).
    - 검증: 인용이 확보한 근거에 실제로 있고 설비가 일치하는지 검사한다(`passes_check`, 고객 안내와 엔지니어 답변 공용).
      출처 ID 검증은 내용의 정확성·안전성을 증명하지 않는다. 그 부분은 프롬프트 제약과 사람의 사후 평가가 맡는다.
    - 검증을 통과하지 못한 답은 보여 주지 않는다. 이 모듈은 통과 여부만 알려 주고, 실패를 `DependencyError`로
      올리는 일은 `pipeline`이 맡는다(spec §2.6).
    - `passes_check`는 `answer.verdict`가 `answered`일 때만 의미가 있다. `pipeline`은 `answered`가
      아니면(근거 부족) 이 함수를 부르지 않고 그 `Answer`를 그대로 돌려준다(spec §2.10의
      `held`). `answered`인데 이 함수가 `False`면 `pipeline`이 `DependencyError`로 올려 `generation_failed`로
      접수한다.
    - 모델은 `set_chat_model`로 받는다(temperature=0). 모듈을 import할 때 API에 접속하지 않는다.
    - 검색·DB에는 접근하지 않는다.

의존: `models`만 import한다(의존 규칙 §2.5).
호출: `rag.pipeline`, `app`(`set_chat_model`).
소유: RAG 파트
"""

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from mpilot.models import (
    Answer,
    CaseContext,
    ChatRequest,
    ChatTurn,
    DependencyError,
    Equipment,
    SearchHit,
)
from mpilot.rag import prompts

# 생성 입력에 넣을 최근 대화 턴 수. 저장된 대화는 지우지 않고 입력에서만 줄인다.
_MAX_HISTORY_TURNS = 6

# 고객 메모의 최대 길이. 너무 긴 글이 프롬프트를 채우지 못하게 자른다.
_MAX_MEMO_CHARS = 2000
_chat_model: BaseChatModel | None = None


def set_chat_model(chat_model: BaseChatModel) -> None:
    """답변 생성에 쓸 모델을 설정한다.

    Args:
        chat_model: 답변 생성에 쓸 LangChain 채팅 모델. `app`이 설정(`chat_model_name`, 제한 시간)으로 만든다.

    Returns:
        None: 모듈 안에 모델을 보관한다. 이후 생성 함수는 이 모델을 쓴다.
    """
    global _chat_model
    _chat_model = chat_model


def generate_customer_guidance(context: CaseContext, hits: list[SearchHit]) -> Answer:
    """고객 안내 답변을 생성한다.

    신고 코드의 매뉴얼 검색 근거(`hits`)만 근거로 쓴다. 과거 이력은 쓰지 않는다.

    Args:
        context: 신고 맥락. 대상 에러코드의 상세와 증상 메모.
        hits: `pipeline`이 신고 코드의 매뉴얼에서 검색해 가려 낸 근거.

    Returns:
        Answer: 단계마다 출처가 붙은 안내. 이 함수는 생성만 하며, 인용 검사는
            `passes_check`가 한다.

    Raises:
        DependencyError: 모델 호출 실패나 제한 시간 초과일 때.
    """
    error_code = context.error_code
    human = prompts.CUSTOMER_HUMAN.format(
        equipment=error_code.equipment.value,
        code=error_code.code,
        name=error_code.name,
        severity=error_code.severity.value,
        memo=_format_memo_for_prompt(context.memo),
        evidence=_format_evidence(hits),
    )
    return _invoke([SystemMessage(content=prompts.CUSTOMER_SYSTEM), HumanMessage(content=human)])


def generate_engineer_answer(
    context: CaseContext, hits: list[SearchHit], request: ChatRequest, cross_equipment: bool = False
) -> Answer:
    """엔지니어 대화 답변을 생성한다.

    Args:
        context: 신고 맥락. 대상 에러코드의 상세와 증상 메모.
        hits: 매뉴얼·이력 검색으로 얻은 근거.
        request: 현재 질문과 저장된 대화. 대화 이력은 생성 입력에서만 줄이고 저장본은 지우지 않는다.
        cross_equipment: `True`이면 `hits`에 신고 설비가 아닌 설비의 근거가 섞여 있다는 뜻이다. 근거에
            설비를 밝히고 다른 설비 근거로 답하게 하는 규칙을 시스템 지시문에 덧붙인다.

    Returns:
        Answer: 단계마다 출처가 붙은 답변. 인용 검사는 `passes_check`가 한다.

    Raises:
        DependencyError: 모델 호출 실패나 제한 시간 초과일 때.
    """
    error_code = context.error_code
    human = prompts.ENGINEER_HUMAN.format(
        equipment=error_code.equipment.value,
        code=error_code.code,
        name=error_code.name,
        cause=error_code.cause,
        action=error_code.action,
        memo=_format_memo_for_prompt(context.memo),
        history=_format_history(request.turns),
        evidence=_format_evidence(hits, with_equipment=cross_equipment),
        question=_escape_tag_text(request.question),
    )
    system = prompts.ENGINEER_SYSTEM + (prompts.ENGINEER_CROSS_EQUIPMENT_RULES if cross_equipment else "")
    return _invoke([SystemMessage(content=system), HumanMessage(content=human)])


class RouteDecision(BaseModel):
    """질문 라우팅 결과. 엔지니어 질문이 가리키는 설비다.

    Attributes:
        equipment: 질문이 묻는 설비. 특정 설비를 가리키지 않거나 4종 밖 설비면 신고 설비다.
    """

    equipment: Equipment


def route_question(context: CaseContext, question: str) -> Equipment:
    """엔지니어 질문이 4종 중 어느 설비에 대한 것인지 모델에 묻는다.

    `pipeline`이 설비 이름·에러코드 규칙으로 정하지 못한 질문에만 부른다(동의어·우회 표현 처리용).

    Args:
        context: 신고 맥락. 신고 설비를 기본값으로 알려 준다.
        question: 앞뒤 공백을 지운 현재 질문.

    Returns:
        Equipment: 질문이 묻는 설비. 특정 설비를 가리키지 않으면 신고 설비다.

    Raises:
        DependencyError: 모델이 설정되지 않았거나 호출이 실패했을 때.
    """
    if _chat_model is None:
        raise DependencyError("답변 생성 모델이 설정되지 않았습니다(set_chat_model 호출 필요).")
    human = prompts.ROUTER_HUMAN.format(
        equipment=context.error_code.equipment.value, question=_escape_tag_text(question)
    )
    try:
        result = _chat_model.with_structured_output(RouteDecision).invoke(
            [SystemMessage(content=prompts.ROUTER_SYSTEM), HumanMessage(content=human)]
        )
    except Exception as exc:
        # 예외 본문에 요청 내용이 섞일 수 있어 종류만 남긴다.
        print("질문 라우팅 모델 호출 실패:", type(exc).__name__)
        raise DependencyError("질문 라우팅 모델 호출에 실패했습니다.") from exc
    return result.equipment


def passes_check(
    answer: Answer, hits: list[SearchHit], equipment: Equipment, allow_other_equipment: bool = False
) -> bool:
    """답변의 인용을 검사한다. 고객 안내와 엔지니어 답변이 함께 쓴다.

    `answered`인데 단계가 없거나, 인용이 `hits`에 없거나(`locator` 포함) 설비가 다르면 통과하지 못한다.
    고객 안내는 `hits`가 매뉴얼 검색 결과뿐이라, 이력 인용은 `hits`에 없어서 이미 막힌다.

    Args:
        answer: 검사할 답변. `answered`인 `Answer`에만 쓴다고 가정한다(`pipeline`이 먼저 가린다).
        hits: 답변 생성에 쓴 검색 근거(인용의 기준).
        equipment: 신고 대상 설비. 인용한 근거의 설비가 이것과 같아야 한다.
        allow_other_equipment: `True`이면 인용한 근거의 설비가 신고 설비와 달라도 통과시킨다.
            신고 설비 자료로 답하지 못해 필터 없이 다시 찾은 답변(폴백)에만 쓴다.

    Returns:
        bool: 모든 인용이 `hits`에 있고 설비가 일치하면(`allow_other_equipment`가 아니라면) `True`, 아니면 `False`.
    """
    if _is_answered(answer) and not answer.steps:
        return False
    return _citations_valid(answer, hits, equipment, allow_other_equipment)


def _invoke(messages: list[BaseMessage]) -> Answer:
    """모델을 호출해 구조화된 `Answer`를 받는다. 실패는 `DependencyError`로 바꾼다.

    Args:
        messages: 모델에 보낼 시스템·사용자 메시지.

    Returns:
        Answer: 모델이 구조화해 돌려준 답변. 인용 검증은 하지 않는다.

    Raises:
        DependencyError: 모델이 설정되지 않았거나 호출이 실패했을 때.
    """
    if _chat_model is None:
        raise DependencyError("답변 생성 모델이 설정되지 않았습니다(set_chat_model 호출 필요).")
    try:
        result = _chat_model.with_structured_output(Answer).invoke(messages)
    except Exception as exc:
        # 예외 본문에 요청 내용이 섞일 수 있어 종류만 남긴다. 원인 체인은 `from exc`로 유지한다.
        print("답변 생성 모델 호출 실패:", type(exc).__name__)
        raise DependencyError("답변 생성 모델 호출에 실패했습니다.") from exc
    return result


def _citations_valid(
    answer: Answer, hits: list[SearchHit], equipment: Equipment, allow_other_equipment: bool = False
) -> bool:
    """모든 단계에 출처가 있고, 각 인용이 `hits`에 실재하며 설비가 일치하는지 본다.

    `allow_other_equipment`이면 설비 일치는 보지 않는다(실재와 `locator` 일치는 그대로 본다).
    """
    by_id = {(hit.kind, hit.ref_id): hit for hit in hits}
    for step in answer.steps:
        if not step.citations:
            return False
        for citation in step.citations:
            found = by_id.get((citation.kind, citation.ref_id))
            # 화면에는 인용의 locator가 출처로 보이므로 근거의 locator와 같아야 한다.
            if found is None or not _same_locator(found.locator, citation.locator):
                return False
            if found.equipment != equipment and not allow_other_equipment:
                return False
    return True


def _same_locator(source: str, cited: str) -> bool:
    """인용의 locator가 근거의 locator와 같은지 본다. 공백 차이와 프롬프트의 이스케이프 형태는 같은 것으로 본다."""
    # 모델은 프롬프트에 이스케이프되어 들어간 locator를 보고 베끼므로 원본·이스케이프 둘 다 받아 준다.
    cited = _collapse_whitespace(cited)
    return cited in (_collapse_whitespace(source), _collapse_whitespace(_escape_tag_text(source)))


def _is_answered(answer: Answer) -> bool:
    """답변 상태가 'answered'(답했음)인지 알려 준다."""
    return answer.verdict.value == "answered"


def _collapse_whitespace(text: str) -> str:
    """공백 차이로 검사가 어긋나지 않도록 연속 공백을 한 칸으로 줄인다."""
    return " ".join(text.split())


def _format_memo_for_prompt(memo: str | None) -> str:
    """고객이 쓴 메모를 프롬프트에 넣기 좋게 다듬는다. 길이를 자르고 태그 글자를 `_escape_tag_text`로 바꾼다."""
    if not memo or not memo.strip():
        return prompts.NO_MEMO
    # 바꾼 글자가 중간에 잘리지 않게 먼저 자른 뒤 태그 글자를 바꾼다.
    return _escape_tag_text(memo.strip()[:_MAX_MEMO_CHARS])


def _escape_tag_text(text: str) -> str:
    """`<evidence>` 태그의 속성값·본문에 안전하게 넣도록 꺾쇠·따옴표 같은 글자를 바꾼다."""
    text = text.replace("&", "&amp;")  # & 를 가장 먼저 바꿔야 다른 치환 결과가 다시 바뀌지 않는다
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    text = text.replace('"', "&quot;")
    text = text.replace("'", "&#x27;")
    return text


def _format_evidence(hits: list[SearchHit], with_equipment: bool = False) -> str:
    """근거를 모델이 그대로 옮겨 쓸 수 있도록 인용 식별자와 원문을 나란히 적는다.

    `with_equipment`이면 태그에 `equipment` 속성(근거의 설비)을 함께 적는다.
    """
    if not hits:
        return prompts.NO_EVIDENCE
    blocks = []
    for hit in hits:
        equipment_attr = f' equipment="{_escape_tag_text(hit.equipment.value)}"' if with_equipment else ""
        block = (
            f'<evidence kind="{_escape_tag_text(str(hit.kind.value))}"{equipment_attr} '
            f'ref_id="{_escape_tag_text(str(hit.ref_id))}" '
            f'locator="{_escape_tag_text(str(hit.locator))}">\n'
            f"{_escape_tag_text(hit.text)}\n</evidence>"
        )
        blocks.append(block)
    return "\n\n".join(blocks)


def _format_history(turns: list[ChatTurn]) -> str:
    """최근 대화 턴만 입력에 넣는다. 저장된 대화 자체는 건드리지 않는다."""
    recent = turns[-_MAX_HISTORY_TURNS:]
    if not recent:
        return prompts.NO_HISTORY
    lines: list[str] = []
    for turn in recent:
        step_texts = [step.text for step in turn.answer.steps]
        if step_texts:
            answer_text = " / ".join(step_texts)
        elif turn.answer.notice:
            answer_text = turn.answer.notice
        else:
            answer_text = turn.answer.verdict.value
        lines.append(f"질문: {_escape_tag_text(turn.question)}\n답변: {_escape_tag_text(answer_text)}")
    return "\n\n".join(lines)
