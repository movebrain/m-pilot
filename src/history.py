"""이력 초안 구조화와 이력 확정. 화면은 ui/history_editor.py가 만든다. 소유: 서비스 이력.
LLM·임베딩은 `config.get_llm()`·`config.get_embeddings()`로만 얻는다.

베이스라인 스텁이다. 담당이 같은 시그니처의 실제 구현으로 덮어쓴다(spec 6장). 스텁의 confirm_case는 저장하지 않는다.
"""
from typing import Literal

from pydantic import BaseModel

from db import ServiceCase, _mock


class ResolutionDraft(BaseModel):
    cause_found: str
    engineer_action: str
    resolution: Literal["해결", "미해결"]


def structure_resolution(case_id: str, freeform: str, draft: ResolutionDraft | None = None) -> ResolutionDraft:
    """세션 기록 초안 + 자유서술 → 원인·조치·결과. 저장하지 않는다(AC-23.1)."""
    return draft or ResolutionDraft(cause_found="모의 원인", engineer_action="모의 조치", resolution="해결")


def confirm_case(case_id: str, fields: ResolutionDraft, diverged: bool) -> ServiceCase:
    """확인된 값 저장 → confirmed=true, add_history, 해결이면 status=완료(AC-23.2, AC-23.3)."""
    status = "완료" if fields.resolution == "해결" else "조치 중"
    return _mock(case_id, **fields.model_dump(), diverged=diverged, confirmed=True, status=status)
