"""다른 파트 모듈을 모의 데이터로 대체하는 픽스처(spec 6장). 단위 테스트와 CI(API 키 없음)가 쓴다. 소유: 서비스 이력.

monkeypatch는 모듈 속성을 바꾸므로, 다른 모듈의 함수는 `import search` 후 `search.retrieve(...)`처럼
모듈을 거쳐 부른다. `from search import retrieve`로 가져오면 대체되지 않는다.
"""
import pytest

import answer
import history
import indexing
import search
from answer import Answer, CaseBrief, Citation, Step
from history import ResolutionDraft
from search import Evidence, QueryAnalysis

MOCK_ANSWER = Answer(
    steps=[Step(text="모의 조치 단계", citations=[Citation(kind="manual", ref_id="RTV/R201", locator="1")])],
    verdict="answered",
)


@pytest.fixture
def fake_indexing(monkeypatch):
    monkeypatch.setattr(indexing, "query", lambda collection, text, equipment, k=3: [])
    monkeypatch.setattr(indexing, "get_source", lambda ref_id: "모의 원문")
    monkeypatch.setattr(indexing, "add_history", lambda case: None)


@pytest.fixture
def fake_search(monkeypatch):
    monkeypatch.setattr(search, "analyze_query",
                        lambda text, default_equipment=None, default_code=None:
                        QueryAnalysis(equipment="RTV", code="R201", code_in_question=True))
    monkeypatch.setattr(search, "retrieve", lambda analysis, k_manual=3, k_history=3: Evidence(has_evidence=True))


@pytest.fixture
def fake_answer(monkeypatch):
    monkeypatch.setattr(answer, "brief", lambda case: CaseBrief(summary="모의 요약", proposal=MOCK_ANSWER))
    monkeypatch.setattr(answer, "ask", lambda case_id, question: MOCK_ANSWER)


@pytest.fixture
def fake_history(monkeypatch):
    monkeypatch.setattr(history, "structure_resolution",
                        lambda case_id, freeform, draft=None:
                        ResolutionDraft(cause_found="모의 원인", engineer_action="모의 조치", resolution="해결"))
