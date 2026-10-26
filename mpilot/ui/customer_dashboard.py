"""고객 신고 목록 화면.

역할:
    고객이 직접 접수한 신고를 최신순·상태별로 보여 주고, 상세·작성 화면으로 보낸다.

책임:
    - 목록은 `service.list_customer_cases`만 쓴다. 고객에게는 직접 접수한 신고만 보인다.
    - 상태(`received`, `in_progress`, `completed`)별로 거를 수 있다. 기간·원본 필터, 상태별 건수, 페이지 이동은 범위 밖이다.
    - 행을 누르면 `case_id`를 채우고 고객 신고 상세로, '신고하기'를 누르면 고객 신고 작성으로 이동한다.
    - 로그아웃하면 로그인 화면으로 돌아간다.

화면 전환: 고객 신고 목록 → 상세(행 선택) / 작성(신고하기) / 로그인(로그아웃).
사용 서비스: `list_customer_cases`.
소유: UI·총괄 파트
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import gradio as gr


def build_customer_dashboard_screen(role: gr.State, case_id: gr.State, screen: gr.State) -> None:
    """고객 본인의 신고 목록을 최신순·상태별로 보여 준다.

    Args:
        role: 로그인한 역할(`Role` 값)을 담은 공유 상태. 고객 역할이 아니면 이 화면을 보이지 않는다.
        case_id: 선택된 신고의 접수번호를 담은 공유 상태. 행을 선택하면 그 신고의 접수번호로 채운다.
        screen: 지금 보여 줄 화면을 가리키는 공유 상태. 화면 전환 그림의 상태 이름과 대응한다. 행 선택은 고객 신고 상세, 신고하기는 고객 신고 작성, 로그아웃은 로그인으로 바꾼다.

    Returns:
        None: Gradio Blocks 안에서 호출되어 컴포넌트와 이벤트를 등록할 뿐 값을 돌려주지 않는다.
    """
    ...
