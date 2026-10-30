"""pipeline 테스트: 입력 검증, 범위 밖 질문의 검색 진행, 근거 확보·생성·검증 연결, 고객 안내(매뉴얼 검색), 보류 답변 모양."""

from types import SimpleNamespace

import pytest

from mpilot.models import (
    Answer,
    AnswerStep,
    CaseContext,
    ChatRequest,
    CitationKind,
    DependencyError,
    ErrorCode,
    SearchSettings,
    ValidationError,
    Verdict,
)
from mpilot.rag import pipeline


# 보류 판정에 잘못 붙은 단계(검증 전 모양만 시험하므로 내용은 상관없다)
STEP = AnswerStep.model_construct(text="조치", citations=[])
HOLD = Answer(verdict=Verdict.INSUFFICIENT_EVIDENCE, steps=[], notice="보류")
ANSWERED = Answer(verdict=Verdict.ANSWERED, steps=[AnswerStep(text="조치", citations=[])], notice=None)


def chunk_hit(ref_id: str = "manual:srm:1:1", code: str | None = None) -> SimpleNamespace:
    """매뉴얼 검색 결과 한 건. pipeline이 읽는 `ref_id`와 `code`만 있으면 된다."""
    return SimpleNamespace(ref_id=ref_id, code=code)


def make_context(equipment: str = "SRM", code: str = "S-104") -> CaseContext:
    error_code = ErrorCode(
        equipment=equipment, code=code, name="주행 위치 편차", severity="하",
        action_owner="고객사 조치", cause="엔코더 커넥터", action="커넥터 재체결",
        manual_id="SRM_SPEC_v1", page=41, section="9.4",
    )
    return CaseContext(error_code=error_code, memo="")


def ask(question: str, settings: SearchSettings | None = None, equipment: str = "SRM"):
    return pipeline.run_engineer_answer(
        make_context(equipment), ChatRequest.model_construct(question=question, turns=[]),
        settings or SearchSettings(manual_k=3, history_k=3),
    )


def ask_customer(equipment: str = "SRM", settings: SearchSettings | None = None):
    return pipeline.run_customer_guidance(
        make_context(equipment), settings or SearchSettings(manual_k=3, history_k=3)
    )


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    """retriever와 generator를 가짜로 바꿔, pipeline이 부품을 잇는 방식만 시험한다.

    시험에서 `fakes.generated = HOLD`처럼 값을 바꿔 상황을 만든다.
    """
    fake = SimpleNamespace(
        # 엔지니어 답변
        generated_seq=None,  # 값이 있으면 생성 호출마다 하나씩 꺼내 쓴다(1차·폴백 판정을 따로 정할 때)
        check_ok=True,  # 인용 검증을 통과하는가(고객 안내와 엔지니어 답변 공용)
        search_error=None,  # 검색 장애로 던질 예외
        generate_error=None,  # 생성 장애로 던질 예외
        generated=Answer.model_construct(verdict=Verdict.ANSWERED, steps=[], notice=None),
        searches=[],  # 검색 호출 기록
        manual_hits=None,  # 값이 있으면 매뉴얼 검색이 이것을 돌려준다(설비 필터가 있든 없든)
        hits_seen=[],  # 생성 호출마다 넘긴 근거
        generate_calls=0,
        cross_flags=[],  # 생성 호출마다 넘긴 cross_equipment
        allow_flags=[],  # 인용 검증 호출마다 넘긴 allow_other_equipment
        check_calls=0,
        # 고객 안내
        guidance_error=None,  # 안내 생성 장애로 던질 예외
        guidance=Answer.model_construct(verdict=Verdict.ANSWERED, steps=[], notice=None),
        guidance_hits_seen=[],  # 안내 생성 호출마다 넘긴 근거
        guidance_calls=0,
    )

    def search(kind, equipment, query, k):
        if kind is CitationKind.HISTORY:
            fake.searches.append(("history", equipment, query, k))
            return ["history-hit"]
        if fake.search_error:
            raise fake.search_error
        fake.searches.append(("manual", equipment, query, k))
        return fake.manual_hits if fake.manual_hits is not None else [chunk_hit()]

    def generate_engineer_answer(context, hits, request, cross_equipment=False):
        fake.generate_calls += 1
        fake.hits_seen.append(hits)
        fake.cross_flags.append(cross_equipment)
        if fake.generate_error:
            raise fake.generate_error
        return fake.generated_seq.pop(0) if fake.generated_seq else fake.generated

    def passes_check(answer, hits, equipment, allow_other_equipment=False):
        fake.check_calls += 1
        fake.allow_flags.append(allow_other_equipment)
        return fake.check_ok

    def generate_customer_guidance(context, hits):
        fake.guidance_calls += 1
        fake.guidance_hits_seen.append(hits)
        if fake.guidance_error:
            raise fake.guidance_error
        return fake.guidance

    monkeypatch.setattr(pipeline.retriever, "search", search)
    monkeypatch.setattr(pipeline.generator, "generate_engineer_answer", generate_engineer_answer)
    monkeypatch.setattr(pipeline.generator, "passes_check", passes_check)
    monkeypatch.setattr(pipeline.generator, "generate_customer_guidance", generate_customer_guidance)
    return fake


# --- 입력 검증 ---------------------------------------------------------
@pytest.mark.parametrize("question", ["", "   ", "\n\t"])
def test_빈_질문은_ValidationError(question):
    with pytest.raises(ValidationError):
        ask(question)


def test_2000자_초과_질문은_ValidationError():
    with pytest.raises(ValidationError):
        ask("가" * 2001)


def test_2000자_질문은_검증을_통과한다():
    assert ask("가" * 2000).verdict == Verdict.ANSWERED


@pytest.mark.parametrize("settings", [
    SearchSettings(manual_k=0, history_k=3),
    SearchSettings(manual_k=3, history_k=0),
])
def test_잘못된_검색_설정은_ValidationError(settings):
    with pytest.raises(ValidationError):
        ask("위치가 어긋나요", settings)


# --- 범위 밖 질문도 검색으로 이어진다 ----------------------------------
@pytest.mark.parametrize("question", [
    "컨베이어 이재 시간 초과 원인이 뭐야?",
    "SRM 포트에서 정위치 정지 실패 원인?",
])
def test_다른_설비나_컨베이어를_말해도_신고_설비로_검색한다(fakes, question):
    answer = ask(question, equipment="AGV")
    assert answer.verdict == Verdict.ANSWERED
    expected_query = f"S-104 주행 위치 편차 {question}"
    assert [
        (kind, equipment.value, query, k)
        for kind, equipment, query, k in fakes.searches
    ] == [
        ("manual", "AGV", expected_query, 3 * pipeline.MANUAL_FETCH_FACTOR),
        ("history", "AGV", expected_query, 3),
    ]


def test_밖_설비_질문에_모델이_근거_부족이라_하면_근거_부족으로_알린다(fakes):
    fakes.generated = HOLD
    answer = ask("지게차 포크가 안 올라가요", equipment="AGV")
    assert answer.verdict == Verdict.INSUFFICIENT_EVIDENCE
    assert answer.steps == []
    assert answer.notice


# --- 엔지니어 답변: 근거 확보·생성·검증 --------------------------------
def test_신고_설비로_검색하고_설정한_건수를_넘긴다(fakes):
    settings = SearchSettings(manual_k=4, history_k=2)
    ask("위치가 어긋나요", settings)
    expected_query = "S-104 주행 위치 편차 위치가 어긋나요"
    assert [
        (kind, equipment.value, query, k)
        for kind, equipment, query, k in fakes.searches
    ] == [
        ("manual", "SRM", expected_query, 4 * pipeline.MANUAL_FETCH_FACTOR),
        ("history", "SRM", expected_query, 2),
    ]


# --- 검색 함수: run_*이 생성기에 넘기는 근거와 같다 ----------------------------
def test_엔지니어_검색_함수는_1차_검색에서_생성기에_넘긴_근거와_같다(fakes):
    fakes.manual_hits = [chunk_hit("manual:srm:6:1", "S-104"), chunk_hit("manual:srm:5:1", "S-999")]
    settings = SearchSettings(manual_k=3, history_k=3)

    searched = pipeline.search_engineer_evidence(make_context(), "위치가 어긋나요", settings)
    ask("위치가 어긋나요", settings)

    assert searched == [chunk_hit("manual:srm:6:1", "S-104"), "history-hit"]  # 다른 코드 청크는 빠지고 이력이 뒤에 붙는다
    assert fakes.hits_seen == [searched]


def test_엔지니어_폴백_검색_함수는_폴백에서_생성기에_넘긴_근거와_같고_설비로_거르지_않는다(fakes):
    other = chunk_hit("manual:agv:5:1", "A-999")
    fakes.manual_hits = [other]
    fakes.generated_seq = [HOLD, ANSWERED]
    settings = SearchSettings(manual_k=3, history_k=3)

    searched = pipeline.search_engineer_fallback_evidence(QUESTION, settings)
    ask(QUESTION, settings, equipment="AGV")

    assert searched == [other, "history-hit"]  # 코드로 거르지 않는다
    assert fakes.hits_seen[1] == searched
    assert [(s[0], s[1]) for s in fakes.searches[-2:]] == [("manual", None), ("history", None)]


def test_고객_안내_검색_함수는_생성기에_넘긴_근거와_같다(fakes):
    fakes.manual_hits = [chunk_hit("manual:srm:6:1", "S-104"), chunk_hit("manual:srm:5:1", "S-999")]
    settings = SearchSettings(manual_k=3, history_k=3)

    searched = pipeline.search_customer_evidence(make_context(), settings)
    ask_customer(settings=settings)

    assert searched == [chunk_hit("manual:srm:6:1", "S-104")]
    assert fakes.guidance_hits_seen == [searched]


def test_다른_코드의_매뉴얼_청크는_빼고_설정한_건수만_남긴다(fakes):
    other = chunk_hit("manual:srm:5:1", "S-999")
    same = chunk_hit("manual:srm:6:1", "S-104")
    common = chunk_hit("manual:srm:7:1")  # 코드가 없는 공통 절은 남긴다
    other_row = chunk_hit("error:SRM:S-999", "S-999")  # 다른 코드의 코드 표 행도 거른다
    same_row = chunk_hit("error:SRM:S-104", "S-104")  # 신고 코드의 표 행은 남긴다
    same_late = chunk_hit("manual:srm:8:1", "S-104")
    fakes.manual_hits = [other, same, other, common, other_row, same_row, same_late]
    ask("위치가 어긋나요")  # manual_k는 3
    assert fakes.hits_seen == [[same, common, same_row, "history-hit"]]


def test_거르고_남은_근거가_모자라도_있는_만큼만_넘긴다(fakes):
    fakes.manual_hits = [chunk_hit("manual:srm:5:1", "S-999")]
    ask("위치가 어긋나요")
    assert fakes.hits_seen == [["history-hit"]]


def test_폴백_검색은_코드로_거르지_않는다(fakes):
    other = chunk_hit("manual:agv:5:1", "A-999")
    fakes.manual_hits = [other]
    fakes.generated_seq = [HOLD, ANSWERED]  # 1차는 거른 뒤 모델이 보류해 폴백한다
    ask(QUESTION, equipment="AGV")
    assert fakes.hits_seen == [["history-hit"], [other, "history-hit"]]


def test_모델이_답하면_생성한_답변을_돌려준다(fakes):
    answer = ask("위치가 어긋나요")
    assert answer is fakes.generated
    assert fakes.check_calls == 1


def test_인용_검증에_실패하면_DependencyError(fakes):
    fakes.check_ok = False
    with pytest.raises(DependencyError):
        ask("위치가 어긋나요")


@pytest.mark.parametrize("hold", [
    Answer.model_construct(verdict=Verdict.INSUFFICIENT_EVIDENCE, steps=[STEP], notice="보류"),
    Answer.model_construct(verdict=Verdict.INSUFFICIENT_EVIDENCE, steps=[], notice=None),
    Answer.model_construct(verdict=Verdict.INSUFFICIENT_EVIDENCE, steps=[], notice="  "),
])
def test_보류_판정이_보류_모양이_아니면_DependencyError(fakes, hold):
    fakes.generated = hold
    with pytest.raises(DependencyError):
        ask("위치가 어긋나요")


def test_검색_장애는_DependencyError_그대로_올라간다(fakes):
    fakes.search_error = DependencyError("검색 장애")
    with pytest.raises(DependencyError, match="검색 장애"):
        ask("위치가 어긋나요")


def test_생성_장애는_DependencyError_그대로_올라간다(fakes):
    fakes.generate_error = DependencyError("모델 장애")
    with pytest.raises(DependencyError, match="모델 장애"):
        ask("위치가 어긋나요")


# --- 엔지니어 답변: 다른 설비 폴백 -------------------------------------
QUESTION = "SRM 포트에서 정위치 정지 실패 원인?"


def test_모델이_답하면_폴백하지_않는다(fakes):
    answer = ask(QUESTION, equipment="AGV")
    assert answer is fakes.generated
    assert fakes.cross_flags == [False]
    assert fakes.allow_flags == [False]
    assert [s[1] for s in fakes.searches] == ["AGV", "AGV"]


def test_모델이_보류하면_필터_없이_질문_원문으로_다시_찾는다(fakes):
    fakes.generated_seq = [HOLD, ANSWERED]
    ask(QUESTION, equipment="AGV")
    first_query = f"S-104 주행 위치 편차 {QUESTION}"  # 1차는 신고 코드·이름을 붙이고, 폴백은 원문만 쓴다
    assert fakes.searches == [
        ("manual", "AGV", first_query, 3 * pipeline.MANUAL_FETCH_FACTOR), ("history", "AGV", first_query, 3),
        ("manual", None, QUESTION, 3), ("history", None, QUESTION, 3),
    ]


def test_폴백_근거로_답하면_다른_설비_안내를_붙이고_인용_검증은_다른_설비를_허용한다(fakes):
    fakes.generated_seq = [HOLD, ANSWERED]
    answer = ask(QUESTION, equipment="AGV")
    assert answer.verdict == Verdict.ANSWERED
    assert answer.steps == ANSWERED.steps
    assert answer.notice == pipeline.CROSS_EQUIPMENT_NOTICE
    assert fakes.cross_flags == [False, True]
    assert fakes.allow_flags == [True]  # 보류는 인용을 검사하지 않고, 폴백 답변만 검사한다


def test_폴백에서도_보류하면_그_보류를_안내_없이_돌려준다(fakes):
    second = Answer(verdict=Verdict.INSUFFICIENT_EVIDENCE, steps=[], notice="두 번째 보류")
    fakes.generated_seq = [HOLD, second]
    assert ask(QUESTION, equipment="AGV") is second
    assert fakes.generate_calls == 2
    assert fakes.check_calls == 0


def test_폴백에서_인용_검증에_실패하면_DependencyError(fakes):
    fakes.generated_seq = [HOLD, ANSWERED]
    fakes.check_ok = False
    with pytest.raises(DependencyError):
        ask(QUESTION, equipment="AGV")


# --- 고객 안내: 매뉴얼 검색·생성·검증 ----------------------------------
def test_신고_코드의_매뉴얼만_검색하고_폴백과_이력은_쓰지_않는다(fakes):
    ask_customer("AGV")
    assert [
        (kind, equipment.value, query, k)
        for kind, equipment, query, k in fakes.searches
    ] == [("manual", "AGV", "S-104 주행 위치 편차", 3 * pipeline.MANUAL_FETCH_FACTOR)]


def test_검색_건수는_설정값을_쓴다(fakes):
    ask_customer(settings=SearchSettings(manual_k=2, history_k=9))
    assert fakes.searches[0][3] == 2 * pipeline.MANUAL_FETCH_FACTOR


def test_다른_코드의_매뉴얼_청크는_안내_근거에서_뺀다(fakes):
    own = chunk_hit("manual:srm:9:1", code="S-104")
    other = chunk_hit("manual:srm:9:2", code="S-105")
    fakes.manual_hits = [other, own]
    ask_customer()
    assert fakes.guidance_hits_seen == [[own]]


def test_검색_결과가_없으면_보류하고_다시_찾지_않으며_생성은_부르지_않는다(fakes):
    fakes.manual_hits = []
    answer = ask_customer()
    assert answer.verdict == Verdict.INSUFFICIENT_EVIDENCE
    assert answer.steps == []
    assert answer.notice
    assert fakes.guidance_calls == 0
    assert len(fakes.searches) == 1  # 폴백 검색을 하지 않는다


def test_근거가_있으면_모델에_맡겨_생성한_안내를_돌려준다(fakes):
    answer = ask_customer()
    assert answer is fakes.guidance
    assert fakes.check_calls == 1


def test_안내_검증에_실패하면_DependencyError(fakes):
    fakes.check_ok = False
    with pytest.raises(DependencyError):
        ask_customer()


def test_안내_생성기가_보류를_돌려주면_검증_없이_그대로_돌려준다(fakes):
    hold = Answer(verdict=Verdict.INSUFFICIENT_EVIDENCE, steps=[], notice="보류")
    fakes.guidance = hold
    assert ask_customer() is hold
    assert fakes.check_calls == 0


@pytest.mark.parametrize("hold", [
    Answer.model_construct(verdict=Verdict.INSUFFICIENT_EVIDENCE, steps=[STEP], notice="보류"),
    Answer.model_construct(verdict=Verdict.INSUFFICIENT_EVIDENCE, steps=[], notice=None),
    Answer.model_construct(verdict=Verdict.INSUFFICIENT_EVIDENCE, steps=[], notice="  "),
])
def test_안내_보류_판정이_보류_모양이_아니면_DependencyError(fakes, hold):
    fakes.guidance = hold
    with pytest.raises(DependencyError):
        ask_customer()


def test_안내용_검색_장애는_DependencyError_그대로_올라간다(fakes):
    fakes.search_error = DependencyError("검색 장애")
    with pytest.raises(DependencyError, match="검색 장애"):
        ask_customer()

def test_안내_생성_장애는_DependencyError_그대로_올라간다(fakes):
    fakes.guidance_error = DependencyError("모델 장애")
    with pytest.raises(DependencyError, match="모델 장애"):
        ask_customer()


# --- 보류 답변 모양 ----------------------------------------------------
def test_보류_답변은_조치_단계가_없다():
    answer = pipeline._make_hold_answer("근거가 부족합니다.")
    assert answer.steps == []
    assert answer.notice == "근거가 부족합니다."
    assert answer.verdict == Verdict.INSUFFICIENT_EVIDENCE