"""다른 파트 모듈, 메모리 db(fake_db), LLM·임베딩(fake_llm)을 모의 데이터로 대체하는 픽스처(spec 6장). 단위 테스트와 CI(API 키 없음)가 쓴다. 소유: 서비스 이력.

monkeypatch는 모듈 속성을 바꾸므로, 다른 모듈의 함수는 `import search` 후 `search.retrieve(...)`처럼
모듈을 거쳐 부른다. `from search import retrieve`로 가져오면 대체되지 않는다.
"""
from datetime import datetime, timezone

import pytest

import answer
import config
import db
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


# spec 4장 상태 전이 표: 이벤트 → 바뀌는 필드
TRANSITIONS = {
    "open_session": {"status": "분석 중"},
    "customer_resolved": {"status": "완료"},
    "customer_unresolved": {"status": "접수", "branch": "support"},
    "start_action": {"status": "조치 중"},
    "confirm_resolved": {"status": "완료"},
    "confirm_unresolved": {"status": "조치 중"},
}


@pytest.fixture
def fake_db(monkeypatch):
    """메모리 dict로 db 공개 함수를 대체한다. 허용되지 않는 전이 거부 같은 검증은 db 담당의 AC 테스트 몫이다."""
    cases: dict[str, db.ServiceCase] = {}
    messages: dict[str, list[dict[str, str]]] = {}

    def now():
        return datetime.now(timezone.utc)

    def create_case(errors, memo=None, photo=None):
        case_id = f"SC-{now():%Y%m%d}-{len(cases) + 1:03d}"
        cases[case_id] = db.ServiceCase(case_id=case_id, errors=errors, memo=memo, photo_path=photo,
                                        created_at=now(), updated_at=now())
        return cases[case_id]

    def update_case(case_id, **fields):
        cases[case_id] = cases[case_id].model_copy(update={**fields, "updated_at": now()})
        return cases[case_id]

    def append_message(case_id, role, content):
        messages.setdefault(case_id, []).append({"role": role, "content": content, "created_at": now().isoformat()})

    fakes = {
        "create_case": create_case,
        "get_case": cases.get,
        "list_cases": lambda: [c for c in cases.values() if c.source != "authored"],
        "update_case": update_case,
        "append_message": append_message,
        "get_messages": lambda case_id: list(messages.get(case_id, [])),
        "transition": lambda case_id, event: update_case(case_id, **TRANSITIONS[event]),
        "load_seed": lambda path=db.SEED_CSV: 0,
    }
    for name, fn in fakes.items():
        monkeypatch.setattr(db, name, fn)
    return cases


@pytest.fixture
def fake_llm(monkeypatch):
    """config.get_llm·get_embeddings를 가짜 모델로 바꾼다. 응답 목록을 돌려주므로 테스트가 원하는 응답을 넣는다."""
    from langchain_core.embeddings import FakeEmbeddings
    from langchain_core.language_models import FakeListChatModel

    responses = ["모의 응답"]
    monkeypatch.setattr(config, "get_llm", lambda: FakeListChatModel(responses=responses))
    monkeypatch.setattr(config, "get_embeddings", lambda: FakeEmbeddings(size=8))
    return responses
