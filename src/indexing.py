"""Chroma manual·history 생성·질의(설비 필터), 인용 원문 조회, 확정 이력 추가. 소유: 벡터 DB·인덱싱.

질문 해석·판정은 하지 않는다. import할 때 Chroma에 접속하지 않는다. LLM·임베딩은 `config.get_llm()`·`config.get_embeddings()`로만 얻는다.
베이스라인 스텁이다. 담당이 같은 시그니처의 실제 구현으로 덮어쓴다(spec 6장). 스텁의 add_history는 저장하지 않는다.
"""
import argparse
from typing import Literal

from pydantic import BaseModel

from db import ServiceCase
from errorcodes import DATA_DIR

CHROMA_DIR = DATA_DIR / "chroma"
Collection = Literal["manual", "history"]


class Hit(BaseModel):
    ref_id: str
    text: str
    distance: float
    metadata: dict[str, str | int | float]


def build_index(reset: bool = False) -> None:
    """errorcodes.csv, 사양서 본문, 이력 시드 → Chroma manual, history(AC-02.1)."""


def add_history(case: ServiceCase) -> None:
    """확정된 접수 건만 history에 추가한다. 복합 건은 쌍마다 문서 1개(AC-03.1)."""


def query(collection: Collection, text: str, equipment: str, k: int = 3) -> list[Hit]:
    """설비 필터를 적용한 벡터 질의(AC-02.2)."""
    return [Hit(ref_id=f"{equipment}/R201", text="모의 히트", distance=0.1, metadata={"equipment": equipment})]


def get_source(ref_id: str) -> str:
    """인용 원문(표 행, 본문, 이력)을 돌려준다(AC-02.2)."""
    return "모의 원문"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Chroma 인덱스 생성")
    parser.add_argument("--reset", action="store_true", help="기존 인덱스를 지우고 시드 상태로 다시 만든다")
    build_index(parser.parse_args().reset)
