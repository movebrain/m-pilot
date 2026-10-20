"""에러코드 표(data/errorcodes.csv) 로더·검증과 설비 목록. 소유: 벡터 DB·데이터.

베이스라인 스텁이다. 담당이 같은 시그니처의 실제 구현으로 덮어쓴다(spec 6장).
"""
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

Equipment = Literal["Mini Loader", "SRM", "AGV", "RTV"]

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
ERRORCODES_CSV = DATA_DIR / "errorcodes.csv"


class ErrorCode(BaseModel):
    equipment: Equipment
    code: str
    name: str
    severity: Literal["상", "중", "하"]
    action_owner: Literal["고객사 조치", "엔지니어 필요"]
    cause: str
    action: str
    extra: dict[str, str] = {}


_MOCK = [
    ErrorCode(equipment="RTV", code="R201", name="모의 에러(상)", severity="상",
              action_owner="엔지니어 필요", cause="모의 원인", action="모의 조치"),
    ErrorCode(equipment="SRM", code="S101", name="모의 에러(저위험)", severity="하",
              action_owner="고객사 조치", cause="모의 원인", action="모의 조치"),
]


def load_error_codes(path: Path = ERRORCODES_CSV) -> list[ErrorCode]:
    """CSV → ErrorCode 목록. 심각도·조치 구분이 비었거나 (설비, 코드)가 중복이면 예외(AC-01.1)."""
    return list(_MOCK)


def equipment_list(codes: list[ErrorCode]) -> list[str]:
    """에러코드 표의 equipment 고유값(설비 4종). 질문 분석의 고정 목록 원천이다."""
    return sorted({c.equipment for c in codes})
