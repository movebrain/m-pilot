"""UI에서 RAG까지 동작을 확인하기 위한 가짜 DB. DB 파트가 구현되기 전에 `rdb`·`parser`를 메모리로 대신한다. 벡터 저장소는 진짜 `vectordb`를 쓴다.

역할:
    `service`와 `rag`는 진짜 코드를 그대로 쓰고, 그 아래 DB 계층만 바꿔 끼운다(`install()`).
    `fake_service`가 `service` 전체를 가짜로 바꾸는 것과 달리 `pipeline`·`retriever`·`generator`가 실제로 돈다.

책임:
    - `rdb`: 에러코드 표·시드 이력 200건·신고·대화·조치를 메모리에 둔다. 상태 전이는
      `models.raise_if_case_completed`를 쓰고, 쓰기는 잠금 하나로 직렬화한다(조건부 UPDATE 대신).
      시각은 UTC 시간대를 붙여 돌려준다. 시드 CSV의 시각은 시간대가 없어 KST로 본다.
    - `vectordb`: 바꾸지 않는다. 진짜 `connect`(메모리 Chroma)·`get_store`·`get_document`와 색인 쓰기를 그대로 쓴다.
    - `parser`: `data/manuals` PDF를 쪽 단위로 잘라 청크로 만든다. 파싱에 20초 안팎이 걸려 한 번만 한다.
    - 에러코드 표의 출처(`manual_id`·`page`·`section`)는 같은 설비 매뉴얼에서 그 코드가 가장 많이 나오는 청크를
      골라 붙인다. 배선 확인용 값이며 RAG 품질을 판단하는 근거로 쓰지 않는다.
    - `rdb.load_*_from_csv`는 `scripts.init_db` 전용이라 바꾸지 않는다. `connect`가 곧바로 시드를 올린다.

의존: `fake_service`(CSV 로더·조회 도우미), `db.*`(바꿔 끼울 대상), `models`.
소유: UI·총괄 파트
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
import threading
from datetime import UTC, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from langchain_core.embeddings import Embeddings

from fake import fake_service
from mpilot.db import parser, rdb
from mpilot.models import (
    Answer,
    CaseDetail,
    CaseInput,
    CaseStatus,
    ChatTurn,
    Chunk,
    DependencyError,
    Equipment,
    ErrorCode,
    GuideFailure,
    InvalidStateError,
    Resolution,
    Route,
    ServiceCase,
    Source,
    ValidationError,
    Verdict,
    raise_if_case_completed,
)

_MANUALS_DIR = Path(__file__).resolve().parent.parent / "data" / "manuals"  # 에러코드의 매뉴얼 출처를 고를 폴더
_KST = timezone(timedelta(hours=9))
_CHUNK_CHARS = 800  # 청크 하나의 최대 글자 수
_HEADING = re.compile(r"^\d+(?:\.\d+)+\s+[가-힣A-Za-z]")  # 예: "3.6 처리 능력 기준"
_MANUAL_FILE = re.compile(r"^(?P<prefix>[a-z_]+?)_spec_sheet_dummy_rev(?P<major>\d+)_(?P<minor>\d+)$")
_PREFIX_EQUIPMENT = {
    "mini_loader": Equipment.MINI_LOADER,
    "srm": Equipment.SRM,
    "agv": Equipment.AGV,
    "rtv": Equipment.RTV,
}

_lock = threading.RLock()
_store: fake_service._Store | None = None  # connect가 만든다


# ---------------------------------------------------------------------------
# 임베딩
# ---------------------------------------------------------------------------


class HashEmbeddings(Embeddings):
    """글자 두 개 묶음을 해시해 만든 결정적 임베딩. API 키 없이 같은 글이면 같은 벡터가 나온다.

    의미를 이해하지 못하고 겹치는 글자 묶음이 많을수록 가까워질 뿐이다. 배선 검증용이다.
    """

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def _embed(self, text: str) -> list[float]:
        compact = re.sub(r"\s+", "", text.lower())
        vec = [0.0] * self.dim
        for i in range(max(len(compact) - 1, 1)):
            h = int.from_bytes(hashlib.blake2b(compact[i : i + 2].encode(), digest_size=4).digest(), "big")
            vec[h % self.dim] += 1.0 if (h >> 31) & 1 else -1.0  # 부호를 섞어 서로 무관한 글이 가까워지지 않게 함
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


@lru_cache(maxsize=None)
def _parse(directory: Path) -> tuple[Chunk, ...]:
    """폴더의 매뉴얼 PDF를 청크로 자른다(폴더별로 한 번만)."""
    import pdfplumber  # 파싱할 때만 필요하다

    logging.getLogger("pdfminer").setLevel(logging.ERROR)  # 글꼴 경고가 수천 줄 나온다
    chunks: list[Chunk] = []
    for path in sorted(directory.glob("*.pdf")):
        match = _MANUAL_FILE.match(path.stem)
        if not match or match["prefix"] not in _PREFIX_EQUIPMENT:
            continue
        equipment = _PREFIX_EQUIPMENT[match["prefix"]]
        manual_id = f"{match['prefix']}_rev{match['major']}_{match['minor']}"
        version = f"{match['major']}.{match['minor']}"
        section = "머리말"
        with pdfplumber.open(path) as pdf:
            for page_no, page in enumerate(pdf.pages, start=1):
                page_chunks: list[tuple[str, str]] = []  # (절, 본문)
                buf = ""

                def flush() -> None:
                    nonlocal buf
                    if buf.strip():
                        page_chunks.append((section, buf.strip()))
                    buf = ""

                for line in (page.extract_text() or "").splitlines():
                    if _HEADING.match(line):
                        flush()
                        section = line.strip()[:60]
                    if len(buf) + len(line) + 1 > _CHUNK_CHARS:
                        flush()
                    buf += line + "\n"
                flush()
                for chunk_no, (sec, text) in enumerate(page_chunks, start=1):
                    chunks.append(
                        Chunk(
                            manual_id=manual_id, equipment=equipment, page=page_no, section=sec,
                            chunk_no=chunk_no, version=version, text=text,
                        )
                    )
    return tuple(chunks)


def parse_manuals(directory: Path) -> list[Chunk]:
    """`parser.parse_manuals`의 가짜. PDF를 쪽 단위로 잘라 `Chunk` 목록으로 돌려준다.

    Args:
        directory: 매뉴얼 PDF 폴더(`data/manuals`). 파일 이름이 `<설비>_spec_sheet_dummy_rev<M>_<m>.pdf`가 아니면 건너뛴다.

    Returns:
        list[Chunk]: 쪽·절 정보가 붙은 청크. 폴더가 비어 있으면 빈 목록.
    """
    return list(_parse(Path(directory).resolve()))


# ---------------------------------------------------------------------------
# rdb
# ---------------------------------------------------------------------------


def _with_manual_sources(codes: dict[tuple[Equipment, str], ErrorCode]) -> dict[tuple[Equipment, str], ErrorCode]:
    """같은 설비 매뉴얼에서 코드가 가장 많이 나온 청크를 출처로 붙인다."""
    by_equipment: dict[Equipment, list[Chunk]] = {e: [] for e in Equipment}
    for chunk in _parse(_MANUALS_DIR.resolve()):
        by_equipment[chunk.equipment].append(chunk)
    result = {}
    for key, code in codes.items():
        word = re.compile(rf"\b{re.escape(code.code)}\b")
        counts = [(len(word.findall(c.text)), c) for c in by_equipment[code.equipment]]
        count, best = max(counts, key=lambda pair: pair[0], default=(0, None))  # 동률이면 앞쪽 청크
        if best is None or count == 0:
            result[key] = code
            continue
        update = {"manual_id": best.manual_id, "page": best.page, "section": best.section}
        result[key] = code.model_copy(update=update)
    return result


def _utc(naive: datetime) -> datetime:
    """시간대 없는 시각을 KST로 보고 UTC 시간대를 붙인다."""
    return naive.replace(tzinfo=_KST).astimezone(UTC)


def _require_store() -> fake_service._Store:
    """연결된 저장소를 돌려준다. `connect` 전이면 DependencyError."""
    if _store is None:
        raise DependencyError("가짜 DB에 연결하지 않았습니다(connect 필요).")
    return _store


def connect(db_url: str) -> None:
    """`rdb.connect`의 가짜. 접속하지 않고 에러코드 표와 시드 이력 200건으로 메모리 DB를 새로 만든다.

    Args:
        db_url: 쓰지 않는다(`rdb`와 시그니처를 맞추기 위해 둔다).

    Returns:
        None: 이후 가짜 `rdb` 함수가 이 자료를 쓴다. 다시 부르면 접수 건·대화·조치가 사라진다.
    """
    global _store
    store = fake_service._Store(error_codes=_with_manual_sources(fake_service._load_error_codes()), cases={})
    fake_service._load_seed_history(store)
    store.cases = {
        key: case.model_copy(update={"created_at": _utc(case.created_at), "completed_at": _utc(case.completed_at)})
        for key, case in store.cases.items()
    }
    with _lock:
        _store = store


def reset_schema() -> None:
    """`rdb.reset_schema`의 가짜. 시드 상태로 되돌린다."""
    connect("")


def fetch_error_codes(equipment: Equipment) -> list[ErrorCode]:
    """`rdb.fetch_error_codes`의 가짜. 계약은 같다."""
    equipment = fake_service._parse_enum(Equipment, equipment, "설비")
    return [c for c in _require_store().error_codes.values() if c.equipment is equipment]


def fetch_cases(status: CaseStatus | None = None, source: Source | None = None) -> list[CaseDetail]:
    """`rdb.fetch_cases`의 가짜. 계약은 같다(최신순)."""
    store = _require_store()
    cases = fake_service._filter_by_status(store.cases.values(), status)
    if source is not None:
        source = fake_service._parse_enum(Source, source, "출처")
        cases = [c for c in cases if c.source is source]
    return [fake_service._to_detail(store, c) for c in fake_service._latest_first(cases)]


def fetch_case_detail(case_id: str) -> CaseDetail:
    """`rdb.fetch_case_detail`의 가짜. 계약은 같다."""
    store = _require_store()
    return fake_service._to_detail(store, fake_service._get_case(store, case_id))


def _save_new_case(
    case_id: str, data: CaseInput, route: Route, guide_failure: GuideFailure | None, answer: Answer | None
) -> CaseDetail:
    """새 신고를 저장한다. 셀프 가이드(`answer`가 있음)는 고객 확인 전까지 조치 중으로 둔다."""
    with _lock:
        store = _require_store()
        if (data.equipment, data.code) not in store.error_codes:
            raise ValidationError(f"등록되지 않은 에러코드입니다: {data.equipment} {data.code}")
        if case_id in store.cases:
            raise DependencyError(f"접수번호가 이미 있습니다: {case_id}")
        now = datetime.now(UTC)
        store.cases[case_id] = ServiceCase(
            case_id=case_id, equipment=data.equipment, code=data.code, memo=data.memo, source=Source.REPORTED,
            status=CaseStatus.IN_PROGRESS if answer is not None else CaseStatus.RECEIVED, route=route,
            guide_failure=guide_failure, guidance_answer=answer, created_at=now, completed_at=None,
        )
        return fake_service._to_detail(store, store.cases[case_id])


def save_self_guide_case(case_id: str, data: CaseInput, guidance_answer: Answer) -> CaseDetail:
    """`rdb.save_self_guide_case`의 가짜. 계약은 같다."""
    if guidance_answer.verdict is not Verdict.ANSWERED:
        raise ValidationError("셀프 가이드는 answered 안내만 저장할 수 있습니다.")
    return _save_new_case(case_id, data, Route.SELF_GUIDE, None, guidance_answer)


def save_support_case(case_id: str, data: CaseInput, guide_failure: GuideFailure | None) -> CaseDetail:
    """`rdb.save_support_case`의 가짜. 계약은 같다."""
    return _save_new_case(case_id, data, Route.SUPPORT, guide_failure, None)


def _update_pending_self_guide(case_id: str, **update) -> CaseDetail:
    """고객 확인을 기다리는 셀프 가이드 건(`self_guide`·`in_progress`)을 잠그고 `update`를 적용한다."""
    with _lock:
        store = _require_store()
        case = fake_service._get_case(store, case_id)
        if case.route is not Route.SELF_GUIDE or case.status is not CaseStatus.IN_PROGRESS:
            raise InvalidStateError("이미 처리된 셀프 가이드 건")
        store.cases[case_id] = case.model_copy(update=update)
        return fake_service._to_detail(store, store.cases[case_id])


def resolve_self_guide_case(case_id: str) -> CaseDetail:
    """`rdb.resolve_self_guide_case`의 가짜. 계약은 같다."""
    return _update_pending_self_guide(
        case_id, status=CaseStatus.COMPLETED, completed_at=datetime.now(UTC)
    )


def escalate_self_guide_case(case_id: str) -> CaseDetail:
    """`rdb.escalate_self_guide_case`의 가짜. 계약은 같다."""
    return _update_pending_self_guide(case_id, route=Route.SUPPORT, guide_failure=GuideFailure.UNRESOLVED)


def fetch_chat_turns(case_id: str) -> list[ChatTurn]:
    """`rdb.fetch_chat_turns`의 가짜. 계약은 같다(오래된 순)."""
    return list(_require_store().chat_turns.get(case_id, []))


def save_chat_turn(case_id: str, question: str, answer: Answer) -> ChatTurn:
    """`rdb.save_chat_turn`의 가짜. 계약은 같다. 첫 질문이면 조치 중으로 바꾼다."""
    question = question.strip()
    if not 1 <= len(question) <= 2000:
        raise ValidationError("질문은 공백 제외 1~2,000자여야 합니다.")
    with _lock:
        store = _require_store()
        case = fake_service._get_case(store, case_id)
        raise_if_case_completed(case)
        turn = ChatTurn(
            turn_id=store.next_turn_id, case_id=case_id, question=question, answer=answer, created_at=datetime.now(UTC)
        )
        store.next_turn_id += 1
        store.chat_turns.setdefault(case_id, []).append(turn)
        if case.status is CaseStatus.RECEIVED:
            store.cases[case_id] = case.model_copy(update={"status": CaseStatus.IN_PROGRESS})
        return turn


def save_confirmed_resolution(case_id: str, record: Resolution) -> CaseDetail:
    """`rdb.save_confirmed_resolution`의 가짜. 계약은 같다. 잠금 안에서 확인하고 쓰므로 이중 확정이 막힌다."""
    with _lock:
        store = _require_store()
        case = fake_service._get_case(store, case_id)
        raise_if_case_completed(case)
        store.resolutions[case_id] = record
        store.cases[case_id] = case.model_copy(
            update={"status": CaseStatus.COMPLETED, "completed_at": max(datetime.now(UTC), case.created_at)}
        )
        return fake_service._to_detail(store, store.cases[case_id])


def fetch_resolved_cases() -> list[CaseDetail]:
    """`rdb.fetch_resolved_cases`의 가짜. 조치 기록이 있는 건(시드 포함)만 최신순으로 돌려준다."""
    store = _require_store()
    resolved = [c for c in store.cases.values() if c.case_id in store.resolutions]
    return [fake_service._to_detail(store, c) for c in fake_service._latest_first(resolved)]


# ---------------------------------------------------------------------------
# 설치
# ---------------------------------------------------------------------------

_TARGETS = (
    (
        rdb,
        {
            "connect": connect,
            "reset_schema": reset_schema,
            "fetch_error_codes": fetch_error_codes,
            "fetch_cases": fetch_cases,
            "fetch_case_detail": fetch_case_detail,
            "save_self_guide_case": save_self_guide_case,
            "save_support_case": save_support_case,
            "resolve_self_guide_case": resolve_self_guide_case,
            "escalate_self_guide_case": escalate_self_guide_case,
            "fetch_chat_turns": fetch_chat_turns,
            "save_chat_turn": save_chat_turn,
            "save_confirmed_resolution": save_confirmed_resolution,
            "fetch_resolved_cases": fetch_resolved_cases,
        },
    ),
    (parser, {"parse_manuals": parse_manuals}),
)
"""(바꿀 모듈, {모듈의 함수 이름: 대신 끼울 가짜 함수}). 이름이 같은 `connect`가 둘이라 사전으로 둔다."""


def install() -> None:
    """`rdb`·`parser`의 함수를 이 모듈의 가짜로 바꿔 끼운다.

    `service`와 `retriever`는 `rdb.<함수>`처럼 모듈 속성으로 부르므로, 바꿔 끼운 뒤에는 진짜 `service`·`rag`가
    가짜 DB 위에서 동작한다. `vectordb`는 바꾸지 않는다.

    Returns:
        None: 이후 두 모듈의 해당 함수 호출이 메모리 구현으로 처리된다.
    """
    for module, fakes in _TARGETS:
        for name, fake in fakes.items():
            setattr(module, name, fake)
