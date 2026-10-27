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

from mpilot.ui import common

SCREEN = common.SCREEN_CUSTOMER_DETAIL


def build_customer_report_detail_screen(role: gr.State, case_id: gr.State, screen: gr.State) -> None:
    """신고의 진행 상태와 셀프 가이드 또는 조치 결과를 보여 주고 출처 원문을 펼친다.

    Args:
        role: 앱이 고정한 역할(`customer`)을 담은 공유 상태. 고객 앱에서만 이 화면을 조립한다.
        case_id: 선택된 신고의 접수번호를 담은 공유 상태. 보여 줄 신고의 접수번호. 이 화면은 읽기만 한다.
        screen: 지금 보여 줄 화면을 가리키는 공유 상태. 화면 전환 그림의 상태 이름과 대응한다. 뒤로 가기와 접근 불가는 고객 신고 목록으로 바꾼다.

    Returns:
        None: Gradio Blocks 안에서 호출되어 컴포넌트와 이벤트를 등록할 뿐 값을 돌려주지 않는다.
    """
    with gr.Column(visible=common.is_screen_visible(screen.value, SCREEN)) as column:
        gr.Markdown("## 고객 신고 상세")
        title = gr.Markdown()
        back_btn = gr.Button("뒤로")

    def on_screen_change(current: str, selected: str | None):
        # 이 화면이 보일 때마다 고른 접수번호를 다시 읽는다.
        visible = common.is_screen_visible(current, SCREEN)
        if not visible:
            return gr.Column(visible=False), gr.skip()
        return gr.Column(visible=True), f"접수번호: {selected or '-'}"

    screen.change(on_screen_change, inputs=[screen, case_id], outputs=[column, title])
    back_btn.click(lambda: common.SCREEN_CUSTOMER_LIST, outputs=screen)
