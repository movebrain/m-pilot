"""앱 조립·실행 공통부: 설정·모듈 초기화, 역할별 화면 생성(CHG-01).

역할:
    고객 앱(`app_customer.py`)과 엔지니어 앱(`app_engineer.py`)이 함께 쓰는 시작 순서와 화면 조립을 둔다.
    직접 실행하지 않고 두 진입 파일이 `main(role, port)`를 부른다.

책임:
    - 시작 순서: 설정 로드 → `service.set_config` → `rdb.connect` → 임베딩으로 `vectordb.connect` →
      모델로 `generator.set_chat_model` → `service.prepare_search_index`(끝난 뒤에야 첫 화면이 열린다)
      → 화면 조립 → 실행.
    - 초기화가 `ValidationError`·`DependencyError`로 실패하면 화면을 띄우지 않고 콘솔에 고정 문구를 출력한 뒤 종료한다.
      예외의 원문은 비밀 정보를 담을 수 있어 출력하지 않는다.
    - `--fake`로 실행하면 초기화를 건너뛰고 `fake_service.install()`로 `service` 함수를 메모리 구현으로 바꿔
      화면만 띄운다(DB·RAG 연결 전 화면 확인용).
    - 화면 상태 3개(`role`, `case_id`, `screen`)를 `gr.State`로 만들어 그 역할의 화면 함수에 같게 넘긴다.
      `role`은 앱마다 고정하고, `screen`은 그 역할의 첫 화면에서 시작한다.
    - 모든 화면 위에 역할별 시그니처 색(고객 남색·엔지니어 진회색)의 상단 내비바(로고·메뉴)를,
      맨 아래에 "모든 데이터는 가상" 배너를 보인다.
    - 업무 규칙은 두지 않는다. 규칙은 `models`·`service`에 있다.

의존: `ui/*`, `service`, `config`, `models`, `db.rdb`, `db.vectordb`, `rag.generator`(의존 규칙 §2.5).
소유: UI·총괄 파트
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence

import gradio as gr

from fake import fake_service
from mpilot import service
from mpilot.config import load_config
from mpilot.db import rdb, vectordb
from mpilot.models import DependencyError, Role, ValidationError
from mpilot.rag import generator
from mpilot.ui import (
    common,
    customer_dashboard,
    customer_report_detail,
    customer_report_form,
    engineer_chat,
    engineer_dashboard,
    engineer_resolution,
)

STARTUP_FAILED_MESSAGE = "앱을 시작하지 못했습니다. 설정(.env)과 DB·검색 자료 준비 상태를 확인해 주세요."
"""초기화 실패 때 콘솔에 출력하는 고정 문구."""

ScreenBuilder = Callable[[gr.State, gr.State, gr.State], None]

SCREEN_BUILDERS: dict[Role, tuple[ScreenBuilder, ...]] = {
    Role.CUSTOMER: (
        customer_dashboard.build_customer_dashboard_screen,
        customer_report_form.build_customer_report_form_screen,
        customer_report_detail.build_customer_report_detail_screen,
    ),
    Role.ENGINEER: (
        engineer_dashboard.build_engineer_dashboard_screen,
        engineer_chat.build_engineer_chat_screen,
        engineer_resolution.build_engineer_resolution_screen,
    ),
}
"""역할별 앱에 조립하는 화면 함수. 순서는 `common.ROLE_SCREENS`와 같다."""

NAV_ITEMS: dict[Role, tuple[tuple[str, str], ...]] = {
    Role.CUSTOMER: (
        ("신고 목록", common.SCREEN_CUSTOMER_LIST),
        ("신고 작성", common.SCREEN_CUSTOMER_FORM),
    ),
    Role.ENGINEER: (
        ("대시보드", common.SCREEN_ENGINEER_LIST),
    ),
}
"""역할별 상단 내비바 메뉴. 신고 상세·장애 분석 대화·이력 저장은 특정 건을 골라야만 열려 메뉴에 두지 않는다."""


def initialize() -> None:
    """설정을 읽고 DB·검색 저장소·모델을 연결한 뒤 검색 색인을 준비한다.

    Returns:
        None: 각 모듈이 연결을 보관한다.

    Raises:
        ValidationError: 설정이 틀렸거나 검색 자료 검증에 실패했을 때.
        DependencyError: DB·임베딩·모델·검색 색인 준비에 실패했을 때.
    """
    # langchain_openai는 실제 실행 때만 필요하므로 여기서 불러온다(--fake와 테스트는 설치 없이 동작).
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings

    config = load_config()
    service.set_config(config)
    rdb.connect(config.db_url.get_secret_value())
    vectordb.connect(
        OpenAIEmbeddings(model=config.embedding_model_name, api_key=config.openai_api_key.get_secret_value())
    )
    generator.set_chat_model(
        ChatOpenAI(
            model=config.chat_model_name,
            api_key=config.openai_api_key.get_secret_value(),
            timeout=config.llm_timeout_seconds,
        )
    )
    service.prepare_search_index()


def build_app(role: Role) -> gr.Blocks:
    """역할의 화면들을 한 Blocks에 조립한다.

    Args:
        role: 이 앱의 역할. 그 역할의 화면 3개만 조립한다.

    Returns:
        gr.Blocks: 내비바·배너와 화면 3개, 공유 상태 3개가 들어 있는 앱. 첫 화면만 보인다.
    """
    title = "M-Pilot 고객" if role == Role.CUSTOMER else "M-Pilot 엔지니어"
    with gr.Blocks(title=title, fill_width=True) as demo:
        role_state = gr.State(role)
        case_id = gr.State(None)
        screen = gr.State(common.first_screen(role))
        common.build_navbar(title, NAV_ITEMS[role], screen, common.first_screen(role))
        for build_screen in SCREEN_BUILDERS[role]:
            build_screen(role_state, case_id, screen)
        gr.Markdown(common.VIRTUAL_DATA_BANNER, elem_classes=[common.CENTERED_TEXT_CLASS])
    return demo


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """실행 인자를 읽는다. `--fake`이면 초기화를 건너뛴다."""
    parser = argparse.ArgumentParser(description="M-Pilot 앱 실행")
    parser.add_argument("--fake", action="store_true", help="DB·RAG 없이 가짜 데이터로 화면만 띄운다")
    return parser.parse_args(argv)


def main(role: Role, port: int, argv: Sequence[str] | None = None) -> None:
    """역할의 앱을 조립해 실행한다.

    Args:
        role: 띄울 앱의 역할(`customer` 또는 `engineer`).
        port: Gradio 서버 포트. 고객 7860, 엔지니어 7861.
        argv: 실행 인자. `None`이면 명령줄 인자를 읽는다.

    Returns:
        None: Gradio 서버를 띄우고, 종료될 때까지 돌려주지 않는다.

    Raises:
        SystemExit: 초기화가 실패했을 때(종료 코드 1). 화면은 띄우지 않는다.
    """
    args = _parse_args(argv)
    if args.fake:
        fake_service.install()
    else:
        try:
            initialize()
        except (ValidationError, DependencyError) as e:
            print(f"{STARTUP_FAILED_MESSAGE} ({type(e).__name__})", file=sys.stderr)
            raise SystemExit(1) from None
    build_app(role).launch(server_port=port, css=common.app_css(role))
