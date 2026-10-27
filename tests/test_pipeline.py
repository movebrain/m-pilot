"""pipeline 테스트: 입력 검증, 범위 확인, 근거 확보·생성·검증 연결, 고객 안내, 보류 답변 모양."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError as PydanticValidationError

from mpilot.models import (
    Answer,
    AnswerStep,
    CaseContext,
    ChatRequest,
    DependencyError,
    ErrorCode,
    SearchSettings,
    ValidationError,
    Verdict,
)
from mpilot.rag import pipeline


# 보류 판정에 잘못 붙은 단계(검증 전 모양만 시험하므로 내용은 상관없다)
STEP = AnswerStep.model_construct(text="조치", citations=[])


def make_context(equipment: str = "SRM", code: str = "S-104") -> CaseContext:
    error_code = ErrorCode(
        equipment=equipment, code=code, name="주행 위치 편차", severity="하",
        action_owner="고객사 조치", cause="엔코더 커넥터", action="커넥터 재체결",
        manual_id="SRM_SPEC_v1", page=41, section="9.4",
        customer_procedure_ref_ids=[], required_safety_rules=[],
    )
    return CaseContext(error_code=error_code, memo="")


def ask(question: str, settings: SearchSettings | None = None, equipment: str = "SRM"):
    return pipeline.run_engineer_answer(
        make_context(equipment), ChatRequest.model_construct(question=question, turns=[]),
        settings or SearchSettings(manual_k=3, history_k=3, evidence_threshold=0.35),
    )


def ask_customer(equipment: str = "SRM"):
    return pipeline.run_customer_guidance(make_context(equipment))


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    """retriever와 generator를 가짜로 바꿔, pipeline이 부품을 잇는 방식만 시험한다.

    시험에서 `fakes.enough = False`처럼 값을 바꿔 상황을 만든다.
    """
    fake = SimpleNamespace(
        # 엔지니어 답변
        enough=True,  # 근거가 충분한가
        check_ok=True,  # 인용 검증을 통과하는가
        search_error=None,  # 검색 장애로 던질 예외
        generate_error=None,  # 생성 장애로 던질 예외
        generated=Answer.model_construct(verdict=Verdict.ANSWERED, steps=[], notice=None),
        searches=[],  # 검색 호출 기록
        generate_calls=0,
        check_calls=0,
        # 고객 안내
        procedures=["procedure-hit"],  # 허용 절차 (비우면 절차 없음)
        guidance_check_ok=True,  # 인용·안전 문구 검증을 통과하는가
        fetch_error=None,  # 절차 조회 장애로 던질 예외
        guidance_error=None,  # 안내 생성 장애로 던질 예외
        guidance=Answer.model_construct(verdict=Verdict.ANSWERED, steps=[], notice=None),
        fetched=[],  # 절차 조회 기록(에러코드)
        guidance_calls=0,
        guidance_check_calls=0,
    )

    def search_manual(equipment, query, k):
        if fake.search_error:
            raise fake.search_error
        fake.searches.append(("manual", equipment, query, k))
        return ["manual-hit"]

    def search_history(equipment, query, k):
        fake.searches.append(("history", equipment, query, k))
        return ["history-hit"]

    def generate_engineer_answer(context, hits, request):
        fake.generate_calls += 1
        if fake.generate_error:
            raise fake.generate_error
        return fake.generated

    def passes_answer_check(answer, hits, equipment):
        fake.check_calls += 1
        return fake.check_ok

    def fetch_customer_procedures(error_code):
        if fake.fetch_error:
            raise fake.fetch_error
        fake.fetched.append(error_code.code)
        return fake.procedures

    def generate_customer_guidance(context, hits):
        fake.guidance_calls += 1
        if fake.guidance_error:
            raise fake.guidance_error
        return fake.guidance

    def passes_guidance_check(answer, hits, error_code):
        fake.guidance_check_calls += 1
        return fake.guidance_check_ok

    monkeypatch.setattr(pipeline.retriever, "search_manual", search_manual)
    monkeypatch.setattr(pipeline.retriever, "search_history", search_history)
    monkeypatch.setattr(pipeline.retriever, "has_enough_evidence", lambda hits, threshold: fake.enough)
    monkeypatch.setattr(pipeline.retriever, "fetch_customer_procedures", fetch_customer_procedures)
    monkeypatch.setattr(pipeline.generator, "generate_engineer_answer", generate_engineer_answer)
    monkeypatch.setattr(pipeline.generator, "passes_answer_check", passes_answer_check)
    monkeypatch.setattr(pipeline.generator, "generate_customer_guidance", generate_customer_guidance)
    monkeypatch.setattr(pipeline.generator, "passes_guidance_check", passes_guidance_check)
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
    SearchSettings(manual_k=0, history_k=3, evidence_threshold=0.35),
    SearchSettings(manual_k=3, history_k=0, evidence_threshold=0.35),
])
def test_잘못된_검색_설정은_ValidationError(settings):
    with pytest.raises(ValidationError):
        ask("위치가 어긋나요", settings)


def test_음수_임계값은_SearchSettings_생성_오류():
    with pytest.raises(PydanticValidationError):
        SearchSettings(manual_k=3, history_k=3, evidence_threshold=-0.1)


# --- 범위 확인 ---------------------------------------------------------
def test_범위_밖_키워드는_not_applicable_보류(fakes):
    answer = ask("컨베이어 모터가 고장났어요")
    assert answer.verdict == Verdict.NOT_APPLICABLE
    assert answer.steps == []
    assert answer.notice
    assert fakes.searches == []  # 범위 밖이면 검색하지 않는다


def test_다른_설비만_말하면_not_applicable_보류():
    answer = ask("AGV 배터리는 어떻게 교체해?", equipment="SRM")
    assert answer.verdict == Verdict.NOT_APPLICABLE
    assert answer.steps == []


def test_신고_설비와_다른_설비를_같이_말하면_범위_안():
    assert ask("SRM이랑 AGV가 같이 멈췄어요", equipment="SRM").verdict == Verdict.ANSWERED


def test_설비를_말하지_않은_질문은_범위_안():
    assert ask("리셋해도 다시 떠요").verdict == Verdict.ANSWERED


def test_별칭도_인식한다():
    assert ask("스태커 크레인이 멈췄어요", equipment="SRM").verdict == Verdict.ANSWERED


# --- 엔지니어 답변: 근거 확보·생성·검증 --------------------------------
def test_신고_설비로_검색하고_설정한_건수를_넘긴다(fakes):
    settings = SearchSettings(manual_k=4, history_k=2, evidence_threshold=0.35)
    ask("위치가 어긋나요", settings)
    assert fakes.searches == [
        ("manual", "SRM", "위치가 어긋나요", 4),
        ("history", "SRM", "위치가 어긋나요", 2),
    ]


def test_근거가_부족하면_보류하고_생성은_부르지_않는다(fakes):
    fakes.enough = False
    answer = ask("위치가 어긋나요")
    assert answer.verdict == Verdict.INSUFFICIENT_EVIDENCE
    assert answer.steps == []
    assert answer.notice
    assert fakes.generate_calls == 0


def test_근거가_충분하면_생성한_답변을_돌려준다(fakes):
    answer = ask("위치가 어긋나요")
    assert answer is fakes.generated
    assert fakes.check_calls == 1


def test_인용_검증에_실패하면_DependencyError(fakes):
    fakes.check_ok = False
    with pytest.raises(DependencyError):
        ask("위치가 어긋나요")


def test_생성기가_보류를_돌려주면_검증_없이_그대로_돌려준다(fakes):
    hold = Answer(verdict=Verdict.INSUFFICIENT_EVIDENCE, steps=[], notice="보류")
    fakes.generated = hold
    assert ask("위치가 어긋나요") is hold
    assert fakes.check_calls == 0


@pytest.mark.parametrize("hold", [
    Answer.model_construct(verdict=Verdict.INSUFFICIENT_EVIDENCE, steps=[STEP], notice="보류"),
    Answer.model_construct(verdict=Verdict.NOT_APPLICABLE, steps=[], notice=None),
    Answer.model_construct(verdict=Verdict.NOT_APPLICABLE, steps=[], notice="  "),
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


# --- 고객 안내: 절차 조회·생성·검증 ------------------------------------
def test_신고한_에러코드의_허용_절차를_조회하고_검색은_하지_않는다(fakes):
    ask_customer()
    assert fakes.fetched == ["S-104"]
    assert fakes.searches == []


def test_허용_절차가_없으면_보류하고_생성은_부르지_않는다(fakes):
    fakes.procedures = []
    answer = ask_customer()
    assert answer.verdict == Verdict.INSUFFICIENT_EVIDENCE
    assert answer.steps == []
    assert answer.notice
    assert fakes.guidance_calls == 0


def test_허용_절차가_있으면_생성한_안내를_돌려준다(fakes):
    answer = ask_customer()
    assert answer is fakes.guidance
    assert fakes.guidance_check_calls == 1


def test_안내_검증에_실패하면_DependencyError(fakes):
    fakes.guidance_check_ok = False
    with pytest.raises(DependencyError):
        ask_customer()


def test_안내_생성기가_보류를_돌려주면_검증_없이_그대로_돌려준다(fakes):
    hold = Answer(verdict=Verdict.INSUFFICIENT_EVIDENCE, steps=[], notice="보류")
    fakes.guidance = hold
    assert ask_customer() is hold
    assert fakes.guidance_check_calls == 0


@pytest.mark.parametrize("hold", [
    Answer.model_construct(verdict=Verdict.INSUFFICIENT_EVIDENCE, steps=[STEP], notice="보류"),
    Answer.model_construct(verdict=Verdict.NOT_APPLICABLE, steps=[], notice=None),
    Answer.model_construct(verdict=Verdict.NOT_APPLICABLE, steps=[], notice="  "),
])
def test_안내_보류_판정이_보류_모양이_아니면_DependencyError(fakes, hold):
    fakes.guidance = hold
    with pytest.raises(DependencyError):
        ask_customer()


def test_절차_조회_장애는_DependencyError_그대로_올라간다(fakes):
    fakes.fetch_error = DependencyError("조회 장애")
    with pytest.raises(DependencyError, match="조회 장애"):
        ask_customer()


def test_안내_생성_장애는_DependencyError_그대로_올라간다(fakes):
    fakes.guidance_error = DependencyError("모델 장애")
    with pytest.raises(DependencyError, match="모델 장애"):
        ask_customer()


# --- 보류 답변 모양 ----------------------------------------------------
def test_보류_답변은_조치_단계가_없다():
    answer = pipeline._make_hold_answer(Verdict.INSUFFICIENT_EVIDENCE, "근거가 부족합니다.")
    assert answer.steps == []
    assert answer.notice == "근거가 부족합니다."
    assert answer.verdict == Verdict.INSUFFICIENT_EVIDENCE