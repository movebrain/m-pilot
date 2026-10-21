"""spec 6장 계약 테스트: 함수 시그니처(인자 이름·순서·타입, 반환 타입)와 모델 필드(이름·타입)가 바뀌지 않았는지 검사한다.
소유: 서비스 이력.

각 파트는 이 계약 위에서 병렬로 작업한다. 스텁을 실제 구현으로 덮어쓸 때 계약을 깨면 여기서 실패한다.
동작은 각 파트의 AC 테스트가 검사한다.
계약을 바꾸려면 spec.md 6장을 먼저 고치고 이 표를 같이 고친다. 이 파일을 바꾼 PR에는 `계약 변경` 라벨을 붙인다.
타입은 문자열이 아니라 객체로 비교하므로 `from __future__ import annotations`를 써도 결과가 같다.
"""
import importlib
import inspect
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, get_type_hints

import pytest
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel

from answer import Answer, CaseBrief, Citation, Step
from db import Branch, Event, ServiceCase, Status
from errorcodes import Equipment, ErrorCode
from history import ResolutionDraft
from indexing import Collection, Hit
from parsing import Chunk
from search import Evidence, QueryAnalysis, Verdict

Pairs = list[tuple[str, str]]  # (설비, 에러코드) 쌍 목록

# 함수 → ({인자: 타입}을 선언 순서대로, 반환 타입)
FUNCTIONS = {
    "config.get_llm": ({}, BaseChatModel),
    "config.get_embeddings": ({}, Embeddings),
    "errorcodes.load_error_codes": ({"path": Path}, list[ErrorCode]),
    "errorcodes.equipment_list": ({"codes": list[ErrorCode]}, list[str]),
    "parsing.parse_body": ({"dir": Path}, list[Chunk]),
    "indexing.build_index": ({"reset": bool}, None),
    "indexing.add_history": ({"case": ServiceCase}, None),
    "indexing.query": ({"collection": Collection, "text": str, "equipment": str, "k": int}, list[Hit]),
    "indexing.get_source": ({"ref_id": str}, str),
    "db.create_case": ({"errors": Pairs, "memo": str | None, "photo": str | None}, ServiceCase),
    "db.get_case": ({"case_id": str}, ServiceCase | None),
    "db.list_cases": ({}, list[ServiceCase]),
    "db.update_case": ({"case_id": str, "fields": Any}, ServiceCase),
    "db.append_message": ({"case_id": str, "role": str, "content": str}, None),
    "db.get_messages": ({"case_id": str}, list[dict[str, str]]),
    "db.load_seed": ({"path": Path}, int),
    "db.transition": ({"case_id": str, "event": Event}, ServiceCase),
    "search.analyze_query": ({"text": str, "default_equipment": str | None, "default_code": str | None},
                             QueryAnalysis),
    "search.lookup_error_code": ({"equipment": str, "code": str}, ErrorCode | None),
    "search.retrieve": ({"analysis": QueryAnalysis, "k_manual": int, "k_history": int}, Evidence),
    "search.decide_verdict": ({"analysis": QueryAnalysis, "evidence": Evidence | None}, Verdict),
    "answer.brief": ({"case": ServiceCase}, CaseBrief),
    "answer.ask": ({"case_id": str, "question": str}, Answer),
    "routing.route": ({"pairs": Pairs}, Literal["support", "guide"]),
    "routing.guide_for": ({"errors": Pairs}, list[str] | None),
    "history.structure_resolution": ({"case_id": str, "freeform": str, "draft": ResolutionDraft | None},
                                     ResolutionDraft),
    "history.confirm_case": ({"case_id": str, "fields": ResolutionDraft, "diverged": bool}, ServiceCase),
    "evaluate.run_eval": ({"questions_csv": Path}, dict[str, float]),
}

# 모델 → {필드: 타입}. 파트 사이에 오가는 데이터라 함수 시그니처와 같은 계약이다.
MODELS = {
    ErrorCode: {
        "equipment": Equipment, "code": str, "name": str, "severity": Literal["상", "중", "하"],
        "action_owner": Literal["고객사 조치", "엔지니어 필요"], "cause": str, "action": str,
        "extra": dict[str, str],
    },
    Chunk: {"equipment": Equipment, "section": str, "page": int, "text": str},
    Hit: {"ref_id": str, "text": str, "distance": float, "metadata": dict[str, str | int | float]},
    ServiceCase: {
        "case_id": str, "company_id": str, "errors": Pairs, "memo": str | None, "photo_path": str | None,
        "status": Status, "branch": Branch, "summary": str | None, "proposal": dict | None,
        "cause_found": str | None, "engineer_action": str | None,
        "resolution": Literal["해결", "미해결"] | None, "resolution_note": str | None, "actor": str | None,
        "diverged": bool | None, "source": Literal["authored", "engineer"], "confirmed": bool,
        "created_at": datetime, "updated_at": datetime,
    },
    QueryAnalysis: {
        "equipment": Equipment | Literal["other"], "code": str | None, "code_in_question": bool,
        "symptom": str | None,
    },
    Evidence: {
        "exact_row": ErrorCode | None, "manual_hits": list[Hit], "history_hits": list[Hit], "has_evidence": bool,
    },
    Citation: {"kind": Literal["manual", "history"], "ref_id": str, "locator": str},
    Step: {"text": str, "citations": list[Citation]},
    Answer: {"steps": list[Step], "verdict": Verdict, "notice": str | None},
    CaseBrief: {"summary": str, "proposal": Answer},
    ResolutionDraft: {"cause_found": str, "engineer_action": str, "resolution": Literal["해결", "미해결"]},
}

MODULES = sorted({name.split(".")[0] for name in FUNCTIONS})
UI_MODULES = ["ui.customer", "ui.engineer", "ui.history_editor"]


def _none_type(t):
    return type(None) if t is None else t


@pytest.mark.parametrize("name", FUNCTIONS)
def test_function_signature(name):
    module, func = name.rsplit(".", 1)
    fn = getattr(importlib.import_module(module), func)
    params, ret = FUNCTIONS[name]
    hints = get_type_hints(fn)
    assert list(inspect.signature(fn).parameters) == list(params), "인자 이름·순서가 계약과 다르다"
    assert {p: hints.get(p) for p in params} == params, "인자 타입이 계약과 다르다"
    assert hints.get("return") == _none_type(ret), "반환 타입이 계약과 다르다"


@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.__name__)
def test_model_fields(model):
    assert {name: f.annotation for name, f in model.model_fields.items()} == MODELS[model]


@pytest.mark.parametrize("module", MODULES)
def test_public_models_are_in_contract(module):
    """모듈에 공개 모델을 새로 만들면 MODELS에도 올려야 계약 검사 대상이 된다."""
    mod = importlib.import_module(module)
    models = {obj for name, obj in vars(mod).items()
              if not name.startswith("_") and inspect.isclass(obj) and issubclass(obj, BaseModel)
              and obj.__module__ == module}
    missing = sorted(m.__name__ for m in models - MODELS.keys())
    assert not missing, f"MODELS에 없는 모델: {missing}"


@pytest.mark.parametrize("module", UI_MODULES)
def test_ui_build(module):
    pytest.importorskip("gradio")
    fn = importlib.import_module(module).build
    assert list(inspect.signature(fn).parameters) == []
