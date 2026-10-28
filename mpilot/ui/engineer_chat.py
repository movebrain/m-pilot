"""장애 분석 대화 화면.

역할:
    엔지니어가 신고 건에서 질문을 이어가며 출처가 붙은 답을 받는다.

책임:
    - 신고 정보는 `service.get_engineer_case`, 대화는 `service.list_chat_turns`·`service.send_chat_question`,
      출처 원문은 `service.get_citation_text`만 쓴다.
    - 답변의 각 단계에 출처를 보여 주고 펼치면 원문을 보여 준다.
    - 근거 부족은 조치 없이 안내만 보여 주고, AI 오류(`DependencyError`)는 고정 문구로 안내한다.
    - 첫 질문에서 신고가 '조치 중'이 되는 것은 서비스가 처리하며 이 화면은 결과만 반영한다.
    - 완료된 건은 질문할 수 없다('이미 완료'). 완료 후 채팅·재개는 범위 밖이다.
    - 질문 전송 중에는 버튼을 비활성화한다.

화면 전환: 대화 → 이력 저장(조치 결과 작성) / 대시보드(뒤로).
사용 서비스: `get_engineer_case`, `list_chat_turns`, `send_chat_question`, `get_citation_text`.
소유: UI·총괄 파트
"""

from __future__ import annotations

import gradio as gr

from mpilot import service
from mpilot.models import CaseDetail, CaseStatus, ChatTurn, Citation, NotFoundError
from mpilot.ui import common

SCREEN = common.SCREEN_ENGINEER_CHAT

QUESTION_MAX_LENGTH = service.MAX_QUESTION_LENGTH
"""질문 입력의 최대 글자 수(서비스 검증과 같다)."""

EMPTY_CHAT_NOTICE = "아직 대화가 없습니다. 첫 질문을 보내면 신고가 '조치 중'이 됩니다."
"""대화가 없을 때 보이는 문구."""

COMPLETED_NOTICE = "조치 완료된 건이라 더 질문할 수 없습니다."
"""완료된 건에서 질문 입력 대신 보이는 문구."""


def case_summary_markdown(detail: CaseDetail) -> str:
    """신고 정보를 화면용 Markdown으로 만든다.

    Args:
        detail: `service.get_engineer_case`가 돌려준 신고 상세.

    Returns:
        str: 접수번호·설비·코드·에러명·심각도·상태·접수 시각·증상 메모를 담은 Markdown.
    """
    case = detail.service_case
    return "\n\n".join([
        f"**접수번호** {case.case_id}",
        f"**설비** {case.equipment} · **코드** {case.code} · **에러명** {detail.error_code.name}"
        f" · **심각도** {detail.error_code.severity}",
        f"**상태** {common.status_label(case.status)} · **접수 시각** {common.format_datetime(case.created_at)}",
        f"**증상 메모** {case.memo or '-'}",
    ])


def chat_markdown(turns: list[ChatTurn]) -> str:
    """대화 목록을 화면용 Markdown으로 만든다. 답변은 `common.format_answer_markdown` 표기를 쓴다.

    Args:
        turns: 오래된 순 대화 목록.

    Returns:
        str: 질문·답변을 차례로 담은 Markdown. 대화가 없으면 `EMPTY_CHAT_NOTICE`.
    """
    if not turns:
        return EMPTY_CHAT_NOTICE
    blocks = [
        f"**Q{no}.** {turn.question}\n\n{common.format_answer_markdown(turn.answer, show_notice=True)}"
        for no, turn in enumerate(turns, start=1)
    ]
    return "\n\n---\n\n".join(blocks)


def citations_of(turns: list[ChatTurn]) -> list[Citation]:
    """대화 전체의 출처를 나온 순서대로, 겹치지 않게 모은다.

    Args:
        turns: 오래된 순 대화 목록.

    Returns:
        list[Citation]: 출처 목록. 근거 부족 답변에는 단계가 없어 출처도 없다.
    """
    seen: dict[str, Citation] = {}
    for turn in turns:
        for step in turn.answer.steps:
            for citation in step.citations:
                seen.setdefault(citation.ref_id, citation)
    return list(seen.values())


def can_ask(detail: CaseDetail) -> bool:
    """이 신고에 질문할 수 있는지 판정한다.

    Args:
        detail: 신고 상세.

    Returns:
        bool: 완료되지 않은 건이면 `True`. 완료 건(셀프 가이드 건 포함)은 `False`.
    """
    return detail.service_case.status != CaseStatus.COMPLETED


def build_engineer_chat_screen(role: gr.State, case_id: gr.State, screen: gr.State) -> None:
    """신고 정보와 대화를 보여 주고 질문 전송·출처 원문 확인을 처리한다.

    Args:
        role: 앱이 고정한 역할(`engineer`)을 담은 공유 상태. 엔지니어 앱에서만 이 화면을 조립한다.
        case_id: 선택된 신고의 접수번호를 담은 공유 상태. 대화할 신고의 접수번호. 이 화면은 읽기만 한다.
        screen: 지금 보여 줄 화면을 가리키는 공유 상태. 화면 전환 그림의 상태 이름과 대응한다. 조치 결과 작성은 이력 저장, 뒤로는 엔지니어 대시보드로 바꾼다.

    Returns:
        None: Gradio Blocks 안에서 호출되어 컴포넌트와 이벤트를 등록할 뿐 값을 돌려주지 않는다.
    """
    citations = gr.State([])  # 지금 보이는 대화의 출처 목록
    with gr.Column(visible=common.is_screen_visible(screen.value, SCREEN)) as column:
        gr.Markdown("## 장애 분석 대화")
        summary = gr.Markdown()
        chat = gr.Markdown()
        source_pick = gr.Dropdown(choices=[], value=None, label="출처를 고르면 원문을 봅니다", visible=False)
        with gr.Accordion("출처 원문", open=True, visible=False) as source_box:
            source_text = gr.Textbox(show_label=False, lines=6, interactive=False)
        question = gr.Textbox(label="질문", lines=3, max_length=QUESTION_MAX_LENGTH)
        send_btn = gr.Button("질문 보내기", variant="primary")
        with gr.Row():
            back_btn = gr.Button("뒤로")
            resolve_btn = gr.Button("조치 결과 작성")

    def load_case(selected: str | None):
        # 신고와 대화를 다시 읽어 화면 값을 만든다. 출력 순서는 refresh_outputs와 같다.
        detail = service.get_engineer_case(selected or "")
        turns = service.list_chat_turns(selected or "")
        found = citations_of(turns)
        choices = [(common.citation_label(c), str(i)) for i, c in enumerate(found)]
        open_ = can_ask(detail)
        return (
            case_summary_markdown(detail), chat_markdown(turns),
            gr.Dropdown(choices=choices, value=None, visible=bool(found)), gr.Accordion(visible=False), found,
            gr.Textbox(interactive=open_, placeholder=None if open_ else COMPLETED_NOTICE),
            gr.Button(interactive=open_),
        )

    refresh_outputs = [summary, chat, source_pick, source_box, citations, question, send_btn]

    def on_screen_change(current: str, selected: str | None):
        # 이 화면에 들어올 때마다 고른 신고를 다시 읽는다.
        if not common.is_screen_visible(current, SCREEN):
            return (gr.Column(visible=False),) + (gr.skip(),) * len(refresh_outputs) + (gr.skip(),)
        try:
            return (gr.Column(visible=True),) + load_case(selected) + (gr.skip(),)
        except NotFoundError as e:
            gr.Warning(common.error_message(e))
            return (gr.skip(),) * (len(refresh_outputs) + 1) + (common.SCREEN_ENGINEER_LIST,)
        except Exception as e:  # 화면에는 고정 문구만 보인다
            gr.Warning(common.error_message(e))
            return (gr.Column(visible=True), "", "") + (gr.skip(),) * (len(refresh_outputs) - 2) + (gr.skip(),)

    def on_send(selected: str | None, text: str | None):
        # 질문을 보내고 성공하면 입력을 비운다. 실패하면 질문을 남겨 다시 보낼 수 있게 한다.
        try:
            service.send_chat_question(selected or "", text or "")
        except Exception as e:  # 근거 부족은 예외가 아니라 저장된 답변으로 보인다
            gr.Warning(common.error_message(e))
            return gr.skip()
        return ""

    def on_after_send(selected: str | None):
        # 보낸 결과(새 대화, 조치 중 전환)를 다시 읽고 버튼 잠금을 푼다.
        try:
            return load_case(selected)
        except Exception as e:
            gr.Warning(common.error_message(e))
            return (gr.skip(),) * (len(refresh_outputs) - 1) + (gr.Button(interactive=True),)

    def on_source_pick(index: str | None, found: list[Citation]):
        # 출처를 고르면 그 원문을 펼친다.
        if index is None or index == "":
            return gr.Accordion(visible=False), ""
        try:
            text = service.get_citation_text(found[int(index)])
        except Exception as e:
            gr.Warning(common.error_message(e))
            return gr.Accordion(visible=False), ""
        return gr.Accordion(visible=True), text

    screen.change(on_screen_change, inputs=[screen, case_id], outputs=[column, *refresh_outputs, screen])
    send_btn.click(lambda: gr.Button(interactive=False), outputs=send_btn).then(
        on_send, inputs=[case_id, question], outputs=question,
    ).then(on_after_send, inputs=case_id, outputs=refresh_outputs)
    source_pick.change(on_source_pick, inputs=[source_pick, citations], outputs=[source_box, source_text])
    back_btn.click(lambda: common.SCREEN_ENGINEER_LIST, outputs=screen)
    resolve_btn.click(lambda: common.SCREEN_ENGINEER_RESOLVE, outputs=screen)
