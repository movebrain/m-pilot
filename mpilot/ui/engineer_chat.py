"""장애 분석 대화 화면.

역할:
    엔지니어가 신고 건에서 질문을 이어가며 출처가 붙은 답을 받는다.

책임:
    - 신고 정보는 `service.get_engineer_case`, 대화는 `service.list_chat_turns`·`service.send_chat_question`,
      출처 원문은 `service.get_citation_text`만 쓴다.
    - 답변의 각 단계에 출처를 보여 주고 고르면 모달 팝업으로 원문을 보여 준다.
    - 근거 부족은 조치 없이 안내만 보여 주고, AI 오류(`DependencyError`)는 고정 문구로 안내한다.
    - 첫 질문에서 신고가 '조치 중'이 되는 것은 서비스가 처리하며 이 화면은 결과만 반영한다.
    - 완료된 건은 읽기 전용이다. 질문 입력·[질문 보내기]·[조치 결과 작성]을 숨기고 대화 기록과 조치 결과만 보인다.
      완료 후 채팅·재개는 범위 밖이다.
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
"""진행 중인 건에 대화가 없을 때 보이는 문구."""

COMPLETED_NO_CHAT_NOTICE = "AI 대화 없이 완료된 건입니다."
"""완료된 건에 대화가 없을 때 보이는 문구(시드 이력 포함). 더 질문할 수 없으므로 질문 안내를 하지 않는다."""

NO_RESOLUTION_NOTICE = "아직 조치 이력이 없습니다."
"""확정된 조치가 없을 때 접수 정보 아래에 보이는 문구."""

RESOLUTION_BOX_COLOR = "#e0f2fe"
"""조치 결과 박스 배경색(연한 하늘색). 접수 정보 박스(회색)와 구분한다."""


def case_summary_html(detail: CaseDetail) -> str:
    """신고 정보를 옅은 회색 박스 안에 `라벨 : 값` 줄로 세로 나열한 HTML로 만든다.

    고객 신고 상세의 접수 정보 박스(`customer_report_detail.info_html`)와 같은 모양이다
    (`common.info_box_html`).

    Args:
        detail: `service.get_engineer_case`가 돌려준 신고 상세.

    Returns:
        str: 접수번호·상태·설비·심각도·접수 시각·에러코드·증상 메모를 한 줄씩 담은 HTML.
    """
    case = detail.service_case
    fields = [
        ("접수번호", case.case_id),
        ("상태", common.status_label(case.status)),
        ("설비", str(case.equipment)),
        ("심각도", str(detail.error_code.severity)),
        ("접수 시각", common.format_datetime(case.created_at)),
        ("에러코드", f"{case.code} · {detail.error_code.name}"),
        ("증상 메모", case.memo or "-"),
    ]
    return common.info_box_html(fields)


def chat_markdown(turns: list[ChatTurn], completed: bool = False) -> str:
    """대화 목록을 화면용 Markdown으로 만든다. 답변은 `common.format_answer_markdown` 표기를 쓴다.

    Args:
        turns: 오래된 순 대화 목록.
        completed: 완료된 건이면 `True`. 대화가 없을 때 보일 문구만 달라진다.

    Returns:
        str: 질문·답변을 차례로 담은 Markdown. 대화가 없으면 완료 건은 `COMPLETED_NO_CHAT_NOTICE`,
            진행 중인 건은 `EMPTY_CHAT_NOTICE`.
    """
    if not turns:
        return COMPLETED_NO_CHAT_NOTICE if completed else EMPTY_CHAT_NOTICE
    blocks = [
        f"**Q{no}.** {turn.question}\n\n{common.format_answer_markdown(turn.answer, show_notice=True)}"
        for no, turn in enumerate(turns, start=1)
    ]
    return "\n\n---\n\n".join(blocks)


def resolution_html(detail: CaseDetail) -> str:
    """확정된 조치 결과를 연한 하늘색 박스 안에 왼쪽 정렬로 담은 HTML로 만든다. 엔지니어 화면이라 원인도 보인다.

    Args:
        detail: 신고 상세.

    Returns:
        str: 원인·조치 내용·결과를 담은 왼쪽 정렬 박스 HTML. 조치 결과가 없으면 `NO_RESOLUTION_NOTICE`만
            담은 왼쪽 정렬 텍스트.
    """
    record = detail.resolution
    if record is None:
        return f'<div style="text-align:left">{NO_RESOLUTION_NOTICE}</div>'
    fields = [("원인", record.cause), ("조치 내용", record.action), ("결과", record.result)]
    return common.info_box_html(fields, color=RESOLUTION_BOX_COLOR, align="left", title="조치 결과")


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
        with common.back_button_row("## 장애 분석 대화") as back_btn:
            resolve_btn = gr.Button("조치 결과 작성", scale=0, min_width=140)
        with gr.Row(equal_height=False, elem_classes=[common.WIDE_GAP_ROW_CLASS]):
            with gr.Column(scale=4):  # 왼쪽: 신고 정보 + 조치 결과(확정 전엔 안내 문구)
                summary = gr.HTML()
                resolution = gr.HTML()
            with gr.Column(scale=6):  # 오른쪽: 대화
                chat = gr.Markdown()
                source_pick = gr.Dropdown(choices=[], value=None, label="출처를 고르면 원문을 봅니다", visible=False)
                question = gr.Textbox(label="질문", lines=3, max_length=QUESTION_MAX_LENGTH)
                send_btn = gr.Button("질문 보내기", variant="primary")
        with gr.Column(visible=False, elem_classes=[common.MODAL_OVERLAY_CLASS]) as source_box:
            with gr.Column(elem_classes=[common.MODAL_CARD_CLASS]):
                gr.Markdown("### 출처 원문")
                source_text = gr.Textbox(show_label=False, lines=10, interactive=False)
                source_close_btn = gr.Button("닫기")

    def load_case(selected: str | None):
        # 신고와 대화를 다시 읽어 화면 값을 만든다. 출력 순서는 refresh_outputs와 같다.
        detail = service.get_engineer_case(selected or "")
        turns = service.list_chat_turns(selected or "")
        found = citations_of(turns)
        choices = [(common.citation_label(c), str(i)) for i, c in enumerate(found)]
        open_ = can_ask(detail)
        # 완료된 건은 읽기 전용: 질문 입력과 두 버튼을 숨긴다. send_btn은 잠금 해제 때문에 맨 끝에 둔다.
        return (
            case_summary_html(detail), chat_markdown(turns, completed=not open_), resolution_html(detail),
            gr.Dropdown(choices=choices, value=None, visible=bool(found)), gr.Column(visible=False), found,
            gr.Textbox(visible=open_, interactive=open_), gr.Button(visible=open_),
            gr.Button(visible=open_, interactive=open_),
        )

    refresh_outputs = [summary, chat, resolution, source_pick, source_box, citations, question, resolve_btn, send_btn]

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
            return (gr.Column(visible=True), "", "", "") + (gr.skip(),) * (len(refresh_outputs) - 3) + (gr.skip(),)

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
        # 출처를 고르면 모달로 그 원문을 보여 준다.
        if index is None or index == "":
            return gr.Column(visible=False), ""
        try:
            text = service.get_citation_text(found[int(index)])
        except Exception as e:
            gr.Warning(common.error_message(e))
            return gr.Column(visible=False), ""
        return gr.Column(visible=True), text

    def on_source_close():
        # 모달을 닫으면서 고른 출처도 비운다. 그래야 같은 출처를 다시 골라도 change가 발생해 모달이 열린다.
        return gr.Column(visible=False), None, ""

    screen.change(on_screen_change, inputs=[screen, case_id], outputs=[column, *refresh_outputs, screen])
    send_btn.click(lambda: gr.Button(interactive=False), outputs=send_btn).then(
        on_send, inputs=[case_id, question], outputs=question,
    ).then(on_after_send, inputs=case_id, outputs=refresh_outputs)
    source_pick.change(on_source_pick, inputs=[source_pick, citations], outputs=[source_box, source_text])
    source_close_btn.click(on_source_close, outputs=[source_box, source_pick, source_text])
    back_btn.click(lambda: common.SCREEN_ENGINEER_LIST, outputs=screen)
    resolve_btn.click(lambda: common.SCREEN_ENGINEER_RESOLVE, outputs=screen)
