"""사양서 PDF 본문 → 쪽·절 청크. 표 9-4는 scripts/merge_errorcodes.py가 다룬다. 소유: 벡터 DB·데이터.

베이스라인 스텁이다. 담당이 같은 시그니처의 실제 구현으로 덮어쓴다(spec 6장).
"""
from pathlib import Path

from pydantic import BaseModel

from errorcodes import DATA_DIR, Equipment

MANUALS_DIR = DATA_DIR / "manuals"


class Chunk(BaseModel):
    equipment: Equipment
    section: str
    page: int
    text: str


def parse_body(dir: Path = MANUALS_DIR) -> list[Chunk]:
    """사양서 PDF 4종 → 쪽·절 메타데이터가 붙은 본문 청크(AC-01.3)."""
    return [Chunk(equipment="RTV", section="11.1", page=1, text="모의 본문")]
