"""RAG 품질 평가 실행·기록 스크립트(`python -m scripts.evaluate`, 수동 실행, CI에서는 돌리지 않는다).

역할:
    `data/eval_questions.csv`의 문항을 `rag.pipeline`으로 실행하고 결과를 JSON 한 개로 남긴다.

책임(spec §2.7):
    - 목적(`customer_guidance`, `engineer_chat`)별로 따로 집계한다. 문항은 목적별 10문항 이상이며
      정상 답변·근거 부족(신고 설비 자료에 근거가 없는 질문)을 모두 포함하고 4종 설비에 나눈다.
      다른 설비·컨베이어를 언급해도 신고 설비 자료에 근거가 있으면 정상 답변 대상이다.
      문항은 데이터를 만든 사람이 아닌 다른 사람이 쓴다. AI가 쓴 초안이면 사람이 검토한다.
    - 지표와 합격선: 인용 검증 100%(공개된 답변의 인용이 모두 확보한 근거에 있고 설비가 일치),
      정상 답변율 80% 이상(초기 목표이며 실측값이 아니다), 보류 정확도 90% 이상.
      정상 문항의 분모가 0이거나 엔지니어 채팅에 근거 부족 문항이 없으면 평가 미완료로 보고 `ValidationError`로 멈춘다.
      고객 안내는 평가 대상 코드 모두에 매뉴얼의 고객 절차가 있어 근거 부족 문항이 없을 수 있으며, 이때 보류 정확도는
      측정 불가(`null`)로 기록한다.
    - 기록 항목: `versions`(모델·프롬프트·문항 파일 버전), `settings`(검색 설정), `results`(질문별 판정·인용),
      `summary`(목적별 지표). 합격선을 바꾸면 이유를 평가 결과에 남긴다.
    - 인용 검증은 이 스크립트가 다시 검사한다. 색인에서 인용 문서를 다시 읽어 있는지와 설비가 신고 설비인지를
      본다(폴백으로 답한 건은 설비 일치를 제외). `locator` 일치는 `generator.passes_check`가 실행 때마다 막는다.
      고객 안내의 안전 수칙 준수는 자동 검증하지 않아 지표로 세지 않는다.
    - 한 문항의 모델·검색 장애는 그 문항의 판정을 `error`로 기록하고 나머지를 계속 실행한다.
    - 사람 검수(생성된 고객 안내 전부, 엔지니어 `answered` 답변 최소 5건)는 이 스크립트 밖에서 한다.

의존: `config`, `db.rdb`, `db.vectordb`, `models`, `rag.pipeline`, `app`(`initialize`로 색인·모델을 준비)을 쓴다.
소유: RAG 파트
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from mpilot.config import load_config
from mpilot.db import rdb, vectordb
from mpilot.models import (
    CaseContext,
    ChatRequest,
    CitationKind,
    Equipment,
    MpilotError,
    NotFoundError,
    ValidationError,
    Verdict,
)
from mpilot.rag import pipeline

_ROOT = Path(__file__).resolve().parent.parent
_PURPOSES = ("customer_guidance", "engineer_chat")
_COLUMNS = ("purpose", "equipment", "code", "question", "expected", "note")
_MIN_QUESTIONS = 10  # 목적별 최소 문항 수
# 목적별로 문항에 꼭 들어 있어야 하는 기대 판정. 고객 안내는 평가 대상 코드 전부에 매뉴얼의 고객 절차가 있어
# 근거 부족 문항을 만들 수 없으므로 정상 답변만 요구한다(보류 정확도는 측정 불가).
_REQUIRED = {
    "customer_guidance": {Verdict.ANSWERED},
    "engineer_chat": {Verdict.ANSWERED, Verdict.INSUFFICIENT_EVIDENCE},
}
_PASS_LINES = {"citation_valid": 1.0, "normal_answer": 0.8, "hold_accuracy": 0.9}


def run_eval(questions_path: Path, output_dir: Path) -> dict[str, object]:
    """평가 문항을 실행하고 결과를 기록한다.

    Args:
        questions_path: 평가 문항 CSV 경로(`data/eval_questions.csv`). 열은 `purpose,equipment,code,question,expected,note`.
        output_dir: 결과 JSON을 쓸 폴더.

    Returns:
        dict[str, object]: `versions`, `settings`, `results`, `summary` 키를 가진 평가 결과 한 개.
            같은 내용이 `output_dir`에 JSON으로 저장된다.

    Raises:
        ValidationError: 문항 파일의 형식·필수 열이 틀렸거나 목적별 문항 수·구성이 기준에 못 미칠 때.
        DependencyError: 모델·검색·DB 장애나 파일 쓰기 실패일 때.
    """
    rows = _read_questions(questions_path)
    config = load_config()
    results = [_run_row(row, config.search) for row in rows]
    summary = {purpose: _summarize([r for r in results if r["purpose"] == purpose]) for purpose in _PURPOSES}
    evaluation = {
        "versions": {
            "chat_model": config.chat_model_name,
            "embedding_model": config.embedding_model_name,
            "prompts_sha256": hashlib.sha256((_ROOT / "mpilot" / "rag" / "prompts.py").read_bytes()).hexdigest(),
            "questions_sha256": hashlib.sha256(questions_path.read_bytes()).hexdigest(),
        },
        "settings": config.search.model_dump(),
        "results": results,
        "summary": summary,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    (output_dir / f"eval_{stamp}.json").write_text(
        json.dumps(evaluation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return evaluation


def _read_questions(path: Path) -> list[dict[str, str]]:
    """문항 CSV를 읽고 형식·구성을 검사한다."""
    try:
        with path.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            if tuple(reader.fieldnames or ()) != _COLUMNS:
                raise ValidationError(f"문항 파일의 열은 {','.join(_COLUMNS)}이어야 합니다.")
            rows = list(reader)
    except OSError as e:
        raise ValidationError(f"문항 파일을 읽을 수 없습니다: {path}") from e
    for row in rows:
        if row["purpose"] not in _PURPOSES or row["expected"] not in (Verdict.ANSWERED, Verdict.INSUFFICIENT_EVIDENCE):
            raise ValidationError(f"문항의 purpose 또는 expected 값이 틀렸습니다: {row}")
        if row["equipment"] not in {e.value for e in Equipment}:
            raise ValidationError(f"문항의 설비가 4종 밖입니다: {row}")
        if row["purpose"] == "engineer_chat" and not row["question"].strip():
            raise ValidationError(f"엔지니어 문항에 질문이 없습니다: {row}")
    for purpose in _PURPOSES:
        expected = [r["expected"] for r in rows if r["purpose"] == purpose]
        if len(expected) < _MIN_QUESTIONS or not _REQUIRED[purpose] <= set(expected):
            raise ValidationError(
                f"{purpose}는 {_MIN_QUESTIONS}문항 이상이고 기대 판정 {sorted(_REQUIRED[purpose])}을 모두 포함해야 합니다."
            )
    return rows


def _run_row(row: dict[str, str], settings) -> dict[str, object]:
    """문항 하나를 pipeline으로 실행하고 판정과 인용 재검사 결과를 담는다."""
    equipment = Equipment(row["equipment"])
    error_code = next((c for c in rdb.fetch_error_codes(equipment) if c.code == row["code"]), None)
    if error_code is None:
        raise ValidationError(f"문항의 에러코드가 없습니다: {equipment.value} {row['code']}")
    context = CaseContext(error_code=error_code, memo=None)
    result = {**row, "verdict": "error", "steps": 0, "citations": [], "citations_ok": None, "notice": None}
    try:
        if row["purpose"] == "customer_guidance":
            answer = pipeline.run_customer_guidance(context, settings)
        else:
            answer = pipeline.run_engineer_answer(context, ChatRequest(question=row["question"], turns=[]), settings)
    except MpilotError as e:
        result["notice"] = f"{type(e).__name__}: {e}"
        return result
    citations = [c for step in answer.steps for c in step.citations]
    result.update(verdict=answer.verdict.value, steps=len(answer.steps), notice=answer.notice,
                  citations=[c.ref_id for c in citations])
    if answer.verdict == Verdict.ANSWERED:
        cross = answer.notice == pipeline.CROSS_EQUIPMENT_NOTICE
        result["citations_ok"] = all(_citation_exists(c, equipment, cross) for c in citations)
    return result


def _citation_exists(citation, equipment: Equipment, cross: bool) -> bool:
    """인용 문서가 색인에 있고, 폴백이 아니면 신고 설비의 문서인지 본다."""
    try:
        doc = vectordb.get_document(CitationKind(citation.kind), citation.ref_id)
    except NotFoundError:
        return False
    return cross or doc.metadata.get("equipment") == equipment.value


def _summarize(results: list[dict[str, object]]) -> dict[str, object]:
    """목적 하나의 지표 세 개와 합격 여부를 계산한다. 분모가 없는 지표는 `None`이다."""
    by_expected = defaultdict(list)
    for r in results:
        by_expected[r["expected"]].append(r)
    answered = [r for r in results if r["verdict"] == Verdict.ANSWERED]
    normal, hold = by_expected[Verdict.ANSWERED], by_expected[Verdict.INSUFFICIENT_EVIDENCE]
    rates = {
        "citation_valid": sum(bool(r["citations_ok"]) for r in answered) / len(answered) if answered else None,
        "normal_answer": sum(r["verdict"] == Verdict.ANSWERED for r in normal) / len(normal),
        "hold_accuracy": sum(r["verdict"] == Verdict.INSUFFICIENT_EVIDENCE for r in hold) / len(hold) if hold else None,
    }
    # 측정하지 못한 지표(None)는 합격으로 세지 않고 None으로 남긴다.
    passed = {name: None if rates[name] is None else rates[name] >= line for name, line in _PASS_LINES.items()}
    return {"counts": {"questions": len(results), "answered": len(answered), "errors": sum(r["verdict"] == "error" for r in results)},
            "rates": rates, "passed": passed}


def main() -> int:
    """앱과 같은 설정으로 색인·모델을 준비하고 평가를 실행해 요약을 출력한다.

    Returns:
        int: 평가를 끝까지 실행했으면 `0`, 설정·문항·장애로 중단했으면 `1`. 합격선 통과 여부는 요약에 보이며 종료 코드에 반영하지 않는다.
    """
    import app  # Gradio를 불러오므로 실행할 때만 가져온다

    try:
        app.initialize()
        evaluation = run_eval(_ROOT / "data" / "eval_questions.csv", _ROOT / "data" / "eval_results")
    except MpilotError as e:
        print(f"평가를 실행하지 못했습니다({type(e).__name__}): {e}", file=sys.stderr)
        return 1
    print(json.dumps(evaluation["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
