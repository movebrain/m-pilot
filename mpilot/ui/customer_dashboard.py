"""고객 신고 목록 화면.

역할:
    고객이 직접 접수한 신고를 최신순·상태별로 보여 주고, 상세·작성 화면으로 보낸다.

책임:
    - 목록은 `service.list_customer_cases`만 쓴다. 고객에게는 직접 접수한 신고만 보인다.
    - 상태(`received`, `in_progress`, `completed`)별로 거를 수 있다. 기간·원본 필터, 상태별 건수, 페이지 이동은 범위 밖이다.
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
        list[list[str]]: 표의 행. 조회에 실패하면 빈 목록이며 `gr.Warning`으로 고정 문구를 보인다.
    """
    try:
        cases = service.list_customer_cases(common.parse_status_filter(filter_value))
    except Exception as e:  # 화면에는 고정 문구만 보인다
        gr.Warning(common.error_message(e))
        return []
    return case_rows(cases)


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
    with gr.Column(visible=common.is_screen_visible(screen.value, SCREEN)) as column:
        gr.Markdown("## 고객 신고 목록")
        new_btn = gr.Button("신고하기", variant="primary")
        status_filter = gr.Radio(choices=common.status_choices(), value="", label="상태")
        # value에 함수를 주면 페이지를 열 때마다 목록을 새로 읽는다.
        table = gr.Dataframe(
            headers=TABLE_HEADERS, value=load_rows, interactive=False, wrap=True,
            type="array", label="신고를 누르면 상세를 봅니다",
        )

    def on_screen_change(current: str, filter_value: str | None):
        # 이 화면이 다시 보일 때마다 목록을 새로 읽는다(새 신고·상태 변화 반영).
        if not common.is_screen_visible(current, SCREEN):
            return gr.Column(visible=False), gr.skip()
        return gr.Column(visible=True), load_rows(filter_value)

    def on_select(rows: list[list[str]], evt: gr.SelectData):
        selected = picked_case_id(rows, evt.index[0])
        if selected is None:
            return gr.skip(), gr.skip()
        return selected, common.SCREEN_CUSTOMER_DETAIL

    screen.change(on_screen_change, inputs=[screen, status_filter], outputs=[column, table])
    status_filter.change(load_rows, inputs=status_filter, outputs=table)
    table.select(on_select, inputs=table, outputs=[case_id, screen])
    new_btn.click(lambda: common.SCREEN_CUSTOMER_FORM, outputs=screen)
