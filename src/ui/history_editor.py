"""이력 초안 확인·수정, "예상과 달랐는지" 체크, 확정. 구조화·저장은 history가 한다.

소유: 서비스 이력. 베이스라인 스텁이다. 담당이 같은 시그니처의 실제 구현으로 덮어쓴다(spec 6장).
"""
import gradio as gr


def build() -> None:
    """gr.Tab 안에서 호출되어 이 화면의 컴포넌트와 이벤트를 만든다."""
    gr.Markdown("history_editor: 구현 예정")
