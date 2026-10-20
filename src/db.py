"""접수 건·쌍·대화 저장, 상태 전이, 시드 적재. SQL은 이 파일에만 둔다. 소유: MySQL.

LLM·Chroma를 모르고 answer를 임포트하지 않는다(proposal은 JSON으로 저장).
베이스라인 스텁이다. 담당이 같은 시그니처의 실제 구현으로 덮어쓴다(spec 6장). 스텁은 저장하지 않고 모의 접수 건을 돌려준다.
"""
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from errorcodes import DATA_DIR

Status = Literal["접수", "분석 중", "조치 중", "완료"]
Branch = Literal["support", "guide"]
# spec 4장 상태 전이 표의 이벤트(위에서부터): 세션 열기, 고객 "해결됨", 고객 "해결 안 됨",
# "조치 중"으로 변경, 이력 확정(해결), 이력 확정(미해결)
Event = Literal["open_session", "customer_resolved", "customer_unresolved",
                "start_action", "confirm_resolved", "confirm_unresolved"]

SEED_CSV = DATA_DIR / "history_seed.csv"


class ServiceCase(BaseModel):
    case_id: str
    company_id: str = "COMP-DEMO"
    errors: list[tuple[str, str]]
    memo: str | None = None
    photo_path: str | None = None
    status: Status = "접수"
    branch: Branch = "support"
    summary: str | None = None
    proposal: dict | None = None
    cause_found: str | None = None
    engineer_action: str | None = None
    resolution: Literal["해결", "미해결"] | None = None
    resolution_note: str | None = None
    actor: str | None = None
    diverged: bool | None = None
    source: Literal["authored", "engineer"] = "engineer"
    confirmed: bool = False
    created_at: datetime
    updated_at: datetime


def _mock(case_id: str = "SC-20261021-001", **fields) -> ServiceCase:
    now = datetime.now(timezone.utc)
    base = dict(case_id=case_id, errors=[("RTV", "R201")], created_at=now, updated_at=now)
    return ServiceCase(**(base | fields))


def create_case(errors: list[tuple[str, str]], memo: str | None = None, photo: str | None = None) -> ServiceCase:
    """쌍 1~4개로 접수 건을 만든다. 접수번호 부여, status=접수(AC-04.1)."""
    return _mock(errors=errors, memo=memo, photo_path=photo)


def get_case(case_id: str) -> ServiceCase | None:
    return _mock(case_id)


def list_cases() -> list[ServiceCase]:
    """접수 목록. source=authored 시드는 나오지 않는다(AC-12.1)."""
    return [_mock()]


def update_case(case_id: str, **fields) -> ServiceCase:
    return _mock(case_id, **fields)


def append_message(case_id: str, role: str, content: str) -> None:
    """대화 기록 한 줄을 case_messages에 저장한다."""


def get_messages(case_id: str) -> list[dict[str, str]]:
    """대화 기록(role, content, created_at)을 시간 순으로 돌려준다."""
    return []


def load_seed(path: Path = SEED_CSV) -> int:
    """history_seed.csv → cases(source=authored, confirmed=true). 적재 건수를 돌려준다(AC-05.2)."""
    return 0


def transition(case_id: str, event: Event) -> ServiceCase:
    """spec 4장 상태 전이 표대로만 상태를 바꾼다. 허용되지 않는 전이는 예외(AC-04.2)."""
    return _mock(case_id)
