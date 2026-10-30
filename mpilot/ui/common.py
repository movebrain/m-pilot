"""화면 공통 표시 처리: 표기 변환·오류 문구·가상 데이터 배너.

역할:
    여러 화면이 같은 모양으로 쓰는 표시 규칙을 한 곳에 모은다. 함수 인터페이스는 spec에 정하지 않았으며
    화면을 구현하면서 필요한 것만 추가한다(추가하면 spec §2.3.3을 같은 PR에서 고친다).

책임:
    - 표기 변환: 열거형 값(`CaseStatus`, `Route`, `Verdict` 등)을 화면 라벨로 바꾼다.
      예) `self_guide`는 '고객 안내', `in_progress`는 '조치 중', `completed`는 '조치 완료'.
    - 오류 문구: 예외 종류(`ValidationError`, `NotFoundError`, `InvalidStateError`, `DependencyError`)를
      고정 문구로 바꾼다. 모델·DB의 원문 오류 메시지를 화면에 그대로 내보내지 않는다.
    - 가상 데이터 배너: 모든 화면에 '모든 데이터는 가상'을 보인다.
    - 용어는 '에러', '심각도', '고객 안내', '서비스 이력 추가'를 쓴다('알람·오류·등급·환류' 등은 쓰지 않는다).
    - 업무 규칙과 서비스 호출은 두지 않는다.
    - 공통 레이아웃 조각: 고객·엔지니어 대시보드가 함께 쓰는 상태 필터 메뉴(`status_filter_row`)처럼
      여러 화면이 똑같이 그리는 작은 컴포넌트 묶음을 만든다. 서비스 호출·업무 규칙은 두지 않는다.

의존: `models`, `gradio`를 import한다.
소유: UI·총괄 파트
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from html import escape

import gradio as gr

from mpilot.models import (
    Answer,
    CaseStatus,
    Citation,
    CitationKind,
    DependencyError,
    InvalidStateError,
    NotFoundError,
    Role,
    Route,
    ValidationError,
    Verdict,
)

# ---------------------------------------------------------------------------
# 가상 데이터 배너
# ---------------------------------------------------------------------------

VIRTUAL_DATA_BANNER = "**모든 데이터는 가상입니다.** 실제 설비·고객·서비스 이력이 아닙니다."
"""모든 화면 맨 아래 가운데에 `gr.Markdown(VIRTUAL_DATA_BANNER)`로 보이는 문구."""

STICKY_TOOLBAR_CLASS = "mpilot-sticky-toolbar"
"""스크롤해도 화면 위에 고정해 보일 영역(상태 필터 등)에 붙이는 `elem_classes` 값."""

NAVBAR_HEIGHT = "61px"
"""내비바 실측 높이(패딩 포함). 상태 필터가 내비바 아래에 겹치지 않고 붙도록 top 오프셋으로도 쓴다."""

STICKY_TOOLBAR_CSS = f"""
.gradio-container {{
    /* Gradio 기본값(overflow: hidden)이 세로축까지 막아서, sticky가 실제로 스크롤하는
       문서(body)가 아니라 이 컨테이너를 기준으로 계산돼 버려 먹히지 않는다.
       overflow-x·overflow-y 중 하나만 visible이면 스펙상 visible이 auto로 강제 변환되어
       그래도 스크롤 컨테이너 취급되므로, 가로축도 함께 visible로 바꿔야 한다. */
    overflow-x: visible !important;
    overflow-y: visible !important;
}}


.{STICKY_TOOLBAR_CLASS} {{
    position: sticky !important;
    top: {NAVBAR_HEIGHT} !important;
    z-index: 9999 !important;
    background: var(--background-fill-primary, white) !important;
    box-shadow: none !important;
    padding-top: var(--spacing-sm);
    padding-bottom: var(--spacing-sm);
}}

/* Gradio 기본 .html-container padding(--block-padding)이 gr.HTML로 만든 설비 카드·
   뒤로 가기 화살표의 레이아웃을 깨뜨려서 없앤다. */
.gradio-container .html-container {{
    padding: 0 !important;
}}

/* 신고 목록 표: 실제로 보이는 행은 table이 아니라 virtual-row/body-cell div다
   (table tbody tr는 열 너비 계산용 숨김 요소). 행을 누를 수 있다는 걸 보이려 커서를
   포인터로 바꾼다. body-cell 안 span(svelte-odwpey)이 자체로 cursor: text를 걸어 두므로
   그것도 같이 덮어써야 한다. Gradio 기본 셀 선택 때 나오는 주황색 깜빡임 애니메이션·배경과,
   선택된 셀 테두리로 쓰는 주황색 inset box-shadow를 모두 끈다. 이 box-shadow는 `.cell-selected`
   div 자체가 아니라 그 안의 span(svelte-odwpey)에 걸려 있어, span도 같이 꺼야 한다. 이 표는
   누르면 바로 다른 화면으로 넘어가므로 선택 표시 자체가 필요 없다. */
.gradio-container .virtual-row,
.gradio-container .body-cell,
.gradio-container .body-cell .cell-wrap,
.gradio-container .body-cell span.svelte-odwpey {{
    cursor: pointer !important;
}}

.gradio-container .body-cell.flash.cell-selected {{
    animation: none !important;
    background: transparent !important;
}}

.gradio-container .body-cell.cell-selected,
.gradio-container .body-cell.cell-selected span.svelte-odwpey {{
    box-shadow: none !important;
}}

/* 셀을 선택하면 가장자리에 뜨는 주황색 `.selection-button`(끌어서 선택 범위를 늘리는 손잡이, 흰
   화살표 아이콘)도 끈다. 이 표는 여러 칸을 끌어서 고르는 스프레드시트가 아니라 한 줄 누르면 바로
   다른 화면으로 넘어가는 목록이라 필요 없다. */
.gradio-container .selection-button {{
    display: none !important;
}}
"""
"""`gr.Blocks(css=...)`에 넣어 `STICKY_TOOLBAR_CLASS`를 고정 위치로 만드는 스타일."""

BACK_BUTTON_CLASS = "mpilot-back-btn"
"""뒤로 가기 화살표 아이콘에 붙이는 `elem_classes` 값."""

BACK_TITLE_CLASS = "mpilot-back-title"
"""뒤로 가기 화살표 옆 제목 Markdown에 붙이는 `elem_classes` 값. 위아래 여백을 없애 화살표와 줄을 맞춘다."""

BACK_ROW_CLASS = "mpilot-back-row"
"""뒤로 가기 Row에 붙이는 `elem_classes` 값. Gradio 기본 Row 간격(`--layout-gap`)이 화살표·제목 사이를
너무 넓게 띄워서 줄인다."""

BACK_ICON_SVG = (
    '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" '
    'stroke-width="3" stroke-linecap="round" stroke-linejoin="round">'
    '<polyline points="15 18 9 12 15 6"></polyline>'
    "</svg>"
)
"""둥근 끝의 `‹` 모양 뒤로 가기 아이콘(SVG)."""

BACK_BUTTON_CSS = f"""
.{BACK_BUTTON_CLASS} {{
    /* gr.HTML의 바깥 wrapper div는 scale=0을 줘도 flex-grow:1로 남아 제목과 폭을 반씩
       나눠 가지므로(Button과 달리 래퍼가 하나 더 있다), 직접 눌러서 고정 폭으로 만든다. */
    display: flex !important;
    align-items: center !important;
    cursor: pointer !important;
    flex-grow: 0 !important;
    flex-basis: auto !important;
    width: 40px !important;
    color: var(--body-text-color, #27272a);
}}

.{BACK_TITLE_CLASS} p {{
    margin: 0 !important;
}}

.{BACK_ROW_CLASS} {{
    gap: 8px !important;
    align-items: center !important;
}}

.{BACK_ROW_CLASS} > * {{
    align-self: center !important;
}}
"""
"""`gr.Blocks(css=...)`에 넣어 화살표 아이콘을 누를 수 있게 보이고, 옆 제목과 줄·간격을 맞추는 스타일."""


@contextmanager
def back_button_row(title: str):
    """화살표 아이콘 뒤로 가기 버튼과 화면 제목을 같은 줄에 둔다.

    Args:
        title: 제목 Markdown 문구(예: `"## 고객 신고 작성"`).

    Yields:
        gr.HTML: 뒤로 가기 아이콘. `with` 블록 안에서 같은 Row에 화면의 주 동작 버튼(제출 등)을
            오른쪽에 더 넣을 수 있다. 그 버튼엔 `scale=0`을 줘야 한다(제목이 남는 폭을 다 차지해
            오른쪽 끝에 붙는다). 화면은 아이콘에 `click` 이벤트를 붙여 이전 화면으로 돌아가게 한다.
    """
    with gr.Row(equal_height=True, elem_classes=[BACK_ROW_CLASS]):
        back_btn = gr.HTML(BACK_ICON_SVG, elem_classes=[BACK_BUTTON_CLASS], scale=0, min_width=40)
        gr.Markdown(title, elem_classes=[BACK_TITLE_CLASS])
        yield back_btn


@contextmanager
def status_filter_row():
    """상태 필터(전체/접수/조치 중/조치 완료) 메뉴를 스크롤해도 고정되는 Row로 만든다.

    고객·엔지니어 대시보드가 똑같이 쓰는 공통 메뉴다. `with` 블록 안에서 같은 Row에
    다른 컴포넌트(고객 대시보드의 '신고하기' 버튼 등)를 더 넣을 수 있다.

    Yields:
        gr.Radio: 상태 필터 컴포넌트. 화면이 `change` 이벤트를 붙이고 값을 읽는다.
    """
    with gr.Row(equal_height=False, elem_classes=[STICKY_TOOLBAR_CLASS]) as row:
        status_filter = gr.Radio(choices=status_choices(), value="", container=False, scale=4)
        yield status_filter


# ---------------------------------------------------------------------------
# 목록 페이지네이션
# ---------------------------------------------------------------------------

PAGE_SIZE = 20
"""목록 한 페이지에 보여줄 건수."""

TABLE_MAX_HEIGHT = 1200
"""페이지당 최대 건수(`PAGE_SIZE`)가 내부 스크롤 없이 다 보이도록 넉넉히 잡은 표 높이(px)."""


def page_count(total: int) -> int:
    """전체 건수로 전체 페이지 수를 센다.

    Args:
        total: 전체 건수.

    Returns:
        int: 전체 페이지 수(건수가 0이어도 최소 1).
    """
    return max(1, -(-total // PAGE_SIZE))


def clamp_page(page: int, total: int) -> int:
    """페이지 번호를 0 이상, 마지막 페이지 이하로 자른다.

    Args:
        page: 요청한 페이지 번호(0부터).
        total: 전체 건수.

    Returns:
        int: 범위 안으로 자른 페이지 번호.
    """
    return max(0, min(page, page_count(total) - 1))


def page_slice(rows: list, page: int) -> list:
    """목록에서 한 페이지(`PAGE_SIZE`건)만 잘라낸다.

    Args:
        rows: 필터가 적용된 전체 행 목록.
        page: 보여줄 페이지 번호(0부터). 범위를 벗어나면 안으로 자른다.

    Returns:
        list: 그 페이지의 행 목록.
    """
    start = clamp_page(page, len(rows)) * PAGE_SIZE
    return rows[start:start + PAGE_SIZE]


def page_label(page: int, total: int) -> str:
    """'3 / 10' 같은 페이지 표시 문구를 만든다.

    Args:
        page: 지금 페이지 번호(0부터). 범위를 벗어나면 안으로 자른다.
        total: 전체 건수.

    Returns:
        str: `"{지금 페이지}/{전체 페이지}"` 문구(1부터 센 번호로 보인다).
    """
    return f"{clamp_page(page, total) + 1} / {page_count(total)}"


PAGE_LABEL_CLASS = "mpilot-page-label"
"""페이지 표시 Markdown에 붙이는 `elem_classes` 값. 버튼과 높이·글자를 맞춘다."""

PAGINATION_ROW_CLASS = "mpilot-pagination-row"
"""이전/다음 버튼과 페이지 표시를 가운데로 모으고 간격을 좁히는 Row에 붙이는 `elem_classes` 값."""

PAGINATION_CSS = f"""
.{PAGE_LABEL_CLASS} {{
    flex-grow: 0 !important;
    flex-basis: auto !important;
    width: auto !important;
}}

.{PAGE_LABEL_CLASS} p {{
    margin: 0 !important;
    text-align: center !important;
    font-weight: 700 !important;
}}

.{PAGINATION_ROW_CLASS} {{
    justify-content: center !important;
    gap: 8px !important;
}}
"""
"""`gr.Blocks(css=...)`에 넣어 페이지 표시 문구를 굵게, 버튼과 가까이 가운데 모아 보이는 스타일."""

CENTER_ROW_CLASS = "mpilot-center-row"
"""Row 안 내용을 가운데로 모으는 `elem_classes` 값(확인 버튼처럼 혼자 있는 버튼을 가운데 두는 용도)."""

CENTER_ROW_CSS = f"""
.{CENTER_ROW_CLASS} {{
    justify-content: center !important;
}}
"""
"""`gr.Blocks(css=...)`에 넣어 `CENTER_ROW_CLASS`의 내용을 가운데로 모으는 스타일."""

PROSE_PADDING_CSS = """
.prose {
    padding: 0 !important;
}
"""
"""`gr.Blocks(css=...)`에 넣어 Gradio Markdown 기본 타이포그래피(`.prose`)의 안쪽 여백을 없애는 스타일."""

CENTERED_TEXT_CLASS = "mpilot-centered-text"
"""셀프 가이드·조치 결과처럼 가운데 정렬하고 글자 크기로 강약을 주는 Markdown에 붙이는 `elem_classes` 값."""

CENTERED_TEXT_CSS = f"""
.{CENTERED_TEXT_CLASS}, .{CENTERED_TEXT_CLASS} * {{
    text-align: center !important;
}}

.{CENTERED_TEXT_CLASS} ol, .{CENTERED_TEXT_CLASS} ul {{
    padding-left: 0 !important;
    list-style-position: inside !important;
}}

.{CENTERED_TEXT_CLASS} ul {{
    /* 출처 같은 하위 목록은 작고 옅게 둬 본문 글자와 강약을 준다. */
    font-size: 0.85em !important;
    opacity: 0.7 !important;
}}
"""
"""`gr.Blocks(css=...)`에 넣어 `CENTERED_TEXT_CLASS`를 가운데 정렬·글자 강약으로 보이는 스타일."""

WIDE_GAP_ROW_CLASS = "mpilot-wide-gap-row"
"""장애 분석 대화처럼 왼쪽 정보·오른쪽 대화를 나란히 두는 Row에 붙이는 `elem_classes` 값.
기본 Row 간격(`--layout-gap`)보다 넓게 띄워 두 영역을 또렷이 나눈다."""

WIDE_GAP_ROW_CSS = f"""
.{WIDE_GAP_ROW_CLASS} {{
    gap: 20px !important;
}}
"""
"""`gr.Blocks(css=...)`에 넣어 `WIDE_GAP_ROW_CLASS`의 좌우 간격을 넓히는 스타일."""

CHAT_HISTORY_BOX_CLASS = "mpilot-chat-history-box"
"""장애 분석 대화의 질문·답변 기록에 붙이는 `elem_classes` 값. 연회색 1px 테두리 안에 높이를 고정하고
넘치면 스크롤되게 해, 대화가 없을 때(접수)·진행 중일 때(조치 중)·많이 쌓였을 때(조치 완료)
모두 같은 모양으로 보이게 한다."""

CHAT_HISTORY_BOX_CSS = f"""
.{CHAT_HISTORY_BOX_CLASS} {{
    border: 1px solid #e4e4e7 !important;
    border-radius: 8px !important;
    padding: 16px !important;
    height: 420px !important;
    overflow-y: auto !important;
}}

.{CHAT_HISTORY_BOX_CLASS} :not(button):not(button *) {{
    /* Gradio 기본 블록·prose 테두리가 겹쳐 보이지 않도록, 바깥 테두리 하나만 남긴다.
       [원문보기] 버튼 자체는 제 모양을 유지해야 하므로 버튼과 그 안쪽은 건드리지 않는다. */
    border: none !important;
    box-shadow: none !important;
    background: none !important;
}}
"""
"""`gr.Blocks(css=...)`에 넣어 `CHAT_HISTORY_BOX_CLASS`를 스크롤되는 고정 높이 박스로 보이는 스타일.
안쪽 Gradio 기본 테두리는 모두 지워 바깥 테두리 하나만 보이게 한다(버튼은 예외)."""

QUESTION_INPUT_ROW_CLASS = "mpilot-question-input-row"
"""질문 입력창과 전송 버튼을 한 알약 모양 테두리 안에 나란히 두는 Row에 붙이는 `elem_classes` 값.
높이를 고정해 `CHAT_HISTORY_BOX_CLASS`와 마찬가지로 신고 상태와 상관없이 같은 모양으로 보이게 한다."""

QUESTION_INPUT_ROW_CSS = f"""
.{QUESTION_INPUT_ROW_CLASS} {{
    border: 1px solid #e4e4e7 !important;
    border-radius: 24px !important;
    padding: 4px 6px 4px 20px !important;
    align-items: center !important;
    height: 56px !important;
    min-height: 56px !important;
    max-height: 56px !important;
}}

.{QUESTION_INPUT_ROW_CLASS} .block,
.{QUESTION_INPUT_ROW_CLASS} textarea {{
    border: none !important;
    box-shadow: none !important;
    background: none !important;
    padding: 0 !important;
}}

.{QUESTION_INPUT_ROW_CLASS} textarea {{
    resize: none !important;
}}
"""
"""`gr.Blocks(css=...)`에 넣어 `QUESTION_INPUT_ROW_CLASS`를 알약 모양 단일 테두리로 보이는 스타일."""

LIGHT_BORDER_BOX_CLASS = "mpilot-light-border-box"
"""셀프 가이드·조치 결과처럼 배경색 없이 연회색 1px 테두리 안에 줄글만 두는 `elem_classes` 값."""

LIGHT_BORDER_BOX_CSS = f"""
.{LIGHT_BORDER_BOX_CLASS} {{
    border: 1px solid #e4e4e7 !important;
    border-radius: 8px !important;
    padding: 16px 24px !important;
    max-width: 800px !important;
    margin: 0 auto !important;
}}
"""
"""`gr.Blocks(css=...)`에 넣어 `LIGHT_BORDER_BOX_CLASS`를 배경 없는 연회색 테두리 박스로 보이는 스타일.
접수내역 표와 같은 최대 너비(800px)를 둬서 폭을 맞춘다."""

VIEW_SOURCE_BUTTON_CLASS = "mpilot-view-source-button"
"""단계별 출처 옆 [원문보기] 버튼에 붙이는 `elem_classes` 값. 본문 글자 크기에 맞춘 작은 버튼으로 보인다."""

VIEW_SOURCE_BUTTON_CSS = f"""
.{VIEW_SOURCE_BUTTON_CLASS} {{
    flex-grow: 0 !important;
    min-width: fit-content !important;
    width: fit-content !important;
    height: auto !important;
    padding: 4px 8px !important;
    font-size: 12px !important;
    white-space: nowrap !important;
}}
"""
"""`gr.Blocks(css=...)`에 넣어 `VIEW_SOURCE_BUTTON_CLASS`를 8px 안팎의 작은 버튼으로 보이는 스타일."""

CHAT_NOTICE_CLASS = "mpilot-chat-notice"
"""대화 기록이 비어 있을 때 보이는 안내 문구 중, 완료된 건(AI 대화 없이 완료)처럼 눈에 띄어야 하는
문구에 붙이는 `elem_classes` 값. 가운데 정렬하고 본문보다 조금 큰 글자로 보인다."""

CHAT_NOTICE_CSS = f"""
.{CHAT_NOTICE_CLASS}, .{CHAT_NOTICE_CLASS} p {{
    text-align: center !important;
    font-size: 16px !important;
}}
"""
"""`gr.Blocks(css=...)`에 넣어 `CHAT_NOTICE_CLASS`를 가운데 정렬·조금 큰 글자로 보이는 스타일."""

MODAL_OVERLAY_CLASS = "mpilot-modal-overlay"
"""출처 원문처럼 화면 전체를 덮는 모달의 바깥(반투명 배경) 레이어에 붙이는 `elem_classes` 값.
Gradio에 모달 컴포넌트가 없어 CSS로 흉내 낸다."""

MODAL_CARD_CLASS = "mpilot-modal-card"
"""모달 가운데 흰 카드 영역에 붙이는 `elem_classes` 값."""

MODAL_CSS = f"""
.{MODAL_OVERLAY_CLASS} {{
    position: fixed !important;
    inset: 0 !important;
    background: rgba(0, 0, 0, 0.5) !important;
    z-index: 20000 !important;
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
}}

.{MODAL_CARD_CLASS} {{
    background: var(--background-fill-primary, white) !important;
    max-width: 600px !important;
    width: 90% !important;
    max-height: 80vh !important;
    overflow: auto !important;
    padding: 24px !important;
    border-radius: 8px !important;
}}
"""
"""`gr.Blocks(css=...)`에 넣어 `MODAL_OVERLAY_CLASS`·`MODAL_CARD_CLASS`를 화면 가운데 모달 카드로 보이는 스타일."""


@contextmanager
def pagination_row(initial_label: str | Callable[[], str] = "1 / 1"):
    """이전/다음 버튼과 페이지 표시("3 / 10")를 한 줄에 둔다.

    고객·엔지니어 대시보드가 똑같이 쓰는 공통 페이지네이션 UI다. 버튼을 누른 뒤 실제 페이지 번호·행은
    화면이 `click` 이벤트에서 `page_slice`·`clamp_page`·`page_label`로 다시 계산해 채운다.

    Args:
        initial_label: 페이지 표시의 처음 값. 함수를 주면 화면을 열 때마다 새로 계산해 보인다
            (`table`의 초깃값과 맞춰야 할 때 쓴다).

    Yields:
        tuple[gr.Button, gr.Markdown, gr.Button]: (이전 버튼, 페이지 표시, 다음 버튼).
    """
    with gr.Row(equal_height=True, elem_classes=[PAGINATION_ROW_CLASS]):
        prev_btn = gr.Button("‹ 이전", size="sm", scale=0, min_width=90)
        label = gr.Markdown(initial_label, elem_classes=[PAGE_LABEL_CLASS], container=False, scale=0)
        next_btn = gr.Button("다음 ›", size="sm", scale=0, min_width=90)
        yield prev_btn, label, next_btn


# ---------------------------------------------------------------------------
# 역할별 시그니처 색
# ---------------------------------------------------------------------------

CUSTOMER_ACCENT = "#1d3461"
"""고객 앱 시그니처 색(남색)."""

ENGINEER_ACCENT = "#374151"
"""엔지니어 앱 시그니처 색(진회색)."""


def accent_css(role: Role | str) -> str:
    """역할별 시그니처 색을 기본 버튼·선택 표시·목록 행 호버에 적용하는 스타일.

    목록 행(`.virtual-row`)의 Gradio 기본 호버색은 주황색이라 역할 시그니처 색과 어긋나므로,
    고객은 옅은 남색, 엔지니어는 옅은 회색으로 바꾼다.

    Args:
        role: `Role` 값 또는 같은 문자열. 고객은 남색, 엔지니어는 진회색이다.

    Returns:
        str: `gr.Blocks(css=...)`에 더할 스타일.
    """
    is_customer = Role(role) == Role.CUSTOMER
    color = CUSTOMER_ACCENT if is_customer else ENGINEER_ACCENT
    row_hover = "#e8eef7" if is_customer else "#f3f4f6"
    return f"""
.gradio-container {{
    --button-primary-background-fill: {color} !important;
    --button-primary-background-fill-hover: {color}cc !important;
    --button-primary-border-color: {color} !important;
    --button-primary-border-color-hover: {color} !important;
    --checkbox-background-color-selected: {color} !important;
    --checkbox-border-color-selected: {color} !important;
}}

.gradio-container .virtual-row:hover {{
    background: {row_hover} !important;
}}
"""

# ---------------------------------------------------------------------------
# 상단 내비바(로고·메뉴)
# ---------------------------------------------------------------------------

NAVBAR_CLASS = "mpilot-navbar"
NAVBAR_LOGO_CLASS = "mpilot-navbar-logo"
NAVBAR_MENU_CLASS = "mpilot-navbar-menu"
NAVBAR_LINK_CLASS = "mpilot-navbar-link"


def navbar_css(role: Role | str) -> str:
    """상단 내비바를 역할별 시그니처 색의 고정 바로 만드는 스타일.

    Args:
        role: `Role` 값 또는 같은 문자열. 고객은 남색, 엔지니어는 진회색 바탕이다.

    Returns:
        str: `gr.Blocks(css=...)`에 더할 스타일.
    """
    color = CUSTOMER_ACCENT if Role(role) == Role.CUSTOMER else ENGINEER_ACCENT
    return f"""
.{NAVBAR_CLASS} {{
    position: sticky !important;
    top: 0 !important;
    z-index: 10000 !important;
    background: {color} !important;
    justify-content: space-between !important;
    align-items: center !important;
    padding: 14px 24px !important;
    margin-bottom: var(--spacing-lg) !important;
    /* 내비바만 뷰 가장자리까지 꽉 차게, 부모(.app)의 기본 여백(16px 32px)만큼 밖으로 뺀다.
       나머지 화면 내용은 그 여백을 그대로 유지한다. */
    margin-left: -32px !important;
    margin-right: -32px !important;
    margin-top: -16px !important;
    width: calc(100% + 64px) !important;
}}

.{NAVBAR_CLASS} * {{
    color: white !important;
}}

.{NAVBAR_LOGO_CLASS} {{
    flex-grow: 0 !important;
    flex-basis: auto !important;
    width: auto !important;
    min-width: 0 !important;
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    font-size: 1.25rem !important;
    font-weight: 700 !important;
    padding: 0 !important;
    cursor: pointer !important;
}}

.{NAVBAR_LOGO_CLASS}:hover {{
    opacity: 0.85 !important;
}}

.{NAVBAR_MENU_CLASS} {{
    flex-grow: 0 !important;
    flex-wrap: nowrap !important;
    width: auto !important;
    justify-content: flex-end !important;
    gap: 4px !important;
}}

.{NAVBAR_LINK_CLASS} {{
    flex: 0 0 auto !important;
    width: auto !important;
    min-width: 0 !important;
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    font-weight: 500 !important;
    white-space: nowrap !important;
}}

.{NAVBAR_LINK_CLASS}:hover {{
    background: rgba(255, 255, 255, 0.15) !important;
}}
"""


def build_navbar(title: str, nav_items: list[tuple[str, str]], screen: gr.State, home_screen: str) -> None:
    """화면 맨 위 내비바(로고·메뉴)를 만든다. 역할의 앱이 한 번만 둔다.

    Args:
        title: 왼쪽에 보일 로고·타이틀 문구. 누르면 `home_screen`으로 돌아가는 버튼이다.
        nav_items: `(메뉴 라벨, 누르면 바꿀 screen 값)` 목록. 오른쪽에 순서대로 둔다.
        screen: 공유 `screen` 상태. 로고·메뉴를 누르면 그 화면으로 바꾼다.
        home_screen: 로고를 누르면 돌아갈 화면. 그 역할의 첫 화면(`first_screen(role)`)을 넘긴다.

    Returns:
        None: Gradio Blocks 안에서 호출되어 컴포넌트와 이벤트를 등록할 뿐 값을 돌려주지 않는다.
    """
    with gr.Row(elem_classes=[NAVBAR_CLASS]):
        logo_btn = gr.Button(title, elem_classes=[NAVBAR_LOGO_CLASS], scale=0)
        logo_btn.click(lambda: home_screen, outputs=screen)
        with gr.Row(elem_classes=[NAVBAR_MENU_CLASS]):
            for label, target in nav_items:
                nav_btn = gr.Button(label, elem_classes=[NAVBAR_LINK_CLASS], size="sm")
                nav_btn.click(lambda t=target: t, outputs=screen)


FONT_CSS = """
@import url('https://cdn.jsdelivr.net/npm/@kfonts/nanum-square@0.2.0/index.css');

.gradio-container, .gradio-container * {
    font-family: '나눔스퀘어', sans-serif !important;
}
"""
"""전체 화면 글꼴을 나눔스퀘어로 바꾸는 스타일(jsDelivr CDN, OFL-1.1 라이선스)."""


def app_css(role: Role | str) -> str:
    """역할의 앱 전체에 적용할 스타일을 한데 모은다.

    스티키 메뉴, 뒤로 가기 화살표, 페이지네이션, 가운데 정렬 Row·텍스트, 넓은 간격 Row, Markdown
    기본 여백 제거, 스크롤 대화 기록 박스, 알약 모양 질문 입력 Row, 연회색 테두리 안내 박스, 대화
    안내 문구, 모달, 글꼴, 역할별 시그니처 색, 상단 내비바 스타일을 합친다.

    Args:
        role: `Role` 값 또는 같은 문자열.

    Returns:
        str: `gr.Blocks.launch(css=...)`에 그대로 넘기는 전체 스타일.
    """
    return (
        STICKY_TOOLBAR_CSS + BACK_BUTTON_CSS + PAGINATION_CSS + CENTER_ROW_CSS + CENTERED_TEXT_CSS
        + WIDE_GAP_ROW_CSS + PROSE_PADDING_CSS + CHAT_HISTORY_BOX_CSS + QUESTION_INPUT_ROW_CSS
        + LIGHT_BORDER_BOX_CSS + VIEW_SOURCE_BUTTON_CSS + CHAT_NOTICE_CSS + MODAL_CSS + FONT_CSS
        + accent_css(role)
        + navbar_css(role)
    )

# ---------------------------------------------------------------------------
# 화면 이름 (`screen` 공유 상태에 넣는 값). spec §2.3.3 화면 전환 그림의 상태 이름과 같다.
# ---------------------------------------------------------------------------

SCREEN_CUSTOMER_LIST = "cust_list"
SCREEN_CUSTOMER_FORM = "cust_form"
SCREEN_CUSTOMER_DETAIL = "cust_detail"
SCREEN_ENGINEER_LIST = "eng_list"
SCREEN_ENGINEER_CHAT = "eng_chat"
SCREEN_ENGINEER_RESOLVE = "eng_resolve"

ROLE_SCREENS: dict[Role, tuple[str, ...]] = {
    Role.CUSTOMER: (SCREEN_CUSTOMER_LIST, SCREEN_CUSTOMER_FORM, SCREEN_CUSTOMER_DETAIL),
    Role.ENGINEER: (SCREEN_ENGINEER_LIST, SCREEN_ENGINEER_CHAT, SCREEN_ENGINEER_RESOLVE),
}
"""앱(역할)별로 조립하는 화면 목록. 맨 앞이 그 앱의 첫 화면이다(CHG-01)."""


def first_screen(role: Role | str) -> str:
    """역할별 앱이 처음 보여 줄 화면 이름을 돌려준다.

    Args:
        role: `Role` 값 또는 같은 문자열(`"customer"`, `"engineer"`).

    Returns:
        str: 고객은 고객 신고 목록, 엔지니어는 엔지니어 대시보드의 화면 이름.

    Raises:
        ValueError: 허용되지 않은 역할 값일 때.
    """
    return ROLE_SCREENS[Role(role)][0]


def is_screen_visible(current: str | None, own: str) -> bool:
    """`screen` 공유 상태 값으로 한 화면을 보일지 정한다. 화면 전환 구조의 판정 규칙이다.

    Args:
        current: `screen` 공유 상태의 지금 값.
        own: 판정할 화면의 이름(`SCREEN_*`).

    Returns:
        bool: 지금 값이 그 화면이면 `True`. 한 번에 한 화면만 `True`가 된다.
    """
    return current == own

# ---------------------------------------------------------------------------
# 표기 변환
# ---------------------------------------------------------------------------

STATUS_LABELS: dict[CaseStatus, str] = {
    CaseStatus.RECEIVED: "접수",
    CaseStatus.IN_PROGRESS: "조치 중",
    CaseStatus.COMPLETED: "조치 완료",
}

ROUTE_LABELS: dict[Route, str] = {
    Route.SELF_GUIDE: "고객 안내",
    Route.SUPPORT: "기술지원",
}

VERDICT_LABELS: dict[Verdict, str] = {
    Verdict.ANSWERED: "답변",
    Verdict.INSUFFICIENT_EVIDENCE: "근거 부족",
}

CITATION_KIND_LABELS: dict[CitationKind, str] = {
    CitationKind.MANUAL: "매뉴얼",
    CitationKind.HISTORY: "서비스 이력",
}

# 보류 판정인데 notice가 비어 있을 때 대신 보여 줄 문구.
_DEFAULT_NOTICES: dict[Verdict, str] = {
    Verdict.INSUFFICIENT_EVIDENCE: "매뉴얼과 서비스 이력에서 답할 근거를 찾지 못했습니다. 조치는 추측하지 않습니다.",
}

_KST = timezone(timedelta(hours=9))


def status_label(status: CaseStatus | str) -> str:
    """진행 상태 값을 화면 라벨로 바꾼다.

    Args:
        status: `CaseStatus` 값 또는 같은 문자열(예: `"in_progress"`).

    Returns:
        str: 화면 라벨(예: '조치 중').

    Raises:
        ValueError: 허용되지 않은 상태 값일 때.
    """
    return STATUS_LABELS[CaseStatus(status)]


def route_label(route: Route | str) -> str:
    """처리 경로 값을 화면 라벨로 바꾼다.

    Args:
        route: `Route` 값 또는 같은 문자열(예: `"self_guide"`).

    Returns:
        str: 화면 라벨('고객 안내' 또는 '기술지원').

    Raises:
        ValueError: 허용되지 않은 경로 값일 때.
    """
    return ROUTE_LABELS[Route(route)]


def verdict_label(verdict: Verdict | str) -> str:
    """답변 판정 값을 화면 라벨로 바꾼다.

    Args:
        verdict: `Verdict` 값 또는 같은 문자열.

    Returns:
        str: 화면 라벨('답변', '근거 부족').

    Raises:
        ValueError: 허용되지 않은 판정 값일 때.
    """
    return VERDICT_LABELS[Verdict(verdict)]


def status_choices() -> list[tuple[str, str]]:
    """상태 필터의 선택지를 만든다. 맨 앞은 '전체'다.

    Returns:
        list[tuple[str, str]]: `(라벨, 값)` 목록. '전체'의 값은 빈 문자열이다.
            `gr.Radio`·`gr.Dropdown`의 `choices`에 그대로 넣는다.
    """
    return [("전체", "")] + [(label, status.value) for status, label in STATUS_LABELS.items()]


def parse_status_filter(value: str | None) -> CaseStatus | None:
    """상태 필터 값을 서비스 인자로 바꾼다.

    Args:
        value: 필터에서 고른 값. 빈 문자열이나 `None`은 '전체'다.

    Returns:
        CaseStatus | None: 고른 상태. '전체'면 `None`.

    Raises:
        ValueError: 허용되지 않은 상태 값일 때.
    """
    return CaseStatus(value) if value else None


def format_datetime(value: datetime | None) -> str:
    """시각을 한국 시간 `YYYY-MM-DD HH:MM` 문자열로 바꾼다.

    Args:
        value: 표시할 시각. 시간대가 없으면 UTC로 본다. `None`이면 빈 칸.

    Returns:
        str: 화면 표기 문자열. `None`이면 `"-"`.
    """
    if value is None:
        return "-"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_KST).strftime("%Y-%m-%d %H:%M")


def citation_label(citation: Citation) -> str:
    """인용 한 개를 화면 표기로 바꾼다.

    Args:
        citation: 답변 단계의 인용.

    Returns:
        str: `[매뉴얼] p.12 · 3.2 점검` 같은 표기. 종류 라벨과 `locator`를 잇는다.
    """
    return f"[{CITATION_KIND_LABELS[citation.kind]}] {citation.locator}"


def format_answer_markdown(answer: Answer, show_notice: bool = False) -> str:
    """답변을 화면용 Markdown으로 바꾼다. 고객 안내와 엔지니어 대화가 같이 쓴다.

    `answered`이면 번호 붙은 단계와 단계별 출처를, 보류 판정이면 판정 라벨과 안내 문구만 보인다.
    출처 원문은 이 문자열에 넣지 않는다(엔지니어 화면만 펼치기로 따로 보이고, 고객 화면은 표기만 보인다).

    Args:
        answer: RAG 답변.
        show_notice: `True`이고 `answered`인 답변에 `notice`가 있으면 단계 앞에 인용문으로 보인다
            (다른 설비 자료로 답했다는 안내). 고객 안내는 이 값을 쓰지 않는다.

    Returns:
        str: Markdown 문자열.
    """
    if answer.verdict != Verdict.ANSWERED:
        notice = answer.notice or _DEFAULT_NOTICES[answer.verdict]
        return f"**{verdict_label(answer.verdict)}**\n\n{notice}"

    lines: list[str] = []
    if show_notice and answer.notice:
        lines.extend([f"> {answer.notice}", ""])
    for no, step in enumerate(answer.steps, start=1):
        lines.append(f"{no}. {step.text}")
        if step.citations:
            sources = ", ".join(citation_label(c) for c in step.citations)
            lines.append(f"   - 출처: {sources}")
    return "\n".join(lines)


# info_table_html이 그리는 표의 공통 스타일 조각. 한 곳에 모아 두면 가로형·세로형이 서로 어긋나지 않는다.
_TABLE_BORDER = "1px solid #e4e4e7"
"""표 바깥 테두리·줄 사이 구분선에 쓰는 연회색 1px 선."""

_TABLE_CELL_PADDING = "padding:6px 12px !important;margin:0 !important"
"""모든 th·td에 공통으로 주는 안쪽 여백. Gradio 기본값을 밀어내려면 `!important`가 있어야 한다."""

_TABLE_NO_BORDER = "border:none !important"
"""Gradio 기본 테마가 table·tr·th·td에 걸어 둔 테두리를 지우는 선언. 우리가 원하는 구분선만 따로 그린다."""

_TABLE_RESET_STYLE = f"border-collapse:collapse;width:100%;{_TABLE_NO_BORDER};" \
    "margin:0 !important;padding:0 !important;border-spacing:0 !important"
"""`<table>` 태그 자체에 줄 style. Gradio 기본 테마가 table에도 마진·패딩을 걸어 둬서,
th·td만 지우면 회색 테두리 안쪽 위에 빈 여백이 남는다. table도 똑같이 지운다."""


def _vertical_table_rows_html(fields: list[tuple[str, str]]) -> str:
    """세로형 표(조치 결과 등)의 `<tr>` HTML을 라벨·값 한 줄씩 만든다.

    라벨 칸은 값 칸이 넓어 보이도록 너비를 100px로 고정한다. 마지막 줄만 아래 구분선을 생략해
    바깥 테두리와 겹쳐 보이지 않게 한다.

    Args:
        fields: `(라벨, 값)` 목록. 위에서 아래로 그 순서대로 한 줄씩 담는다.

    Returns:
        str: `<tr>` 태그를 이어 붙인 HTML. `label`·`value`는 이스케이프되어 들어간다.
    """
    last_row_index = len(fields) - 1
    row_html_list = []
    for row_index, (label, value) in enumerate(fields):
        is_last_row = row_index == last_row_index
        bottom_border = "" if is_last_row else f";border-bottom:{_TABLE_BORDER} !important"

        label_style = (
            f"{_TABLE_CELL_PADDING};text-align:left;font-weight:700;font-size:14px;color:#71717a;"
            f"{_TABLE_NO_BORDER};background:#f4f4f5 !important;"
            f"max-width:100px !important;width:100px !important{bottom_border}"
        )
        value_style = (
            f"{_TABLE_CELL_PADDING};text-align:left;font-size:15px;color:#27272a;"
            f"{_TABLE_NO_BORDER};background:#ffffff !important{bottom_border}"
        )
        row_html_list.append(
            f'<tr style="{_TABLE_NO_BORDER}">'
            f'<th style="{label_style}">{escape(label)}</th>'
            f'<td style="{value_style}">{escape(value)}</td>'
            "</tr>"
        )
    return "".join(row_html_list)


def _split_into_groups(fields: list[tuple[str, str]], columns_per_row: int | None) -> list[list[tuple[str, str]]]:
    """가로형 표의 필드를 한 줄에 둘 열 수만큼씩 끊는다.

    Args:
        fields: `(라벨, 값)` 목록.
        columns_per_row: 한 줄에 둘 열 수. 없으면 끊지 않고 전부 한 줄로 묶는다.

    Returns:
        list[list[tuple[str, str]]]: 줄마다 담길 필드 묶음 목록.
    """
    if not columns_per_row:
        return [fields]
    return [fields[start:start + columns_per_row] for start in range(0, len(fields), columns_per_row)]


def _horizontal_group_html(group: list[tuple[str, str]], *, is_first_group: bool) -> str:
    """가로형 표에서 한 묶음(열 제목 줄 + 값 줄)의 `<tr>` HTML 두 개를 만든다.

    Args:
        group: 그 줄에 나란히 보일 `(라벨, 값)` 목록.
        is_first_group: 표의 첫 묶음이면 `True`. 첫 묶음이 아니면 위쪽에 구분선을 그어 앞 묶음과
            나눠 보이게 한다.

    Returns:
        str: 열 제목 줄 `<tr>`과 값 줄 `<tr>`을 이어 붙인 HTML.
    """
    top_border = "" if is_first_group else f";border-top:{_TABLE_BORDER} !important"

    header_cells = []
    for label, _ in group:
        header_style = (
            f"{_TABLE_CELL_PADDING};text-align:left;font-weight:700;font-size:14px;color:#18181b;"
            f"{_TABLE_NO_BORDER};background:#f4f4f5 !important;"
            f"border-bottom:{_TABLE_BORDER} !important{top_border}"
        )
        header_cells.append(f'<th style="{header_style}">{escape(label)}</th>')

    value_cells = []
    for _, value in group:
        value_style = (
            f"{_TABLE_CELL_PADDING};text-align:left;font-size:15px;color:#27272a;"
            f"{_TABLE_NO_BORDER};background:#ffffff !important"
        )
        value_cells.append(f'<td style="{value_style}">{escape(value)}</td>')

    header_row = f'<tr style="{_TABLE_NO_BORDER}">{"".join(header_cells)}</tr>'
    value_row = f'<tr style="{_TABLE_NO_BORDER}">{"".join(value_cells)}</tr>'
    return header_row + value_row


def _note_row_html(note: tuple[str, str], column_count: int) -> str:
    """증상 메모처럼 긴 항목을 표 맨 아래 전체 너비 한 줄로 보여 줄 `<tr>` HTML을 만든다.

    Args:
        note: `(라벨, 값)`.
        column_count: 표의 한 줄당 열 수. 이 값으로 `colspan`을 줘서 전체 너비를 채운다.

    Returns:
        str: 위쪽에 구분선을 그은 전체 너비 `<tr>` HTML.
    """
    note_label, note_value = note
    cell_style = (
        f"{_TABLE_CELL_PADDING};{_TABLE_NO_BORDER};background:#ffffff !important;"
        f"border-top:{_TABLE_BORDER} !important"
    )
    label_html = f'<span style="color:#71717a;font-weight:700;font-size:14px">{escape(note_label)}</span>'
    value_html = f'<div style="font-size:15px;color:#27272a;margin-top:4px">{escape(note_value)}</div>'
    return (
        f'<tr style="{_TABLE_NO_BORDER}">'
        f'<td colspan="{column_count}" style="{cell_style}">{label_html}{value_html}</td>'
        "</tr>"
    )


def info_table_html(
    fields: list[tuple[str, str]], *, title: str | None = None, max_width: str | None = None,
    min_width: str | None = None, vertical: bool = False, center: bool = False,
    note: tuple[str, str] | None = None, columns_per_row: int | None = None,
) -> str:
    """접수 내역·조치 결과처럼 여러 항목을 신고 목록 표와 같은 모양의 칼럼 표로 만든다.

    기본(가로형)은 신고 목록 표(`gr.Dataframe`)와 같은 느낌으로 라벨을 열 제목(연회색 배경), 값을 그
    아래 한 행(흰 배경)으로 나란히 보여 준다(접수 내역). `vertical=True`면 라벨·값을 한 줄씩 나란히
    쌓은 세로형 표를 만든다(조치 결과처럼 값이 문장이라 여러 열로 늘어놓기 어려울 때 쓴다). 세로형의
    라벨 칸은 값 칸이 넓게 보이도록 너비를 100px로 고정한다. 두 모양 모두 테두리·안쪽 여백·글자
    크기·라벨(연회색)·값(흰색) 배경을 같게 둬서 한 화면에 나란히 둬도 같은 디자인처럼 보이게 한다.

    Args:
        fields: `(라벨, 값)` 목록. 보일 순서 그대로 담는다(가로형은 왼쪽부터 열, 세로형은 위부터 줄).
        title: 표 위에 둘 제목. 없으면 생략한다.
        max_width: 바깥 컨테이너의 최대 너비. 없으면 칸 너비를 다 쓴다.
        min_width: 바깥 컨테이너의 최소 너비. 열이 많은 가로형이 너무 좁아지지 않게 잡아 둔다.
        vertical: `True`면 라벨·값을 한 줄씩 쌓은 세로형 표로 만든다. 가로형(기본)은 각 필드가 열이다.
        center: `True`면 칸 가운데에 띄운다(고객 신고 상세처럼 본문 전체가 가운데 정렬인 화면용).
            기본은 왼쪽 정렬이다(장애 분석 대화처럼 뒤로 가기·제목이 왼쪽에 붙는 화면과 줄을 맞춘다).
        note: 가로형에서 증상 메모처럼 길어질 수 있어 열로 두기 어려운 항목. `(라벨, 값)`을 표 맨 아래
            전체 너비 한 줄로 내려 보여 준다. 세로형에는 쓰지 않는다(이미 한 줄씩 쌓는 모양이다).
        columns_per_row: 가로형에서 한 줄에 둘 열 수. 주면 `fields`를 이 수만큼씩 끊어 여러 줄(열 제목
            행+값 행 묶음)로 내려 쌓는다. 없으면(기본) 모든 열을 한 줄에 그대로 둔다. 세로형에는 쓰지 않는다.

    Returns:
        str: 옅은 테두리의 표 HTML. `title`·`label`·`value`는 이스케이프되어 들어간다.
    """
    # `gr.HTML`은 내용을 정화하지 않는다. 고객 메모·엔지니어 입력이 들어오므로 값마다 이스케이프한다.
    if vertical:
        rows_html = _vertical_table_rows_html(fields)
    else:
        groups = _split_into_groups(fields, columns_per_row)
        rows_html = "".join(
            _horizontal_group_html(group, is_first_group=group_index == 0)
            for group_index, group in enumerate(groups)
        )
        if note is not None:
            column_count = columns_per_row or len(fields)
            rows_html += _note_row_html(note, column_count)

    table_html = f'<table style="{_TABLE_RESET_STYLE}"><tbody>{rows_html}</tbody></table>'

    heading_html = ""
    if title:
        heading_html = f'<div style="font-weight:700;font-size:16px;margin-bottom:6px">{escape(title)}</div>'

    container_width_style = ""
    if max_width:
        container_width_style += f"max-width:{max_width};"
    if min_width:
        container_width_style += f"min-width:{min_width};"
    container_margin = "margin:0 auto" if center else "margin:0"

    return (
        f'<div style="{container_width_style}{container_margin}">{heading_html}'
        f'<div style="border:{_TABLE_BORDER};overflow:hidden">{table_html}</div>'
        "</div>"
    )


# ---------------------------------------------------------------------------
# 오류 문구
# ---------------------------------------------------------------------------

ERROR_MESSAGES: dict[type[Exception], str] = {
    ValidationError: "입력값을 확인해 주세요.",
    NotFoundError: "요청한 신고나 자료를 찾을 수 없습니다.",
    InvalidStateError: "이미 완료된 건이라 더 저장할 수 없습니다.",
    DependencyError: "일시적인 문제로 처리하지 못했습니다. 잠시 뒤 다시 시도해 주세요.",
}

UNKNOWN_ERROR_MESSAGE = "알 수 없는 문제가 생겼습니다. 담당자에게 알려 주세요."


def error_message(exc: Exception) -> str:
    """예외를 화면에 보일 고정 문구로 바꾼다. 예외의 원문 메시지는 쓰지 않는다.

    Args:
        exc: 서비스 호출에서 잡은 예외.

    Returns:
        str: 예외 종류별 고정 문구. 업무 예외 4종이 아니면 `UNKNOWN_ERROR_MESSAGE`.
    """
    for exc_type, message in ERROR_MESSAGES.items():
        if isinstance(exc, exc_type):
            return message
    return UNKNOWN_ERROR_MESSAGE
