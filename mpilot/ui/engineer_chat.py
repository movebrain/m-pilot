"""장애 분석 대화 화면.

역할:
    엔지니어가 신고 건에서 질문을 이어가며 출처가 붙은 답을 받는다.

책임:
    - 신고 정보는 `service.get_engineer_case`, 대화는 `service.list_chat_turns`·`service.send_chat_question`,
      출처 원문은 `service.get_citation_text`만 쓴다.
    - 답변의 각 단계에 출처를 보여 주고 펼치면 원문을 보여 준다.
    - 근거 부족·대상 아님은 조치 없이 안내만 보여 주고, AI 오류(`DependencyError`)는 고정 문구로 안내한다.
    - 첫 질문에서 신고가 '조치 중'이 되는 것은 서비스가 처리하며 이 화면은 결과만 반영한다.
    - 완료된 건은 질문할 수 없다('이미 완료'). 완료 후 채팅·재개는 범위 밖이다.
    - 질문 전송 중에는 버튼을 비활성화한다.

화면 전환: 대화 → 이력 저장(조치 결과 작성) / 대시보드(뒤로).
사용 서비스: `get_engineer_case`, `list_chat_turns`, `send_chat_question`, `get_citation_text`.
소유: UI·총괄 파트
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import gradio as gr


def build_engineer_chat_screen(role: gr.State, case_id: gr.State, screen: gr.State) -> None:
    """신고 정보와 대화를 보여 주고 질문 전송·출처 원문 확인을 처리한다.

    Args:
        role: 로그인한 역할(`Role` 값)을 담은 공유 상태. 엔지니어 역할이 아니면 이 화면을 보이지 않는다.
        case_id: 선택된 신고의 접수번호를 담은 공유 상태. 대화할 신고의 접수번호. 이 화면은 읽기만 한다.
        screen: 지금 보여 줄 화면을 가리키는 공유 상태. 화면 전환 그림의 상태 이름과 대응한다. 조치 결과 작성은 이력 저장, 뒤로는 엔지니어 대시보드로 바꾼다.

    Returns:
        None: Gradio Blocks 안에서 호출되어 컴포넌트와 이벤트를 등록할 뿐 값을 돌려주지 않는다.
    """
    ...
