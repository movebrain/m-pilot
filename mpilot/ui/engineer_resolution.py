"""조치 이력 저장 화면.

역할:
    엔지니어가 원인·실제 조치·결과를 입력해 신고를 조치 완료로 확정한다.

책임:
    - 신고 정보는 `service.get_engineer_case`, 확정은 `service.confirm_resolution`만 쓴다.
    - 원인·조치·결과는 각각 앞뒤 공백 제거 후 1~5,000자다. 임시 저장과 AI 초안은 범위 밖이다.
    - 확정하면 신고가 '조치 완료'가 되고 다음 질문의 근거가 된다. 검색 반영에 실패해도 저장은 유지되며
      반영 결과(`bool`)에 따라 안내한다.
    - 이미 완료된 건은 '이미 완료'로 안내한다. 확정 뒤에는 수정할 수 없다.
    - 제출 중에는 버튼을 비활성화한다. 원인(`cause`)은 고객에게 공개되지 않는다.

화면 전환: 이력 저장 → 대화(뒤로) / 대시보드(확정 후).
사용 서비스: `get_engineer_case`, `confirm_resolution`.
소유: UI·총괄 파트
"""

from __future__ import annotations

import gradio as gr

from mpilot import service
from mpilot.models import CaseDetail, CaseStatus, NotFoundError, Resolution, ValidationError
from mpilot.ui import common

SCREEN = common.SCREEN_ENGINEER_RESOLVE

FIELD_MAX_LENGTH = 5000
"""원인·조치·결과 각각의 최대 글자 수(`Resolution`과 같다)."""

INDEXED_NOTICE = "조치 완료로 등록했습니다. 이 이력은 이후 질문의 검색 근거가 됩니다."
"""확정과 검색 반영이 모두 성공했을 때의 안내."""

NOT_INDEXED_NOTICE = "조치 완료로 등록했습니다. 검색 반영은 실패해 앱을 다시 시작하면 반영됩니다."
"""확정은 됐지만 검색 반영(`index_history`)이 실패했을 때의 안내."""

COMPLETED_NOTICE = "이미 조치 완료된 건이라 더 등록할 수 없습니다."
"""완료된 건에 들어왔을 때 보이는 안내."""


def case_summary_markdown(detail: CaseDetail) -> str:
    """신고 정보를 화면용 Markdown으로 만든다.

    Args:
        detail: `service.get_engineer_case`가 돌려준 신고 상세.

    Returns:
        str: 접수번호·설비·코드·에러명·심각도·상태·증상 메모를 담은 Markdown.
    """
    case = detail.service_case
    return "\n\n".join([
        f"**접수번호** {case.case_id} · **상태** {common.status_label(case.status)}",
        f"**설비** {case.equipment} · **코드** {case.code} · **에러명** {detail.error_code.name}"
        f" · **심각도** {detail.error_code.severity}",
        f"**증상 메모** {case.memo or '-'}",
    ])


def build_resolution(cause: str | None, action: str | None, result: str | None) -> Resolution:
    """화면 입력값을 조치 기록으로 바꾼다.

    Args:
        cause: 실제 원인.
        action: 실제 조치.
        result: 조치 결과.

    Returns:
        Resolution: 앞뒤 공백을 지운 조치 기록.

    Raises:
        ValidationError: 비었거나 5,000자를 넘는 칸이 있을 때.
    """
    values = [(v or "").strip() for v in (cause, action, result)]
    if not all(1 <= len(v) <= FIELD_MAX_LENGTH for v in values):
        raise ValidationError("원인·조치·결과 길이")
    return Resolution(cause=values[0], action=values[1], result=values[2])


def confirm_notice(indexed: bool) -> str:
    """확정 결과 안내 문구를 고른다.

    Args:
        indexed: `service.confirm_resolution`이 돌려준 검색 반영 성공 여부.

    Returns:
        str: 반영 성공이면 `INDEXED_NOTICE`, 실패면 `NOT_INDEXED_NOTICE`.
    """
    return INDEXED_NOTICE if indexed else NOT_INDEXED_NOTICE


def build_engineer_resolution_screen(role: gr.State, case_id: gr.State, screen: gr.State) -> None:
    """원인·실제 조치·결과를 입력받아 조치 완료로 등록한다.

    Args:
        role: 앱이 고정한 역할(`engineer`)을 담은 공유 상태. 엔지니어 앱에서만 이 화면을 조립한다.
        case_id: 선택된 신고의 접수번호를 담은 공유 상태. 완료 처리할 신고의 접수번호. 이 화면은 읽기만 한다.
        screen: 지금 보여 줄 화면을 가리키는 공유 상태. 화면 전환 그림의 상태 이름과 대응한다. 뒤로 가기는 장애 분석 대화, 확정 후에는 엔지니어 대시보드로 바꾼다.

    Returns:
        None: Gradio Blocks 안에서 호출되어 컴포넌트와 이벤트를 등록할 뿐 값을 돌려주지 않는다.
    """
    with gr.Column(visible=common.is_screen_visible(screen.value, SCREEN)) as column:
        gr.Markdown("## 조치 이력 저장")
        summary = gr.Markdown()
        cause = gr.Textbox(label="실제 원인 (고객에게 공개되지 않습니다)", lines=3, max_length=FIELD_MAX_LENGTH)
        action = gr.Textbox(label="실제 조치", lines=3, max_length=FIELD_MAX_LENGTH)
        result = gr.Textbox(label="조치 결과", lines=3, max_length=FIELD_MAX_LENGTH)
        confirm_btn = gr.Button("조치 완료로 확정", variant="primary")
        back_btn = gr.Button("뒤로")

    inputs = [cause, action, result]

    def on_screen_change(current: str, selected: str | None):
        # 이 화면에 들어올 때마다 신고를 다시 읽고 입력을 비운다. 완료된 건은 입력을 막는다.
        if not common.is_screen_visible(current, SCREEN):
            return (gr.Column(visible=False),) + (gr.skip(),) * 6
        try:
            detail = service.get_engineer_case(selected or "")
        except NotFoundError as e:
            gr.Warning(common.error_message(e))
            return (gr.skip(),) * 6 + (common.SCREEN_ENGINEER_LIST,)
        except Exception as e:  # 화면에는 고정 문구만 보인다
            gr.Warning(common.error_message(e))
            return (gr.Column(visible=True), "") + (gr.skip(),) * 5
        open_ = detail.service_case.status != CaseStatus.COMPLETED
        if not open_:
            gr.Info(COMPLETED_NOTICE)
        boxes = tuple(gr.Textbox(value="", interactive=open_) for _ in inputs)
        return (gr.Column(visible=True), case_summary_markdown(detail)) + boxes + (gr.Button(interactive=open_), gr.skip())

    def on_confirm(selected: str | None, cause_text: str | None, action_text: str | None, result_text: str | None):
        # 확정에 성공하면 결과를 안내하고 대시보드로 간다. 실패하면 입력을 남겨 다시 시도하게 한다.
        try:
            _, indexed = service.confirm_resolution(
                selected or "", build_resolution(cause_text, action_text, result_text)
            )
        except Exception as e:
            gr.Warning(common.error_message(e))
            return gr.skip()
        gr.Info(confirm_notice(indexed))
        return common.SCREEN_ENGINEER_LIST

    screen.change(on_screen_change, inputs=[screen, case_id], outputs=[column, summary, *inputs, confirm_btn, screen])
    confirm_btn.click(lambda: gr.Button(interactive=False), outputs=confirm_btn).then(
        on_confirm, inputs=[case_id, *inputs], outputs=screen,
    ).then(lambda: gr.Button(interactive=True), outputs=confirm_btn)
    back_btn.click(lambda: common.SCREEN_ENGINEER_CHAT, outputs=screen)
