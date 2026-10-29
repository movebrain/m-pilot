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
    - `passes_guidance_check`는 `answer.verdict`가 `answered`일 때만 의미가 있다. `pipeline`은 `answered`가
      아니면(근거 부족) 이 함수를 부르지 않고 그 `Answer`를 그대로 돌려준다(spec §2.10의
      `held`). `answered`인데 이 함수가 `False`면 `pipeline`이 `DependencyError`로 올려 `generation_failed`로
      접수한다.
    - 모델은 `set_chat_model`로 받는다(temperature=0). 모듈을 import할 때 API에 접속하지 않는다.
    - 검색·DB에는 접근하지 않는다.

의존: `models`만 import한다(의존 규칙 §2.5).
호출: `rag.pipeline`, `app`(`set_chat_model`).
소유: RAG 파트
"""

# 채팅 모델 타입
from langchain_core.language_models import BaseChatModel
# 모델에게 보낼 메시지 종류(기본, 사용자, 시스템 지시)
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

# 생성 입력에 넣을 최근 대화 턴 수. 저장된 대화는 지우지 않고 입력에서만 줄인다.
# 지난 대화를 최대 6번까지만 AI에게 보여 준다.
_MAX_HISTORY_TURNS = 6

# 고객 메모의 최대 길이. 너무 긴 글이 프롬프트를 채우지 못하게 자른다.
# 메모는 2000글자까지만 쓴다.
_MAX_MEMO_CHARS = 2000
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


    #고객 안내 답변 만들기 (생성)
def generate_customer_guidance(context: CaseContext, hits: list[SearchHit]) -> Answer:
    """고객 안내 답변을 생성한다.

    신고 코드의 매뉴얼 검색 근거(`hits`)만 근거로 쓴다. 과거 이력은 쓰지 않는다.

    Args:
        context: 신고 맥락. 대상 에러코드의 상세와 증상 메모.
        hits: `pipeline`이 신고 코드의 매뉴얼에서 검색해 가려 낸 근거.

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
        memo=_format_memo_for_prompt(context.memo),
        # 안전 문구를 줄줄이 적는다. 하나도 없으면 "(없음)"을 넣는다.
        safety_rules=_bullet_lines(safety_rules) or prompts.NO_SAFETY_RULES,
        # 근거 자료(매뉴얼 절차)를 AI가 읽기 좋은 모양으로 넣는다.
        evidence=_format_evidence(hits),
    )
    # 고객용 역할 지시 + 위에서 만든 질문글을 AI에게 보내고, 받은 답을 그대로 돌려준다.
    return _invoke([SystemMessage(content=prompts.CUSTOMER_SYSTEM), HumanMessage(content=human)])

#엔지니어 안내 답변 만들기 (생성)
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
        memo=_format_memo_for_prompt(context.memo),
        # 지난 대화를 최근 것만 골라서 글로 만든다.
        history=_format_history(request.turns),
        # 근거 자료(매뉴얼 + 과거 이력)를 AI가 읽기 좋은 모양으로 넣는다.
        evidence=_format_evidence(hits, with_equipment=cross_equipment),
        # 엔지니어가 지금 물어본 질문
        question=_escape_tag_text(request.question),
    )
    # 엔지니어용 역할 지시 + 위에서 만든 질문글을 AI에게 보내고, 받은 답을 그대로 돌려준다.
    system = prompts.ENGINEER_SYSTEM + (prompts.ENGINEER_CROSS_EQUIPMENT_RULES if cross_equipment else "")
    return _invoke([SystemMessage(content=system), HumanMessage(content=human)])

    #답변 검사 (보여 줘도 되는지 판단)
def passes_guidance_check(answer: Answer, hits: list[SearchHit], error_code: ErrorCode) -> bool:
    """고객 안내의 인용과 안전 문구를 검사한다.

    인용이 모두 `hits`에 있고(`locator` 포함) 설비가 일치하는지, `error_code.required_safety_rules`의 문구가
    빠지지 않았는지 확인한다.

    Args:
        answer: 검사할 고객 안내.
        hits: 안내 생성에 쓴 매뉴얼 검색 근거(인용의 기준).
        error_code: 신고한 에러코드. 필수 안전 문구 목록을 읽는다.

    Returns:
        bool: 모두 통과하면 `True`. 하나라도 어긋나면 `False`다.

            주의: 이 함수는 `answer.verdict`가 `answered`인 `Answer`에만 쓴다고 가정한다. `answered`가
            아닌 `Answer`(근거 부족)를 넣으면 `_is_answered` 검사 때문에 역시 `False`가
            나오지만, 그 경우는 `generation_failed`가 아니라 `held`다(spec §2.10). `verdict`가
            `answered`가 아닌지는 호출 전에 `pipeline`이 먼저 가려 `held`로 처리하고, 이 함수는 그 뒤
            `answered`인 `Answer`에만 호출해 그 결과를 `generation_failed` 여부로 쓴다.
    """
    # 고객 안내는 절차를 알려 주는 것이 목적이므로 answered 이외의 판정은 가이드로 쓸 수 없다.
    # "답했음" 상태가 아니거나, 단계가 하나도 없으면 안내로 쓸 수 없다.
    if not _is_answered(answer) or not answer.steps:
        # 불합격으로 돌려보낸다.
        return False
    # 모든 단계의 출처가 진짜 있는 근거이고 설비도 맞는지 확인한다.
    if not _citations_valid(answer, hits, error_code.equipment):
        return False
    # 고객 안내의 근거는 매뉴얼 절차뿐이다. 이력 인용이 섞이면 실패로 본다.
    # 모든 출처를 하나씩 보면서, 매뉴얼이 아닌 것(과거 이력)이 하나라도 있는지 찾는다.
    for step in answer.steps:
        for c in step.citations:
            if c.kind.value != "manual":
                return False
    # 마지막으로 꼭 들어가야 할 안전 문구가 다 있는지 확인하고, 그 결과(True/False)를 돌려준다.
    return _contains_all_safety_rules(answer, error_code.required_safety_rules)


def passes_answer_check(
    answer: Answer, hits: list[SearchHit], equipment: Equipment, allow_other_equipment: bool = False
) -> bool:
    """엔지니어 답변의 인용을 검사한다.

    Args:
        answer: 검사할 엔지니어 답변.
        hits: 답변 생성에 쓴 검색 근거(인용의 기준).
        equipment: 신고 대상 설비. 인용한 근거의 설비가 이것과 같아야 한다.
        allow_other_equipment: `True`이면 인용한 근거의 설비가 신고 설비와 달라도 통과시킨다.
            신고 설비 자료만으로 근거가 부족해 필터 없이 다시 찾은 답변에만 쓴다.

    Returns:
        bool: 모든 인용이 `hits`에 있고(`locator` 포함) 설비가 일치하면(`allow_other_equipment`가 아니라면) `True`,
            아니면 `False`.
    """
    # "답했음"이라면서 단계가 하나도 없으면 말이 안 된다.
    if _is_answered(answer) and not answer.steps:
        # 불합격으로 돌려보낸다.
        return False
    # 모든 단계의 출처가 진짜 있는 근거이고 설비도 맞는지 확인한 결과를 돌려준다.
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
    # 각 단계의 출처가 hits 안에 진짜 있고 설비도 맞는지 하나씩 확인한다.
    for step in answer.steps:
        if not step.citations:
            return False
        for citation in step.citations:
            found = None
            for hit in hits:
                if hit.kind == citation.kind and hit.ref_id == citation.ref_id:
                    found = hit
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
    haystack = _collapse_whitespace(" ".join(texts))
    # 안전 문구가 하나씩 전부 그 글 안에 있어야 합격. (빈 문구는 건너뛴다)
    return all(_collapse_whitespace(rule) in haystack for rule in rules if rule.strip())


def _is_answered(answer: Answer) -> bool:
    """답변 상태가 'answered'(답했음)인지 알려 준다."""
    return answer.verdict.value == "answered"



def _collapse_whitespace(text: str) -> str:
    """공백 차이로 검사가 어긋나지 않도록 연속 공백을 한 칸으로 줄인다."""
    # 띄어쓰기·줄바꿈이 여러 개 이어진 곳을 공백 한 칸으로 바꾸고, 앞뒤 공백도 지운다.
    return " ".join(text.split())


def _format_memo_for_prompt(memo: str | None) -> str:
    """고객이 쓴 메모를 프롬프트에 넣기 좋게 다듬는다. 길이를 자르고 태그 글자를 `_escape_tag_text`로 바꾼다."""
    # 메모가 없거나 공백뿐이라면
    if not memo or not memo.strip():
        # "(없음)"을 돌려준다.
        return prompts.NO_MEMO
    # 너무 길면 앞에서부터 자른 뒤(바꾼 글자가 중간에 잘리지 않게) 태그 글자를 바꿔서 돌려준다.
    return _escape_tag_text(memo.strip()[:_MAX_MEMO_CHARS])


def _bullet_lines(items: list[str]) -> str:
    lines = []
    for item in items:
        lines.append(f"- {item}")
    return "\n".join(lines)


def _escape_tag_text(text: str) -> str:
    """`<evidence>` 태그의 속성값·본문에 안전하게 넣도록 꺾쇠·따옴표 같은 글자를 바꾼다."""
    # &, <, >, ", ' 를 모두 안전한 글자로 바꾼다. (속성값·본문 양쪽에 다 써도 안전하다)
    text = text.replace("&", "&amp;")   # & 를 가장 먼저 바꿔야 합니다
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    text = text.replace('"', "&quot;")
    text = text.replace("'", "&#x27;")
    return text


def _format_evidence(hits: list[SearchHit], with_equipment: bool = False) -> str:
    """근거를 모델이 그대로 옮겨 쓸 수 있도록 인용 식별자와 원문을 나란히 적는다.

    `with_equipment`이면 태그에 `equipment` 속성(근거의 설비)을 함께 적는다.
    """
    # 근거가 하나도 없다면
    if not hits:
        # "(없음)"을 돌려준다.
        return prompts.NO_EVIDENCE
    # 근거를 안전한 <evidence> 태그와 방패(_escape_tag_text)로 감싸서 만든다!
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
        step_texts = []
        for step in turn.answer.steps:
            step_texts.append(step.text)

        if step_texts:
            answer_text = " / ".join(step_texts)
        elif turn.answer.notice:
            answer_text = turn.answer.notice
        else:
            answer_text = turn.answer.verdict.value

        lines.append(f"질문: {_escape_tag_text(turn.question)}\n답변: {_escape_tag_text(answer_text)}")
    # 대화와 대화 사이에 빈 줄을 하나 두고 이어 붙인다.
    return "\n\n".join(lines)