"""로그인 화면.

역할:
    데모 계정(ID·비밀번호)으로 로그인하고 역할에 맞는 첫 화면으로 보낸다.

책임:
    - 로그인은 `service.login`만 쓴다. 실패(`ValidationError`)는 고정 문구로 안내한다.
    - 고객이면 고객 신고 목록, 엔지니어면 엔지니어 대시보드로 `screen`을 바꾸고 `role`을 채운다.
    - 로그아웃은 각 영역의 목록 화면에서 이 화면으로 돌아온다. 이때 `role`·`case_id`를 비운다.
    - 역할 분리는 데모용이며 인증·권한 보안이 아니다.

화면 전환: 시작 → 로그인 → 고객 신고 목록 / 엔지니어 대시보드.
사용 서비스: `login`.
소유: UI·총괄 파트
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import gradio as gr


def build_login_screen(role: gr.State, case_id: gr.State, screen: gr.State) -> None:
    """로그인 입력(ID·비밀번호)을 받아 로그인하고 실패를 안내한다.

    Args:
        role: 로그인한 역할(`Role` 값)을 담은 공유 상태. 로그인에 성공하면 이 화면이 `customer` 또는 `engineer`로 채운다.
        case_id: 선택된 신고의 접수번호를 담은 공유 상태. 이 화면은 읽지 않으며 로그인 때 비운다.
        screen: 지금 보여 줄 화면을 가리키는 공유 상태. 화면 전환 그림의 상태 이름과 대응한다. 로그인에 성공하면 역할의 첫 화면(고객 신고 목록 또는 엔지니어 대시보드)으로 바꾼다.

    Returns:
        None: Gradio Blocks 안에서 호출되어 컴포넌트와 이벤트를 등록할 뿐 값을 돌려주지 않는다.
    """
    ...
