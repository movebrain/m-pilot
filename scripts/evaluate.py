"""RAG 품질 평가 실행·기록 스크립트(수동 실행, CI에서는 돌리지 않는다).

역할:
    `data/eval_questions.csv`의 문항으로 엔지니어 대화(`rag.pipeline.run_engineer_answer`)와 고객 안내
    (`rag.pipeline.run_customer_guidance`)를 실행하고 결과를 JSON 한 개로 남긴다.

책임(spec §2.7):
    - 문항의 `purpose`(`engineer_chat`·`customer_guidance`)별로 따로 집계한다. 고객 안내 문항의 `question`은
      신고 증상 메모이며 비워도 된다. 답변 가능 고객 안내 문항의 코드는 셀프 가이드 후보여야 한다. 답변 불가 문항은
      조치 구분이 엔지니어 필요인 코드를 고객 안내 흐름에 직접 넣어 만든다(운영에서는 경로 분기가 먼저 엔지니어에게
      접수하므로, 고객에게 엔지니어 조치를 안내하지 않는 안전 장치를 시험한다).
    - 문항은 답변 가능(`expected=answered`, 정답 근거 `gold_ref_ids`가 있다)과 답변 불가
      (`expected=insufficient_evidence`, `gold_ref_ids=[]`)로 나뉜다.
    - `state`가 문항의 성격을 더 나누며 `expected`를 정한다. 엔지니어 대화는 `own_code`·`own_common`·`own_history`
      (신고 설비 자료), `other_code`·`other_common`(다른 설비 자료, 폴백 안내가 붙어야 한다), `none_code`·
      `none_common`(4종 어디에도 근거 없음)이고 고객 안내는 `guided`·`not_guided`다. 결과 `summary`의 `by_state`에
      상태별 결과 분류와 폴백 안내 기대에서 어긋난 건수를 센다(합격 조건이 아니다).
    - 지표와 합격선: 환각(답변 불가 문항 중 `answered`) 1건 이하, 엉뚱한 출처(답변 가능 문항 중 `answered`인데
      인용이 정답 근거 기준에 못 미침) 1건 이하, 정답 적중(답변 가능 문항 중 `answered`이고 인용이 정답 근거
      기준을 만족) 21건 이상. 과잉 보류(답변 가능 문항 중 보류)는 합격선이 없다. 정답 적중 21건은 초기 목표이며
      실측값이 아니다.
    - 정답 근거 기준: 두 목적 모두 인용에 `gold_ref_ids`가 하나라도 있으면 만족한다.
    - 목적별로 답변 가능 30개·답변 불가 20개에 못 미치면 합격 여부를 판정하지 않는다(평가 미완료). 전체 합격은
      두 목적이 모두 합격일 때다(두 목적이 같은 `manual_k`를 공유한다).
    - 생성 실패(`DependencyError`)는 별도로 센다. 답변 가능 문항에서는 정답 적중이 아니고, 답변 불가 문항에서는
      환각이 아니다. 지어낸 인용(인용 검증 실패)도 생성 실패라서 목적마다 1건 이하여야 합격이다.
    - 기록 항목: `versions`(모델 이름, 프롬프트·문항 파일 해시), `settings`(검색 설정), `results`(문항별 판정·인용·
      결과 분류), `summary`(건수·합격선·합격 여부), `caveats`(작은 표본 해석 주의).
    - 검색 지표: 정답 근거가 있는 문항마다 운영과 같은 검색을 생성 없이 한 번 더 실행해 컬렉션(`manual`·`history`)별
      Recall@k·Hit·역순위를 기록하고 목적별 평균(`summary`의 `retrieval`)을 낸다. 고객 안내와 `own_*`은 1차 검색,
      `other_*`은 폴백 검색(설비 필터 없이 질문 원문)을 잰다. 합격 조건에는 쓰지 않는다.
    - 후보 `manual_k`·`history_k`(`.env`의 `MANUAL_K`·`HISTORY_K`)마다 따로 실행한다. 결과 파일이 설정을 함께 남긴다.
    - 사람 검수(생성된 고객 안내 전부, 엔지니어 `answered` 답변 최소 5건)는 이 스크립트 밖에서 한다. 고객 안내의
      안전 수칙 준수는 자동으로 재지 않는다.

실행 전제: `main`이 `app.initialize()`로 DB·검색 저장소·모델을 연결하고 검색 색인을 준비한다. `run_eval`을 직접
부르려면 같은 연결이 끝나 있어야 한다.

의존: `config`, `db.rdb`, `models`, `rag.generator`, `rag.pipeline`, `rag.retriever`(폴백 검색), `app`(`initialize`)을
쓸 수 있다.
소유: RAG 파트
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError as PydanticValidationError

from mpilot.config import load_config
from mpilot.db import rdb
from mpilot.models import (
    Answer,
    CaseContext,
    ChatRequest,
    CitationKind,
    DependencyError,
    Equipment,
    ErrorCode,
    SearchSettings,
    ValidationError,
    Verdict,
    is_self_guide_candidate,
)
from mpilot.rag import generator, pipeline, prompts, retriever

_ROOT = Path(__file__).resolve().parents[1]

ENGINEER = "engineer_chat"
CUSTOMER = "customer_guidance"
_PURPOSES = (ENGINEER, CUSTOMER)
_LABELS = {ENGINEER: "엔지니어 대화", CUSTOMER: "고객 안내"}

_COLUMNS = ["purpose", "equipment", "code", "question", "expected", "state", "gold_ref_ids"]

# 문항 상태 → 기대 판정(spec §2.7). 엔지니어 대화는 자료 범위(own 신고 설비·other 다른 설비·none 4종 어디에도 없음)
# × 질문 성격(code 에러코드 절·common 코드 없는 공통 본문·history 과거 이력), 고객 안내는 안내 가능 여부다.
_STATES = {
    ENGINEER: {
        "own_code": Verdict.ANSWERED,
        "own_common": Verdict.ANSWERED,
        "own_history": Verdict.ANSWERED,
        "other_code": Verdict.ANSWERED,
        "other_common": Verdict.ANSWERED,
        "none_code": Verdict.INSUFFICIENT_EVIDENCE,
        "none_common": Verdict.INSUFFICIENT_EVIDENCE,
    },
    CUSTOMER: {"guided": Verdict.ANSWERED, "not_guided": Verdict.INSUFFICIENT_EVIDENCE},
}

MIN_ANSWERABLE = 30  # 답변 가능 문항 최소 수
MIN_UNANSWERABLE = 20  # 답변 불가 문항 최소 수
MAX_HALLUCINATION = 1  # 환각 허용 건수
MAX_WRONG_SOURCE = 1  # 엉뚱한 출처 허용 건수
MIN_HIT = 21  # 정답 적중 최소 건수(답변 가능 30개의 70%, 초기 목표)
MAX_GENERATION_FAILED = 1  # 생성 실패 허용 건수(지어낸 인용이 여기로 오므로 환각과 같은 한도)

_CAVEATS = [
    "합격선은 이 문항 안의 결정 규칙이며 실제 환각률의 보증이 아니다.",
    "k 선택과 합격 판정에 같은 문항을 쓰므로 수치가 실제보다 낙관적이다.",
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
    state: str
    gold_ref_ids: tuple[str, ...]


def _expects_fallback(state: str) -> bool | None:
    """상태가 기대하는 폴백 안내 여부. 다른 설비 자료로 답해야 하면 `True`, 신고 설비 자료로 답해야 하면 `False`, 답을 기대하지 않거나 폴백과 무관하면 `None`."""
    if state.startswith("other_"):
        return True
    return False if state.startswith("own_") else None


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
        state = values["state"]
        if state not in _STATES[purpose]:
            raise ValidationError(f"{no}번째 행: {purpose}의 state는 {', '.join(_STATES[purpose])} 중 하나여야 합니다: {state}")
        if _STATES[purpose][state] != expected:
            raise ValidationError(
                f"{no}번째 행: state {state}의 expected는 {_STATES[purpose][state].value}여야 합니다: {expected.value}"
            )
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
        questions.append(_Question(no, purpose, equipment, values["code"], values["question"], expected, state, gold))
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
    # 답변 불가 문항은 후보가 아닌 코드를 고객 흐름에 직접 넣어 안전 장치를 시험하므로 후보 검사에서 뺀다.
    if question.purpose == CUSTOMER and question.expected == Verdict.ANSWERED and not is_self_guide_candidate(found):
        raise ValidationError(
            f"{question.no}번째 행: 답변 가능 고객 안내 문항의 코드는 셀프 가이드 후보(심각도 상 아님, 고객사 조치)여야 "
            f"합니다: {question.equipment.value} {question.code}"
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
    return "hit" if _cited_ref_ids(answer) & set(question.gold_ref_ids) else "wrong_source"


def _search(question: _Question, context: CaseContext, settings: SearchSettings) -> tuple[str | None, list[str]]:
    """검색 지표용으로 운영과 같은 검색만 실행한다(생성은 하지 않는다).

    Args:
        question: 검증을 통과한 문항.
        context: 문항의 신고 맥락.
        settings: 이 실행의 검색 설정.

    Returns:
        tuple[str | None, list[str]]: 쓴 검색(`first`·`fallback`, 정답 근거가 없는 문항은 `None`)과 검색된 문서
            ID 목록(`manual` 다음 `history`, 컬렉션마다 가까운 순).

    Raises:
        ValidationError: 검색 설정이 허용 범위를 벗어났을 때.
        DependencyError: 임베딩 생성이나 저장소 검색에 실패했을 때.
    """
    if question.expected == Verdict.INSUFFICIENT_EVIDENCE:
        return None, []
    if question.purpose == CUSTOMER:
        return "first", [h.ref_id for h in pipeline.search_customer_evidence(context, settings)]
    if question.state.startswith("own_"):
        return "first", [h.ref_id for h in pipeline.search_engineer_evidence(context, question.question, settings)]
    # other_*의 정답은 다른 설비에 있어 1차(신고 설비 필터)로는 찾을 수 없으므로 폴백 검색을 잰다.
    hits = retriever.search(CitationKind.MANUAL, None, question.question, settings.manual_k)
    hits += retriever.search(CitationKind.HISTORY, None, question.question, settings.history_k)
    return "fallback", [h.ref_id for h in hits]


def _retrieval(retrieved: list[str], gold: tuple[str, ...]) -> dict[str, dict[str, object] | None]:
    """컬렉션별 Recall@k·Hit·역순위. 그 컬렉션에 정답 근거가 없으면 `None`이다.

    Args:
        retrieved: 검색된 문서 ID 목록(컬렉션마다 가까운 순).
        gold: 문항의 정답 근거. `history:` 접두로 컬렉션을 가른다.

    Returns:
        dict[str, dict[str, object] | None]: `manual`·`history`마다 `recall`(정답 중 검색된 비율), `hit`(하나라도
            검색됐는지), `rr`(컬렉션 안 첫 정답 순위의 역수, 없으면 0).
    """
    metrics: dict[str, dict[str, object] | None] = {}
    for name, is_history in (("manual", False), ("history", True)):
        wanted = {ref for ref in gold if ref.startswith("history:") == is_history}
        if not wanted:
            metrics[name] = None
            continue
        ranked = [ref for ref in retrieved if ref.startswith("history:") == is_history]
        ranks = [rank for rank, ref in enumerate(ranked, 1) if ref in wanted]
        metrics[name] = {
            "recall": len(wanted & set(ranked)) / len(wanted),
            "hit": bool(ranks),
            "rr": 1 / ranks[0] if ranks else 0.0,
        }
    return metrics


def _summarize_retrieval(results: list[dict[str, object]]) -> dict[str, object]:
    """한 목적의 문항별 검색 지표를 컬렉션별 평균으로 모은다. 지표가 없는 컬렉션은 `None`이다."""
    summary: dict[str, object] = {}
    for name in ("manual", "history"):
        picked = [r["retrieval"][name] for r in results if r["retrieval"] and r["retrieval"][name]]
        summary[name] = (
            {
                "questions": len(picked),
                "recall": sum(m["recall"] for m in picked) / len(picked),
                "hit_rate": sum(m["hit"] for m in picked) / len(picked),
                "mrr": sum(m["rr"] for m in picked) / len(picked),
            }
            if picked
            else None
        )
    summary["search_failed"] = sum(r["search_error"] is not None for r in results)
    return summary


def _by_state(questions: list[_Question], outcomes: list[str], fallbacks: list[bool | None]) -> dict[str, object]:
    """상태별 문항 수와 결과 분류 건수. 합격 조건에는 쓰이지 않는다.

    `fallback_mismatch`는 답한 문항 중 폴백 안내가 상태의 기대(`other_*` 있음, `own_*` 없음)와 어긋난 건수이며,
    폴백과 무관한 상태는 `None`이다.
    """
    summary: dict[str, object] = {}
    for state in (s for purpose in _PURPOSES for s in _STATES[purpose]):
        picked = [(o, f) for q, o, f in zip(questions, outcomes, fallbacks) if q.state == state]
        if not picked:
            continue
        expected = _expects_fallback(state)
        summary[state] = {
            "questions": len(picked),
            "outcomes": dict(Counter(o for o, _ in picked)),
            "fallback_mismatch": None if expected is None else sum(f is not None and f != expected for _, f in picked),
        }
    return summary


def _summarize(questions: list[_Question], outcomes: list[str], fallbacks: list[bool | None]) -> dict[str, object]:
    """문항별 결과 분류로 건수·합격선·합격 여부를 계산한다.

    Args:
        questions: 문항 목록.
        outcomes: `questions`와 같은 순서의 결과 분류.
        fallbacks: `questions`와 같은 순서의 폴백 안내 여부(답하지 않았으면 `None`).

    Returns:
        dict[str, object]: 건수, 지표별 `count`·`total`·합격선, `by_state`, `complete`, `passed`.
            `complete`가 거짓이면 `passed`는 `None`이다.
    """
    answerable = [o for q, o in zip(questions, outcomes) if q.expected == Verdict.ANSWERED]
    unanswerable = [o for q, o in zip(questions, outcomes) if q.expected == Verdict.INSUFFICIENT_EVIDENCE]
    hallucination = unanswerable.count("hallucination")
    wrong_source = answerable.count("wrong_source")
    hit = answerable.count("hit")
    failed = outcomes.count("generation_failed")
    complete = len(answerable) >= MIN_ANSWERABLE and len(unanswerable) >= MIN_UNANSWERABLE
    passed = (
        hallucination <= MAX_HALLUCINATION
        and wrong_source <= MAX_WRONG_SOURCE
        and hit >= MIN_HIT
        and failed <= MAX_GENERATION_FAILED
    )
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
            "limit": MAX_GENERATION_FAILED,
        },
        "by_state": _by_state(questions, outcomes, fallbacks),
        "passed": passed if complete else None,
    }


def _summarize_all(questions: list[_Question], outcomes: list[str], fallbacks: list[bool | None]) -> dict[str, object]:
    """목적별 요약과 전체 완료·합격 여부를 만든다.

    Args:
        questions: 문항 목록.
        outcomes: `questions`와 같은 순서의 결과 분류.
        fallbacks: `questions`와 같은 순서의 폴백 안내 여부.

    Returns:
        dict[str, object]: 목적 이름(`engineer_chat`·`customer_guidance`)마다 `_summarize` 결과, 그리고 전체
            `complete`(두 목적이 모두 완료), `passed`(두 목적이 모두 합격. 미완료이면 `None`).
    """
    by_purpose = {}
    for purpose in _PURPOSES:
        picked = [(q, o, f) for q, o, f in zip(questions, outcomes, fallbacks) if q.purpose == purpose]
        by_purpose[purpose] = _summarize([q for q, _, _ in picked], [o for _, o, _ in picked], [f for _, _, f in picked])
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
    fallbacks: list[bool | None] = []
    for question, context in zip(questions, contexts):
        search_kind, retrieved, search_error = None, [], None
        try:
            search_kind, retrieved = _search(question, context, config.search)
        except DependencyError as e:
            search_error = str(e)
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
        # 다른 설비 자료로 답했다는 고정 안내가 붙었는지(폴백). 답하지 않았으면 판단하지 않는다.
        fallback_used = (
            answer.notice == pipeline.CROSS_EQUIPMENT_NOTICE if answer and answer.verdict == Verdict.ANSWERED else None
        )
        fallbacks.append(fallback_used)
        results.append(
            {
                "row": question.no,
                "purpose": question.purpose,
                "equipment": question.equipment.value,
                "code": question.code,
                "question": question.question,
                "expected": question.expected.value,
                "state": question.state,
                "gold_ref_ids": list(question.gold_ref_ids),
                "verdict": answer.verdict.value if answer else None,
                "fallback_used": fallback_used,
                "cited_ref_ids": sorted(_cited_ref_ids(answer)) if answer else [],
                "outcome": outcome,
                "error": error,
                "search": search_kind,
                "retrieved_ref_ids": retrieved,
                "retrieval": _retrieval(retrieved, question.gold_ref_ids) if search_kind else None,
                "search_error": search_error,
            }
        )

    summary = _summarize_all(questions, outcomes, fallbacks)
    for purpose in _PURPOSES:
        summary[purpose]["retrieval"] = _summarize_retrieval([r for r in results if r["purpose"] == purpose])

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
        "summary": summary,
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
    lines = [
        f"[{label}] {verdict}",
        f"  환각 {hallucination['count']}/{hallucination['total']}건 (한도 {hallucination['limit']})",
        f"  엉뚱한 출처 {wrong_source['count']}/{wrong_source['total']}건 (한도 {wrong_source['limit']})",
        f"  정답 적중 {hit['count']}/{hit['total']}건 (최소 {hit['minimum']})",
        f"  과잉 보류 {over_hold['count']}/{over_hold['total']}건",
        f"  생성 실패 답변 가능 {failed['answerable']}건, 답변 불가 {failed['unanswerable']}건 (합계 한도 {failed['limit']})",
    ]
    for state, s in summary["by_state"].items():
        outcomes = ", ".join(f"{name} {n}" for name, n in s["outcomes"].items())
        mismatch = "" if s["fallback_mismatch"] is None else f" (폴백 기대와 어긋남 {s['fallback_mismatch']}건)"
        lines.append(f"  - {state} {s['questions']}건: {outcomes}{mismatch}")
    retrieval = summary.get("retrieval")
    if retrieval:  # 합격 판정과 무관한 검색 지표
        for name in ("manual", "history"):
            m = retrieval[name]
            if m:
                lines.append(
                    f"  검색 {name} {m['questions']}건: "
                    f"Recall {m['recall']:.2f}, Hit {m['hit_rate']:.2f}, MRR {m['mrr']:.2f}"
                )
        if retrieval["search_failed"]:
            lines.append(f"  검색 실패 {retrieval['search_failed']}건")
    return lines


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
    """앱과 같은 설정으로 연결·색인을 준비해 평가를 실행하고 성공 0·실패 1을 반환한다.

    `app.initialize()`가 만든 채팅 모델을 `temperature=0`으로 다시 만들어 끼운다(실행마다 결과가 달라지는 편차를
    줄인다).

    Returns:
        int: 평가가 끝나면 `0`(합격 여부와 무관), 검증 오류나 DB·모델 장애 등 앱 예외가 났으면 `1`.
            앱 예외는 밖으로 던지지 않고 안내를 출력한 뒤 `1`로 바꿔 돌려준다.
    """
    import app  # Gradio를 불러오므로 실행할 때만 가져온다
    from langchain_openai import ChatOpenAI  # 실제 실행 때만 필요하다(app.py와 같다)

    try:
        app.initialize()
        config = load_config()
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
