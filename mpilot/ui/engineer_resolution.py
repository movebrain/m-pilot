"""조치 이력 저장 화면.

역할:
    엔지니어가 원인·실제 조치·결과를 입력해 신고를 조치 완료로 확정한다.

책임:
    - 신고 정보는 `service.get_engineer_case`, 확정은 `service.confirm_resolution`만 쓴다.
    - 원인·조치·결과는 각각 앞뒤 공백 제거 후 1~5,000자다. 임시 저장과 AI 초안은 범위 밖이다.
    - 확정하면 신고가 '조치 완료'가 되고 다음 질문의 근거가 된다. 검색 반영에 실패해도 저장은 유지되며
      반영 결과(`bool`)에 따라 안내한다.
    - 이미 완료된 건은 '이미 완료'로 안내한다. 확정 뒤에는 수정할 수 없다.
    - 제출 중에는 버튼을 비활성화한다. 원인(`cause`)은 고객에게 공개되지 않는다.

화면 전환: 이력 저장 → 대화(뒤로).
사용 서비스: `get_engineer_case`, `confirm_resolution`.
소유: UI·총괄 파트
"""

from __future__ import annotations

import gradio as gr

from mpilot.ui import common

SCREEN = common.SCREEN_ENGINEER_RESOLVE


def build_engineer_resolution_screen(role: gr.State, case_id: gr.State, screen: gr.State) -> None:
    """원인·실제 조치·결과를 입력받아 조치 완료로 등록한다.

    Args:
        role: 앱이 고정한 역할(`engineer`)을 담은 공유 상태. 엔지니어 앱에서만 이 화면을 조립한다.
        case_id: 선택된 신고의 접수번호를 담은 공유 상태. 완료 처리할 신고의 접수번호. 이 화면은 읽기만 한다.
        screen: 지금 보여 줄 화면을 가리키는 공유 상태. 화면 전환 그림의 상태 이름과 대응한다. 뒤로 가기는 장애 분석 대화로 바꾼다.

    Returns:
        None: Gradio Blocks 안에서 호출되어 컴포넌트와 이벤트를 등록할 뿐 값을 돌려주지 않는다.
    """
    with gr.Column(visible=common.is_screen_visible(screen.value, SCREEN)) as column:
        gr.Markdown("## 조치 이력 저장")
        title = gr.Markdown()
        back_btn = gr.Button("뒤로")

    def on_screen_change(current: str, selected: str | None):
        if not common.is_screen_visible(current, SCREEN):
            return gr.Column(visible=False), gr.skip()
        return gr.Column(visible=True), f"접수번호: {selected or '-'}"

    screen.change(on_screen_change, inputs=[screen, case_id], outputs=[column, title])
    back_btn.click(lambda: common.SCREEN_ENGINEER_CHAT, outputs=screen)
