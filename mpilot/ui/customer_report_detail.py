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
    - 셀프 가이드 건은 안내를 받고 해결 여부를 알릴 때까지 '조치 중'이다. 하단 버튼은 [기술지원접수]·[해결완료] 두 개다. 누르면 각각
      `service.request_support`(조치 중 유지, 엔지니어 이관)·`service.resolve_self_guide`(조치 완료)를 부른 뒤
      신고를 다시 읽어 [확인] 버튼 하나로 바꾼다. 이미 해결완료했거나 이관된 건에는 두 버튼이 보이지 않는다.

화면 전환: 상세 → 목록(확인·접근 불가).
사용 서비스: `get_customer_case`, `resolve_self_guide`, `request_support`.
소유: UI·총괄 파트
"""

from __future__ import annotations

from html import escape

import gradio as gr

from mpilot import service
from mpilot.models import Answer, CaseStatus, CustomerCaseResult, NotFoundError, Route
from mpilot.ui import common

SCREEN = common.SCREEN_CUSTOMER_DETAIL

GUIDE_BOX_TITLE = "### 셀프 가이드"
"""셀프 가이드 박스 맨 위 제목."""

WAITING_NOTICE = "엔지니어가 확인하고 있습니다. 조치가 끝나면 이 화면에서 결과를 볼 수 있습니다."
"""엔지니어 처리 건이 완료되기 전에 맨 위 헤드라인 아래 부제로 보이는 문구."""

CONFIRM_LABEL = "확인"
"""일반 건(접수·조치 중·엔지니어 조치 완료)의 하단 버튼 문구."""

SUPPORT_REQUEST_LABEL = "기술지원접수"
"""셀프 가이드 건에서 안내로 해결되지 않을 때 누르는 버튼 문구. 조치 중을 유지한 채 엔지니어에게 이관한다."""

RESOLVED_LABEL = "해결완료"
"""셀프 가이드 건에서 안내로 해결됐을 때 누르는 버튼 문구. 조치 완료로 전환한다."""


def status_header_html(case: CustomerCaseResult) -> str:
    """상세 맨 위에 보일 아이콘·헤드라인·부제를 가운데 정렬 HTML로 만든다.

    Args:
        case: 고객 공개 신고 상세.

    Returns:
        str: 상태(셀프 가이드 제공·해결완료·기술지원 요청·조치 완료·접수 대기)에 따라 문구가 달라지는 HTML.
    """
    if case.route == Route.SELF_GUIDE and case.status == CaseStatus.COMPLETED:
        icon, headline, subtitle = "✅", "셀프 가이드로 해결되었습니다", "아래 안내는 그대로 다시 볼 수 있습니다."
    elif case.route == Route.SELF_GUIDE:
        icon, headline, subtitle = "✅", "셀프 가이드 안내가 제공되었습니다", "아래 안내에 따라 조치한 뒤 해결 여부를 알려 주세요."
    elif case.guidance_answer is not None and case.action is None:
        icon, headline, subtitle = "🕒", "기술지원팀에 요청되었습니다", WAITING_NOTICE
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

def guide_steps_html(answer: Answer) -> str:
    """셀프 가이드 단계를 번호 붙은 줄글로, 위에서 아래로 나열한 HTML로 만든다.

    Args:
        answer: 셀프 가이드 `Answer`(판정 `answered`).

    Returns:
        str: 단계마다 번호·본문·출처를 한 줄씩 담아 세로로 쌓은 HTML.
    """
    lines = []
    for no, step in enumerate(answer.steps, start=1):
        # 단계 문구는 LLM이 만든 값이라 `gr.HTML`에 넣기 전에 이스케이프한다.
        sources = escape(", ".join(common.citation_label(c) for c in step.citations))
        source_html = f'<div style="font-size:12px;opacity:.6;margin-top:2px">{sources}</div>' if sources else ""
        lines.append(f'<div style="margin:12px 0">{no}. {escape(step.text)}{source_html}</div>')
    return "".join(lines)


def guide_html(case: CustomerCaseResult) -> str:
    """셀프 가이드 박스 안에 넣을 단계별 안내를 세로로 쌓은 줄글 HTML로 만든다.

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
        str: 완료된 엔지니어 처리 건(셀프 가이드에서 이관된 건 포함)은 조치·결과. 완료 전이거나 셀프 가이드로
            끝난 건은 빈 문자열(기다림 문구는 맨 위 헤드라인 부제로 이미 보이므로 중복해 보이지 않는다).
    """
    if case.action is None:
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
        # 접수 정보 아래: 셀프 가이드 또는 조치 결과·안내 문구.
        with gr.Column(visible=False, elem_classes=[common.LIGHT_BORDER_BOX_CLASS]) as guide_box:  # 셀프 가이드 건에만 보인다
            gr.Markdown(GUIDE_BOX_TITLE, elem_classes=[common.CENTERED_TEXT_CLASS])
            guide = gr.HTML()  # 번호 붙은 줄글을 세로로 쌓은 단계(guide_html)
        with gr.Column(visible=False, elem_classes=[common.WIDE_NOTICE_BOX_CLASS]) as result_box:  # 조치 완료 건에만 보인다
            body = gr.Markdown(elem_classes=[common.CENTERED_TEXT_CLASS])
        with gr.Row(elem_classes=[common.CENTER_ROW_CLASS]):
            confirm_btn = gr.Button(CONFIRM_LABEL, variant="primary", scale=0, min_width=160)
            support_btn = gr.Button(SUPPORT_REQUEST_LABEL, variant="secondary", scale=0, min_width=140, visible=False)
            resolve_btn = gr.Button(RESOLVED_LABEL, variant="primary", scale=0, min_width=140, visible=False)

    action_buttons = [confirm_btn, support_btn, resolve_btn]
    case_outputs = [header, info, guide_box, guide, result_box, body, *action_buttons]

    def view_of(case: CustomerCaseResult) -> tuple:
        # 해결 여부를 기다리는 셀프 가이드 건(self_guide·조치 중)만 [확인] 대신 두 버튼을 보인다.
        waiting = case.route == Route.SELF_GUIDE and case.status == CaseStatus.IN_PROGRESS
        result_text = result_markdown(case)
        return (
            status_header_html(case), info_html(case),
            gr.Column(visible=case.guidance_answer is not None), guide_html(case),
            gr.Column(visible=bool(result_text)), result_text,
            gr.Button(visible=not waiting), gr.Button(visible=waiting), gr.Button(visible=waiting),
        )

    def on_screen_change(current: str, selected: str | None):
        # 이 화면에 들어올 때마다 고른 신고를 다시 읽는다.
        if not common.is_screen_visible(current, SCREEN):
            return (gr.Column(visible=False),) + (gr.skip(),) * 10
        try:
            case = service.get_customer_case(selected or "")
        except NotFoundError as e:
            gr.Warning(common.error_message(e))
            return (gr.skip(),) * 10 + (common.SCREEN_CUSTOMER_LIST,)
        except Exception as e:  # 화면에는 고정 문구만 보인다
            gr.Warning(common.error_message(e))
            return (
                gr.Column(visible=True), "", "", gr.Column(visible=False), "", gr.Column(visible=False), "",
                gr.Button(visible=True), gr.Button(visible=False), gr.Button(visible=False), gr.skip(),
            )
        return (gr.Column(visible=True),) + view_of(case) + (gr.skip(),)

    def on_answer(action_name: str):
        # 버튼을 누르면 서비스를 부르고 신고를 다시 읽는다. 실패해도 다시 읽어 실제 상태를 보인다.
        # fake가 service 함수를 바꿔 끼워도 따라가도록 누를 때 이름으로 찾는다.
        def handler(selected: str | None):
            try:
                getattr(service, action_name)(selected or "")
            except Exception as e:
                gr.Warning(common.error_message(e))
            try:
                return view_of(service.get_customer_case(selected or ""))
            except Exception as e:
                gr.Warning(common.error_message(e))
                return (gr.skip(),) * len(case_outputs)

        handler.__name__ = f"on_{action_name}"
        return handler

    screen.change(on_screen_change, inputs=[screen, case_id], outputs=[column, *case_outputs, screen])
    confirm_btn.click(lambda: common.SCREEN_CUSTOMER_LIST, outputs=screen)
    support_btn.click(on_answer("request_support"), inputs=case_id, outputs=case_outputs)
    resolve_btn.click(on_answer("resolve_self_guide"), inputs=case_id, outputs=case_outputs)
