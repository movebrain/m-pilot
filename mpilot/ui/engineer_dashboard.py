"""엔지니어 대시보드 화면.

역할:
    엔지니어가 시드 이력과 신규 신고를 상태별로 보고 대응할 건을 고른다.

책임:
    - 목록은 `service.list_engineer_cases`만 쓴다. 시드 이력을 포함한 전체 신고를 상태별로 보여 준다.
    - 기술지원(`support`) 건을 누르면 `case_id`를 채우고 장애 분석 대화로 이동한다.
    - 셀프 가이드 건은 '고객 안내'로만 표시하고 대화창으로 보내지 않는다.
    - 기간·원본 필터, 상태별 건수, '대응 시작' 버튼, 새 신고 표시(N/NEW), 긴급 강조는 범위 밖이다.

화면 전환: 대시보드 → 장애 분석 대화(기술지원 건 선택). 엔지니어 앱의 첫 화면이다(CHG-01).
사용 서비스: `list_engineer_cases`.
소유: UI·총괄 파트
"""

from __future__ import annotations

import gradio as gr

from mpilot import service
from mpilot.models import Route
from mpilot.ui import common

SCREEN = common.SCREEN_ENGINEER_LIST


def _support_case_choices() -> list[str]:
    """대화창으로 보낼 수 있는 기술지원 건의 접수번호 목록을 만든다(뼈대용, 표는 대시보드 이슈에서 만든다)."""
    return [d.service_case.case_id for d in service.list_engineer_cases() if d.service_case.route == Route.SUPPORT]


def build_engineer_dashboard_screen(role: gr.State, case_id: gr.State, screen: gr.State) -> None:
    """시드 이력을 포함한 전체 신고를 상태별로 보여 주고 대응할 건을 선택받는다.

    Args:
        role: 앱이 고정한 역할(`engineer`)을 담은 공유 상태. 엔지니어 앱에서만 이 화면을 조립한다.
        case_id: 선택된 신고의 접수번호를 담은 공유 상태. 기술지원 건을 선택하면 그 신고의 접수번호로 채운다.
        screen: 지금 보여 줄 화면을 가리키는 공유 상태. 화면 전환 그림의 상태 이름과 대응한다. 기술지원 건 선택은 장애 분석 대화로 바꾼다.

    Returns:
        None: Gradio Blocks 안에서 호출되어 컴포넌트와 이벤트를 등록할 뿐 값을 돌려주지 않는다.
    """
    with gr.Column(visible=common.is_screen_visible(screen.value, SCREEN)) as column:
        gr.Markdown("## 엔지니어 대시보드")
        picked = gr.Dropdown(choices=_support_case_choices(), label="기술지원 건 선택")
        open_btn = gr.Button("대화 열기", variant="primary")

    def on_screen_change(current: str):
        # 이 화면이 다시 보일 때마다 목록을 새로 읽는다(새 신고·완료 반영).
        if not common.is_screen_visible(current, SCREEN):
            return gr.Column(visible=False), gr.skip()
        return gr.Column(visible=True), gr.Dropdown(choices=_support_case_choices(), value=None)

    def on_open(selected: str | None):
        if not selected:
            return gr.skip(), gr.skip()
        return selected, common.SCREEN_ENGINEER_CHAT

    screen.change(on_screen_change, inputs=screen, outputs=[column, picked])
    open_btn.click(on_open, inputs=picked, outputs=[case_id, screen])
