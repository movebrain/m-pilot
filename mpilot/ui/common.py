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

의존: `models`만 import한다.
소유: UI·총괄 파트
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from mpilot.models import (
    Answer,
    CaseStatus,
    Citation,
    CitationKind,
    DependencyError,
    InvalidStateError,
    NotFoundError,
    Route,
    ValidationError,
    Verdict,
)

# ---------------------------------------------------------------------------
# 가상 데이터 배너
# ---------------------------------------------------------------------------

VIRTUAL_DATA_BANNER = "⚠️ **모든 데이터는 가상입니다.** 실제 설비·고객·서비스 이력이 아닙니다."
"""모든 화면 맨 위에 `gr.Markdown(VIRTUAL_DATA_BANNER)`로 보이는 문구."""

# ---------------------------------------------------------------------------
# 화면 이름 (`screen` 공유 상태에 넣는 값). spec §2.3.3 화면 전환 그림의 상태 이름과 같다.
# ---------------------------------------------------------------------------

SCREEN_CUSTOMER_LIST = "cust_list"
SCREEN_CUSTOMER_FORM = "cust_form"
SCREEN_CUSTOMER_DETAIL = "cust_detail"
SCREEN_ENGINEER_LIST = "eng_list"
SCREEN_ENGINEER_CHAT = "eng_chat"
SCREEN_ENGINEER_RESOLVE = "eng_resolve"

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
    Verdict.NOT_APPLICABLE: "적용 대상 아님",
}

CITATION_KIND_LABELS: dict[CitationKind, str] = {
    CitationKind.MANUAL: "매뉴얼",
    CitationKind.HISTORY: "서비스 이력",
}

# 보류 판정인데 notice가 비어 있을 때 대신 보여 줄 문구.
_DEFAULT_NOTICES: dict[Verdict, str] = {
    Verdict.INSUFFICIENT_EVIDENCE: "매뉴얼과 서비스 이력에서 답할 근거를 찾지 못했습니다. 조치는 추측하지 않습니다.",
    Verdict.NOT_APPLICABLE: "지원 대상 설비(Mini Loader, SRM, AGV, RTV) 밖의 질문이라 답할 수 없습니다.",
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
        str: 화면 라벨('답변', '근거 부족', '적용 대상 아님').

    Raises:
        ValueError: 허용되지 않은 판정 값일 때.
    """
    return VERDICT_LABELS[Verdict(verdict)]


def status_choices() -> list[tuple[str, str]]:
    """상태 필터 드롭다운의 선택지를 만든다. 맨 앞은 '전체'다.

    Returns:
        list[tuple[str, str]]: `(라벨, 값)` 목록. '전체'의 값은 빈 문자열이다.
            `gr.Dropdown(choices=...)`에 그대로 넣는다.
    """
    return [("전체", "")] + [(label, status.value) for status, label in STATUS_LABELS.items()]


def parse_status_filter(value: str | None) -> CaseStatus | None:
    """상태 필터 드롭다운 값을 서비스 인자로 바꾼다.

    Args:
        value: 드롭다운에서 고른 값. 빈 문자열이나 `None`은 '전체'다.

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


def format_answer_markdown(answer: Answer) -> str:
    """답변을 화면용 Markdown으로 바꾼다. 고객 안내와 엔지니어 대화가 같이 쓴다.

    `answered`이면 번호 붙은 단계와 단계별 출처를, 보류 판정이면 판정 라벨과 안내 문구만 보인다.
    출처 원문은 이 문자열에 넣지 않는다(화면이 펼치기로 따로 보인다).

    Args:
        answer: RAG 답변.

    Returns:
        str: Markdown 문자열.
    """
    if answer.verdict != Verdict.ANSWERED:
        notice = answer.notice or _DEFAULT_NOTICES[answer.verdict]
        return f"**{verdict_label(answer.verdict)}**\n\n{notice}"

    lines: list[str] = []
    for no, step in enumerate(answer.steps, start=1):
        lines.append(f"{no}. {step.text}")
        if step.citations:
            sources = ", ".join(citation_label(c) for c in step.citations)
            lines.append(f"   - 출처: {sources}")
    return "\n".join(lines)


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
