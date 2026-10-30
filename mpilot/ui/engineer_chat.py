"""장애 분석 대화 화면.

역할:
    엔지니어가 신고 건에서 질문을 이어가며 출처가 붙은 답을 받는다.

책임:
    - 신고 정보는 `service.get_engineer_case`, 대화는 `service.list_chat_turns`·`service.send_chat_question`,
      출처 원문은 `service.get_citation_text`만 쓴다.
    - 답변의 각 단계에 출처 표기와 [원문보기] 버튼을 나란히 보이고, 누르면 모달 팝업으로 원문을 보여 준다.
    - 근거 부족은 조치 없이 안내만 보여 주고, AI 오류(`DependencyError`)는 고정 문구로 안내한다.
    - 첫 질문에서 신고가 '조치 중'이 되는 것은 서비스가 처리하며 이 화면은 결과만 반영한다.
    - 완료된 건과 고객 확인 전 셀프 가이드 건은 읽기 전용이다. [조치 결과 작성]은 숨기고, 질문 입력창·[전송]은
      화면 구조를 그대로 유지하기 위해 숨기지 않고 비활성화만 한다. 완료 후 채팅·재개는 범위 밖이다.
      고객이 [기술지원접수]으로 이관(`route=support`)한 건은 조치 중이므로 질문할 수 있다.
    - 셀프 가이드를 받은 건은 고객에게 나간 안내 단계를 조치 결과 박스 아래에 보여 준다.
    - 질문 전송 중에는 버튼을 비활성화한다.

화면 전환: 대화 → 이력 저장(조치 결과 작성) / 대시보드(뒤로).
사용 서비스: `get_engineer_case`, `list_chat_turns`, `send_chat_question`, `get_citation_text`.
소유: UI·총괄 파트
"""

from __future__ import annotations

import gradio as gr

from mpilot import service
from mpilot.models import Answer, CaseDetail, CaseStatus, ChatTurn, Citation, NotFoundError, Route, Verdict
from mpilot.ui import common
from mpilot.ui.customer_report_detail import guide_steps_html

SCREEN = common.SCREEN_ENGINEER_CHAT

QUESTION_MAX_LENGTH = service.MAX_QUESTION_LENGTH
"""질문 입력의 최대 글자 수(서비스 검증과 같다)."""

EMPTY_CHAT_NOTICE = "아직 대화가 없습니다. 첫 질문을 보내면 신고가 '조치 중'이 됩니다."
"""진행 중인 건에 대화가 없을 때 보이는 문구."""

COMPLETED_NO_CHAT_NOTICE = "AI 대화 없이 완료된 건입니다."
"""완료된 건에 대화가 없을 때 보이는 문구(시드 이력 포함). 더 질문할 수 없으므로 질문 안내를 하지 않는다."""

SELF_GUIDE_WAITING_NOTICE = "고객이 셀프 가이드를 확인하고 있습니다. 고객이 기술지원접수를 누르면 이 대화를 쓸 수 있습니다."
"""고객 확인 전 셀프 가이드 건에 대화가 없을 때 보이는 문구."""

RESOLUTION_BOX_COLOR = "#e0f2fe"
"""조치 결과 박스 배경색(연한 하늘색). 접수 정보 박스(회색)와 구분한다."""

VIEW_SOURCE_LABEL = "원문보기"
"""단계별 출처 옆에 두는 작은 버튼 문구. 누르면 모달로 그 단계의 출처 원문을 보여 준다."""

COLUMNS_PER_ROW = 3
"""접수내역 표의 한 줄당 열 수. 항목이 6개라 한 줄에 다 두면 각 열이 너무 좁아져 3개씩 두 줄로 나눈다."""

QUESTION_PLACEHOLDER = "질문을 입력하세요"
"""질문을 보낼 수 있는 건의 입력창 플레이스홀더."""

QUESTION_PLACEHOLDER_COMPLETED = "조치가 완료되었습니다"
"""완료된 건의 입력창 플레이스홀더. 더 이상 질문을 보낼 일이 없음을 알린다."""


def case_summary_html(detail: CaseDetail) -> str:
    """신고 정보를 '접수내역' 제목을 단, 신고 목록 표와 같은 모양의 칼럼 표로 만든다.

    고객 신고 상세의 접수 정보 표(`customer_report_detail.info_html`)와 기본 모양은 같되
    (`common.info_table_html`), 조치 결과와 같은 좁은 왼쪽 열 안에 있으므로 고정 너비를 두지 않고
    열 너비를 그대로 따른다. 항목도 6개로 더 많아 한 줄에 다 두면 좁아지므로 3열씩 두 줄로 나눠
    보여 준다(`COLUMNS_PER_ROW`). 증상 메모는 길어질 수 있어 열에 넣지 않고 표 맨 아래 전체 너비
    한 줄로 내려 보여 준다.

    Args:
        detail: `service.get_engineer_case`가 돌려준 신고 상세.

    Returns:
        str: 접수번호·상태·설비(1행)·심각도·접수 시각·에러코드(2행)를 열로, 증상 메모를 아래 한 줄로
            담은 HTML.
    """
    case = detail.service_case
    fields = [
        ("접수번호", case.case_id),
        ("상태", common.status_label(case.status)),
        ("설비", str(case.equipment)),
        ("심각도", str(detail.error_code.severity)),
        ("접수 시각", common.format_datetime(case.created_at)),
        ("에러코드", f"{case.code} · {detail.error_code.name}"),
    ]
    return common.info_table_html(
        fields, title="접수내역", max_width="100%", columns_per_row=COLUMNS_PER_ROW,
        note=("증상 메모", case.memo or "-"),
    )


def chat_state_of(turns: list[ChatTurn], completed: bool, waiting_customer: bool = False) -> dict:
    """대화 기록 렌더링(`render_chat`)에 넘길 상태를 만든다.

    `gr.render`는 바뀌었는지 보려고 입력 상태를 해시하는데, `ChatTurn`처럼 `list` 필드를 가진 pydantic
    모델은 그대로 해시할 수 없다(`TypeError: unhashable type: 'list'`). 그래서 각 턴을 평범한
    `dict`(`model_dump()`)로 바꿔 담는다.

    Args:
        turns: 오래된 순 대화 목록.
        completed: 완료된 건이면 `True`. 대화가 없을 때 보일 문구만 달라진다.
        waiting_customer: 고객 확인 전 셀프 가이드 건이면 `True`. 대화가 없을 때 보일 문구만 달라진다.

    Returns:
        dict: `{"turns": [턴마다 model_dump() 결과], "completed": ..., "waiting_customer": ...}`.
            `gr.render`의 입력 `gr.State` 값으로 그대로 쓴다.
    """
    return {"turns": [turn.model_dump() for turn in turns], "completed": completed, "waiting_customer": waiting_customer}


def resolution_html(detail: CaseDetail) -> str:
    """확정된 조치 결과를 세로형 표로 담은 HTML로 만든다. 엔지니어 화면이라 원인도 보인다.

    표·라벨(조치 결과·원인·조치 내용·결과)은 조치 결과가 있든 없든 똑같이 보인다. 확정된 조치가
    없으면 값만 공란으로 둔다. 접수내역 표(`case_summary_html`)와 테두리·여백·글자 크기·라벨(연회색)·
    값(흰색) 배경을 같게 둬서 두 표가 한 디자인처럼 보이게 한다(`common.info_table_html`). 대화 옆
    좁은 왼쪽 열 안에 접수내역과 함께 있으므로, 고정 너비를 강제하지 않고 열 너비를 그대로 따른다
    (강제하면 대화창 쪽으로 넘친다).

    Args:
        detail: 신고 상세.

    Returns:
        str: 원인·조치 내용·결과를 담은 세로형 표 HTML. 조치 결과가 없으면 값이 모두 빈 칸이다.
    """
    record = detail.resolution
    fields = [
        ("원인", record.cause if record else ""),
        ("조치 내용", record.action if record else ""),
        ("결과", record.result if record else ""),
    ]
    return common.info_table_html(
        fields, title="조치 결과", vertical=True, max_width="100%",
    )


def can_ask(detail: CaseDetail) -> bool:
    """이 신고에 질문할 수 있는지 판정한다.

    Args:
        detail: 신고 상세.

    Returns:
        bool: 완료되지 않은 엔지니어 처리 건(`support`)이면 `True`. 완료 건과 고객 확인 전 셀프 가이드 건은 `False`.
    """
    case = detail.service_case
    return case.status != CaseStatus.COMPLETED and case.route == Route.SUPPORT


def guide_box_html(detail: CaseDetail) -> str:
    """고객에게 나간 셀프 가이드 단계를 조치 결과 박스 아래에 둘 HTML로 만든다.

    Args:
        detail: 신고 상세.

    Returns:
        str: 셀프 가이드를 받은 건은 제목과 단계가 든 박스 HTML. 받지 않은 건은 빈 문자열.
    """
    answer = detail.service_case.guidance_answer
    if answer is None:
        return ""
    return (
        f'<div style="margin-top:12px;background:#f4f4f5;border-radius:8px;padding:16px;text-align:left">'
        f'<div style="font-weight:700">고객 안내(셀프 가이드)</div>{guide_steps_html(answer)}</div>'
    )


def citation_source_text(citations: list[Citation]) -> str:
    """단계 하나의 출처들을 원문 모달에 보일 문자열로 모은다.

    Args:
        citations: 한 단계의 출처 목록.

    Returns:
        str: 출처마다 표기(`common.citation_label`)와 원문(`service.get_citation_text`)을 담아
            여러 개면 구분선으로 잇는다.
    """
    return "\n\n---\n\n".join(
        f"{common.citation_label(c)}\n{service.get_citation_text(c)}" for c in citations
    )


def build_engineer_chat_screen(role: gr.State, case_id: gr.State, screen: gr.State) -> None:
    """신고 정보와 대화를 보여 주고 질문 전송·출처 원문 확인을 처리한다.

    Args:
        role: 앱이 고정한 역할(`engineer`)을 담은 공유 상태. 엔지니어 앱에서만 이 화면을 조립한다.
        case_id: 선택된 신고의 접수번호를 담은 공유 상태. 대화할 신고의 접수번호. 이 화면은 읽기만 한다.
        screen: 지금 보여 줄 화면을 가리키는 공유 상태. 화면 전환 그림의 상태 이름과 대응한다. 조치 결과 작성은 이력 저장, 뒤로는 엔지니어 대시보드로 바꾼다.

    Returns:
        None: Gradio Blocks 안에서 호출되어 컴포넌트와 이벤트를 등록할 뿐 값을 돌려주지 않는다.
    """
    chat_state = gr.State(chat_state_of([], False))  # 지금 보이는 대화 기록(render_chat 입력)
    with gr.Column(visible=common.is_screen_visible(screen.value, SCREEN)) as column:
        with common.back_button_row("## 장애 분석 대화") as back_btn:
            resolve_btn = gr.Button("조치 결과 작성", scale=0, min_width=140)
        with gr.Row(equal_height=False, elem_classes=[common.WIDE_GAP_ROW_CLASS]):
            with gr.Column(scale=4):  # 왼쪽: 접수내역 + 조치 결과(확정 전엔 안내 문구)
                summary = gr.HTML()
                resolution = gr.HTML()
            with gr.Column(scale=6):  # 오른쪽: 대화
                with gr.Column(elem_classes=[common.CHAT_HISTORY_BOX_CLASS]):

                    @gr.render(inputs=chat_state)
                    def render_chat(state: dict) -> None:
                        # 질문·답변을 차례로 그리고, 단계마다 출처 옆에 [원문보기] 버튼을 둔다.
                        # 턴은 chat_state_of()가 평범한 dict로 바꿔 둔 것이라, 여기서 Answer로 되살린다.
                        turns: list[dict] = state["turns"]
                        if not turns:
                            if state["completed"]:
                                notice = COMPLETED_NO_CHAT_NOTICE
                            elif state["waiting_customer"]:
                                notice = SELF_GUIDE_WAITING_NOTICE
                            else:
                                notice = EMPTY_CHAT_NOTICE
                            gr.Markdown(notice)
                            return
                        for no, turn in enumerate(turns, start=1):
                            if no > 1:
                                gr.Markdown("---")
                            gr.Markdown(f"**Q{no}.** {turn['question']}")
                            answer = Answer(**turn["answer"])
                            if answer.verdict != Verdict.ANSWERED:
                                gr.Markdown(common.format_answer_markdown(answer, show_notice=True))
                                continue
                            if answer.notice:
                                gr.Markdown(f"> {answer.notice}")
                            for step_no, step in enumerate(answer.steps, start=1):
                                if not step.citations:
                                    gr.Markdown(f"{step_no}. {step.text}")
                                    continue
                                sources = ", ".join(common.citation_label(c) for c in step.citations)
                                with gr.Row():
                                    gr.Markdown(f"{step_no}. {step.text}\n   - 출처: {sources}", scale=8)
                                    view_btn = gr.Button(
                                        VIEW_SOURCE_LABEL, size="sm", scale=0,
                                        elem_classes=[common.VIEW_SOURCE_BUTTON_CLASS],
                                    )

                                    def on_view_source(cites=step.citations):
                                        try:
                                            text = citation_source_text(cites)
                                        except Exception as e:
                                            gr.Warning(common.error_message(e))
                                            return gr.Column(visible=False), ""
                                        return gr.Column(visible=True), text

                                    view_btn.click(on_view_source, outputs=[source_box, source_text])

                with gr.Row(elem_classes=[common.QUESTION_INPUT_ROW_CLASS]):
                    question = gr.Textbox(
                        show_label=False, placeholder=QUESTION_PLACEHOLDER, lines=1,
                        max_length=QUESTION_MAX_LENGTH, container=False, scale=8,
                    )
                    send_btn = gr.Button("전송", variant="primary", scale=0, min_width=72)
        with gr.Column(visible=False, elem_classes=[common.MODAL_OVERLAY_CLASS]) as source_box:
            with gr.Column(elem_classes=[common.MODAL_CARD_CLASS]):
                gr.Markdown("### 출처 원문")
                source_text = gr.Textbox(show_label=False, lines=10, interactive=False)
                source_close_btn = gr.Button("닫기")

    def load_case(selected: str | None):
        # 신고와 대화를 다시 읽어 화면 값을 만든다. 출력 순서는 refresh_outputs와 같다.
        detail = service.get_engineer_case(selected or "")
        turns = service.list_chat_turns(selected or "")
        open_ = can_ask(detail)
        completed = detail.service_case.status == CaseStatus.COMPLETED
        waiting_customer = not open_ and not completed
        # 완료된 건과 고객 확인 전 셀프 가이드 건은 읽기 전용: 질문 입력창·전송 버튼은 그대로 두고 비활성화만 한다(화면 구조를 신고
        # 상태와 상관없이 그대로 유지하기 위해 숨기지 않는다). [조치 결과 작성]만 숨긴다.
        # send_btn은 잠금 해제 때문에 맨 끝에 둔다. 신고를 새로 불러올 때마다 열려 있던 원문 모달은 닫는다.
        placeholder = QUESTION_PLACEHOLDER if open_ else QUESTION_PLACEHOLDER_COMPLETED
        return (
            case_summary_html(detail), chat_state_of(turns, completed, waiting_customer),
            resolution_html(detail) + guide_box_html(detail),
            gr.Column(visible=False), "",
            gr.Textbox(visible=True, interactive=open_, placeholder=placeholder), gr.Button(visible=open_),
            gr.Button(visible=True, interactive=open_),
        )

    refresh_outputs = [summary, chat_state, resolution, source_box, source_text, question, resolve_btn, send_btn]

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
            empty_chat = chat_state_of([], completed=False)
            return (
                gr.Column(visible=True), "", empty_chat, "",
            ) + (gr.skip(),) * (len(refresh_outputs) - 3) + (gr.skip(),)

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

    screen.change(on_screen_change, inputs=[screen, case_id], outputs=[column, *refresh_outputs, screen])
    send_btn.click(lambda: gr.Button(interactive=False), outputs=send_btn).then(
        on_send, inputs=[case_id, question], outputs=question,
    ).then(on_after_send, inputs=case_id, outputs=refresh_outputs)
    source_close_btn.click(lambda: gr.Column(visible=False), outputs=source_box)
    back_btn.click(lambda: common.SCREEN_ENGINEER_LIST, outputs=screen)
    resolve_btn.click(lambda: common.SCREEN_ENGINEER_RESOLVE, outputs=screen)
