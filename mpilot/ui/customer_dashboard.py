"""고객 신고 목록 화면.

역할:
    고객이 직접 접수한 신고를 최신순·상태별로 보여 주고, 상세·작성 화면으로 보낸다.

책임:
    - 목록은 `service.list_customer_cases`만 쓴다. 고객에게는 직접 접수한 신고만 보인다.
    - 상태(`received`, `in_progress`, `completed`)별로 거를 수 있다. `common.PAGE_SIZE`건씩 페이지로 나눠 보여
      주고(내부 스크롤 없음), 이전·다음 버튼으로 넘긴다. 기간·원본 필터, 상태별 건수는 범위 밖이다.
    - 표의 행을 누르면 `case_id`를 채우고 고객 신고 상세로, '신고하기'를 누르면 고객 신고 작성으로 이동한다.
    - 조회에 실패하면 표를 비우고 고정 문구(`common.error_message`)만 보인다.

화면 전환: 고객 신고 목록 → 상세(행 선택) / 작성(신고하기).
사용 서비스: `list_customer_cases`.
소유: UI·총괄 파트
"""

from __future__ import annotations

import gradio as gr

from mpilot import service
from mpilot.models import CustomerCaseResult
from mpilot.ui import common

SCREEN = common.SCREEN_CUSTOMER_LIST

TABLE_HEADERS = ["접수번호", "설비", "코드", "에러명", "상태", "접수 시각"]
"""신고 표의 열 이름. `case_rows`가 만드는 행의 순서와 같다."""


def case_rows(cases: list[CustomerCaseResult]) -> list[list[str]]:
    """신고 목록을 표의 행으로 바꾼다.

    Args:
        cases: `service.list_customer_cases`가 돌려준 공개 신고 목록(최신순).

    Returns:
        list[list[str]]: `TABLE_HEADERS` 순서의 행 목록. 상태는 화면 라벨, 시각은 한국 시간이다.
    """
    return [
        [
            c.case_id,
            str(c.equipment),
            c.code,
            c.error_name,
            common.status_label(c.status),
            common.format_datetime(c.created_at),
        ]
        for c in cases
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
        cases = service.list_customer_cases(common.parse_status_filter(filter_value))
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


def picked_case_id(rows: list[list[str]], row_index: int) -> str | None:
    """표에서 누른 행의 접수번호를 꺼낸다.

    Args:
        rows: 지금 표에 보이는 행(`case_rows` 형식).
        row_index: 누른 행의 번호(0부터).

    Returns:
        str | None: 그 행의 접수번호. 빈 표이거나 범위 밖이면 `None`.
    """
    if 0 <= row_index < len(rows) and rows[row_index] and rows[row_index][0]:
        return rows[row_index][0]
    return None


def build_customer_dashboard_screen(role: gr.State, case_id: gr.State, screen: gr.State) -> None:
    """고객 본인의 신고 목록을 최신순·상태별로 보여 준다.

    Args:
        role: 앱이 고정한 역할(`customer`)을 담은 공유 상태.
        case_id: 선택된 신고의 접수번호를 담은 공유 상태. 행을 누르면 그 접수번호로 채운다.
        screen: 지금 보여 줄 화면 이름을 담은 공유 상태. 행 선택은 고객 신고 상세, 신고하기는 고객 신고 작성으로 바꾼다.

    Returns:
        None: Gradio Blocks 안에서 호출되어 컴포넌트와 이벤트를 등록할 뿐 값을 돌려주지 않는다.
    """
    page = gr.State(0)
    with gr.Column(visible=common.is_screen_visible(screen.value, SCREEN)) as column:
        with common.status_filter_row() as status_filter:
            new_btn = gr.Button("신고하기", variant="primary", size="lg", scale=0, min_width=120)
        # value·initial_label에 함수를 주면 페이지를 열 때마다 첫 페이지를 새로 읽는다.
        table = gr.Dataframe(
            headers=TABLE_HEADERS, value=lambda: load_page(None, 0)[0], interactive=False, wrap=True,
            type="array", label="신고를 누르면 상세를 봅니다", show_row_numbers=True,
            max_height=common.TABLE_MAX_HEIGHT,
        )
        with common.pagination_row(lambda: load_page(None, 0)[1]) as (prev_btn, page_label, next_btn):
            pass

    def on_screen_change(current: str, filter_value: str | None):
        # 이 화면이 다시 보일 때마다 첫 페이지부터 새로 읽는다(새 신고·상태 변화 반영).
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
        selected = picked_case_id(rows, evt.index[0])
        if selected is None:
            return gr.skip(), gr.skip()
        return selected, common.SCREEN_CUSTOMER_DETAIL

    page_outputs = [table, page_label, page]
    screen.change(on_screen_change, inputs=[screen, status_filter], outputs=[column, *page_outputs])
    status_filter.change(on_filter_change, inputs=status_filter, outputs=page_outputs)
    prev_btn.click(on_prev, inputs=[status_filter, page], outputs=page_outputs)
    next_btn.click(on_next, inputs=[status_filter, page], outputs=page_outputs)
    table.select(on_select, inputs=table, outputs=[case_id, screen])
    new_btn.click(lambda: common.SCREEN_CUSTOMER_FORM, outputs=screen)
