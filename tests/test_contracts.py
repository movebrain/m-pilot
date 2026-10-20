"""spec 6장 계약 테스트: 함수의 인자 이름·순서와 반환 타입이 바뀌지 않았는지 검사한다. 소유: 서비스 이력.

스텁을 실제 구현으로 덮어쓸 때 계약을 깨면 여기서 실패한다. 동작은 각 파트의 AC 테스트가 검사한다.
계약을 바꾸려면 spec.md 6장을 먼저 고치고 이 표를 같이 고친다.
"""
import importlib
import inspect

import pytest

CONTRACT = {
    "config.get_llm": ([], 'BaseChatModel'),
    "config.get_embeddings": ([], 'Embeddings'),
    "errorcodes.load_error_codes": (['path'], 'list[errorcodes.ErrorCode]'),
    "errorcodes.equipment_list": (['codes'], 'list[str]'),
    "parsing.parse_body": (['dir'], 'list[parsing.Chunk]'),
    "indexing.build_index": (['reset'], 'None'),
    "indexing.add_history": (['case'], 'None'),
    "indexing.query": (['collection', 'text', 'equipment', 'k'], 'list[indexing.Hit]'),
    "indexing.get_source": (['ref_id'], 'str'),
    "db.create_case": (['errors', 'memo', 'photo'], 'db.ServiceCase'),
    "db.get_case": (['case_id'], 'db.ServiceCase | None'),
    "db.list_cases": ([], 'list[db.ServiceCase]'),
    "db.update_case": (['case_id', 'fields'], 'db.ServiceCase'),
    "db.append_message": (['case_id', 'role', 'content'], 'None'),
    "db.get_messages": (['case_id'], 'list[dict[str, str]]'),
    "db.load_seed": (['path'], 'int'),
    "db.transition": (['case_id', 'event'], 'db.ServiceCase'),
    "search.analyze_query": (['text', 'default_equipment', 'default_code'], 'search.QueryAnalysis'),
    "search.lookup_error_code": (['equipment', 'code'], 'errorcodes.ErrorCode | None'),
    "search.retrieve": (['analysis', 'k_manual', 'k_history'], 'search.Evidence'),
    "search.decide_verdict": (['analysis', 'evidence'], "Literal['answered', 'insufficient_evidence', 'not_applicable']"),
    "answer.brief": (['case'], 'answer.CaseBrief'),
    "answer.ask": (['case_id', 'question'], 'answer.Answer'),
    "routing.route": (['pairs'], "Literal['support', 'guide']"),
    "routing.guide_for": (['errors'], 'list[str] | None'),
    "history.structure_resolution": (['case_id', 'freeform', 'draft'], 'history.ResolutionDraft'),
    "history.confirm_case": (['case_id', 'fields', 'diverged'], 'db.ServiceCase'),
    "evaluate.run_eval": (['questions_csv'], 'dict[str, float]'),
}

UI_MODULES = ["ui.customer", "ui.engineer", "ui.history_editor"]


def _return(fn):
    ret = inspect.signature(fn).return_annotation
    return ret if isinstance(ret, str) else inspect.formatannotation(ret)


@pytest.mark.parametrize("name", CONTRACT)
def test_signature(name):
    module, func = name.rsplit(".", 1)
    fn = getattr(importlib.import_module(module), func)
    params, ret = CONTRACT[name]
    assert list(inspect.signature(fn).parameters) == params
    assert _return(fn) == ret


@pytest.mark.parametrize("module", UI_MODULES)
def test_ui_build(module):
    pytest.importorskip("gradio")
    fn = importlib.import_module(module).build
    assert list(inspect.signature(fn).parameters) == []
