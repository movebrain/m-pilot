"""평가 스크립트 테스트: 문항 검증, 결과 분류, 합격 판정, 평가 미완료, 결과 파일 기록.

pipeline·DB·설정을 가짜로 바꿔 모델과 DB 없이 지표 계산만 시험한다.
"""

import csv
import json
from types import SimpleNamespace

import pytest

from mpilot.models import (
    Answer,
    AnswerStep,
    Citation,
    CitationKind,
    DependencyError,
    ErrorCode,
    SearchSettings,
    ValidationError,
    Verdict,
)
from scripts import evaluate

CODE = "S-104"
GOLD = "manual:SRM_SPEC_v1:41:1"
OTHER = "manual:SRM_SPEC_v1:50:2"

SETTINGS = SearchSettings(manual_k=3, history_k=3, evidence_threshold=0.35)


def make_code(equipment: str, code: str) -> ErrorCode:
    return ErrorCode(
        equipment=equipment, code=code, name="주행 위치 편차", severity="하",
        action_owner="고객사 조치", cause="엔코더 커넥터", action="커넥터 재체결",
        manual_id="SRM_SPEC_v1", page=41, section="9.4",
        customer_procedure_ref_ids=[], required_safety_rules=[],
    )


def answered(*ref_ids: str) -> Answer:
    citations = [Citation(kind=CitationKind.MANUAL, ref_id=r, locator="41쪽") for r in ref_ids]
    return Answer(verdict=Verdict.ANSWERED, steps=[AnswerStep(text="조치", citations=citations)], notice=None)


def held() -> Answer:
    return Answer(verdict=Verdict.INSUFFICIENT_EVIDENCE, steps=[], notice="근거가 부족합니다.")


def write_csv(path, rows, header=None):
    """rows: (설비, 코드, 질문, expected, gold 목록) 튜플의 목록."""
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header or evaluate._COLUMNS)
        for equipment, code, question, expected, gold in rows:
            writer.writerow([equipment, code, question, expected, gold if isinstance(gold, str) else json.dumps(gold)])
    return path


@pytest.fixture
def behavior():
    """질문 글자 → 가짜 pipeline이 돌려줄 `Answer`(또는 던질 예외). 비어 있으면 안 되는 질문은 시험이 채운다."""
    return {}


@pytest.fixture(autouse=True)
def fakes(monkeypatch, behavior):
    monkeypatch.setattr(
        evaluate, "load_config",
        lambda: SimpleNamespace(chat_model_name="chat-x", embedding_model_name="embed-x", search=SETTINGS),
    )
    monkeypatch.setattr(evaluate.rdb, "fetch_error_codes", lambda equipment: [make_code(equipment.value, CODE)])

    def fake_pipeline(context, request, settings):
        assert settings == SETTINGS  # 설정은 load_config의 값을 그대로 넘긴다
        result = behavior[request.question]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(evaluate.pipeline, "run_engineer_answer", fake_pipeline)


def full_set(behavior, n_answerable=30, n_unanswerable=20):
    """모두 맞게 답하는 문항 세트. 문항 행 목록을 돌려주고 `behavior`를 채운다."""
    rows = []
    for i in range(n_answerable):
        rows.append(("SRM", CODE, f"가능{i}", "answered", [GOLD]))
        behavior[f"가능{i}"] = answered(GOLD)
    for i in range(n_unanswerable):
        rows.append(("SRM", CODE, f"불가{i}", "insufficient_evidence", []))
        behavior[f"불가{i}"] = held()
    return rows


def run(tmp_path, rows):
    return evaluate.run_eval(write_csv(tmp_path / "q.csv", rows), tmp_path / "out")


# --- 결과 분류 -------------------------------------------------------------


def test_문항_하나씩_결과를_분류하고_건수로_센다(tmp_path, behavior):
    rows = [
        ("SRM", CODE, "정답", "answered", [GOLD]),
        ("SRM", CODE, "인용섞임", "answered", [GOLD]),
        ("SRM", CODE, "엉뚱", "answered", [GOLD]),
        ("SRM", CODE, "보류됨", "answered", [GOLD]),
        ("SRM", CODE, "가능실패", "answered", [GOLD]),
        ("SRM", CODE, "환각", "insufficient_evidence", []),
        ("SRM", CODE, "올바른보류", "insufficient_evidence", []),
        ("SRM", CODE, "불가실패", "insufficient_evidence", []),
    ]
    behavior.update({
        "정답": answered(GOLD),
        "인용섞임": answered(OTHER, GOLD),  # gold가 하나라도 있으면 적중이다
        "엉뚱": answered(OTHER),
        "보류됨": held(),
        "가능실패": DependencyError("모델 장애"),
        "환각": answered(GOLD),
        "올바른보류": held(),
        "불가실패": DependencyError("검색 장애"),
    })

    result = run(tmp_path, rows)

    outcomes = {r["question"]: r["outcome"] for r in result["results"]}
    assert outcomes == {
        "정답": "hit", "인용섞임": "hit", "엉뚱": "wrong_source", "보류됨": "over_hold",
        "가능실패": "generation_failed", "환각": "hallucination", "올바른보류": "correct_hold",
        "불가실패": "generation_failed",
    }
    summary = result["summary"]
    assert summary["counts"] == {"answerable": 5, "unanswerable": 3}
    assert summary["hit"] == {"count": 2, "total": 5, "minimum": 21}
    assert summary["wrong_source"]["count"] == 1
    assert summary["over_hold"]["count"] == 1
    assert summary["hallucination"] == {"count": 1, "total": 3, "limit": 1}
    assert summary["generation_failed"] == {"answerable": 1, "unanswerable": 1}


def test_생성_실패는_메시지를_남기고_다음_문항을_계속한다(tmp_path, behavior):
    rows = [("SRM", CODE, "실패", "answered", [GOLD]), ("SRM", CODE, "정상", "answered", [GOLD])]
    behavior.update({"실패": DependencyError("모델 장애"), "정상": answered(GOLD)})

    results = run(tmp_path, rows)["results"]

    assert results[0]["error"] == "모델 장애" and results[0]["verdict"] is None
    assert results[1]["error"] is None and results[1]["verdict"] == "answered"
    assert results[1]["cited_ref_ids"] == [GOLD]


def test_pipeline이_거부한_검색_설정은_삼키지_않고_올린다(tmp_path, behavior, monkeypatch):
    def reject(context, request, settings):
        raise ValidationError("evidence_threshold가 허용 범위를 벗어났습니다")

    monkeypatch.setattr(evaluate.pipeline, "run_engineer_answer", reject)
    with pytest.raises(ValidationError):
        run(tmp_path, [("SRM", CODE, "q", "answered", [GOLD])])


# --- 합격 판정 -------------------------------------------------------------


def test_모두_맞으면_합격이다(tmp_path, behavior):
    summary = run(tmp_path, full_set(behavior))["summary"]

    assert summary["complete"] is True and summary["passed"] is True
    assert summary["hit"]["count"] == 30 and summary["hallucination"]["count"] == 0


def test_환각_한도는_1건이다(tmp_path, behavior):
    rows = full_set(behavior)
    behavior["불가0"] = answered(GOLD)
    assert run(tmp_path, rows)["summary"]["passed"] is True  # 1건은 허용

    behavior["불가1"] = answered(GOLD)
    assert run(tmp_path, rows)["summary"]["passed"] is False  # 2건은 불합격


def test_엉뚱한_출처_한도는_1건이다(tmp_path, behavior):
    rows = full_set(behavior)
    behavior["가능0"] = answered(OTHER)
    assert run(tmp_path, rows)["summary"]["passed"] is True

    behavior["가능1"] = answered(OTHER)
    assert run(tmp_path, rows)["summary"]["passed"] is False


def test_정답_적중은_21건_이상이어야_한다(tmp_path, behavior):
    rows = full_set(behavior)
    for i in range(9):  # 과잉 보류 9건 → 적중 21건
        behavior[f"가능{i}"] = held()
    assert run(tmp_path, rows)["summary"]["passed"] is True

    behavior["가능9"] = held()  # 적중 20건
    summary = run(tmp_path, rows)["summary"]
    assert summary["hit"]["count"] == 20 and summary["passed"] is False


def test_생성_실패는_환각도_적중도_아니다(tmp_path, behavior):
    rows = full_set(behavior)
    behavior["불가0"] = DependencyError("장애")
    behavior["불가1"] = DependencyError("장애")
    summary = run(tmp_path, rows)["summary"]

    assert summary["hallucination"]["count"] == 0
    assert summary["generation_failed"]["unanswerable"] == 2
    assert summary["passed"] is True


def test_문항이_모자라면_합격_여부를_판정하지_않는다(tmp_path, behavior):
    summary = run(tmp_path, full_set(behavior, n_answerable=29, n_unanswerable=20))["summary"]
    assert summary["complete"] is False and summary["passed"] is None

    summary = run(tmp_path, full_set(behavior, n_answerable=30, n_unanswerable=19))["summary"]
    assert summary["complete"] is False and summary["passed"] is None


# --- 결과 기록 -------------------------------------------------------------


def test_결과를_JSON_파일로_남기고_반환값과_같다(tmp_path, behavior):
    result = run(tmp_path, full_set(behavior))

    files = list((tmp_path / "out").glob("eval_*.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_text(encoding="utf-8")) == json.loads(json.dumps(result))
    assert set(result) == {"evaluated_at", "versions", "settings", "results", "summary", "caveats"}
    assert result["settings"] == {"manual_k": 3, "history_k": 3, "evidence_threshold": 0.35}
    assert result["versions"]["chat_model"] == "chat-x" and result["versions"]["embedding_model"] == "embed-x"
    assert len(result["versions"]["prompts_sha256"]) == 64 and len(result["versions"]["questions_sha256"]) == 64
    assert result["caveats"], "낙관적 수치라는 주의가 결과에 함께 남아야 한다"


def test_결과_폴더에_쓸_수_없으면_DependencyError다(tmp_path, behavior):
    (tmp_path / "out").write_text("파일이라 폴더를 만들 수 없다", encoding="utf-8")
    with pytest.raises(DependencyError):
        run(tmp_path, full_set(behavior))


def test_문항_파일이_없으면_DependencyError다(tmp_path):
    with pytest.raises(DependencyError):
        evaluate.run_eval(tmp_path / "없음.csv", tmp_path / "out")


# --- 문항 검증 -------------------------------------------------------------


@pytest.mark.parametrize(
    "row, message",
    [
        (("SRM", CODE, "q", "answered", []), "1번째 행.*gold_ref_ids가 비어"),
        (("SRM", CODE, "q", "insufficient_evidence", [GOLD]), "1번째 행.*\\[\\] 여야"),
        (("SRM", CODE, "q", "maybe", [GOLD]), "1번째 행.*expected"),
        (("SRM", CODE, "q", "answered", "not json"), "1번째 행.*JSON 배열"),
        (("SRM", CODE, "q", "answered", '{"a": 1}'), "1번째 행.*문자열 배열"),
        (("SRM", CODE, "q", "answered", "[1]"), "1번째 행.*문자열 배열"),
        (("PLC", CODE, "q", "answered", [GOLD]), "1번째 행.*알 수 없는 설비"),
        (("SRM", CODE, "  ", "answered", [GOLD]), "1번째 행.*빈 값"),
        (("SRM", CODE, "가" * 2001, "answered", [GOLD]), "1번째 행.*1~2,000자"),
        (("SRM", "X-999", "q", "answered", [GOLD]), "1번째 행.*에러코드 표에 없는 코드"),
    ],
)
def test_잘못된_문항은_몇_번째_행인지_알려_준다(tmp_path, row, message):
    with pytest.raises(ValidationError, match=message):
        run(tmp_path, [row])


def test_중복_문항은_거부한다(tmp_path):
    row = ("SRM", CODE, "같은 질문", "answered", [GOLD])
    with pytest.raises(ValidationError, match="2번째 행.*이미 있습니다"):
        run(tmp_path, [row, row])


def test_머리글이_다르면_거부한다(tmp_path):
    path = write_csv(tmp_path / "q.csv", [], header=["purpose", *evaluate._COLUMNS])
    with pytest.raises(ValidationError, match="머리글"):
        evaluate.run_eval(path, tmp_path / "out")


def test_문항이_없으면_거부한다(tmp_path):
    path = write_csv(tmp_path / "q.csv", [])
    with pytest.raises(ValidationError, match="문항이 없습니다"):
        evaluate.run_eval(path, tmp_path / "out")


def test_열_개수가_다른_행은_거부한다(tmp_path):
    path = tmp_path / "q.csv"
    path.write_text("equipment,code,question,expected,gold_ref_ids\nSRM,S-104,q,answered\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="1번째 행.*열 개수"):
        evaluate.run_eval(path, tmp_path / "out")


def test_문항_오류가_있으면_모델을_한_번도_부르지_않는다(tmp_path, behavior, monkeypatch):
    calls = []
    monkeypatch.setattr(evaluate.pipeline, "run_engineer_answer", lambda *a: calls.append(a))
    rows = [("SRM", CODE, "정상", "answered", [GOLD]), ("SRM", "X-999", "코드 오류", "answered", [GOLD])]

    with pytest.raises(ValidationError):
        run(tmp_path, rows)

    assert calls == []


# --- 터미널 요약 -----------------------------------------------------------


def test_요약_문구는_합격_불합격_미완료를_구분한다(tmp_path, behavior):
    rows = full_set(behavior)
    ok = evaluate._format_summary(run(tmp_path, rows)["summary"])
    assert "결과: 합격" in ok and "정답 적중 30/30건 (최소 21)" in ok

    behavior["불가0"] = answered(GOLD)
    behavior["불가1"] = answered(GOLD)
    bad = evaluate._format_summary(run(tmp_path, rows)["summary"])
    assert "결과: 불합격" in bad and "환각 2/20건 (한도 1)" in bad

    short = evaluate._format_summary(run(tmp_path, full_set(behavior, 5, 5))["summary"])
    assert "평가 미완료(답변 가능 5/30, 답변 불가 5/20)" in short
