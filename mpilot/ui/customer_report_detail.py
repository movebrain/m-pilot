"""고객 신고 상세 화면.

역할:
    고객이 신고의 진행 상태와, 셀프 가이드 또는 엔지니어가 등록한 조치 결과를 확인한다.

책임:
    - 상세는 `service.get_customer_case`만 쓴다.
    - 셀프 가이드 건은 단계별 안내와 출처 표기를 "셀프 가이드" 박스 안에 보여 주고,
      엔지니어 처리 건은 완료 뒤 조치 결과(`action`, `result`)를 보여 준다.
    - 출처 원문은 고객에게 펼쳐 보이지 않는다. 출처는 단계 아래 표기(매뉴얼 쪽·절)로만 보인다.
    - 내부 원인·`guide_failure`·엔지니어 내부 분석은 보이지 않는다(`CustomerCaseResult`에 없다).
    - 접근할 수 없는 신고(`NotFoundError`)는 고객 신고 목록으로 돌려보낸다.
    - 셀프 가이드 완료는 안내 제공의 종료이지 실제 해결 확인이 아니다. 추가 지원은 새 신고로 안내한다.

화면 전환: 상세 → 목록(뒤로·접근 불가).
사용 서비스: `get_customer_case`.
소유: UI·총괄 파트
"""

from __future__ import annotations

import gradio as gr

from mpilot import service
from mpilot.models import CustomerCaseResult, NotFoundError
from mpilot.ui import common

SCREEN = common.SCREEN_CUSTOMER_DETAIL

GUIDE_BOX_TITLE = "### 셀프 가이드"
"""셀프 가이드 박스 맨 위 제목."""

SELF_GUIDE_NOTICE = "자동으로 제공된 안내입니다. 조치 후에도 문제가 계속되면 새로 신고해 주세요."
"""셀프 가이드 건 아래에 보이는 문구. 셀프 가이드 완료는 실제 해결 확인이 아니다(spec §4)."""

WAITING_NOTICE = "엔지니어가 확인하고 있습니다. 조치가 끝나면 이 화면에서 결과를 볼 수 있습니다."
"""엔지니어 처리 건이 완료되기 전에 보이는 문구."""


def summary_markdown(case: CustomerCaseResult) -> str:
    """신고의 기본 정보를 화면용 Markdown으로 만든다.

    Args:
        case: 고객 공개 신고 상세.

    Returns:
        str: 접수번호·설비·코드·에러명·상태·접수 시각·메모를 담은 Markdown.
    """
    return "\n\n".join([
        f"**접수번호** {case.case_id}",
        f"**설비** {case.equipment} · **코드** {case.code} · **에러명** {case.error_name}",
        f"**상태** {common.status_label(case.status)} · **접수 시각** {common.format_datetime(case.created_at)}",
        f"**증상 메모** {case.memo or '-'}",
    ])


def guide_markdown(case: CustomerCaseResult) -> str:
    """셀프 가이드 박스 안에 넣을 단계별 안내를 화면용 Markdown으로 만든다.

    Args:
        case: 고객 공개 신고 상세.

    Returns:
        str: 셀프 가이드 건은 단계별 안내(단계마다 출처 표기)와 안내 문구. 셀프 가이드가 아니면 빈 문자열.
    """
    if case.guidance_answer is None:
        return ""
    return f"{common.format_answer_markdown(case.guidance_answer)}\n\n{SELF_GUIDE_NOTICE}"


def result_markdown(case: CustomerCaseResult) -> str:
    """엔지니어 처리 건의 조치 결과 또는 기다림 문구를 화면용 Markdown으로 만든다. 내부 원인은 담지 않는다.

    Args:
        case: 고객 공개 신고 상세.

    Returns:
        str: 완료된 엔지니어 처리 건은 조치·결과, 완료 전 엔지니어 처리 건은 기다림 문구.
            셀프 가이드 건은 박스가 안내를 보이므로 빈 문자열.
    """
    if case.guidance_answer is not None:
        return ""
    if case.action is not None:
        return f"### 조치 결과\n\n**조치 내용** {case.action}\n\n**결과** {case.result or '-'}"
    return WAITING_NOTICE


def build_customer_report_detail_screen(role: gr.State, case_id: gr.State, screen: gr.State) -> None:
    """신고의 진행 상태와, 셀프 가이드 박스 또는 조치 결과를 보여 준다.

    Args:
        role: 앱이 고정한 역할(`customer`)을 담은 공유 상태. 고객 앱에서만 이 화면을 조립한다.
        case_id: 선택된 신고의 접수번호를 담은 공유 상태. 보여 줄 신고의 접수번호. 이 화면은 읽기만 한다.
        screen: 지금 보여 줄 화면을 가리키는 공유 상태. 화면 전환 그림의 상태 이름과 대응한다. 뒤로 가기와 접근 불가는 고객 신고 목록으로 바꾼다.

    Returns:
        None: Gradio Blocks 안에서 호출되어 컴포넌트와 이벤트를 등록할 뿐 값을 돌려주지 않는다.
    """
    with gr.Column(visible=common.is_screen_visible(screen.value, SCREEN)) as column:
        gr.Markdown("## 고객 신고 상세")
        summary = gr.Markdown()
        with gr.Group(visible=False) as guide_box:  # 셀프 가이드 건에만 보인다
            gr.Markdown(GUIDE_BOX_TITLE, padding=True)
            guide = gr.Markdown(padding=True)
        body = gr.Markdown()
        back_btn = gr.Button("뒤로")

    def on_screen_change(current: str, selected: str | None):
        # 이 화면에 들어올 때마다 고른 신고를 다시 읽는다.
        if not common.is_screen_visible(current, SCREEN):
            return gr.Column(visible=False), gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip()
        try:
            case = service.get_customer_case(selected or "")
        except NotFoundError as e:
            gr.Warning(common.error_message(e))
            return gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(), common.SCREEN_CUSTOMER_LIST
        except Exception as e:  # 화면에는 고정 문구만 보인다
            gr.Warning(common.error_message(e))
            return gr.Column(visible=True), "", gr.Group(visible=False), "", "", gr.skip()
        guide_text = guide_markdown(case)
        return (
            gr.Column(visible=True), summary_markdown(case), gr.Group(visible=bool(guide_text)), guide_text,
            result_markdown(case), gr.skip(),
        )

    screen.change(
        on_screen_change, inputs=[screen, case_id],
        outputs=[column, summary, guide_box, guide, body, screen],
    )
    back_btn.click(lambda: common.SCREEN_CUSTOMER_LIST, outputs=screen)
