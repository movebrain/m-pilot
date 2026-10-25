"""고객 신고 작성 화면.

역할:
    고객이 설비·에러코드·증상 메모를 입력해 신고를 제출한다.

책임:
    - 에러코드 선택지는 `service.list_error_codes`, 제출은 `service.submit_case`만 쓴다.
    - 설비를 고르면 그 설비의 코드만 선택지로 보인다. 증상 메모는 선택이며 2,000자 이하다.
    - 제출하면 접수번호가 발급되고 고객 신고 상세로 이동한다. 처리 경로(셀프 가이드·엔지니어 접수)는 서비스가 정하며
      이 화면은 판단하지 않는다.
    - 제출 중에는 버튼을 비활성화해 중복 제출을 막는다(멱등 키·잠금은 범위 밖).
    - 사진·로그 첨부는 범위 밖이다.

화면 전환: 신고 작성 → 상세(제출 성공) / 목록(뒤로).
사용 서비스: `list_error_codes`, `submit_case`.
소유: UI·총괄 파트
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import gradio as gr


def build_customer_report_form_screen(role: gr.State, case_id: gr.State, screen: gr.State) -> None:
    """설비·에러코드·증상 메모를 입력받아 신고를 제출한다.

    Args:
        role: 로그인한 역할(`Role` 값)을 담은 공유 상태. 고객 역할이 아니면 이 화면을 보이지 않는다.
        case_id: 선택된 신고의 접수번호를 담은 공유 상태. 제출에 성공하면 새 접수번호로 채운다.
        screen: 지금 보여 줄 화면을 가리키는 공유 상태. 화면 전환 그림의 상태 이름과 대응한다. 제출 성공은 고객 신고 상세, 뒤로는 고객 신고 목록으로 바꾼다.

    Returns:
        None: Gradio Blocks 안에서 호출되어 컴포넌트와 이벤트를 등록할 뿐 값을 돌려주지 않는다.
    """
    ...
