"""고객 신고 상세 화면.

역할:
    고객이 신고의 진행 상태와, 셀프 가이드 또는 엔지니어가 등록한 조치 결과를 확인한다.

책임:
    - 상세는 `service.get_customer_case`만 쓴다.
    - 맨 위에 아이콘·헤드라인·부제로 진행 상태를 한눈에 보여 준다(`status_header_html`).
    - 셀프 가이드 건은 단계별 안내와 출처 표기를 "셀프 가이드" 박스 안에 보여 주고,
      엔지니어 처리 건은 완료 뒤 조치 결과(`action`, `result`)를 보여 준다.
    - 출처 원문은 고객에게 펼쳐 보이지 않는다. 출처는 단계 아래 표기(매뉴얼 쪽·절)로만 보인다.
    - 내부 원인·`guide_failure`·엔지니어 내부 분석은 보이지 않는다(`CustomerCaseResult`에 없다).
    - 접근할 수 없는 신고(`NotFoundError`)는 고객 신고 목록으로 돌려보낸다.
    - 셀프 가이드 완료는 안내 제공의 종료이지 실제 해결 확인이 아니다. 추가 지원은 새 신고로 안내한다.

화면 전환: 상세 → 목록(확인·접근 불가).
사용 서비스: `get_customer_case`.
소유: UI·총괄 파트
"""

from __future__ import annotations

import gradio as gr

from mpilot import service
from mpilot.models import Answer, CustomerCaseResult, NotFoundError
from mpilot.ui import common

SCREEN = common.SCREEN_CUSTOMER_DETAIL

GUIDE_BOX_TITLE = "### 셀프 가이드"
"""셀프 가이드 박스 맨 위 제목."""

WAITING_NOTICE = "엔지니어가 확인하고 있습니다. 조치가 끝나면 이 화면에서 결과를 볼 수 있습니다."
"""엔지니어 처리 건이 완료되기 전에 맨 위 헤드라인 아래 부제로 보이는 문구."""


def status_header_html(case: CustomerCaseResult) -> str:
    """상세 맨 위에 보일 아이콘·헤드라인·부제를 가운데 정렬 HTML로 만든다.

    Args:
        case: 고객 공개 신고 상세.

    Returns:
        str: 상태(셀프 가이드 제공·조치 완료·접수 대기)에 따라 문구가 달라지는 HTML.
    """
    if case.guidance_answer is not None:
        icon, headline, subtitle = "✅", "셀프 가이드 안내가 제공되었습니다", "아래 안내에 따라 조치해 주세요."
    elif case.action is not None:
        icon, headline, subtitle = "✅", "조치가 완료되었습니다", "아래에서 조치 결과를 확인할 수 있습니다."
    else:
        icon, headline, subtitle = "🕒", "신고가 접수되었습니다", WAITING_NOTICE
    return (
        '<div style="text-align:center">'
        f'<div style="font-size:40px">{icon}</div>'
        f'<div style="font-size:20px;font-weight:700;margin-top:8px">{headline}</div>'
        f'<div style="font-size:14px;opacity:.7;margin-top:4px">{subtitle}</div>'
        "</div>"
    )


def info_html(case: CustomerCaseResult) -> str:
    """접수 정보를 `라벨 : 값` 줄로 세로로 나열한 HTML을 만든다. 줄 사이 간격을 좁게 둔다.

    Args:
        case: 고객 공개 신고 상세.

    Returns:
        str: 접수번호·상태·설비·접수 시각·에러코드·증상 메모를 한 줄씩 담은 HTML.
    """
    fields = [
        ("접수번호", case.case_id),
        ("상태", common.status_label(case.status)),
        ("설비", str(case.equipment)),
        ("접수 시각", common.format_datetime(case.created_at)),
        ("에러코드", f"{case.code} · {case.error_name}"),
        ("증상 메모", case.memo or "-"),
    ]
    return common.info_box_html(fields)

STEP_CIRCLE_SIZE = 48
"""단계 번호 원의 지름(px). 최대 5단계가 한 줄에 들어가도록 잡은 크기다."""

STEP_COLUMN_WIDTH = 170
"""단계 한 칸의 최대 너비(px). 5단계 기준으로 잡아, 더 적으면 가운데로 모여 보인다."""


def guide_steps_html(answer: Answer) -> str:
    """셀프 가이드 단계를 원형 번호·화살표로 잇는 가로 단계 HTML로 만든다.

    단계 수는 신고마다 다를 수 있어 정해 두지 않고, 최대 5단계가 한 줄에 들어가는 너비를 기준으로
    잡는다. 그보다 적으면 가운데로 모이고, 많으면 다음 줄로 넘어간다.

    Args:
        answer: 셀프 가이드 `Answer`(판정 `answered`).

    Returns:
        str: 단계마다 번호 원·"STEP NN" 라벨·본문·출처를 담고 `›`로 이은 HTML.
    """
    color = common.CUSTOMER_ACCENT
    columns = []
    for no, step in enumerate(answer.steps, start=1):
        sources = ", ".join(common.citation_label(c) for c in step.citations)
        source_html = f'<div style="font-size:11px;opacity:.6;margin-top:4px">{sources}</div>' if sources else ""
        columns.append(
            f'<div style="flex:1 1 0;min-width:120px;max-width:{STEP_COLUMN_WIDTH}px;text-align:center">'
            f'<div style="width:{STEP_CIRCLE_SIZE}px;height:{STEP_CIRCLE_SIZE}px;border-radius:50%;'
            f'border:2px solid {color};display:flex;align-items:center;justify-content:center;'
            f'margin:0 auto;font-weight:700;color:{color}">{no}</div>'
            f'<div style="font-size:12px;font-weight:700;color:{color};margin-top:8px">STEP {no:02d}</div>'
            f'<div style="font-size:14px;margin-top:4px">{step.text}</div>'
            f"{source_html}</div>"
        )
    arrow = '<div style="flex:0 0 auto;color:#a1a1aa;font-size:20px;padding-top:10px">›</div>'
    return (
        '<div style="display:flex;align-items:flex-start;justify-content:center;flex-wrap:wrap;gap:4px">'
        + arrow.join(columns)
        + "</div>"
    )


def guide_html(case: CustomerCaseResult) -> str:
    """셀프 가이드 박스 안에 넣을 단계별 안내를 가로 단계 HTML로 만든다.

    Args:
        case: 고객 공개 신고 상세.

    Returns:
        str: 셀프 가이드 건은 단계 HTML(`guide_steps_html`). 셀프 가이드가 아니면 빈 문자열.
    """
    if case.guidance_answer is None:
        return ""
    return guide_steps_html(case.guidance_answer)


def result_markdown(case: CustomerCaseResult) -> str:
    """엔지니어 처리 건의 조치 결과를 화면용 Markdown으로 만든다. 내부 원인은 담지 않는다.

    Args:
        case: 고객 공개 신고 상세.

    Returns:
        str: 완료된 엔지니어 처리 건은 조치·결과. 완료 전이거나 셀프 가이드 건은 빈 문자열
            (기다림 문구는 맨 위 헤드라인 부제로 이미 보이므로 중복해 보이지 않는다).
    """
    if case.guidance_answer is not None or case.action is None:
        return ""
    return f"### 조치 결과\n\n**조치 내용** {case.action}\n\n**결과** {case.result or '-'}"


def build_customer_report_detail_screen(role: gr.State, case_id: gr.State, screen: gr.State) -> None:
    """신고의 진행 상태와, 셀프 가이드 박스 또는 조치 결과를 보여 준다.

    Args:
        role: 앱이 고정한 역할(`customer`)을 담은 공유 상태. 고객 앱에서만 이 화면을 조립한다.
        case_id: 선택된 신고의 접수번호를 담은 공유 상태. 보여 줄 신고의 접수번호. 이 화면은 읽기만 한다.
        screen: 지금 보여 줄 화면을 가리키는 공유 상태. 화면 전환 그림의 상태 이름과 대응한다. '확인'과 접근 불가는 고객 신고 목록으로 바꾼다.

    Returns:
        None: Gradio Blocks 안에서 호출되어 컴포넌트와 이벤트를 등록할 뿐 값을 돌려주지 않는다.
    """
    with gr.Column(visible=common.is_screen_visible(screen.value, SCREEN)) as column:
        header = gr.HTML()  # 아이콘·헤드라인·부제(진행 상태 요약)
        info = gr.HTML()  # 접수 정보(세로 목록, 줄 간격을 좁게 둔다)
        # 접수 정보 아래: 셀프 가이드 또는 조치 결과·안내 문구
        with gr.Column(visible=False) as guide_box:  # 셀프 가이드 건에만 보인다(박스 색 없이, 가운데 정렬)
            gr.Markdown(GUIDE_BOX_TITLE, elem_classes=[common.CENTERED_TEXT_CLASS])
            guide = gr.HTML()  # 원형 번호로 잇는 가로 단계(guide_html)
        body = gr.Markdown(elem_classes=[common.CENTERED_TEXT_CLASS])
        with gr.Row(elem_classes=[common.CENTER_ROW_CLASS]):
            confirm_btn = gr.Button("확인", variant="primary", scale=0, min_width=160)

    def on_screen_change(current: str, selected: str | None):
        # 이 화면에 들어올 때마다 고른 신고를 다시 읽는다.
        if not common.is_screen_visible(current, SCREEN):
            return (gr.Column(visible=False),) + (gr.skip(),) * 6
        try:
            case = service.get_customer_case(selected or "")
        except NotFoundError as e:
            gr.Warning(common.error_message(e))
            return (gr.skip(),) * 6 + (common.SCREEN_CUSTOMER_LIST,)
        except Exception as e:  # 화면에는 고정 문구만 보인다
            gr.Warning(common.error_message(e))
            return (gr.Column(visible=True), "", "", gr.Column(visible=False), "", "", gr.skip())
        guide_text = guide_html(case)
        return (
            gr.Column(visible=True), status_header_html(case), info_html(case),
            gr.Column(visible=bool(guide_text)), guide_text, result_markdown(case), gr.skip(),
        )

    screen.change(
        on_screen_change, inputs=[screen, case_id],
        outputs=[column, header, info, guide_box, guide, body, screen],
    )
    confirm_btn.click(lambda: common.SCREEN_CUSTOMER_LIST, outputs=screen)
