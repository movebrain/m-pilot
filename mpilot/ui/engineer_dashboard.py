"""엔지니어 대시보드 화면.

역할:
    엔지니어가 시드 이력과 신규 신고를 상태별로 보고 대응할 건을 고른다.

책임:
    - 목록은 `service.list_engineer_cases`만 쓴다. 시드 이력을 포함한 전체 신고를 상태별로 보여 준다.
    - 기술지원(`support`) 건을 누르면 `case_id`를 채우고 장애 분석 대화로 이동한다.
    - 셀프 가이드 건은 '고객 안내'로만 표시하고 대화창으로 보내지 않는다(누르면 안내 문구만 보인다).
    - 조회에 실패하면 표를 비우고 고정 문구(`common.error_message`)만 보인다.
    - `common.PAGE_SIZE`건씩 페이지로 나눠 보여 주고(내부 스크롤 없음), 이전·다음 버튼으로 넘긴다.
    - 기간·원본 필터, 상태별 건수, '대응 시작' 버튼, 새 신고 표시(N/NEW), 긴급 강조는 범위 밖이다.

화면 전환: 대시보드 → 장애 분석 대화(기술지원 건 선택). 엔지니어 앱의 첫 화면이다(CHG-01).
사용 서비스: `list_engineer_cases`.
소유: UI·총괄 파트
"""

from __future__ import annotations

import gradio as gr

from mpilot import service
from mpilot.models import CaseDetail, Route
from mpilot.ui import common

SCREEN = common.SCREEN_ENGINEER_LIST

TABLE_HEADERS = ["접수번호", "설비", "코드", "에러명", "심각도", "경로", "상태", "접수 시각"]
"""신고 표의 열 이름. `case_rows`가 만드는 행의 순서와 같다."""

_ROUTE_COLUMN = TABLE_HEADERS.index("경로")

SELF_GUIDE_NOTICE = "고객 안내 건은 자동 안내로 끝난 건이라 대화창이 없습니다."
"""셀프 가이드 건을 눌렀을 때 보이는 문구."""


def case_rows(cases: list[CaseDetail]) -> list[list[str]]:
    """신고 목록을 표의 행으로 바꾼다.

    Args:
        cases: `service.list_engineer_cases`가 돌려준 신고 목록(최신순, 시드 이력 포함).

    Returns:
        list[list[str]]: `TABLE_HEADERS` 순서의 행 목록. 경로·상태는 화면 라벨, 시각은 한국 시간이다.
    """
    return [
        [
            d.service_case.case_id,
            str(d.service_case.equipment),
            d.service_case.code,
            d.error_code.name,
            str(d.error_code.severity),
            common.route_label(d.service_case.route),
            common.status_label(d.service_case.status),
            common.format_datetime(d.service_case.created_at),
        ]
        for d in cases
    ]


def load_rows(filter_value: str | None = None) -> list[list[str]]:
    """상태 필터 값으로 목록을 읽어 표의 행을 만든다.

    Args:
        filter_value: 상태 필터(`gr.Radio`)의 값. 빈 문자열이나 `None`은 '전체'다.

    Returns:
        list[list[str]]: 필터에 맞는 전체 행(페이지로 자르기 전). 조회에 실패하면 빈 목록이며
            `gr.Warning`으로 고정 문구를 보인다.
    """
    try:
        cases = service.list_engineer_cases(common.parse_status_filter(filter_value))
    except Exception as e:  # 화면에는 고정 문구만 보인다
        gr.Warning(common.error_message(e))
        return []
    return case_rows(cases)


def load_page(filter_value: str | None, page: int) -> tuple[list[list[str]], str, int]:
    """상태 필터 값으로 목록을 읽어 한 페이지만큼의 행을 만든다.

    Args:
        filter_value: 상태 필터(`gr.Radio`)의 값. 빈 문자열이나 `None`은 '전체'다.
        page: 보여줄 페이지 번호(0부터). 범위를 벗어나면 안으로 자른다.

    Returns:
        tuple[list[list[str]], str, int]: (그 페이지의 행, 페이지 표시 문구, 자른 페이지 번호).
    """
    rows = load_rows(filter_value)
    page = common.clamp_page(page, len(rows))
    return common.page_slice(rows, page), common.page_label(page, len(rows)), page


def is_support_row(row: list[str]) -> bool:
    """표의 한 행이 대화창으로 보낼 기술지원 건인지 판정한다.

    Args:
        row: `case_rows` 형식의 한 행.

    Returns:
        bool: 경로가 '기술지원'이면 `True`. 셀프 가이드('고객 안내') 건이면 `False`.
    """
    return len(row) > _ROUTE_COLUMN and row[_ROUTE_COLUMN] == common.route_label(Route.SUPPORT)


def build_engineer_dashboard_screen(role: gr.State, case_id: gr.State, screen: gr.State) -> None:
    """시드 이력을 포함한 전체 신고를 상태별로 보여 주고 대응할 건을 선택받는다.

    Args:
        role: 앱이 고정한 역할(`engineer`)을 담은 공유 상태. 엔지니어 앱에서만 이 화면을 조립한다.
        case_id: 선택된 신고의 접수번호를 담은 공유 상태. 기술지원 건을 선택하면 그 신고의 접수번호로 채운다.
        screen: 지금 보여 줄 화면을 가리키는 공유 상태. 화면 전환 그림의 상태 이름과 대응한다. 기술지원 건 선택은 장애 분석 대화로 바꾼다.

    Returns:
        None: Gradio Blocks 안에서 호출되어 컴포넌트와 이벤트를 등록할 뿐 값을 돌려주지 않는다.
    """
    page = gr.State(0)
    with gr.Column(visible=common.is_screen_visible(screen.value, SCREEN)) as column:
        with common.status_filter_row() as status_filter:
            pass
        # value·initial_label에 함수를 주면 페이지를 열 때마다 첫 페이지를 새로 읽는다.
        table = gr.Dataframe(
            headers=TABLE_HEADERS, value=lambda: load_page(None, 0)[0], interactive=False, wrap=True,
            type="array", label="기술지원 건을 누르면 장애 분석 대화로 이동합니다", show_row_numbers=True,
            max_height=common.TABLE_MAX_HEIGHT,
        )
        with common.pagination_row(lambda: load_page(None, 0)[1]) as (prev_btn, page_label, next_btn):
            pass

    def on_screen_change(current: str, filter_value: str | None):
        # 이 화면이 다시 보일 때마다 첫 페이지부터 새로 읽는다(새 신고·완료 반영).
        if not common.is_screen_visible(current, SCREEN):
            return gr.Column(visible=False), gr.skip(), gr.skip(), gr.skip()
        rows, label, new_page = load_page(filter_value, 0)
        return gr.Column(visible=True), rows, label, new_page

    def on_filter_change(filter_value: str | None):
        # 필터를 바꾸면 첫 페이지로 되돌아간다.
        rows, label, new_page = load_page(filter_value, 0)
        return rows, label, new_page

    def on_prev(filter_value: str | None, current_page: int):
        rows, label, new_page = load_page(filter_value, current_page - 1)
        return rows, label, new_page

    def on_next(filter_value: str | None, current_page: int):
        rows, label, new_page = load_page(filter_value, current_page + 1)
        return rows, label, new_page

    def on_select(rows: list[list[str]], evt: gr.SelectData):
        row_index = evt.index[0]
        if not (0 <= row_index < len(rows)) or not rows[row_index] or not rows[row_index][0]:
            return gr.skip(), gr.skip()
        row = rows[row_index]
        if not is_support_row(row):
            gr.Info(SELF_GUIDE_NOTICE)
            return gr.skip(), gr.skip()
        return row[0], common.SCREEN_ENGINEER_CHAT

    page_outputs = [table, page_label, page]
    screen.change(on_screen_change, inputs=[screen, status_filter], outputs=[column, *page_outputs])
    status_filter.change(on_filter_change, inputs=status_filter, outputs=page_outputs)
    prev_btn.click(on_prev, inputs=[status_filter, page], outputs=page_outputs)
    next_btn.click(on_next, inputs=[status_filter, page], outputs=page_outputs)
    table.select(on_select, inputs=table, outputs=[case_id, screen])
