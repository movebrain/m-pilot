"""고객 신고 상세 화면.

역할:
    고객이 신고의 진행 상태와, 셀프 가이드 또는 엔지니어가 등록한 조치 결과를 확인한다.

책임:
    - 상세는 `service.get_customer_case`, 출처 원문은 `service.get_citation_text`만 쓴다.
    - 셀프 가이드 건은 단계별 안내와 출처를 보여 주고, 엔지니어 처리 건은 완료 뒤 조치 결과(`action`, `result`)를 보여 준다.
    - 내부 원인·`guide_failure`·엔지니어 내부 분석은 보이지 않는다(`CustomerCaseResult`에 없다).
    - 접근할 수 없는 신고(`NotFoundError`)는 고객 신고 목록으로 돌려보낸다.
    - 셀프 가이드 완료는 안내 제공의 종료이지 실제 해결 확인이 아니다. 추가 지원은 새 신고로 안내한다.

화면 전환: 상세 → 목록(뒤로·접근 불가).
사용 서비스: `get_customer_case`, `get_citation_text`.
소유: UI·총괄 파트
"""

from __future__ import annotations

import gradio as gr

from mpilot import service
from mpilot.models import Citation, CustomerCaseResult, NotFoundError
from mpilot.ui import common

SCREEN = common.SCREEN_CUSTOMER_DETAIL

SELF_GUIDE_NOTICE = "자동으로 제공된 안내입니다. 조치 후에도 문제가 계속되면 새로 신고해 주세요."
"""셀프 가이드 건 아래에 보이는 문구. 셀프 가이드 완료는 실제 해결 확인이 아니다(spec §4)."""

WAITING_NOTICE = "엔지니어가 확인하고 있습니다. 조치가 끝나면 이 화면에서 결과를 볼 수 있습니다."
"""엔지니어 처리 건이 완료되기 전에 보이는 문구."""


def summary_markdown(case: CustomerCaseResult) -> str:
    """신고의 기본 정보를 화면용 Markdown으로 만든다.

    Args:
        case: 고객 공개 신고 상세.

    Returns:
        str: 접수번호·설비·코드·에러명·상태·접수 시각·메모를 담은 Markdown.
    """
    return "\n\n".join([
        f"**접수번호** {case.case_id}",
        f"**설비** {case.equipment} · **코드** {case.code} · **에러명** {case.error_name}",
        f"**상태** {common.status_label(case.status)} · **접수 시각** {common.format_datetime(case.created_at)}",
        f"**증상 메모** {case.memo or '-'}",
    ])


def result_markdown(case: CustomerCaseResult) -> str:
    """안내 또는 조치 결과를 화면용 Markdown으로 만든다. 내부 원인은 담지 않는다.

    Args:
        case: 고객 공개 신고 상세.

    Returns:
        str: 셀프 가이드 건은 단계별 안내와 안내 문구, 완료된 엔지니어 처리 건은 조치·결과,
            완료 전 엔지니어 처리 건은 기다림 문구.
    """
    if case.guidance_answer is not None:
        return f"### 조치 안내\n\n{common.format_answer_markdown(case.guidance_answer)}\n\n{SELF_GUIDE_NOTICE}"
    if case.action is not None:
        return f"### 조치 결과\n\n**조치 내용** {case.action}\n\n**결과** {case.result or '-'}"
    return WAITING_NOTICE


def citations_of(case: CustomerCaseResult) -> list[Citation]:
    """셀프 가이드의 출처를 순서대로, 겹치지 않게 모은다.

    Args:
        case: 고객 공개 신고 상세.

    Returns:
        list[Citation]: 단계에 나온 순서의 출처 목록. 셀프 가이드가 아니면 빈 목록.
    """
    if case.guidance_answer is None:
        return []
    seen: dict[str, Citation] = {}
    for step in case.guidance_answer.steps:
        for citation in step.citations:
            seen.setdefault(citation.ref_id, citation)
    return list(seen.values())


def build_customer_report_detail_screen(role: gr.State, case_id: gr.State, screen: gr.State) -> None:
    """신고의 진행 상태와 셀프 가이드 또는 조치 결과를 보여 주고 출처 원문을 펼친다.

    Args:
        role: 앱이 고정한 역할(`customer`)을 담은 공유 상태. 고객 앱에서만 이 화면을 조립한다.
        case_id: 선택된 신고의 접수번호를 담은 공유 상태. 보여 줄 신고의 접수번호. 이 화면은 읽기만 한다.
        screen: 지금 보여 줄 화면을 가리키는 공유 상태. 화면 전환 그림의 상태 이름과 대응한다. 뒤로 가기와 접근 불가는 고객 신고 목록으로 바꾼다.

    Returns:
        None: Gradio Blocks 안에서 호출되어 컴포넌트와 이벤트를 등록할 뿐 값을 돌려주지 않는다.
    """
    citations = gr.State([])  # 지금 보이는 신고의 출처 목록
    with gr.Column(visible=common.is_screen_visible(screen.value, SCREEN)) as column:
        gr.Markdown("## 고객 신고 상세")
        summary = gr.Markdown()
        body = gr.Markdown()
        source_pick = gr.Dropdown(choices=[], value=None, label="출처를 고르면 원문을 봅니다", visible=False)
        with gr.Accordion("출처 원문", open=True, visible=False) as source_box:
            source_text = gr.Textbox(show_label=False, lines=6, interactive=False)
        back_btn = gr.Button("뒤로")

    def on_screen_change(current: str, selected: str | None):
        # 이 화면에 들어올 때마다 고른 신고를 다시 읽는다.
        if not common.is_screen_visible(current, SCREEN):
            return gr.Column(visible=False), gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip()
        try:
            case = service.get_customer_case(selected or "")
        except NotFoundError as e:
            gr.Warning(common.error_message(e))
            return gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(), common.SCREEN_CUSTOMER_LIST
        except Exception as e:  # 화면에는 고정 문구만 보인다
            gr.Warning(common.error_message(e))
            return gr.Column(visible=True), "", "", gr.Dropdown(visible=False), gr.Accordion(visible=False), [], gr.skip()
        found = citations_of(case)
        choices = [(common.citation_label(c), str(i)) for i, c in enumerate(found)]
        return (
            gr.Column(visible=True), summary_markdown(case), result_markdown(case),
            gr.Dropdown(choices=choices, value=None, visible=bool(found)), gr.Accordion(visible=False), found,
            gr.skip(),
        )

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

    screen.change(
        on_screen_change, inputs=[screen, case_id],
        outputs=[column, summary, body, source_pick, source_box, citations, screen],
    )
    source_pick.change(on_source_pick, inputs=[source_pick, citations], outputs=[source_box, source_text])
    back_btn.click(lambda: common.SCREEN_CUSTOMER_LIST, outputs=screen)
