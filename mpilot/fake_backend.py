"""가짜 DB·RAG: `--fake` 실행과 화면 개발용 메모리 구현.

역할:
    DB·RAG 파트의 진짜 구현이 오기 전에 `service`가 부르는 하위 함수(`rdb` 등)를 같은 시그니처의
    메모리 구현으로 바꿔 끼운다. `service`는 진짜 호출 코드 그대로 두고, 아래쪽만 가짜가 된다.

책임:
    - 화면 확인용 가짜 데이터(에러코드 표, 신고, 대화, 인용 원문)를 한 곳에 둔다. 모든 데이터는 가상이다.
    - `install()`이 `rdb` 등의 함수를 이 모듈의 가짜 함수로 바꾼다. `app.py --fake`만 부른다.
    - 가짜 함수는 진짜 함수의 docstring 계약(반환 순서, 예외)을 따른다.
    - `service`의 함수가 진짜 호출로 바뀔 때마다, 그 함수가 부르는 하위 함수의 가짜를 여기에 추가한다.
      아직 바뀌지 않은 `service` 함수는 이 모듈의 데이터를 직접 읽는다(교체가 끝나면 그 사용처를 지운다).
    - 테스트에서는 쓰지 않는다. 테스트는 `monkeypatch`로 필요한 함수만 바꾼다.

의존: `models`, `db.rdb`(바꿔 끼울 대상).
소유: UI·총괄 파트
"""

from __future__ import annotations

from datetime import datetime, timezone

from mpilot.db import rdb
from mpilot.models import (
    ActionOwner,
    Answer,
    AnswerStep,
    CaseDetail,
    CaseStatus,
    ChatTurn,
    Citation,
    CitationKind,
    Equipment,
    ErrorCode,
    Resolution,
    Route,
    ServiceCase,
    Severity,
    Source,
    ValidationError,
    Verdict,
)

# ---------------------------------------------------------------------------
# 가짜 데이터. 메모리에만 있으므로 앱을 다시 시작하면 처음 상태로 돌아간다.
# ---------------------------------------------------------------------------


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    """가짜 데이터용 UTC 시각을 만든다."""
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _manual_citation(ref_id: str, locator: str) -> Citation:
    """가짜 매뉴얼 인용을 만든다."""
    return Citation(kind=CitationKind.MANUAL, ref_id=ref_id, locator=locator)


ERROR_CODES: list[ErrorCode] = [
    ErrorCode(
        equipment=Equipment.MINI_LOADER, code="E-101", name="포크 위치 센서 오감지",
        severity=Severity.LOW, action_owner=ActionOwner.CUSTOMER,
        cause="센서 표면 오염", action="센서 표면 청소 후 재기동",
        manual_id="mini_loader_rev1_1", page=12, section="3.2 센서 점검",
        customer_procedure_ref_ids=["manual:mini_loader_rev1_1:12:1"],
        required_safety_rules=["비상정지 버튼을 누른 뒤 작업한다"],
    ),
    ErrorCode(
        equipment=Equipment.MINI_LOADER, code="E-205", name="주행 모터 과전류",
        severity=Severity.HIGH, action_owner=ActionOwner.ENGINEER,
        cause="모터 구속 또는 드라이버 이상", action="엔지니어 점검",
        manual_id="mini_loader_rev1_1", page=30, section="5.1 구동부",
    ),
    ErrorCode(
        equipment=Equipment.SRM, code="S-310", name="리프트 엔코더 편차",
        severity=Severity.MEDIUM, action_owner=ActionOwner.ENGINEER,
        cause="엔코더 커플링 풀림", action="커플링 체결 확인",
        manual_id="srm_rev1_2", page=41, section="6.3 승강부",
    ),
    ErrorCode(
        equipment=Equipment.AGV, code="A-120", name="배터리 저전압",
        severity=Severity.LOW, action_owner=ActionOwner.CUSTOMER,
        cause="충전 부족", action="충전 스테이션 복귀 후 충전",
        manual_id="agv_rev1_1", page=8, section="2.4 배터리",
        customer_procedure_ref_ids=["manual:agv_rev1_1:8:2"],
        required_safety_rules=["충전 중에는 차량을 이동하지 않는다"],
    ),
    ErrorCode(
        equipment=Equipment.RTV, code="R-402", name="도어 인터락 해제",
        severity=Severity.MEDIUM, action_owner=ActionOwner.CUSTOMER,
        cause="안전 도어 열림", action="도어 닫힘 확인",
        manual_id="rtv_rev2_1", page=15, section="4.1 안전장치",
    ),
]

CITATION_TEXTS: dict[str, str] = {
    "manual:mini_loader_rev1_1:12:1": "[가상 원문] 3.2 센서 점검: 비상정지 버튼을 누른 뒤 마른 천으로 센서 표면을 닦는다.",
    "manual:agv_rev1_1:8:2": "[가상 원문] 2.4 배터리: 충전 스테이션으로 복귀시켜 충전한다. 충전 중에는 차량을 이동하지 않는다.",
    "history:SEED-0001": "[가상 원문] SEED-0001 / SRM S-310: 엔코더 커플링 풀림 → 재체결 → 정상 동작 확인.",
}


def answered(text: str, ref_id: str, locator: str) -> Answer:
    """단계 하나짜리 가짜 답변을 만든다."""
    return Answer(
        verdict=Verdict.ANSWERED,
        steps=[AnswerStep(text=text, citations=[_manual_citation(ref_id, locator)])],
        notice=None,
    )


def find_code(equipment: Equipment, code: str) -> ErrorCode:
    """가짜 에러코드 표에서 `(설비, 코드)` 행을 찾는다.

    Raises:
        ValidationError: 표에 없는 코드일 때.
    """
    for row in ERROR_CODES:
        if row.equipment == equipment and row.code == code:
            return row
    raise ValidationError("미등록 코드")


def make_case(
    case_id: str, equipment: Equipment, code: str, *, source: Source, status: CaseStatus, route: Route,
    created_at: datetime, memo: str | None = None, guidance_answer: Answer | None = None,
    completed_at: datetime | None = None, resolution: Resolution | None = None,
) -> CaseDetail:
    """가짜 신고 한 건을 `CaseDetail`로 만든다."""
    return CaseDetail(
        service_case=ServiceCase(
            case_id=case_id, equipment=equipment, code=code, memo=memo, source=source,
            status=status, route=route, guide_failure=None, guidance_answer=guidance_answer,
            created_at=created_at, completed_at=completed_at,
        ),
        error_code=find_code(equipment, code),
        resolution=resolution,
    )


# 접수번호 → 신고. 가짜 함수들이 이 딕셔너리를 읽고 고친다.
CASES: dict[str, CaseDetail] = {
    detail.service_case.case_id: detail
    for detail in [
        make_case(
            "SC-1a2b3c4d", Equipment.MINI_LOADER, "E-101", source=Source.REPORTED,
            status=CaseStatus.COMPLETED, route=Route.SELF_GUIDE, created_at=_utc(2026, 10, 25, 1),
            memo="포크가 가끔 멈춤",
            guidance_answer=answered(
                "비상정지 버튼을 누른 뒤 센서 표면을 마른 천으로 닦는다.",
                "manual:mini_loader_rev1_1:12:1", "p.12 · 3.2 센서 점검",
            ),
            completed_at=_utc(2026, 10, 25, 1, 1),
        ),
        make_case(
            "SC-5e6f7a8b", Equipment.MINI_LOADER, "E-205", source=Source.REPORTED,
            status=CaseStatus.RECEIVED, route=Route.SUPPORT, created_at=_utc(2026, 10, 26, 0, 30),
            memo="주행 중 정지 후 재기동 안 됨",
        ),
        make_case(
            "SC-9c0d1e2f", Equipment.SRM, "S-310", source=Source.REPORTED,
            status=CaseStatus.COMPLETED, route=Route.SUPPORT, created_at=_utc(2026, 10, 24, 5),
            completed_at=_utc(2026, 10, 24, 9),
            resolution=Resolution(cause="엔코더 커플링 풀림", action="커플링 재체결", result="정상 동작 확인"),
        ),
        make_case(
            "SEED-0001", Equipment.SRM, "S-310", source=Source.SEED,
            status=CaseStatus.COMPLETED, route=Route.SUPPORT, created_at=_utc(2026, 9, 1, 2),
            completed_at=_utc(2026, 9, 1, 6),
            resolution=Resolution(cause="엔코더 커플링 풀림", action="커플링 재체결", result="정상 동작 확인"),
        ),
    ]
}

# 접수번호 → 대화 목록.
TURNS: dict[str, list[ChatTurn]] = {}


# ---------------------------------------------------------------------------
# 가짜 하위 함수. 진짜 함수와 시그니처·반환 순서가 같다.
# ---------------------------------------------------------------------------


def fetch_cases(status: CaseStatus | None = None, source: Source | None = None) -> list[CaseDetail]:
    """`rdb.fetch_cases`의 가짜. 조건에 맞는 신고를 최신순으로 돌려준다.

    Args:
        status: 지정하면 그 상태의 신고만. `None`이면 모든 상태.
        source: 지정하면 그 출처의 신고만. `None`이면 모두.

    Returns:
        list[CaseDetail]: `created_at` 내림차순 신고 목록.
    """
    details = [
        d for d in CASES.values()
        if (status is None or d.service_case.status == status)
        and (source is None or d.service_case.source == source)
    ]
    return sorted(details, key=lambda d: d.service_case.created_at, reverse=True)


def install() -> None:
    """`rdb` 등의 함수를 이 모듈의 가짜 함수로 바꿔 끼운다. `app.py --fake`에서 한 번 부른다.

    Returns:
        None: 이후 `service`가 부르는 하위 함수가 메모리 구현으로 동작한다.
    """
    rdb.fetch_cases = fetch_cases
