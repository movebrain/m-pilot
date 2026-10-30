"""고객 신고 작성 화면.

역할:
    고객이 설비·에러코드·증상 메모를 입력해 신고를 제출한다.

책임:
    - 에러코드 선택지는 `service.list_error_codes`, 제출은 `service.submit_case`만 쓴다.
    - 설비를 고르면 그 설비의 코드만 선택지로 보인다. 증상 메모는 선택이며 2,000자 이하다.
    - 설비는 사진·이름·설명이 든 카드로 보여 준다. 카드를 누르면 고르고, 고른 카드는 테두리가 강조되고
      이름 앞 라디오 점이 찬다. 설비 선택은 필수라 화면을 열면 맨 앞 설비(`DEFAULT_EQUIPMENT`)가 미리 골라져 있다.
    - 제출하면 접수번호가 발급되고 고객 신고 상세로 이동한다. 처리 경로(셀프 가이드·엔지니어 접수)는 서비스가 정하며
      이 화면은 판단하지 않는다.
    - 제출 중에는 버튼을 비활성화해 중복 제출을 막는다(멱등 키·잠금은 범위 밖).
    - 사진·로그 첨부는 범위 밖이다.

화면 전환: 신고 작성 → 상세(제출 성공) / 목록(뒤로).
사용 서비스: `list_error_codes`, `submit_case`.
소유: UI·총괄 파트
"""

from __future__ import annotations

import base64
from pathlib import Path

import gradio as gr

from mpilot import service
from mpilot.models import CaseInput, Equipment, ErrorCode, ValidationError
from mpilot.ui import common

SCREEN = common.SCREEN_CUSTOMER_FORM

MEMO_MAX_LENGTH = 2000
"""증상 메모의 최대 글자 수(`CaseInput.memo`와 같다)."""

PHOTO_DIR = Path("data") / "photos"
"""설비 사진 폴더. 파일명은 설비 값을 소문자로 하고 띄어쓰기를 `_`로 바꾼 것(`mini_loader.jpg`)이다."""

EQUIPMENT_DESC: dict[str, str] = {
    "Mini Loader": "소형 화물(박스·버켓) 전용 고속 운송 설비",
    "SRM": "자동창고(ASRS) 랙 전용 화물 적재·출고 핵심 크레인",
    "AGV": "물류·제조 현장의 자율주행 무인 운반차",
    "RTV": "궤도(레일) 기반 자동창고 입출고 운송 대차",
}
"""설비별 카드 설명. 없으면 설명 칸은 비어 있다."""

ACCENT = "#e8590c"
"""고른 카드의 강조색(주황)."""

DEFAULT_EQUIPMENT = next(iter(Equipment))
"""설비 선택은 필수라 화면을 열면 처음부터 골라 두는 설비(목록 맨 앞)."""


def radio_dot_html(selected: bool) -> str:
    """설비 이름 앞에 보일 라디오 점을 HTML로 만든다.

    Args:
        selected: 고른 카드인지. 고르면 속이 강조색으로 찬다.

    Returns:
        str: 라디오 점 HTML.
    """
    fill = f'<span style="position:absolute;inset:3px;border-radius:50%;background:{ACCENT}"></span>' if selected else ""
    border = ACCENT if selected else "#9ca3af"
    return (
        f'<span style="width:16px;height:16px;border-radius:50%;border:2px solid {border};'
        f'display:inline-block;position:relative;flex-shrink:0;margin-right:6px">{fill}</span>'
    )


def card_html(equipment_value: str, selected: bool) -> str:
    """설비 카드 한 장(사진·이름·설명)을 HTML로 만든다.

    Args:
        equipment_value: 설비 값. 예: `Mini Loader`.
        selected: 고른 카드인지. 고른 카드는 테두리가 강조색이고 이름 앞 라디오 점이 찬다.

    Returns:
        str: 카드 HTML. 사진 파일이 없으면 사진 칸은 비어 있다.
    """
    photo = PHOTO_DIR / f"{equipment_value.lower().replace(' ', '_')}.jpg"
    img = ""
    if photo.exists():
        data = base64.b64encode(photo.read_bytes()).decode()
        img = f'<img src="data:image/jpeg;base64,{data}" style="max-height:100%;max-width:100%">'
    border = ACCENT if selected else "#d4d4d8"
    desc = EQUIPMENT_DESC.get(equipment_value, "")
    return (
        f'<div style="border:1px solid {border};border-radius:0;'
        'overflow:hidden;cursor:pointer;height:100%">'
        '<div style="background:#fff;height:120px;display:flex;align-items:center;justify-content:center">'
        f"{img}</div>"
        '<div style="padding:12px">'
        f'<div style="font-weight:700;display:flex;align-items:center">'
        f"{radio_dot_html(selected)}{equipment_value}</div>"
        f'<div style="font-size:13px;opacity:.7;margin-top:6px;min-height:36px">{desc}</div>'
        "</div></div>"
    )


CODE_PLACEHOLDER = ("에러코드를 선택해주세요", "")
"""에러코드 드롭다운의 안내 선택지. `gr.Dropdown`엔 placeholder가 없어 맨 앞 선택지로 보인다.
값이 빈 문자열이라 `build_case_input`의 '고르지 않았음' 판정과 맞는다."""


def code_choices(codes: list[ErrorCode]) -> list[tuple[str, str]]:
    """에러코드 표 행을 드롭다운 선택지로 바꾼다.

    Args:
        codes: `service.list_error_codes`가 돌려준 한 설비의 에러코드 행.

    Returns:
        list[tuple[str, str]]: `(화면 라벨, 코드)` 목록. 라벨은 `코드 · 에러명`이다.
    """
    return [(f"{c.code} · {c.name}", c.code) for c in codes]


def build_case_input(equipment: str | None, code: str | None, memo: str | None) -> CaseInput:
    """화면 입력값을 신고 입력으로 바꾼다.

    Args:
        equipment: 고른 설비 값. 고르지 않았으면 `None`.
        code: 고른 에러코드. 고르지 않았으면 `None`.
        memo: 증상 메모. 앞뒤 공백을 지우고 비어 있으면 `None`으로 넘긴다.

    Returns:
        CaseInput: `service.submit_case`에 넘길 입력.

    Raises:
        ValidationError: 설비나 코드를 고르지 않았거나 메모가 2,000자를 넘을 때.
    """
    if not equipment or not code:
        raise ValidationError("설비와 에러코드를 골라야 한다")
    memo = (memo or "").strip() or None
    if memo is not None and len(memo) > MEMO_MAX_LENGTH:
        raise ValidationError("메모 길이")
    return CaseInput(equipment=Equipment(equipment), code=code, memo=memo)


def build_customer_report_form_screen(role: gr.State, case_id: gr.State, screen: gr.State) -> None:
    """설비·에러코드·증상 메모를 입력받아 신고를 제출한다.

    Args:
        role: 앱이 고정한 역할(`customer`)을 담은 공유 상태. 고객 앱에서만 이 화면을 조립한다.
        case_id: 선택된 신고의 접수번호를 담은 공유 상태. 제출에 성공하면 새 접수번호로 채운다.
        screen: 지금 보여 줄 화면을 가리키는 공유 상태. 화면 전환 그림의 상태 이름과 대응한다. 제출 성공은 고객 신고 상세, 뒤로는 고객 신고 목록으로 바꾼다.

    Returns:
        None: Gradio Blocks 안에서 호출되어 컴포넌트와 이벤트를 등록할 뿐 값을 돌려주지 않는다.
    """
    with gr.Column(visible=common.is_screen_visible(screen.value, SCREEN)) as column:
        with common.back_button_row("## 고객 신고 작성") as back_btn:
            submit_btn = gr.Button("제출하기", variant="primary", scale=0, min_width=120)

        # 고른 설비 값을 담아 두는 숨김 라디오. 화면에는 아래 카드가 보인다.
        # 설비 선택은 필수라 맨 앞의 설비를 미리 골라 둔다.
        equipment = gr.Radio(choices=[e.value for e in Equipment], value=DEFAULT_EQUIPMENT.value, visible=False)

        # 설비마다 카드 한 장. 카드를 누르면 그 설비를 고른다.
        cards: list[tuple[str, gr.HTML]] = []
        with gr.Row(equal_height=True):
            for e in Equipment:
                with gr.Column(min_width=160):
                    cards.append((e.value, gr.HTML(card_html(e.value, selected=(e == DEFAULT_EQUIPMENT)))))

        code = gr.Dropdown(choices=[CODE_PLACEHOLDER], value="", label="에러코드", filterable=False)
        memo = gr.Textbox(
            label="증상 메모(선택)", lines=4, max_length=MEMO_MAX_LENGTH,
            placeholder="예: 주행 중 멈춘 뒤 다시 움직이지 않음",
        )

    def on_equipment_change(selected: str | None):
        # 설비를 고르면 그 설비의 코드만 선택지로 보인다. 맨 앞엔 항상 안내 선택지를 둔다.
        if not selected:
            return gr.Dropdown(choices=[CODE_PLACEHOLDER], value="")
        try:
            codes = service.list_error_codes(Equipment(selected))
        except Exception as e:
            gr.Warning(common.error_message(e))
            return gr.Dropdown(choices=[CODE_PLACEHOLDER], value="")
        return gr.Dropdown(choices=[CODE_PLACEHOLDER, *code_choices(codes)], value="")

    def mark_selected(selected: str | None):
        # 고른 카드만 테두리를 강조한다.
        return [gr.HTML(card_html(value, selected=(value == selected))) for value, _ in cards]

    def on_screen_change(current: str):
        # 이 화면에 들어올 때마다 입력을 비우고 맨 앞의 설비를 다시 골라 둔다. equipment.change
        # 연쇄에 기대지 않고 코드 선택지·카드 강조를 여기서 바로 다시 계산한다.
        if not common.is_screen_visible(current, SCREEN):
            return (gr.Column(visible=False),) + (gr.skip(),) * (3 + len(cards))
        return (
            gr.Column(visible=True), DEFAULT_EQUIPMENT.value,
            on_equipment_change(DEFAULT_EQUIPMENT.value), "",
            *mark_selected(DEFAULT_EQUIPMENT.value),
        )

    def on_submit(selected_equipment: str | None, selected_code: str | None, memo_text: str | None):
        try:
            new_case_id = service.submit_case(build_case_input(selected_equipment, selected_code, memo_text))
        except Exception as e:  # 화면에는 고정 문구만 보인다
            gr.Warning(common.error_message(e))
            return gr.skip(), gr.skip()
        return new_case_id, common.SCREEN_CUSTOMER_DETAIL

    screen.change(
        on_screen_change, inputs=screen,
        outputs=[column, equipment, code, memo, *[card for _, card in cards]],
    )
    equipment.change(on_equipment_change, inputs=equipment, outputs=code)
    equipment.change(mark_selected, inputs=equipment, outputs=[card for _, card in cards])
    # 카드를 누르면 숨김 라디오 값을 그 설비로 채운다.
    for value, card in cards:
        card.click(lambda v=value: v, outputs=equipment)
    # 제출하는 동안 버튼을 막아 두 번 눌러도 한 번만 접수되게 한다.
    submit_btn.click(lambda: gr.Button(interactive=False), outputs=submit_btn).then(
        on_submit, inputs=[equipment, code, memo], outputs=[case_id, screen],
    ).then(lambda: gr.Button(interactive=True), outputs=submit_btn)
    back_btn.click(lambda: common.SCREEN_CUSTOMER_LIST, outputs=screen)