"""평가 스크립트 테스트: 문항 검증, 결과 분류, 합격 판정, 평가 미완료, 결과 파일 기록.

pipeline·DB·설정을 가짜로 바꿔 모델과 DB 없이 지표 계산만 시험한다. 엔지니어 대화와 고객 안내를 모두 다룬다.
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
GOLD2 = "manual:SRM_SPEC_v1:41:2"
OTHER = "manual:SRM_SPEC_v1:50:2"

ENG = evaluate.ENGINEER
CUS = evaluate.CUSTOMER

SETTINGS = SearchSettings(manual_k=3, history_k=3)


def make_code(equipment: str, code: str, severity: str = "하") -> ErrorCode:
    return ErrorCode(
        equipment=equipment, code=code, name="주행 위치 편차", severity=severity,
        action_owner="고객사 조치", cause="엔코더 커넥터", action="커넥터 재체결",
        manual_id="SRM_SPEC_v1", page=41, section="9.4",
    )


def answered(*ref_ids: str) -> Answer:
    citations = [Citation(kind=CitationKind.MANUAL, ref_id=r, locator="41쪽") for r in ref_ids]
    return Answer(verdict=Verdict.ANSWERED, steps=[AnswerStep(text="조치", citations=citations)], notice=None)


def held() -> Answer:
    return Answer(verdict=Verdict.INSUFFICIENT_EVIDENCE, steps=[], notice="근거가 부족합니다.")


# 시험이 state를 따로 주지 않을 때 쓰는 기본 상태(목적, expected → state)
DEFAULT_STATE = {
    (ENG, "answered"): "own_code",
    (ENG, "insufficient_evidence"): "none_code",
    (CUS, "answered"): "guided",
    (CUS, "insufficient_evidence"): "not_guided",
}


def write_csv(path, rows, header=None):
    """rows: (설비, 코드, 질문, expected, gold 목록[, 목적[, state]]) 튜플의 목록. 목적을 생략하면 엔지니어 대화다.

    state를 생략하면 목적과 expected에 맞는 기본 상태(`DEFAULT_STATE`)를 쓴다.
    고객 안내 행의 질문 자리는 신고 증상 메모다.
    """
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header or evaluate._COLUMNS)
        for equipment, code, question, expected, gold, *rest in rows:
            purpose = rest[0] if rest else ENG
            # purpose·expected가 틀린 행도 그 값의 오류가 먼저 나도록 state는 채워 둔다.
            state = rest[1] if len(rest) > 1 else DEFAULT_STATE.get((purpose, expected), "own_code")
            writer.writerow(
                [
                    purpose,
                    equipment,
                    code,
                    question,
                    expected,
                    state,
                    gold if isinstance(gold, str) else json.dumps(gold),
                ]
            )
    return path


@pytest.fixture
def behavior():
    """질문(고객 안내는 증상 메모) 글자 → 가짜 pipeline이 돌려줄 `Answer`(또는 던질 예외). 시험이 채운다."""
    return {}


@pytest.fixture
def traits():
    """가짜 에러코드 표 행의 심각도. 시험이 `"상"`으로 바꾸면 셀프 가이드 후보가 아니다."""
    return {"severity": "하"}


@pytest.fixture(autouse=True)
def fakes(monkeypatch, behavior, traits):
    monkeypatch.setattr(
        evaluate, "load_config",
        lambda: SimpleNamespace(chat_model_name="chat-x", embedding_model_name="embed-x", search=SETTINGS),
    )
    monkeypatch.setattr(
        evaluate.rdb, "fetch_error_codes",
        lambda equipment: [make_code(equipment.value, CODE, traits["severity"])],
    )

    def respond(key):
        result = behavior[key]
        if isinstance(result, Exception):
            raise result
        return result

    def fake_engineer(context, request, settings):
        assert settings == SETTINGS  # 설정은 load_config의 값을 그대로 넘긴다
        return respond(request.question)

    def fake_customer(context, settings):
        assert settings == SETTINGS
        return respond(context.memo)

    monkeypatch.setattr(evaluate.pipeline, "run_engineer_answer", fake_engineer)
    monkeypatch.setattr(evaluate.pipeline, "run_customer_guidance", fake_customer)


def purpose_rows(behavior, purpose, n_answerable, n_unanswerable):
    """한 목적의 모두 맞게 답하는 문항 행. `behavior`를 채운다. 질문 글자는 목적마다 다르다."""
    prefix = "" if purpose == ENG else "고객"
    rows = []
    for i in range(n_answerable):
        rows.append(("SRM", CODE, f"{prefix}가능{i}", "answered", [GOLD], purpose))
        behavior[f"{prefix}가능{i}"] = answered(GOLD)
    for i in range(n_unanswerable):
        rows.append(("SRM", CODE, f"{prefix}불가{i}", "insufficient_evidence", [], purpose))
        behavior[f"{prefix}불가{i}"] = held()
    return rows


def full_set(behavior, engineer=(30, 20), customer=(30, 20)):
    """두 목적 모두 맞게 답하는 문항 세트. 문항 행 목록을 돌려주고 `behavior`를 채운다."""
    return purpose_rows(behavior, ENG, *engineer) + purpose_rows(behavior, CUS, *customer)


def run(tmp_path, rows):
    return evaluate.run_eval(write_csv(tmp_path / "q.csv", rows), tmp_path / "out")


# --- 결과 분류: 엔지니어 대화 -------------------------------------------------


def test_엔지니어_문항_하나씩_결과를_분류하고_건수로_센다(tmp_path, behavior):
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
        "인용섞임": answered(OTHER, GOLD),  # 엔지니어 대화는 gold가 하나라도 있으면 적중이다
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
    assert all(r["purpose"] == ENG for r in result["results"])
    summary = result["summary"][ENG]
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
    def reject(*args):
        raise ValidationError("manual_k는 1 이상이어야 합니다")

    monkeypatch.setattr(evaluate.pipeline, "run_engineer_answer", reject)
    monkeypatch.setattr(evaluate.pipeline, "run_customer_guidance", reject)
    with pytest.raises(ValidationError):
        run(tmp_path, [("SRM", CODE, "q", "answered", [GOLD])])
    with pytest.raises(ValidationError):
        run(tmp_path, [("SRM", CODE, "메모", "answered", [GOLD], CUS)])


# --- 결과 분류: 고객 안내 -----------------------------------------------------


def test_고객_안내_문항은_고객_안내_pipeline으로_실행하고_결과를_분류한다(tmp_path, behavior):
    rows = [
        ("SRM", CODE, "일부만인용", "answered", [GOLD, GOLD2], CUS),
        ("SRM", CODE, "전부인용", "answered", [GOLD, GOLD2], CUS),
        ("SRM", CODE, "정답과섞임", "answered", [GOLD], CUS),
        ("SRM", CODE, "엉뚱", "answered", [GOLD], CUS),
        ("SRM", CODE, "보류됨", "answered", [GOLD], CUS),
        ("SRM", CODE, "가능실패", "answered", [GOLD], CUS),
        ("SRM", CODE, "환각", "insufficient_evidence", [], CUS),
        ("SRM", CODE, "올바른보류", "insufficient_evidence", [], CUS),
    ]
    behavior.update({
        "일부만인용": answered(GOLD),
        "전부인용": answered(GOLD, GOLD2),
        "정답과섞임": answered(GOLD, OTHER),  # 정답이 있어도 정답 밖 인용이 섞이면 엉뚱한 출처다
        "엉뚱": answered(OTHER),
        "보류됨": held(),
        "가능실패": DependencyError("모델 장애"),
        "환각": answered(GOLD),
        "올바른보류": held(),
    })

    result = run(tmp_path, rows)

    outcomes = {r["question"]: r["outcome"] for r in result["results"]}
    assert outcomes == {
        "일부만인용": "hit", "전부인용": "hit", "정답과섞임": "wrong_source", "엉뚱": "wrong_source",
        "보류됨": "over_hold", "가능실패": "generation_failed", "환각": "hallucination",
        "올바른보류": "correct_hold",
    }
    assert all(r["purpose"] == CUS for r in result["results"])
    summary = result["summary"][CUS]
    assert summary["counts"] == {"answerable": 6, "unanswerable": 2}
    assert summary["hit"]["count"] == 2 and summary["wrong_source"]["count"] == 2
    assert summary["over_hold"]["count"] == 1 and summary["hallucination"]["count"] == 1
    assert summary["generation_failed"] == {"answerable": 1, "unanswerable": 0}
    assert result["summary"][ENG]["counts"] == {"answerable": 0, "unanswerable": 0}


def test_같은_인용이라도_고객_안내만_정답_밖_인용을_엉뚱한_출처로_센다(tmp_path, behavior):
    rows = [
        ("SRM", CODE, "엔지니어", "answered", [GOLD], ENG),
        ("SRM", CODE, "고객", "answered", [GOLD], CUS),
    ]
    behavior.update({"엔지니어": answered(GOLD, OTHER), "고객": answered(GOLD, OTHER)})

    outcomes = {r["purpose"]: r["outcome"] for r in run(tmp_path, rows)["results"]}

    assert outcomes == {ENG: "hit", CUS: "wrong_source"}


def test_고객_안내_문항의_질문은_증상_메모로_넘기고_비우면_None이다(tmp_path, behavior, monkeypatch):
    memos = []

    def spy(context, settings):
        memos.append(context.memo)
        return answered(GOLD)

    monkeypatch.setattr(evaluate.pipeline, "run_customer_guidance", spy)
    rows = [
        ("SRM", CODE, "모터에서 소리가 난다", "answered", [GOLD], CUS),
        ("SRM", CODE, "", "answered", [GOLD], CUS),
    ]

    run(tmp_path, rows)

    assert memos == ["모터에서 소리가 난다", None]


# --- 문항 상태(state) ------------------------------------------------------


@pytest.mark.parametrize(
    "row, message",
    [
        (("SRM", CODE, "q", "answered", [GOLD], ENG, "guided"), "engineer_chat의 state는"),  # 고객 안내 상태
        (("SRM", CODE, "q", "answered", [GOLD], CUS, "own_code"), "customer_guidance의 state는"),  # 엔지니어 상태
        (("SRM", CODE, "q", "answered", [GOLD], ENG, ""), "빈 값"),
        (("SRM", CODE, "q", "answered", [GOLD], ENG, "none_code"), "expected는 insufficient_evidence"),
        (("SRM", CODE, "q", "insufficient_evidence", [], ENG, "other_code"), "expected는 answered"),
        (("SRM", CODE, "m", "answered", [GOLD], CUS, "not_guided"), "expected는 insufficient_evidence"),
    ],
)
def test_state가_목적에_없거나_expected와_맞지_않으면_ValidationError(tmp_path, row, message):
    with pytest.raises(ValidationError, match=f"1번째 행.*{message}"):
        run(tmp_path, [row])


def test_상태별로_결과_분류를_세고_폴백_안내가_기대와_어긋난_건수를_센다(tmp_path, behavior):
    fallback = answered(GOLD).model_copy(update={"notice": evaluate.pipeline.CROSS_EQUIPMENT_NOTICE})
    rows = [
        ("SRM", CODE, "신고 설비로 답함", "answered", [GOLD], ENG, "own_code"),
        ("SRM", CODE, "불필요한 폴백", "answered", [GOLD], ENG, "own_common"),
        ("SRM", CODE, "폴백으로 답함", "answered", [GOLD], ENG, "other_code"),
        ("SRM", CODE, "폴백 안내 없음", "answered", [GOLD], ENG, "other_common"),
        ("SRM", CODE, "근거 없음", "insufficient_evidence", [], ENG, "none_common"),
    ]
    behavior.update(
        {"신고 설비로 답함": answered(GOLD), "불필요한 폴백": fallback, "폴백으로 답함": fallback,
         "폴백 안내 없음": answered(GOLD), "근거 없음": held()}
    )

    result = run(tmp_path, rows)

    by_state = result["summary"][ENG]["by_state"]
    assert list(by_state) == ["own_code", "own_common", "other_code", "other_common", "none_common"]
    assert by_state["own_code"] == {"questions": 1, "outcomes": {"hit": 1}, "fallback_mismatch": 0}
    assert by_state["own_common"]["fallback_mismatch"] == 1  # 신고 설비 자료로 답해야 하는데 폴백을 썼다
    assert by_state["other_code"]["fallback_mismatch"] == 0
    assert by_state["other_common"]["fallback_mismatch"] == 1  # 다른 설비 자료로 답했는데 안내가 없다
    assert by_state["none_common"] == {"questions": 1, "outcomes": {"correct_hold": 1}, "fallback_mismatch": None}
    assert [r["fallback_used"] for r in result["results"]] == [False, True, True, False, None]
    assert [r["state"] for r in result["results"]][:2] == ["own_code", "own_common"]


def test_답변_불가_고객_안내_문항은_후보가_아닌_코드도_된다(tmp_path, behavior, traits):
    traits["severity"] = "상"  # 엔지니어 필요 코드를 고객 안내 흐름에 직접 넣어 안전 장치를 시험한다
    behavior["메모"] = held()

    result = run(tmp_path, [("SRM", CODE, "메모", "insufficient_evidence", [], CUS)])

    assert result["results"][0]["outcome"] == "correct_hold"


def test_답변_가능_고객_안내_문항의_코드는_셀프_가이드_후보여야_한다(tmp_path, traits, monkeypatch):
    calls = []
    monkeypatch.setattr(evaluate.pipeline, "run_customer_guidance", lambda *a: calls.append(a))
    traits["severity"] = "상"  # 심각도 상은 셀프 가이드 후보가 아니다

    with pytest.raises(ValidationError, match="1번째 행.*셀프 가이드 후보"):
        run(tmp_path, [("SRM", CODE, "메모", "answered", [GOLD], CUS)])

    assert calls == []


def test_엔지니어_문항의_코드는_후보가_아니어도_된다(tmp_path, behavior, traits):
    traits["severity"] = "상"
    behavior["q"] = answered(GOLD)

    result = run(tmp_path, [("SRM", CODE, "q", "answered", [GOLD])])

    assert result["results"][0]["outcome"] == "hit"


# --- 합격 판정 -------------------------------------------------------------


def test_두_목적이_모두_맞으면_합격이다(tmp_path, behavior):
    summary = run(tmp_path, full_set(behavior))["summary"]

    assert summary["complete"] is True and summary["passed"] is True
    for purpose in (ENG, CUS):
        assert summary[purpose]["passed"] is True
        assert summary[purpose]["hit"]["count"] == 30 and summary[purpose]["hallucination"]["count"] == 0


def test_환각_한도는_목적마다_1건이다(tmp_path, behavior):
    rows = full_set(behavior)
    behavior["불가0"] = answered(GOLD)
    behavior["고객불가0"] = answered(GOLD)
    assert run(tmp_path, rows)["summary"]["passed"] is True  # 목적마다 1건은 허용

    behavior["불가1"] = answered(GOLD)
    summary = run(tmp_path, rows)["summary"]  # 엔지니어 2건
    assert summary["passed"] is False and summary[ENG]["passed"] is False and summary[CUS]["passed"] is True


def test_엉뚱한_출처_한도는_목적마다_1건이다(tmp_path, behavior):
    rows = full_set(behavior)
    behavior["가능0"] = answered(OTHER)
    behavior["고객가능0"] = answered(GOLD, OTHER)  # 고객 안내는 정답과 섞여도 엉뚱한 출처다
    assert run(tmp_path, rows)["summary"]["passed"] is True

    behavior["고객가능1"] = answered(GOLD, OTHER)
    summary = run(tmp_path, rows)["summary"]
    assert summary["passed"] is False and summary[CUS]["wrong_source"]["count"] == 2


def test_정답_적중은_목적마다_21건_이상이어야_한다(tmp_path, behavior):
    rows = full_set(behavior)
    for i in range(9):  # 과잉 보류 9건 → 적중 21건
        behavior[f"가능{i}"] = held()
        behavior[f"고객가능{i}"] = held()
    assert run(tmp_path, rows)["summary"]["passed"] is True

    behavior["고객가능9"] = held()  # 고객 안내 적중 20건
    summary = run(tmp_path, rows)["summary"]
    assert summary[CUS]["hit"]["count"] == 20 and summary[ENG]["hit"]["count"] == 21
    assert summary["passed"] is False


def test_생성_실패는_환각도_적중도_아니다(tmp_path, behavior):
    rows = full_set(behavior)
    behavior["불가0"] = DependencyError("장애")
    behavior["고객불가0"] = DependencyError("장애")
    behavior["고객불가1"] = DependencyError("장애")
    summary = run(tmp_path, rows)["summary"]

    assert summary[ENG]["hallucination"]["count"] == 0 and summary[CUS]["hallucination"]["count"] == 0
    assert summary[ENG]["generation_failed"]["unanswerable"] == 1
    assert summary[CUS]["generation_failed"]["unanswerable"] == 2
    assert summary["passed"] is True


def test_문항이_모자라면_합격_여부를_판정하지_않는다(tmp_path, behavior):
    summary = run(tmp_path, full_set(behavior, engineer=(29, 20)))["summary"]
    assert summary["complete"] is False and summary["passed"] is None
    assert summary[ENG]["passed"] is None and summary[CUS]["passed"] is True

    summary = run(tmp_path, full_set(behavior, customer=(30, 19)))["summary"]
    assert summary["complete"] is False and summary["passed"] is None
    assert summary[CUS]["complete"] is False


def test_한_목적의_문항이_아예_없으면_미완료다(tmp_path, behavior):
    rows = purpose_rows(behavior, ENG, 30, 20)

    summary = run(tmp_path, rows)["summary"]

    assert summary[ENG]["passed"] is True
    assert summary[CUS]["counts"] == {"answerable": 0, "unanswerable": 0}
    assert summary["complete"] is False and summary["passed"] is None


# --- 결과 기록 -------------------------------------------------------------


def test_결과를_JSON_파일로_남기고_반환값과_같다(tmp_path, behavior):
    result = run(tmp_path, full_set(behavior))

    files = list((tmp_path / "out").glob("eval_*.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_text(encoding="utf-8")) == json.loads(json.dumps(result))
    assert set(result) == {"evaluated_at", "versions", "settings", "results", "summary", "caveats"}
    assert set(result["summary"]) == {ENG, CUS, "complete", "passed"}
    assert {r["purpose"] for r in result["results"]} == {ENG, CUS}
    assert result["settings"] == {"manual_k": 3, "history_k": 3}
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
        (("SRM", CODE, "q", "answered", [GOLD], "support"), "1번째 행.*purpose"),
        (("SRM", CODE, "", "answered", [GOLD], ""), "1번째 행.*빈 값"),
        (("SRM", CODE, "", "answered", [], CUS), "1번째 행.*gold_ref_ids가 비어"),
    ],
)
def test_잘못된_문항은_몇_번째_행인지_알려_준다(tmp_path, row, message):
    with pytest.raises(ValidationError, match=message):
        run(tmp_path, [row])


def test_중복_문항은_거부한다(tmp_path):
    row = ("SRM", CODE, "같은 질문", "answered", [GOLD])
    with pytest.raises(ValidationError, match="2번째 행.*이미 있습니다"):
        run(tmp_path, [row, row])


def test_목적이_다르면_같은_설비_코드_질문도_중복이_아니다(tmp_path, behavior):
    behavior["같은 글자"] = answered(GOLD)
    rows = [
        ("SRM", CODE, "같은 글자", "answered", [GOLD], ENG),
        ("SRM", CODE, "같은 글자", "answered", [GOLD], CUS),
    ]

    assert len(run(tmp_path, rows)["results"]) == 2


def test_머리글이_다르면_거부한다(tmp_path):
    path = write_csv(tmp_path / "q.csv", [], header=evaluate._COLUMNS[1:])  # purpose 열이 없다
    with pytest.raises(ValidationError, match="머리글"):
        evaluate.run_eval(path, tmp_path / "out")


def test_문항이_없으면_거부한다(tmp_path):
    path = write_csv(tmp_path / "q.csv", [])
    with pytest.raises(ValidationError, match="문항이 없습니다"):
        evaluate.run_eval(path, tmp_path / "out")


def test_열_개수가_다른_행은_거부한다(tmp_path):
    path = tmp_path / "q.csv"
    path.write_text(
        "purpose,equipment,code,question,expected,state,gold_ref_ids\nengineer_chat,SRM,S-104,q,answered,own_code\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="1번째 행.*열 개수"):
        evaluate.run_eval(path, tmp_path / "out")


def test_문항_오류가_있으면_모델을_한_번도_부르지_않는다(tmp_path, behavior, monkeypatch):
    calls = []
    monkeypatch.setattr(evaluate.pipeline, "run_engineer_answer", lambda *a: calls.append(a))
    monkeypatch.setattr(evaluate.pipeline, "run_customer_guidance", lambda *a: calls.append(a))
    rows = [
        ("SRM", CODE, "정상", "answered", [GOLD]),
        ("SRM", CODE, "정상 메모", "answered", [GOLD], CUS),
        ("SRM", "X-999", "코드 오류", "answered", [GOLD]),
    ]

    with pytest.raises(ValidationError):
        run(tmp_path, rows)

    assert calls == []


# --- 터미널 요약 -----------------------------------------------------------


def test_요약_문구는_합격_불합격_미완료를_구분한다(tmp_path, behavior):
    rows = full_set(behavior)
    ok = evaluate._format_summary(run(tmp_path, rows)["summary"])
    assert "결과: 합격" in ok and "[엔지니어 대화] 합격" in ok and "[고객 안내] 합격" in ok
    assert "정답 적중 30/30건 (최소 21)" in ok

    behavior["고객불가0"] = answered(GOLD)
    behavior["고객불가1"] = answered(GOLD)
    bad = evaluate._format_summary(run(tmp_path, rows)["summary"])
    assert "결과: 불합격" in bad and "[엔지니어 대화] 합격" in bad and "[고객 안내] 불합격" in bad
    assert "환각 2/20건 (한도 1)" in bad

    short = evaluate._format_summary(run(tmp_path, full_set(behavior, engineer=(5, 5), customer=(30, 20)))["summary"])
    assert "결과: 평가 미완료" in short
    assert "[엔지니어 대화] 평가 미완료(답변 가능 5/30, 답변 불가 5/20)" in short
