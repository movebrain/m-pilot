"""고객 신고 목록 화면.

역할:
    고객이 직접 접수한 신고를 최신순·상태별로 보여 주고, 상세·작성 화면으로 보낸다.

책임:
    - 목록은 `service.list_customer_cases`만 쓴다. 고객에게는 직접 접수한 신고만 보인다.
    - 상태(`received`, `in_progress`, `completed`)별로 거를 수 있다. 기간·원본 필터, 상태별 건수, 페이지 이동은 범위 밖이다.
    - 행을 누르면 `case_id`를 채우고 고객 신고 상세로, '신고하기'를 누르면 고객 신고 작성으로 이동한다.

화면 전환: 고객 신고 목록 → 상세(행 선택) / 작성(신고하기).
사용 서비스: `list_customer_cases`.
소유: UI·총괄 파트
"""

from __future__ import annotations

import gradio as gr

from mpilot import service
from mpilot.ui import common

SCREEN = common.SCREEN_CUSTOMER_LIST


def _case_choices() -> list[str]:
    """선택 드롭다운에 넣을 접수번호 목록을 만든다(10/26 뼈대용, 표는 다음 이슈에서 만든다)."""
    return [c.case_id for c in service.list_customer_cases()]


def build_customer_dashboard_screen(role: gr.State, case_id: gr.State, screen: gr.State) -> None:
    """고객 본인의 신고 목록을 최신순·상태별로 보여 준다.

    Args:
        role: 앱이 고정한 역할(`customer`)을 담은 공유 상태.
        case_id: 선택된 신고의 접수번호를 담은 공유 상태. 신고를 고르면 그 접수번호로 채운다.
        screen: 지금 보여 줄 화면 이름을 담은 공유 상태. 상세 보기는 고객 신고 상세, 신고하기는 고객 신고 작성으로 바꾼다.

    Returns:
        None: Gradio Blocks 안에서 호출되어 컴포넌트와 이벤트를 등록할 뿐 값을 돌려주지 않는다.
    """
    with gr.Column(visible=common.is_screen_visible(screen.value, SCREEN)) as column:
        gr.Markdown("## 고객 신고 목록")
        picked = gr.Dropdown(choices=_case_choices(), label="신고 선택")
        with gr.Row():
            open_btn = gr.Button("상세 보기")
            new_btn = gr.Button("신고하기", variant="primary")

    def on_screen_change(current: str):
        # 이 화면이 다시 보일 때마다 목록을 새로 읽는다(새 신고 반영).
        if not common.is_screen_visible(current, SCREEN):
            return gr.Column(visible=False), gr.skip()
        return gr.Column(visible=True), gr.Dropdown(choices=_case_choices(), value=None)

    def on_open(selected: str | None):
        if not selected:
            return gr.skip(), gr.skip()
        return selected, common.SCREEN_CUSTOMER_DETAIL

    screen.change(on_screen_change, inputs=screen, outputs=[column, picked])
    open_btn.click(on_open, inputs=picked, outputs=[case_id, screen])
    new_btn.click(lambda: common.SCREEN_CUSTOMER_FORM, outputs=screen)