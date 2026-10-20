"""분기(route)와 셀프 가이드(guide_for). DB·LLM 없이 에러코드 표만 읽는다. 소유: 고객 접수.

베이스라인 스텁이다. 담당이 같은 시그니처의 실제 구현으로 덮어쓴다(spec 6장). route는 P1 동작(항상 support)이다.
"""
from typing import Literal


def route(pairs: list[tuple[str, str]]) -> Literal["support", "guide"]:
    """쌍 1~4개 → 건 단위 분기. 모든 쌍이 저위험일 때만 guide(AC-11.1, AC-11.2)."""
    return "support"


def guide_for(errors: list[tuple[str, str]]) -> list[str] | None:
    """모든 쌍이 저위험이면 쌍별 action 목록, 하나라도 아니면 None(AC-11.3)."""
    return None
