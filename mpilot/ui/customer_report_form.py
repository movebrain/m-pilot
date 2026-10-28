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

import gradio as gr

from mpilot import service
from mpilot.models import CaseInput, Equipment, ErrorCode, ValidationError
from mpilot.ui import common

SCREEN = common.SCREEN_CUSTOMER_FORM

MEMO_MAX_LENGTH = 2000
"""증상 메모의 최대 글자 수(`CaseInput.memo`와 같다)."""


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
        gr.Markdown("## 고객 신고 작성")
        equipment = gr.Dropdown(choices=[e.value for e in Equipment], value=None, label="설비")
        code = gr.Dropdown(choices=[], value=None, label="에러코드")
        memo = gr.Textbox(
            label="증상 메모(선택)", lines=4, max_length=MEMO_MAX_LENGTH,
            placeholder="예: 주행 중 멈춘 뒤 다시 움직이지 않음",
        )
        with gr.Row():
            back_btn = gr.Button("뒤로")
            submit_btn = gr.Button("신고하기", variant="primary")

    def on_screen_change(current: str):
        # 이 화면에 들어올 때마다 입력을 비운다.
        if not common.is_screen_visible(current, SCREEN):
            return gr.Column(visible=False), gr.skip(), gr.skip(), gr.skip()
        return gr.Column(visible=True), None, gr.Dropdown(choices=[], value=None), ""

    def on_equipment_change(selected: str | None):
        # 설비를 고르면 그 설비의 코드만 선택지로 보인다.
        if not selected:
            return gr.Dropdown(choices=[], value=None)
        try:
            codes = service.list_error_codes(Equipment(selected))
        except Exception as e:
            gr.Warning(common.error_message(e))
            return gr.Dropdown(choices=[], value=None)
        return gr.Dropdown(choices=code_choices(codes), value=None)

    def on_submit(selected_equipment: str | None, selected_code: str | None, memo_text: str | None):
        try:
            new_case_id = service.submit_case(build_case_input(selected_equipment, selected_code, memo_text))
        except Exception as e:  # 화면에는 고정 문구만 보인다
            gr.Warning(common.error_message(e))
            return gr.skip(), gr.skip()
        return new_case_id, common.SCREEN_CUSTOMER_DETAIL

    screen.change(on_screen_change, inputs=screen, outputs=[column, equipment, code, memo])
    equipment.change(on_equipment_change, inputs=equipment, outputs=code)
    # 제출하는 동안 버튼을 막아 두 번 눌러도 한 번만 접수되게 한다.
    submit_btn.click(lambda: gr.Button(interactive=False), outputs=submit_btn).then(
        on_submit, inputs=[equipment, code, memo], outputs=[case_id, screen],
    ).then(lambda: gr.Button(interactive=True), outputs=submit_btn)
    back_btn.click(lambda: common.SCREEN_CUSTOMER_LIST, outputs=screen)
