"""질문 분석(LLM), 키워드 기반 조회, 매뉴얼·이력 병렬 검색, 판정. 소유: RAG 검색.

판정은 코드가 하고 LLM은 분류만 한다. 답변 문장은 만들지 않는다. LLM·임베딩은 `config.get_llm()`·`config.get_embeddings()`로만 얻는다.
베이스라인 스텁이다. 담당이 같은 시그니처의 실제 구현으로 덮어쓴다(spec 6장).
"""
from typing import Literal

from pydantic import BaseModel

from errorcodes import Equipment, ErrorCode, load_error_codes
from indexing import Hit

Verdict = Literal["answered", "insufficient_evidence", "not_applicable"]


class QueryAnalysis(BaseModel):
    equipment: Equipment | Literal["other"]
    code: str | None = None
    code_in_question: bool = False
    symptom: str | None = None


class Evidence(BaseModel):
    exact_row: ErrorCode | None = None
    manual_hits: list[Hit] = []
    history_hits: list[Hit] = []
    has_evidence: bool


def analyze_query(text: str, default_equipment: str | None = None, default_code: str | None = None) -> QueryAnalysis:
    """질문 → 설비(4종 또는 other), 코드, 증상. 코드 표기 변형은 정규화한다(AC-18.1, AC-21.1)."""
    return QueryAnalysis(equipment=default_equipment or "RTV", code=default_code, symptom=text)


def lookup_error_code(equipment: str, code: str) -> ErrorCode | None:
    """정확히 1행 또는 None. 다른 설비의 같은 이름 에러는 돌려주지 않는다(AC-18.2)."""
    return next((c for c in load_error_codes() if (c.equipment, c.code) == (equipment, code)), None)


def retrieve(analysis: QueryAnalysis, k_manual: int = 3, k_history: int = 3) -> Evidence:
    """키워드 기반 조회 + 매뉴얼·이력 검색을 병렬로, 모두 설비 필터(AC-19.1)."""
    return Evidence(has_evidence=True)


def decide_verdict(analysis: QueryAnalysis, evidence: Evidence | None) -> Verdict:
    """적용 대상 아님 → 근거 부족 순으로 판정한다(spec 7.3)."""
    if analysis.equipment == "other":
        return "not_applicable"
    if evidence is None or not evidence.has_evidence:
        return "insufficient_evidence"
    return "answered"
