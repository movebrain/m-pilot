"""evaluate 테스트: 문항 검증, 지표 계산, 인용 재검사, 장애 문항 처리. pipeline·색인은 가짜로 바꾼다."""

import csv
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mpilot.models import (
    Answer,
    AnswerStep,
    Citation,
    CitationKind,
    DependencyError,
    ErrorCode,
    NotFoundError,
    SearchSettings,
    ValidationError,
    Verdict,
)
from scripts import evaluate

COLUMNS = ["purpose", "equipment", "code", "question", "expected", "note"]
HOLD = Answer(verdict=Verdict.INSUFFICIENT_EVIDENCE, steps=[], notice="보류")


def answered(ref_id: str = "manual:srm:1:1", notice: str | None = None) -> Answer:
    citation = Citation(kind=CitationKind.MANUAL, ref_id=ref_id, locator="1쪽")
    return Answer(verdict=Verdict.ANSWERED, steps=[AnswerStep(text="조치", citations=[citation])], notice=notice)


def write_questions(path: Path, rows: list[dict[str, str]]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def make_rows(normal_hits: int = 8, holds: int = 4) -> list[dict[str, str]]:
    """목적별로 정상 `normal_hits`건·근거 부족 `holds`건."""
    rows = []
    for purpose in evaluate._PURPOSES:
        question = "" if purpose == "customer_guidance" else "질문입니다"
        rows += [dict(purpose=purpose, equipment="SRM", code="S-104", question=question, expected="answered", note="")
                 for _ in range(normal_hits)]
        rows += [dict(purpose=purpose, equipment="SRM", code="S-104", question=question,
                      expected="insufficient_evidence", note="") for _ in range(holds)]
    return rows


@pytest.fixture
def env(monkeypatch):
    """설정·DB·색인·pipeline을 가짜로 바꾼다. `env.answers`로 문항 순서대로 돌려줄 답을 정한다."""
    error_code = ErrorCode(
        equipment="SRM", code="S-104", name="주행 위치 편차", severity="하", action_owner="고객사 조치",
        cause="엔코더 커넥터", action="커넥터 재체결", manual_id="SRM_SPEC_v1", page=41, section="9.4",
    )
    fake = SimpleNamespace(answers=[], docs={"manual:srm:1:1": "SRM"}, calls=[])

    def next_answer(*args):
        fake.calls.append(args)
        item = fake.answers.pop(0) if fake.answers else fake.default
        if isinstance(item, Exception):
            raise item
        return item

    fake.default = answered()
    config = SimpleNamespace(
        search=SearchSettings(manual_k=3, history_k=3), chat_model_name="chat", embedding_model_name="embed",
    )
    monkeypatch.setattr(evaluate, "load_config", lambda: config)
    monkeypatch.setattr(evaluate.rdb, "fetch_error_codes", lambda equipment: [error_code])
    monkeypatch.setattr(evaluate.pipeline, "run_customer_guidance", next_answer)
    monkeypatch.setattr(evaluate.pipeline, "run_engineer_answer", next_answer)

    def get_document(kind, ref_id):
        if ref_id not in fake.docs:
            raise NotFoundError(ref_id)
        return SimpleNamespace(metadata={"equipment": fake.docs[ref_id]})

    monkeypatch.setattr(evaluate.vectordb, "get_document", get_document)
    return fake


def run(tmp_path, rows):
    return evaluate.run_eval(write_questions(tmp_path / "q.csv", rows), tmp_path / "out")


def test_모두_맞으면_세_지표가_합격선을_넘고_결과_JSON을_남긴다(env, tmp_path):
    env.default = answered()
    # 목적별로 정상 8건 → answered, 근거 부족 4건 → 보류가 나오도록 순서대로 정한다
    env.answers = ([answered()] * 8 + [HOLD] * 4) * 2
    result = run(tmp_path, make_rows())

    for purpose in evaluate._PURPOSES:
        summary = result["summary"][purpose]
        assert summary["rates"] == {"citation_valid": 1.0, "normal_answer": 1.0, "hold_accuracy": 1.0}
        assert all(summary["passed"].values())
    saved = json.loads(next((tmp_path / "out").glob("eval_*.json")).read_text(encoding="utf-8"))
    assert set(saved) == {"versions", "settings", "results", "summary"}
    assert saved["settings"] == {"manual_k": 3, "history_k": 3}


def test_정상_문항이_보류되고_근거_부족_문항이_답하면_비율이_낮아진다(env, tmp_path):
    env.answers = ([HOLD] * 2 + [answered()] * 6 + [answered()] * 2 + [HOLD] * 2) * 2
    summary = run(tmp_path, make_rows())["summary"]["engineer_chat"]
    assert summary["rates"]["normal_answer"] == 6 / 8
    assert summary["rates"]["hold_accuracy"] == 2 / 4
    assert summary["passed"]["normal_answer"] is False
    assert summary["passed"]["hold_accuracy"] is False


def test_없는_문서를_인용하면_인용_검증이_실패한다(env, tmp_path):
    env.answers = [answered("manual:없는문서:9:9")] + [answered()] * 7 + [HOLD] * 4 + [answered()] * 8 + [HOLD] * 4
    summary = run(tmp_path, make_rows())["summary"]["customer_guidance"]
    assert summary["rates"]["citation_valid"] == 7 / 8
    assert summary["passed"]["citation_valid"] is False


def test_다른_설비_문서_인용은_폴백이나_라우팅_안내가_있을_때만_통과한다(env, tmp_path):
    env.docs["manual:rtv:1:1"] = "RTV"
    other = answered("manual:rtv:1:1")
    cross = answered("manual:rtv:1:1", notice=evaluate.pipeline.CROSS_EQUIPMENT_NOTICE)
    routed = answered("manual:rtv:1:1", notice=evaluate.pipeline.ROUTED_EQUIPMENT_NOTICE)
    env.answers = [other, cross, routed] + [answered()] * 5 + [HOLD] * 4 + [answered()] * 8 + [HOLD] * 4
    results = run(tmp_path, make_rows())["results"]
    assert [r["citations_ok"] for r in results[:3]] == [False, True, True]


def test_한_문항의_장애는_error로_기록하고_나머지를_계속_실행한다(env, tmp_path):
    env.answers = [DependencyError("모델 장애")] + [answered()] * 7 + [HOLD] * 4 + [answered()] * 8 + [HOLD] * 4
    result = run(tmp_path, make_rows())
    first = result["results"][0]
    assert (first["verdict"], first["citations_ok"]) == ("error", None)
    assert "DependencyError" in first["notice"]
    assert result["summary"]["customer_guidance"]["counts"]["errors"] == 1
    assert result["summary"]["customer_guidance"]["rates"]["normal_answer"] == 7 / 8


def test_엔지니어_문항은_질문을_넘기고_고객_문항은_코드만_넘긴다(env, tmp_path):
    run(tmp_path, make_rows())
    customer_args, engineer_args = env.calls[0], env.calls[12]
    assert len(customer_args) == 2
    assert engineer_args[1].question == "질문입니다"


@pytest.mark.parametrize("rows, message", [
    (make_rows(normal_hits=9, holds=0)[:9] + make_rows()[12:], "10문항"),  # 고객 안내 9문항
    (make_rows(normal_hits=12, holds=0), "두 판정"),  # 근거 부족 문항이 없다
])
def test_목적별_문항_수와_구성이_모자라면_ValidationError(env, tmp_path, rows, message):
    with pytest.raises(ValidationError, match=message):
        run(tmp_path, rows)


def test_열이_다르면_ValidationError(env, tmp_path):
    path = tmp_path / "q.csv"
    path.write_text("purpose,question\ncustomer_guidance,x\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="열"):
        evaluate.run_eval(path, tmp_path / "out")


def test_4종_밖_설비나_없는_에러코드는_ValidationError(env, tmp_path):
    rows = make_rows()
    rows[0]["equipment"] = "지게차"
    with pytest.raises(ValidationError, match="4종 밖"):
        run(tmp_path, rows)

    rows = make_rows()
    rows[0]["code"] = "X-999"
    with pytest.raises(ValidationError, match="에러코드가 없"):
        run(tmp_path, rows)
