"""엔지니어 대시보드 화면.

역할:
    엔지니어가 시드 이력과 신규 신고를 상태별로 보고 대응할 건을 고른다.

책임:
    - 목록은 `service.list_engineer_cases`만 쓴다. 시드 이력을 포함한 전체 신고를 상태별로 보여 준다.
    - 기술지원(`support`) 건을 누르면 `case_id`를 채우고 장애 분석 대화로 이동한다.
    - 셀프 가이드 건은 '고객 안내'로만 표시하고 대화창으로 보내지 않는다.
    - 기간·원본 필터, 상태별 건수, '대응 시작' 버튼, 새 신고 표시(N/NEW), 긴급 강조는 범위 밖이다.
    - 로그아웃하면 로그인 화면으로 돌아간다.

화면 전환: 대시보드 → 장애 분석 대화(기술지원 건 선택) / 로그인(로그아웃).
사용 서비스: `list_engineer_cases`.
소유: UI·총괄 파트
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import gradio as gr


def build_engineer_dashboard_screen(role: gr.State, case_id: gr.State, screen: gr.State) -> None:
    """시드 이력을 포함한 전체 신고를 상태별로 보여 주고 대응할 건을 선택받는다.

    Args:
        role: 로그인한 역할(`Role` 값)을 담은 공유 상태. 엔지니어 역할이 아니면 이 화면을 보이지 않는다.
        case_id: 선택된 신고의 접수번호를 담은 공유 상태. 기술지원 건을 선택하면 그 신고의 접수번호로 채운다.
        screen: 지금 보여 줄 화면을 가리키는 공유 상태. 화면 전환 그림의 상태 이름과 대응한다. 기술지원 건 선택은 장애 분석 대화, 로그아웃은 로그인으로 바꾼다.

    Returns:
        None: Gradio Blocks 안에서 호출되어 컴포넌트와 이벤트를 등록할 뿐 값을 돌려주지 않는다.
    """
    ...
