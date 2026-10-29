"""RAG 품질 평가 실행·기록 스크립트(수동 실행, CI에서는 돌리지 않는다).

역할:
    `data/eval_questions.csv`의 문항으로 엔지니어 대화(`rag.pipeline.run_engineer_answer`)와 고객 안내
    (`rag.pipeline.run_customer_guidance`)를 실행하고 결과를 JSON 한 개로 남긴다.

책임(spec §2.7):
    - 문항의 `purpose`(`engineer_chat`·`customer_guidance`)별로 따로 집계한다. 고객 안내 문항의 `question`은
      신고 증상 메모이며 비워도 된다. 고객 안내 문항의 코드는 셀프 가이드 후보여야 한다.
    - 문항은 답변 가능(`expected=answered`, 정답 근거 `gold_ref_ids`가 있다)과 답변 불가
      (`expected=insufficient_evidence`, `gold_ref_ids=[]`)로 나뉜다.
    - 지표와 합격선: 환각(답변 불가 문항 중 `answered`) 1건 이하, 엉뚱한 출처(답변 가능 문항 중 `answered`인데
      인용이 정답 근거 기준에 못 미침) 1건 이하, 정답 적중(답변 가능 문항 중 `answered`이고 인용이 정답 근거
      기준을 만족) 21건 이상. 과잉 보류(답변 가능 문항 중 보류)는 합격선이 없다. 정답 적중 21건은 초기 목표이며
      실측값이 아니다.
    - 정답 근거 기준: 엔지니어 대화는 인용에 `gold_ref_ids`가 하나라도 있으면 만족한다. 고객 안내는 고객이
      수행할 수 있는 절차만 담아야 하므로 인용이 모두 `gold_ref_ids` 안에 있어야 만족한다.
    - 목적별로 답변 가능 30개·답변 불가 20개에 못 미치면 합격 여부를 판정하지 않는다(평가 미완료). 전체 합격은
      두 목적이 모두 합격일 때다(`evidence_threshold`를 둘이 공유한다).
    - 생성 실패(`DependencyError`)는 별도로 센다. 답변 가능 문항에서는 정답 적중이 아니고, 답변 불가 문항에서는
      환각이 아니다.
    - 기록 항목: `versions`(모델 이름, 프롬프트·문항 파일 해시), `settings`(검색 설정), `results`(문항별 판정·인용·
      결과 분류), `summary`(건수·합격선·합격 여부), `caveats`(작은 표본 해석 주의).
    - 후보 `evidence_threshold`마다 `.env`의 값을 바꿔 따로 실행한다. 결과 파일이 설정을 함께 남긴다.
    - 사람 검수(생성된 고객 안내 전부, 엔지니어 `answered` 답변 최소 5건)는 이 스크립트 밖에서 한다. 고객 안내의
      안전 수칙 준수는 자동으로 재지 않는다.

실행 전제: 검색 색인이 만들어져 있어야 한다(앱을 한 번 시작하면 `service.prepare_search_index`가 만든다).

의존: `config`, `db.rdb`, `db.vectordb`, `models`, `rag.generator`, `rag.pipeline`을 쓸 수 있다.
소유: RAG 파트
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError as PydanticValidationError

from mpilot.config import load_config
from mpilot.db import rdb, vectordb
from mpilot.models import (
    Answer,
    CaseContext,
    ChatRequest,
    DependencyError,
    Equipment,
    ErrorCode,
    ValidationError,
    Verdict,
    is_self_guide_candidate,
)
from mpilot.rag import generator, pipeline, prompts

_ROOT = Path(__file__).resolve().parents[1]

ENGINEER = "engineer_chat"
CUSTOMER = "customer_guidance"
_PURPOSES = (ENGINEER, CUSTOMER)
_LABELS = {ENGINEER: "엔지니어 대화", CUSTOMER: "고객 안내"}

_COLUMNS = ["purpose", "equipment", "code", "question", "expected", "gold_ref_ids"]

MIN_ANSWERABLE = 30  # 답변 가능 문항 최소 수
MIN_UNANSWERABLE = 20  # 답변 불가 문항 최소 수
MAX_HALLUCINATION = 1  # 환각 허용 건수
MAX_WRONG_SOURCE = 1  # 엉뚱한 출처 허용 건수
MIN_HIT = 21  # 정답 적중 최소 건수(답변 가능 30개의 70%, 초기 목표)

_CAVEATS = [
    "합격선은 이 문항 안의 결정 규칙이며 실제 환각률의 보증이 아니다.",
    "임계값 선택과 합격 판정에 같은 문항을 쓰므로 수치가 실제보다 낙관적이다.",
    "고객 안내는 인용이 모두 정답 근거 안에 있어야 적중이고, 엔지니어 대화는 하나라도 있으면 적중이다.",
]


@dataclass(frozen=True)
class _Question:
    """검증을 통과한 문항 한 줄. `no`는 데이터 행 번호(1부터)다.

    고객 안내 문항의 `question`은 신고 증상 메모이며 비어 있을 수 있다.
    """

    no: int
    purpose: str
    equipment: Equipment
    code: str
    question: str
    expected: Verdict
    gold_ref_ids: tuple[str, ...]


def _parse_gold(no: int, text: str) -> tuple[str, ...]:
    """`gold_ref_ids` 열의 JSON 배열을 문서 ID 목록으로 바꾼다."""
    try:
        gold = json.loads(text)
    except json.JSONDecodeError:
        raise ValidationError(f"{no}번째 행: gold_ref_ids가 JSON 배열이 아닙니다.") from None
    if not isinstance(gold, list) or not all(isinstance(x, str) and x.strip() for x in gold):
        raise ValidationError(f"{no}번째 행: gold_ref_ids는 비어 있지 않은 문자열 배열이어야 합니다.")
    return tuple(x.strip() for x in gold)


def _read_questions(path: Path) -> list[_Question]:
    """문항 CSV를 읽어 형식·값·정합을 검증한다.

    Args:
        path: 문항 CSV 경로.

    Returns:
        list[_Question]: 파일 순서대로의 문항.

    Raises:
        ValidationError: 머리글·열 개수·값이 틀렸거나, 답변 가능 문항에 `gold_ref_ids`가 없거나, 답변 불가
            문항에 `gold_ref_ids`가 있거나, 같은 문항이 중복될 때. 몇 번째 행인지 알려 준다.
        DependencyError: 파일을 읽을 수 없을 때.
    """
    try:
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames
            rows = list(reader)
    except (OSError, UnicodeDecodeError) as e:
        raise DependencyError(f"문항 파일을 읽을 수 없습니다({type(e).__name__}): {path}") from None
    except csv.Error as e:
        raise ValidationError(f"{path.name}: CSV 형식이 올바르지 않습니다: {e}") from None
    if header is None or sorted(header) != sorted(_COLUMNS):
        raise ValidationError(f"{path.name}: 머리글이 {_COLUMNS}와 다릅니다.")
    if not rows:
        raise ValidationError(f"{path.name}: 문항이 없습니다.")

    questions: list[_Question] = []
    seen: set[tuple[str, str, str, str]] = set()
    for no, row in enumerate(rows, start=1):
        if None in row or any(v is None for v in row.values()):
            raise ValidationError(f"{no}번째 행: 열 개수가 머리글과 다릅니다.")
        values = {c: row[c].strip() for c in _COLUMNS}
        purpose = values["purpose"]
        # 고객 안내의 question은 선택 사항인 증상 메모라서 비워도 된다.
        required = [c for c in _COLUMNS if not (c == "question" and purpose == CUSTOMER)]
        if not all(values[c] for c in required):
            raise ValidationError(f"{no}번째 행: 빈 값이 있습니다(정답 근거가 없으면 gold_ref_ids는 [] 입니다).")
        if purpose not in _PURPOSES:
            raise ValidationError(f"{no}번째 행: purpose는 {' 또는 '.join(_PURPOSES)}여야 합니다: {purpose}")
        try:
            equipment = Equipment(values["equipment"])
        except ValueError:
            raise ValidationError(f"{no}번째 행: 알 수 없는 설비입니다: {values['equipment']}") from None
        try:
            expected = Verdict(values["expected"])
        except ValueError:
            raise ValidationError(
                f"{no}번째 행: expected는 answered 또는 insufficient_evidence여야 합니다: {values['expected']}"
            ) from None
        gold = _parse_gold(no, values["gold_ref_ids"])
        if expected == Verdict.ANSWERED and not gold:
            raise ValidationError(f"{no}번째 행: 답변 가능(answered) 문항은 gold_ref_ids가 비어 있으면 안 됩니다.")
        if expected == Verdict.INSUFFICIENT_EVIDENCE and gold:
            raise ValidationError(f"{no}번째 행: 답변 불가(insufficient_evidence) 문항의 gold_ref_ids는 [] 여야 합니다.")
        if purpose == ENGINEER:
            try:
                ChatRequest(question=values["question"], turns=[])
            except PydanticValidationError:
                raise ValidationError(f"{no}번째 행: question은 공백을 뺀 1~2,000자여야 합니다.") from None
        key = (purpose, equipment.value, values["code"], values["question"])
        if key in seen:
            raise ValidationError(f"{no}번째 행: 같은 목적·설비·코드·질문이 이미 있습니다.")
        seen.add(key)
        questions.append(_Question(no, purpose, equipment, values["code"], values["question"], expected, gold))
    return questions


def _find_error_code(question: _Question, cache: dict[Equipment, dict[str, ErrorCode]]) -> ErrorCode:
    """문항의 설비·코드에 해당하는 에러코드 행을 찾는다. 설비별 조회 결과는 `cache`에 담아 둔다."""
    if question.equipment not in cache:
        cache[question.equipment] = {c.code: c for c in rdb.fetch_error_codes(question.equipment)}
    found = cache[question.equipment].get(question.code)
    if found is None:
        raise ValidationError(
            f"{question.no}번째 행: 에러코드 표에 없는 코드입니다: {question.equipment.value} {question.code}"
        )
    if question.purpose == CUSTOMER and not is_self_guide_candidate(found):
        raise ValidationError(
            f"{question.no}번째 행: 고객 안내 문항의 코드는 셀프 가이드 후보(심각도 상 아님, 고객사 조치)여야 합니다: "
            f"{question.equipment.value} {question.code}"
        )
    return found


def _cited_ref_ids(answer: Answer) -> set[str]:
    """답변의 모든 단계가 인용한 문서 ID 집합."""
    return {citation.ref_id for step in answer.steps for citation in step.citations}


def _classify(question: _Question, answer: Answer | None) -> str:
    """문항 하나의 결과를 분류한다.

    Args:
        question: 검증을 통과한 문항.
        answer: pipeline 답변. 생성 실패(`DependencyError`)면 `None`.

    Returns:
        str: `generation_failed`, 답변 불가 문항이면 `hallucination`·`correct_hold`, 답변 가능 문항이면
            `hit`·`wrong_source`·`over_hold` 중 하나.
    """
    if answer is None:
        return "generation_failed"
    answered = answer.verdict == Verdict.ANSWERED
    if question.expected == Verdict.INSUFFICIENT_EVIDENCE:
        return "hallucination" if answered else "correct_hold"
    if not answered:
        return "over_hold"
    cited, gold = _cited_ref_ids(answer), set(question.gold_ref_ids)
    if question.purpose == CUSTOMER:
        # 고객은 수행할 수 없는 절차를 인용받으면 위험하므로 인용이 모두 정답 근거 안에 있어야 한다.
        return "hit" if cited and cited <= gold else "wrong_source"
    return "hit" if cited & gold else "wrong_source"


def _summarize(questions: list[_Question], outcomes: list[str]) -> dict[str, object]:
    """문항별 결과 분류로 건수·합격선·합격 여부를 계산한다.

    Args:
        questions: 문항 목록.
        outcomes: `questions`와 같은 순서의 결과 분류.

    Returns:
        dict[str, object]: 건수, 지표별 `count`·`total`·합격선, `complete`, `passed`.
            `complete`가 거짓이면 `passed`는 `None`이다.
    """
    answerable = [o for q, o in zip(questions, outcomes) if q.expected == Verdict.ANSWERED]
    unanswerable = [o for q, o in zip(questions, outcomes) if q.expected == Verdict.INSUFFICIENT_EVIDENCE]
    hallucination = unanswerable.count("hallucination")
    wrong_source = answerable.count("wrong_source")
    hit = answerable.count("hit")
    complete = len(answerable) >= MIN_ANSWERABLE and len(unanswerable) >= MIN_UNANSWERABLE
    passed = hallucination <= MAX_HALLUCINATION and wrong_source <= MAX_WRONG_SOURCE and hit >= MIN_HIT
    return {
        "complete": complete,
        "counts": {"answerable": len(answerable), "unanswerable": len(unanswerable)},
        "hallucination": {"count": hallucination, "total": len(unanswerable), "limit": MAX_HALLUCINATION},
        "wrong_source": {"count": wrong_source, "total": len(answerable), "limit": MAX_WRONG_SOURCE},
        "hit": {"count": hit, "total": len(answerable), "minimum": MIN_HIT},
        "over_hold": {"count": answerable.count("over_hold"), "total": len(answerable)},
        "generation_failed": {
            "answerable": answerable.count("generation_failed"),
            "unanswerable": unanswerable.count("generation_failed"),
        },
        "passed": passed if complete else None,
    }


def _summarize_all(questions: list[_Question], outcomes: list[str]) -> dict[str, object]:
    """목적별 요약과 전체 완료·합격 여부를 만든다.

    Args:
        questions: 문항 목록.
        outcomes: `questions`와 같은 순서의 결과 분류.

    Returns:
        dict[str, object]: 목적 이름(`engineer_chat`·`customer_guidance`)마다 `_summarize` 결과, 그리고 전체
            `complete`(두 목적이 모두 완료), `passed`(두 목적이 모두 합격. 미완료이면 `None`).
    """
    by_purpose = {}
    for purpose in _PURPOSES:
        picked = [(q, o) for q, o in zip(questions, outcomes) if q.purpose == purpose]
        by_purpose[purpose] = _summarize([q for q, _ in picked], [o for _, o in picked])
    complete = all(s["complete"] for s in by_purpose.values())
    passed = all(s["passed"] for s in by_purpose.values())
    return {**by_purpose, "complete": complete, "passed": passed if complete else None}


def _sha256(path: Path) -> str:
    """파일 내용의 SHA-256 16진 문자열. 읽을 수 없으면 `DependencyError`."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as e:
        raise DependencyError(f"파일을 읽을 수 없습니다({type(e).__name__}): {path}") from None


def run_eval(questions_path: Path, output_dir: Path) -> dict[str, object]:
    """평가 문항을 실행하고 결과를 기록한다.

    문항 파일 전체와 에러코드를 먼저 검증한 뒤에 모델을 부르므로, 문항 오류로 호출 비용을 쓰지 않는다.
    검색 설정은 `load_config()`의 값을 쓴다.

    Args:
        questions_path: 평가 문항 CSV 경로(`data/eval_questions.csv`).
        output_dir: 결과 JSON을 쓸 폴더. 없으면 만든다. 파일 이름은 `eval_<UTC 시각>.json`이다.

    Returns:
        dict[str, object]: `evaluated_at`, `versions`, `settings`, `results`, `summary`, `caveats` 키를 가진
            평가 결과 한 개. 같은 내용이 `output_dir`에 JSON으로 저장된다.

    Raises:
        ValidationError: 문항 파일의 형식·값이 틀렸거나 문항의 에러코드가 에러코드 표에 없을 때. pipeline이
            검색 설정을 거부했을 때도 그대로 올린다.
        DependencyError: 문항·프롬프트 파일을 읽을 수 없거나, DB 조회·결과 파일 쓰기에 실패했을 때.
    """
    questions = _read_questions(questions_path)
    config = load_config()
    cache: dict[Equipment, dict[str, ErrorCode]] = {}
    contexts = [
        CaseContext(error_code=_find_error_code(q, cache), memo=(q.question or None) if q.purpose == CUSTOMER else None)
        for q in questions
    ]

    results: list[dict[str, object]] = []
    outcomes: list[str] = []
    for question, context in zip(questions, contexts):
        answer: Answer | None = None
        error: str | None = None
        try:
            if question.purpose == CUSTOMER:
                answer = pipeline.run_customer_guidance(context, config.search)
            else:
                answer = pipeline.run_engineer_answer(
                    context, ChatRequest(question=question.question, turns=[]), config.search
                )
        except DependencyError as e:
            error = str(e)
        outcome = _classify(question, answer)
        outcomes.append(outcome)
        results.append(
            {
                "row": question.no,
                "purpose": question.purpose,
                "equipment": question.equipment.value,
                "code": question.code,
                "question": question.question,
                "expected": question.expected.value,
                "gold_ref_ids": list(question.gold_ref_ids),
                "verdict": answer.verdict.value if answer else None,
                "cited_ref_ids": sorted(_cited_ref_ids(answer)) if answer else [],
                "outcome": outcome,
                "error": error,
            }
        )

    now = datetime.now(UTC)
    result: dict[str, object] = {
        "evaluated_at": now.isoformat(),
        "versions": {
            "chat_model": config.chat_model_name,
            "embedding_model": config.embedding_model_name,
            "prompts_sha256": _sha256(Path(prompts.__file__)),
            "questions_sha256": _sha256(questions_path),
        },
        "settings": config.search.model_dump(),
        "results": results,
        "summary": _summarize_all(questions, outcomes),
        "caveats": _CAVEATS,
    }
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        out_path = output_dir / f"eval_{now:%Y%m%dT%H%M%SZ}.json"
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        raise DependencyError(f"평가 결과를 쓸 수 없습니다({type(e).__name__}): {output_dir}") from None
    return result


def _format_purpose(label: str, summary: dict[str, object]) -> list[str]:
    """한 목적의 요약을 터미널에 보여 줄 줄 목록으로 만든다."""
    counts = summary["counts"]
    hallucination, wrong_source, hit = summary["hallucination"], summary["wrong_source"], summary["hit"]
    over_hold, failed = summary["over_hold"], summary["generation_failed"]
    if summary["complete"]:
        verdict = "합격" if summary["passed"] else "불합격"
    else:
        verdict = (
            f"평가 미완료(답변 가능 {counts['answerable']}/{MIN_ANSWERABLE}, "
            f"답변 불가 {counts['unanswerable']}/{MIN_UNANSWERABLE})"
        )
    return [
        f"[{label}] {verdict}",
        f"  환각 {hallucination['count']}/{hallucination['total']}건 (한도 {hallucination['limit']})",
        f"  엉뚱한 출처 {wrong_source['count']}/{wrong_source['total']}건 (한도 {wrong_source['limit']})",
        f"  정답 적중 {hit['count']}/{hit['total']}건 (최소 {hit['minimum']})",
        f"  과잉 보류 {over_hold['count']}/{over_hold['total']}건",
        f"  생성 실패 답변 가능 {failed['answerable']}건, 답변 불가 {failed['unanswerable']}건",
    ]


def _format_summary(summary: dict[str, object]) -> str:
    """`summary`를 터미널에 보여 줄 여러 줄 문자열로 만든다. 전체 결과 다음에 목적별 결과가 온다."""
    if summary["complete"]:
        overall = "합격" if summary["passed"] else "불합격"
    else:
        overall = "평가 미완료"
    lines = [f"결과: {overall}"]
    for purpose in _PURPOSES:
        lines += _format_purpose(_LABELS[purpose], summary[purpose])
    return "\n".join(lines)


def main() -> int:
    """설정·DB·검색 저장소·모델을 연결해 평가를 실행하고 성공 0·실패 1을 반환한다.

    채팅 모델은 `temperature=0`으로 만든다(실행마다 결과가 달라지는 편차를 줄인다).

    Returns:
        int: 평가가 끝나면 `0`(합격 여부와 무관), 검증 오류나 DB·모델 장애 등 앱 예외가 났으면 `1`.
            앱 예외는 밖으로 던지지 않고 안내를 출력한 뒤 `1`로 바꿔 돌려준다.
    """
    # langchain_openai는 실제 실행 때만 필요하므로 여기서 불러온다(app.py와 같다).
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings

    try:
        config = load_config()
        rdb.connect(config.db_url.get_secret_value())
        vectordb.connect(
            OpenAIEmbeddings(model=config.embedding_model_name, api_key=config.openai_api_key.get_secret_value())
        )
        generator.set_chat_model(
            ChatOpenAI(
                model=config.chat_model_name,
                api_key=config.openai_api_key.get_secret_value(),
                timeout=config.llm_timeout_seconds,
                temperature=0,
            )
        )
        result = run_eval(_ROOT / "data" / "eval_questions.csv", _ROOT / "data" / "eval_results")
    except (ValidationError, DependencyError) as e:
        print(f"평가를 실행하지 못했습니다: {e}")
        return 1
    print(_format_summary(result["summary"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
