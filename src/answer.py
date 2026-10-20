"""요약·대응 방안(brief), 후속 질문 답변(ask), 인용 검증. 소유: 엔지니어 세션.

검색은 search.retrieve로만, 저장은 db로만 한다.
베이스라인 스텁이다. 담당이 같은 시그니처의 실제 구현으로 덮어쓴다(spec 6장).
"""
from typing import Literal

from pydantic import BaseModel

from db import ServiceCase
from search import Verdict


class Citation(BaseModel):
    kind: Literal["manual", "history"]
    ref_id: str
    locator: str


class Step(BaseModel):
    text: str
    citations: list[Citation]


class Answer(BaseModel):
    steps: list[Step]
    verdict: Verdict
    notice: str | None = None


class CaseBrief(BaseModel):
    summary: str
    proposal: Answer


_MOCK_ANSWER = Answer(
    steps=[Step(text="모의 조치 단계", citations=[Citation(kind="manual", ref_id="RTV/R201", locator="1")])],
    verdict="answered",
)


def brief(case: ServiceCase) -> CaseBrief:
    """접수 건 요약과 대응 방안. 저장하고 이미 있으면 재생성하지 않는다(AC-14.1, AC-14.3)."""
    return CaseBrief(summary="모의 요약", proposal=_MOCK_ANSWER)


def ask(case_id: str, question: str) -> Answer:
    """접수번호별 대화 기억을 반영한 답변. 모든 Step에 Citation이 1개 이상(AC-15.1, AC-15.2)."""
    return _MOCK_ANSWER
