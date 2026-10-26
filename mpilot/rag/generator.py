"""모델 호출과 답변 형식·인용 검증.

역할:
    LangChain 채팅 모델을 호출해 구조화된 `Answer`를 만들고, 만들어진 답이 보여 줄 수 있는지 검사한다.

책임:
    - 고객 안내와 엔지니어 답변을 프롬프트(`prompts/`)로 생성한다. 모든 단계(`AnswerStep`)에는 출처가 1개 이상 붙는다.
    - 원문 밖의 원인·절차를 단정하지 않는다. 증상 메모는 확인된 사실과 구분한다(프롬프트 규칙).
    - 검증: 인용이 확보한 근거에 실제로 있고 설비가 일치하는지, 고객 안내에는 필수 안전 문구가 들어 있는지 검사한다.
      출처 ID 검증은 내용의 정확성·안전성을 증명하지 않는다. 그 부분은 프롬프트 제약과 사람의 사후 평가가 맡는다.
    - 검증을 통과하지 못한 답은 보여 주지 않는다. 이 모듈은 통과 여부만 알려 주고, 실패를 `DependencyError`로
      올리는 일은 `pipeline`이 맡는다(spec §2.6).
    - 모델은 `set_chat_model`로 받는다(temperature=0). 모듈을 import할 때 API에 접속하지 않는다.
    - 검색·DB에는 접근하지 않는다.

의존: `models`만 import한다(의존 규칙 §2.5).
호출: `rag.pipeline`, `app`(`set_chat_model`).
소유: RAG 파트
"""

# 타입 표시를 "글자 그대로" 나중에 읽게 한다. 아직 만들어지지 않은 타입 이름도 적을 수 있다.
from __future__ import annotations

# 오류가 났을 때 기록(로그)을 남기는 도구를 가져온다.
import logging
# 글자 패턴을 찾고 바꾸는 도구(정규식)를 가져온다.
import re
# 타입 검사할 때만 true가 되는 스위치를 가져온다.
from typing import TYPE_CHECKING

# 모델에게 보낼 메시지 종류(기본 메시지, 사용자 메시지, 시스템 지시 메시지)를 가져온다.
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

# 우리 프로젝트의 공용 자료형들을 models에서 가져온다.
from mpilot.models import (
    Answer,  # AI가 만든 답변 한 덩어리
    CaseContext,  # 신고 상황 정보(에러코드 + 메모)
    ChatRequest,  # 엔지니어의 질문 + 지난 대화
    ChatTurn,  # 지난 대화 한 번(질문 1개 + 답 1개)
    DependencyError,  # 모델 같은 바깥 도구가 고장 났을 때 쓰는 오류
    Equipment,  # 설비 종류(AGV, SRM 등)
    ErrorCode,  # 에러코드 표의 한 줄
    SearchHit,  # 검색해서 찾은 근거 한 건
)
# 지시문(프롬프트) 모음 파일을 가져온다.
from mpilot.rag import prompts

# 타입 검사할 때만 아래 줄을 읽는다. 실행 중에는 읽지 않는다.
if TYPE_CHECKING:
    # 채팅 모델의 타입 이름만 필요해서 여기서만 가져온다.
    from langchain_core.language_models import BaseChatModel

# 생성 입력에 넣을 최근 대화 턴 수. 저장된 대화는 지우지 않고 입력에서만 줄인다.
# 지난 대화를 최대 6번까지만 AI에게 보여 준다.
_MAX_HISTORY_TURNS = 6

# 고객 메모의 최대 길이. 너무 긴 글이 프롬프트를 채우지 못하게 자른다.
# 메모는 500글자까지만 쓴다.
_MAX_MEMO_CHARS = 500

# 이 파일 이름으로 된 로그 기록 담당자를 만든다.
_logger = logging.getLogger(__name__)

# 쓸 AI 모델을 담아 두는 칸. 처음에는 비어 있다(None).
_chat_model: BaseChatModel | None = None


def set_chat_model(chat_model: BaseChatModel) -> None:
    """답변 생성에 쓸 모델을 설정한다.

    Args:
        chat_model: 답변 생성에 쓸 LangChain 채팅 모델. `app`이 설정(`chat_model_name`, 제한 시간)으로 만든다.

    Returns:
        None: 모듈 안에 모델을 보관한다. 이후 생성 함수는 이 모델을 쓴다.
    """
    # 파일 맨 위에 있는 모델 칸을 직접 바꾸겠다고 알린다.
    global _chat_model
    # 받은 모델을 그 칸에 넣어 둔다. 나중에 답을 만들 때 꺼내 쓴다.
    _chat_model = chat_model


def generate_customer_guidance(context: CaseContext, hits: list[SearchHit]) -> Answer:
    """고객 안내 답변을 생성한다.

    매뉴얼에서 고객이 수행하도록 허용한 절차 원문(`hits`)만 근거로 쓴다. 과거 이력은 쓰지 않는다.

    Args:
        context: 신고 맥락. 대상 에러코드의 상세와 증상 메모.
        hits: `retriever.fetch_customer_procedures`가 가져온 허용 절차 원문.

    Returns:
        Answer: 단계마다 출처가 붙은 안내. 이 함수는 생성만 하며, 인용·안전 문구 검사는
            `passes_guidance_check`가 한다.

    Raises:
        DependencyError: 모델 호출 실패나 제한 시간 초과일 때.
    """
    # 신고된 에러코드 정보를 꺼내서 짧은 이름으로 둔다.
    error_code = context.error_code
    # 이 에러코드에서 꼭 들어가야 하는 안전 문구 목록을 꺼낸다.
    safety_rules = error_code.required_safety_rules
    # 지시문 틀의 빈칸을 채워서 AI에게 보낼 질문글을 만든다.
    human = prompts.CUSTOMER_HUMAN.format(
        # 설비 이름(예: AGV)
        equipment=error_code.equipment.value,
        # 에러코드 번호
        code=error_code.code,
        # 에러 이름
        name=error_code.name,
        # 심각도(상/중/하)
        severity=error_code.severity.value,
        # 고객이 쓴 메모. 너무 길거나 위험한 글자는 다듬어서 넣는다.
        memo=_memo_text(context.memo),
        # 안전 문구를 줄줄이 적는다. 하나도 없으면 "(없음)"을 넣는다.
        safety_rules=_bullet_lines(safety_rules) or prompts.NO_SAFETY_RULES,
        # 근거 자료(매뉴얼 절차)를 AI가 읽기 좋은 모양으로 넣는다.
        evidence=_format_evidence(hits),
    )
    # 고객용 역할 지시 + 위에서 만든 질문글을 AI에게 보내고, 받은 답을 그대로 돌려준다.
    return _invoke([SystemMessage(content=prompts.CUSTOMER_SYSTEM), HumanMessage(content=human)])


# 엔지니어 답변을 만드는 함수의 시작. 받는 재료가 많아서 줄을 나눠 적었다.
def generate_engineer_answer(
    context: CaseContext, hits: list[SearchHit], request: ChatRequest
) -> Answer:
    """엔지니어 대화 답변을 생성한다.

    Args:
        context: 신고 맥락. 대상 에러코드의 상세와 증상 메모.
        hits: 매뉴얼·이력 검색으로 얻은 근거.
        request: 현재 질문과 저장된 대화. 대화 이력은 생성 입력에서만 줄이고 저장본은 지우지 않는다.

    Returns:
        Answer: 단계마다 출처가 붙은 답변. 인용 검사는 `passes_answer_check`가 한다.

    Raises:
        DependencyError: 모델 호출 실패나 제한 시간 초과일 때.
    """
    # 신고된 에러코드 정보를 꺼내서 짧은 이름으로 둔다.
    error_code = context.error_code
    # 지시문 틀의 빈칸을 채워서 AI에게 보낼 질문글을 만든다.
    human = prompts.ENGINEER_HUMAN.format(
        # 설비 이름(예: AGV)
        equipment=error_code.equipment.value,
        # 에러코드 번호
        code=error_code.code,
        # 에러 이름
        name=error_code.name,
        # 에러코드 표에 적힌 원인(참고용)
        cause=error_code.cause,
        # 에러코드 표에 적힌 조치(참고용)
        action=error_code.action,
        # 고객이 쓴 메모. 너무 길거나 위험한 글자는 다듬어서 넣는다.
        memo=_memo_text(context.memo),
        # 지난 대화를 최근 것만 골라서 글로 만든다.
        history=_format_history(request.turns),
        # 근거 자료(매뉴얼 + 과거 이력)를 AI가 읽기 좋은 모양으로 넣는다.
        evidence=_format_evidence(hits),
        # 엔지니어가 지금 물어본 질문
        question=request.question,
    )
    # 엔지니어용 역할 지시 + 위에서 만든 질문글을 AI에게 보내고, 받은 답을 그대로 돌려준다.
    return _invoke([SystemMessage(content=prompts.ENGINEER_SYSTEM), HumanMessage(content=human)])


def passes_guidance_check(answer: Answer, hits: list[SearchHit], error_code: ErrorCode) -> bool:
    """고객 안내의 인용과 안전 문구를 검사한다.

    인용이 모두 `hits`에 있고 설비가 일치하는지, `error_code.required_safety_rules`의 문구가
    빠지지 않았는지 확인한다.

    Args:
        answer: 검사할 고객 안내.
        hits: 안내 생성에 쓴 허용 절차 원문(인용의 기준).
        error_code: 신고한 에러코드. 필수 안전 문구 목록을 읽는다.

    Returns:
        bool: 모두 통과하면 `True`. 하나라도 어긋나면 `False`이며, 이때는 가이드를 만들지 못한 것으로 보아
            엔지니어 처리(`generation_failed`)로 접수한다.
    """
    # 고객 안내는 절차를 알려 주는 것이 목적이므로 answered 이외의 판정은 가이드로 쓸 수 없다.
    # "답했음" 상태가 아니거나, 단계가 하나도 없으면 안내로 쓸 수 없다.
    if not _is_answered(answer) or not answer.steps:
        # 불합격으로 돌려보낸다.
        return False
    # 모든 단계의 출처가 진짜 있는 근거이고 설비도 맞는지 확인한다.
    if not _citations_valid(answer, hits, error_code.equipment):
        # 하나라도 틀리면 불합격.
        return False
    # 고객 안내의 근거는 매뉴얼 절차뿐이다. 이력 인용이 섞이면 실패로 본다.
    # 모든 출처를 하나씩 보면서, 매뉴얼이 아닌 것(과거 이력)이 하나라도 있는지 찾는다.
    if any(_enum_value(c.kind) != "manual" for step in answer.steps for c in step.citations):
        # 이력이 섞여 있으면 불합격.
        return False
    # 마지막으로 꼭 들어가야 할 안전 문구가 다 있는지 확인하고, 그 결과(True/False)를 돌려준다.
    return _contains_all_safety_rules(answer, error_code.required_safety_rules)


def passes_answer_check(answer: Answer, hits: list[SearchHit], equipment: Equipment) -> bool:
    """엔지니어 답변의 인용을 검사한다.

    Args:
        answer: 검사할 엔지니어 답변.
        hits: 답변 생성에 쓴 검색 근거(인용의 기준).
        equipment: 신고 대상 설비. 인용한 근거의 설비가 이것과 같아야 한다.

    Returns:
        bool: 모든 인용이 `hits`에 있고 설비가 일치하면 `True`, 아니면 `False`.
    """
    # "답했음"이라면서 단계가 하나도 없으면 말이 안 된다.
    if _is_answered(answer) and not answer.steps:
        # 불합격으로 돌려보낸다.
        return False
    # 모든 단계의 출처가 진짜 있는 근거이고 설비도 맞는지 확인한 결과를 돌려준다.
    return _citations_valid(answer, hits, equipment)


# --- 내부 도우미 -------------------------------------------------------------


def _invoke(messages: list[BaseMessage]) -> Answer:
    """모델을 호출해 구조화된 `Answer`를 받는다. 실패는 `DependencyError`로 바꾼다."""
    # 모델이 아직 설정되지 않았다면
    if _chat_model is None:
        # "모델을 먼저 설정하세요"라는 오류를 낸다.
        raise DependencyError("답변 생성 모델이 설정되지 않았습니다(set_chat_model 호출 필요).")
    # 모델 호출은 실패할 수 있으니 안전하게 감싸서 시도한다.
    try:
        # 모델에게 "Answer 모양으로 답해줘"라고 정하고, 메시지를 보내서 결과를 받는다.
        result = _chat_model.with_structured_output(Answer).invoke(messages)
    except Exception as exc:  # 네트워크·제한 시간·파싱 실패를 모두 의존 장애로 본다.
        # 예외 원문에는 요청 ID나 키 일부 같은 내부 정보가 있을 수 있어 화면용 문구에는 싣지 않고 로그에만 남긴다.
        # 자세한 오류 내용은 로그 파일에만 적어 둔다.
        _logger.exception("답변 생성 모델 호출 실패")
        # 화면에는 짧고 안전한 문구만 담은 오류를 낸다. (원래 오류는 뒤에 붙여 둔다)
        raise DependencyError("답변 생성 모델 호출에 실패했습니다.") from exc
    # 받은 결과가 Answer 모양이 아니라면
    if not isinstance(result, Answer):
        # "형식이 이상하다"는 오류를 낸다.
        raise DependencyError("답변 생성 모델이 올바른 형식의 답변을 돌려주지 않았습니다.")
    # 제대로 된 답을 돌려준다.
    return result


def _citations_valid(answer: Answer, hits: list[SearchHit], equipment: Equipment) -> bool:
    """모든 단계에 출처가 있고, 각 인용이 `hits`에 실재하며 설비가 일치하는지 본다."""
    # (종류, 번호)를 이름표로 해서 근거들을 찾기 쉽게 사전으로 만든다.
    by_key = {(hit.kind, hit.ref_id): hit for hit in hits}
    # 답변의 단계를 하나씩 본다.
    for step in answer.steps:
        # 이 단계에 출처가 하나도 없으면
        if not step.citations:
            # 불합격.
            return False
        # 이 단계에 붙은 출처를 하나씩 본다.
        for citation in step.citations:
            # 출처의 (종류, 번호)로 우리가 준 근거 중에서 찾아본다. 없으면 None.
            hit = by_key.get((citation.kind, citation.ref_id))
            # 근거를 못 찾았거나(지어낸 출처), 설비가 다르면
            if hit is None or hit.equipment != equipment:
                # 불합격.
                return False
    # 끝까지 문제가 없었으니 합격.
    return True


def _contains_all_safety_rules(answer: Answer, rules: list[str]) -> bool:
    """필수 안전 문구가 단계 본문이나 안내 문구(notice)에 모두 들어 있는지 본다."""
    # 꼭 넣어야 할 안전 문구가 원래 없다면
    if not rules:
        # 검사할 게 없으니 합격.
        return True
    # 각 단계의 글을 모아서 목록으로 만든다.
    texts = [step.text for step in answer.steps]
    # 안내 문구(notice)가 있다면
    if answer.notice:
        # 그 글도 목록에 더한다.
        texts.append(answer.notice)
    # 모은 글을 한 줄로 이어 붙이고, 공백 차이는 없앤다. 이 안에서 문구를 찾는다.
    haystack = _normalize(" ".join(texts))
    # 안전 문구가 하나씩 전부 그 글 안에 있어야 합격. (빈 문구는 건너뛴다)
    return all(_normalize(rule) in haystack for rule in rules if rule.strip())


def _enum_value(member: object) -> object:
    """열거형이면 값을, 문자열이면 그대로 돌려준다(멤버 이름에 의존하지 않기 위함)."""
    # .value가 있으면 그 값을, 없으면 받은 것을 그대로 돌려준다.
    return getattr(member, "value", member)


def _is_answered(answer: Answer) -> bool:
    # 답변 상태가 "answered"(답했음)인지 True/False로 알려 준다.
    return _enum_value(answer.verdict) == "answered"


def _normalize(text: str) -> str:
    """공백 차이로 검사가 어긋나지 않도록 연속 공백을 한 칸으로 줄인다."""
    # 띄어쓰기·줄바꿈이 여러 개 이어진 곳을 공백 한 칸으로 바꾸고, 앞뒤 공백도 지운다.
    return re.sub(r"\s+", " ", text).strip()


def _memo_text(memo: str | None) -> str:
    """고객이 쓴 메모를 프롬프트에 넣기 좋게 다듬는다. 길이를 자르고 <memo> 태그를 벗어나는 글자를 없앤다."""
    # 메모가 없거나 공백뿐이라면
    if not memo or not memo.strip():
        # "(없음)"을 돌려준다.
        return prompts.NO_MEMO
    # <memo> 또는 </memo> 글자를 지운다. (고객이 태그를 닫고 빠져나가는 것을 막는다)
    cleaned = re.sub(r"</?\s*memo\s*>", "", memo, flags=re.IGNORECASE).strip()
    # 500글자까지만 자른다. 다 지워져서 비었다면 "(없음)"을 돌려준다.
    return cleaned[:_MAX_MEMO_CHARS] or prompts.NO_MEMO


def _bullet_lines(items: list[str]) -> str:
    # 항목마다 앞에 "- "를 붙이고, 줄바꿈으로 이어서 한 덩어리 글로 만든다.
    return "\n".join(f"- {item}" for item in items)


def _format_evidence(hits: list[SearchHit]) -> str:
    """근거를 모델이 그대로 옮겨 쓸 수 있도록 인용 식별자와 원문을 나란히 적는다."""
    # 근거가 하나도 없다면
    if not hits:
        # "(없음)"을 돌려준다.
        return prompts.NO_EVIDENCE
    # 근거를 하나씩 "이름표 + 원문" 모양의 글로 바꿔서 목록으로 만든다.
    blocks = [
        # 이름표(종류, 번호, 위치)를 괄호 안에 적고, 줄을 바꿔서 원문을 붙인다.
        f"(kind={hit.kind.value} | ref_id={hit.ref_id} | locator={hit.locator})\n{hit.text}"
        # 받은 근거들을 하나씩 돌면서 만든다.
        for hit in hits
    ]
    # 근거와 근거 사이에 빈 줄을 하나 두고 이어 붙인다.
    return "\n\n".join(blocks)


def _format_history(turns: list[ChatTurn]) -> str:
    """최근 대화 턴만 입력에 넣는다. 저장된 대화 자체는 건드리지 않는다."""
    # 뒤에서부터 6개만 골라 최근 대화로 삼는다. (원본 목록은 그대로 둔다)
    recent = turns[-_MAX_HISTORY_TURNS:]
    # 최근 대화가 하나도 없다면
    if not recent:
        # "(없음)"을 돌려준다.
        return prompts.NO_HISTORY
    # 대화를 글로 바꿔서 담을 빈 목록을 만든다.
    lines: list[str] = []
    # 최근 대화를 한 번씩 본다.
    for turn in recent:
        # 그때의 답변을 글로 만든다. 단계가 있으면 단계 글을 " / "로 이어 붙인다.
        answer_text = " / ".join(step.text for step in turn.answer.steps) or (
            # 단계가 없으면 안내 문구를, 그것도 없으면 상태 이름을 쓴다.
            turn.answer.notice or turn.answer.verdict.value
        )
        # "질문: ... 답변: ..." 모양으로 만들어서 목록에 넣는다.
        lines.append(f"질문: {turn.question}\n답변: {answer_text}")
    # 대화와 대화 사이에 빈 줄을 하나 두고 이어 붙인다.
    return "\n\n".join(lines)