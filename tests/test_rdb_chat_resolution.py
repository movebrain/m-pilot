"""대화 조회·저장과 조치 확정(fetch_chat_turns, save_chat_turn, save_confirmed_resolution) 테스트.

모두 실제 MySQL 테스트 DB가 필요하다(`rdb_db` fixture, tests/conftest.py).
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, insert, select, update

from mpilot.db.schemas import chat_turns, error_codes, resolutions, service_cases
from mpilot.models import (
    Answer,
    AnswerStep,
    CaseStatus,
    Citation,
    CitationKind,
    DependencyError,
    InvalidStateError,
    NotFoundError,
    Resolution,
    ValidationError,
    Verdict,
)

ANSWERED = Answer(
    verdict=Verdict.ANSWERED,
    steps=[
        AnswerStep(
            text="X축 센서를 점검한다",
            citations=[
                Citation(
                    kind=CitationKind.MANUAL,
                    ref_id="manual:HMX-SRM-TSM-001:12:1",
                    locator="p.12 9.4 에러 코드 일람",
                )
            ],
        )
    ],
    notice=None,
)
HELD = Answer(verdict=Verdict.INSUFFICIENT_EVIDENCE, steps=[], notice="근거가 부족합니다.")
RECORD = Resolution(cause="센서 오염", action="센서 청소", result="정상 동작 확인")


def add_error_code(conn):
    """에러코드 표에 SRM S101 한 행을 넣는다."""
    conn.execute(
        insert(error_codes).values(
            equipment="SRM",
            code="S101",
            name="X축 위치 편차 초과",
            severity="중",
            action_owner="고객사 조치",
            cause="원인 문장",
            action="조치 문장",
            manual_id="HMX-SRM-TSM-001",
            page=12,
            section="9.4 에러 코드 일람",
        )
    )


def add_case(conn, case_id, status="received", route="support"):
    """신고 한 건을 넣는다(설비 SRM, 코드 S101)."""
    completed_at = None
    self_guide_answer = None
    if status == "completed":
        completed_at = datetime(2026, 10, 27, 1, 0, 0)
    if route == "self_guide":
        self_guide_answer = ANSWERED.model_dump_json()
    conn.execute(
        insert(service_cases).values(
            case_id=case_id,
            equipment="SRM",
            code="S101",
            customer_memo=None,
            source="reported",
            status=status,
            route=route,
            guide_failure=None,
            self_guide_answer=self_guide_answer,
            created_at=datetime(2026, 10, 27, 0, 0, 0),
            completed_at=completed_at,
        )
    )


@pytest.fixture
def db(rdb_db):
    """신고 4건을 넣어 둔 테스트 DB.

    - SC-RECV: 접수(received)
    - SC-PROG: 조치 중(in_progress)
    - SC-DONE: 현장 조치 완료(completed, support)
    - SC-SELF: 셀프 가이드 완료(completed, self_guide)
    """
    with rdb_db._engine.begin() as conn:
        add_error_code(conn)
        add_case(conn, "SC-RECV")
        add_case(conn, "SC-PROG", status="in_progress")
        add_case(conn, "SC-DONE", status="completed")
        add_case(conn, "SC-SELF", status="completed", route="self_guide")
    return rdb_db


def read_status(rdb, case_id):
    """신고의 현재 상태 문자열을 읽는다."""
    with rdb._engine.connect() as conn:
        row = conn.execute(
            select(service_cases.c.status).where(service_cases.c.case_id == case_id)
        ).first()
    return row.status


def count_rows(rdb, table):
    """테이블의 행 수를 센다."""
    with rdb._engine.connect() as conn:
        return len(conn.execute(select(table)).all())


# ---------------------------------------------------------------------------
# fetch_chat_turns
# ---------------------------------------------------------------------------


def test_fetch_chat_turns_without_turns_is_empty(db):
    assert db.fetch_chat_turns("SC-RECV") == []


def test_fetch_chat_turns_unknown_case_is_empty(db):
    assert db.fetch_chat_turns("SC-없음") == []


def test_fetch_chat_turns_oldest_first(db):
    db.save_chat_turn("SC-RECV", "첫 질문", ANSWERED)
    db.save_chat_turn("SC-RECV", "두 번째 질문", HELD)
    turns = db.fetch_chat_turns("SC-RECV")
    assert [t.question for t in turns] == ["첫 질문", "두 번째 질문"]
    assert turns[0].turn_id < turns[1].turn_id
    assert turns[0].answer == ANSWERED
    assert turns[1].answer == HELD
    assert turns[0].created_at.tzinfo == UTC


def test_fetch_chat_turns_only_that_case(db):
    db.save_chat_turn("SC-RECV", "접수 건 질문", ANSWERED)
    db.save_chat_turn("SC-PROG", "조치 중 건 질문", ANSWERED)
    assert [t.question for t in db.fetch_chat_turns("SC-PROG")] == ["조치 중 건 질문"]


def test_fetch_chat_turns_broken_answer_is_dependency_error(db):
    db.save_chat_turn("SC-RECV", "질문", ANSWERED)
    with db._engine.begin() as conn:
        conn.execute(update(chat_turns).values(answer="깨진 JSON"))
    with pytest.raises(DependencyError):
        db.fetch_chat_turns("SC-RECV")


def test_fetch_chat_turns_db_failure_is_dependency_error(db, monkeypatch):
    broken = create_engine("mysql+pymysql://user:pw@127.0.0.1:1/db?connect_timeout=2")
    monkeypatch.setattr(db, "_engine", broken)
    with pytest.raises(DependencyError):
        db.fetch_chat_turns("SC-RECV")


# ---------------------------------------------------------------------------
# save_chat_turn
# ---------------------------------------------------------------------------


def test_first_question_moves_received_to_in_progress(db):
    turn = db.save_chat_turn("SC-RECV", "센서는 어디에 있나요?", ANSWERED)
    assert turn.turn_id >= 1
    assert turn.case_id == "SC-RECV"
    assert turn.answer == ANSWERED
    assert turn.created_at.tzinfo == UTC
    assert read_status(db, "SC-RECV") == CaseStatus.IN_PROGRESS


def test_next_question_keeps_in_progress(db):
    db.save_chat_turn("SC-PROG", "질문", ANSWERED)
    assert read_status(db, "SC-PROG") == CaseStatus.IN_PROGRESS


def test_held_answer_is_saved(db):
    turn = db.save_chat_turn("SC-RECV", "질문", HELD)
    assert turn.answer.verdict == Verdict.INSUFFICIENT_EVIDENCE
    assert db.fetch_chat_turns("SC-RECV")[0].answer == HELD


def test_returned_turn_matches_saved_turn(db):
    turn = db.save_chat_turn("SC-RECV", "질문", ANSWERED)
    assert db.fetch_chat_turns("SC-RECV") == [turn]


def test_question_is_stripped(db):
    turn = db.save_chat_turn("SC-RECV", "  질문  \n", ANSWERED)
    assert turn.question == "질문"
    assert db.fetch_chat_turns("SC-RECV")[0].question == "질문"


@pytest.mark.parametrize("question", ["", "   ", "가" * 2001])
def test_bad_question_is_validation_error_and_nothing_saved(db, question):
    with pytest.raises(ValidationError):
        db.save_chat_turn("SC-RECV", question, ANSWERED)
    assert count_rows(db, chat_turns) == 0
    assert read_status(db, "SC-RECV") == CaseStatus.RECEIVED


def test_question_of_2000_chars_is_saved(db):
    turn = db.save_chat_turn("SC-RECV", "가" * 2000, ANSWERED)
    assert len(turn.question) == 2000


def test_unknown_case_is_not_found(db):
    with pytest.raises(NotFoundError):
        db.save_chat_turn("SC-없음", "질문", ANSWERED)


def test_case_id_with_other_letter_case_is_not_found(db):
    with pytest.raises(NotFoundError):
        db.save_chat_turn("sc-recv", "질문", ANSWERED)


@pytest.mark.parametrize("case_id", ["SC-DONE", "SC-SELF"])
def test_completed_or_self_guide_case_rejects_question(db, case_id):
    with pytest.raises(InvalidStateError, match="이미 완료"):
        db.save_chat_turn(case_id, "질문", ANSWERED)
    assert count_rows(db, chat_turns) == 0


# ---------------------------------------------------------------------------
# save_confirmed_resolution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case_id", ["SC-RECV", "SC-PROG"])
def test_resolution_completes_case(db, case_id):
    before = datetime.now(UTC).replace(microsecond=0)
    detail = db.save_confirmed_resolution(case_id, RECORD)

    case = detail.service_case
    assert case.status == CaseStatus.COMPLETED
    assert case.completed_at is not None
    assert case.completed_at >= before
    assert case.completed_at >= case.created_at
    assert detail.resolution == RECORD
    assert detail.error_code.code == "S101"
    assert read_status(db, case_id) == CaseStatus.COMPLETED


def test_resolution_text_is_stripped(db):
    record = Resolution(cause=" 원인 ", action="조치\n", result="\t결과")
    detail = db.save_confirmed_resolution("SC-PROG", record)
    assert detail.resolution == Resolution(cause="원인", action="조치", result="결과")


def test_resolution_too_long_is_validation_error(db):
    # Resolution은 만들 때 길이를 검사하므로, 검사를 건너뛴 값으로 저장 직전 검사를 확인한다.
    record = Resolution.model_construct(cause="가" * 5001, action="조치", result="결과")
    with pytest.raises(ValidationError):
        db.save_confirmed_resolution("SC-PROG", record)
    assert count_rows(db, resolutions) == 0
    assert read_status(db, "SC-PROG") == CaseStatus.IN_PROGRESS


def test_resolution_unknown_case_is_not_found(db):
    with pytest.raises(NotFoundError):
        db.save_confirmed_resolution("SC-없음", RECORD)


@pytest.mark.parametrize("case_id", ["SC-DONE", "SC-SELF"])
def test_resolution_on_completed_or_self_guide_case_is_rejected(db, case_id):
    with pytest.raises(InvalidStateError, match="이미 완료"):
        db.save_confirmed_resolution(case_id, RECORD)
    assert count_rows(db, resolutions) == 0


def test_second_resolution_is_rejected_and_first_is_kept(db):
    db.save_confirmed_resolution("SC-PROG", RECORD)
    other = Resolution(cause="다른 원인", action="다른 조치", result="다른 결과")
    with pytest.raises(InvalidStateError, match="이미 완료"):
        db.save_confirmed_resolution("SC-PROG", other)
    with db._engine.connect() as conn:
        rows = conn.execute(select(resolutions)).all()
    assert len(rows) == 1
    assert rows[0].cause == "센서 오염"


def test_question_after_resolution_is_rejected(db):
    db.save_confirmed_resolution("SC-PROG", RECORD)
    with pytest.raises(InvalidStateError, match="이미 완료"):
        db.save_chat_turn("SC-PROG", "질문", ANSWERED)


def test_failed_insert_rolls_back_status(db, monkeypatch):
    # 조치 기록 저장이 실패하면 완료 처리도 되돌아가야 한다(한 트랜잭션).
    with db._engine.begin() as conn:
        conn.execute(
            insert(resolutions).values(case_id="SC-PROG", cause="미리", action="넣은", result="행")
        )
    with pytest.raises(DependencyError):
        db.save_confirmed_resolution("SC-PROG", RECORD)  # PK 충돌
    assert read_status(db, "SC-PROG") == CaseStatus.IN_PROGRESS


def test_conditional_update_blocks_completion_after_status_check(db, monkeypatch):
    # 상태 확인을 통과한 뒤 다른 요청이 먼저 완료한 상황을 만든다.
    # 상태 확인을 일부러 끄면, 마지막 방어선인 조건부 UPDATE가 '이미 완료'로 막아야 한다.
    db.save_confirmed_resolution("SC-PROG", RECORD)
    monkeypatch.setattr(db, "raise_if_case_completed", lambda case: None)
    with pytest.raises(InvalidStateError, match="이미 완료"):
        db.save_confirmed_resolution("SC-PROG", RECORD)
    assert count_rows(db, resolutions) == 1


def test_concurrent_resolutions_only_one_succeeds(db):
    # 두 요청이 같은 건을 동시에 완료하려 하면 하나만 성공하고 하나는 '이미 완료'다.
    barrier = threading.Barrier(2)
    results = []

    def complete():
        barrier.wait()
        try:
            db.save_confirmed_resolution("SC-PROG", RECORD)
            results.append("ok")
        except InvalidStateError:
            results.append("already")

    threads = [threading.Thread(target=complete), threading.Thread(target=complete)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == ["already", "ok"]
    assert count_rows(db, resolutions) == 1
