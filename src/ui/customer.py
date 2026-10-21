"""신고 폼, 접수번호 조회, 기술지원 대기 안내, 셀프 가이드(해결됨/해결 안 됨).
제출 핸들러가 db.create_case → routing.route → db.update_case 순서로 부른다(AC-06.3).
고객에게 보이는 필드만 렌더링한다(AC-07.2).

소유: 고객 접수. 베이스라인 스텁이다. 담당이 같은 시그니처의 실제 구현으로 덮어쓴다(spec 6장).
"""
import gradio as gr


def build() -> None:
    """gr.Tab 안에서 호출되어 이 화면의 컴포넌트와 이벤트를 만든다."""
    gr.Markdown("customer: 구현 예정")
