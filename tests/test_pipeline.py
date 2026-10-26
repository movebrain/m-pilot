"""pipeline PR1 테스트: 입력 검증, 범위 확인, 보류 답변 모양."""
import pytest


from mpilot.models import (
    CaseContext,
    ChatRequest,
    ErrorCode,
    SearchSettings,
    ValidationError,
    Verdict,
)
from mpilot.rag import pipeline


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


# --- 입력 검증 ---------------------------------------------------------
@pytest.mark.parametrize("question", ["", "   ", "\n\t"])
def test_빈_질문은_ValidationError(question):
    with pytest.raises(ValidationError):
        ask(question)


def test_2000자_초과_질문은_ValidationError():
    with pytest.raises(ValidationError):
        ask("가" * 2001)


def test_2000자_질문은_검증을_통과한다():
    with pytest.raises(NotImplementedError):  # 검증은 통과하고 PR2 구현 전 지점에서 멈춘다
        ask("가" * 2000)


@pytest.mark.parametrize("settings", [
    SearchSettings(manual_k=0, history_k=3, evidence_threshold=0.35),
    SearchSettings(manual_k=3, history_k=0, evidence_threshold=0.35),
    SearchSettings(manual_k=3, history_k=3, evidence_threshold=-0.1),
])
def test_잘못된_검색_설정은_ValidationError(settings):
    with pytest.raises(ValidationError):
        ask("위치가 어긋나요", settings)


# --- 범위 확인 ---------------------------------------------------------
def test_범위_밖_키워드는_not_applicable_보류():
    answer = ask("컨베이어 모터가 고장났어요")
    assert answer.verdict == Verdict("not_applicable")
    assert answer.steps == []
    assert answer.notice


def test_다른_설비만_말하면_not_applicable_보류():
    answer = ask("AGV 배터리는 어떻게 교체해?", equipment="SRM")
    assert answer.verdict == Verdict("not_applicable")
    assert answer.steps == []


def test_신고_설비와_다른_설비를_같이_말하면_범위_안():
    with pytest.raises(NotImplementedError):  # 범위 안이라 다음 단계(PR2)로 넘어간다
        ask("SRM이랑 AGV가 같이 멈췄어요", equipment="SRM")


def test_설비를_말하지_않은_질문은_범위_안():
    with pytest.raises(NotImplementedError):
        ask("리셋해도 다시 떠요")


def test_별칭도_인식한다():
    with pytest.raises(NotImplementedError):
        ask("스태커 크레인이 멈췄어요", equipment="SRM")


# --- 보류 답변 모양 ----------------------------------------------------
def test_보류_답변은_조치_단계가_없다():
    answer = pipeline._hold(Verdict("insufficient_evidence"), "근거가 부족합니다.")
    assert answer.steps == []
    assert answer.notice == "근거가 부족합니다."
    assert answer.verdict == Verdict("insufficient_evidence")
