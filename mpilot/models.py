"""공통 업무 모델: 예외·열거형·업무 객체와 순수 업무 규칙.

역할:
    모든 파트가 주고받는 공통 계약(spec §2.4)과, DB·모델·네트워크 없이 판정할 수 있는
    업무 규칙(spec §2.10)을 한 곳에 정의한다.

책임:
    - 예외 4종(입력·없음·상태·의존), 열거형 10종, 업무 객체를 정의한다.
      업무 객체는 모두 불변(frozen)이며 필드 검증은 pydantic이 맡는다.
    - 경로 결정과 완료 건 차단 같은 순수 규칙 함수를 둔다.
      같은 입력에는 항상 같은 결과를 내야 한다.
    - DB·Chroma·LLM에 접속하지 않고, 다른 mpilot 모듈을 import하지 않는다(의존 규칙 §2.5).

계약 변경:
    필드 이름·타입이나 함수 시그니처를 바꾸면 spec.md §2.4를 같은 PR에서 고치고
    영향받는 파트에 알린다.

소유: 공통
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# ---------------------------------------------------------------------------
# 예외
# ---------------------------------------------------------------------------


class MpilotError(Exception):
    """M-Pilot 업무 예외의 공통 부모. 실패 원인을 구분해 화면에 전달한다."""


class ValidationError(MpilotError):
    """입력 오류, 로그인 실패, 미등록 코드."""


class NotFoundError(MpilotError):
    """없는 접수번호·코드·원문."""


class InvalidStateError(MpilotError):
    """상태 규칙 위반(이미 완료된 건, 셀프 가이드 건 등)."""


class DependencyError(MpilotError):
    """DB·모델·검색 장애, 시간 초과."""


# ---------------------------------------------------------------------------
# 열거형
# ---------------------------------------------------------------------------


class Equipment(StrEnum):
    """신고 대상 설비 4종."""

    MINI_LOADER = "Mini Loader"
    SRM = "SRM"
    AGV = "AGV"
    RTV = "RTV"


class Severity(StrEnum):
    """에러코드 표의 심각도."""

    HIGH = "상"
    MEDIUM = "중"
    LOW = "하"


class ActionOwner(StrEnum):
    """에러코드 표의 조치 구분."""

    CUSTOMER = "고객사 조치"
    ENGINEER = "엔지니어 필요"


class CaseStatus(StrEnum):
    """신고의 진행 단계."""

    RECEIVED = "received"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class Route(StrEnum):
    """신고가 셀프 가이드인지 엔지니어 처리인지."""

    SELF_GUIDE = "self_guide"
    SUPPORT = "support"


class GuideFailure(StrEnum):
    """셀프 가이드 후보가 엔지니어 접수된 사유."""

    HELD = "held"
    GENERATION_FAILED = "generation_failed"


class Source(StrEnum):
    """신고가 시드 이력인지 앱에서 접수한 건인지."""

    SEED = "seed"
    REPORTED = "reported"


class Verdict(StrEnum):
    """답변의 판정."""

    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class CitationKind(StrEnum):
    """인용 종류(검색 컬렉션 이름과 같다)."""

    MANUAL = "manual"
    HISTORY = "history"


class Role(StrEnum):
    """로그인 역할."""

    CUSTOMER = "customer"
    ENGINEER = "engineer"


# ---------------------------------------------------------------------------
# 업무 객체 공통 설정
# ---------------------------------------------------------------------------


class _Model(BaseModel):
    """모든 업무 객체의 부모. 생성 뒤 값을 바꿀 수 없게 한다."""

    model_config = ConfigDict(frozen=True)


Text5000 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]
Text2000 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]

# ---------------------------------------------------------------------------
# 대화·답변
# ---------------------------------------------------------------------------


class Citation(_Model):
    """답변 단계가 가리키는 근거를 식별한다.

    Attributes:
        kind: 인용 종류(`manual` 또는 `history`).
        ref_id: 인용한 검색 문서 ID. `error:<설비>:<코드>`, `manual:<manual_id>:<쪽>:<chunk_no>`,
            `history:<case_id>` 중 하나(spec §3.4).
        locator: 화면에 보이는 출처 표기. 매뉴얼은 쪽·절, 이력은 접수번호·코드.
    """

    kind: CitationKind
    ref_id: str
    locator: str


class AnswerStep(_Model):
    """답변의 조치 단계 한 개와 그 출처.

    Attributes:
        text: 조치 단계 문장.
        citations: 이 단계의 근거 목록. 모든 단계에 1개 이상 있어야 한다.
    """

    text: str
    citations: list[Citation]


class Answer(_Model):
    """RAG 답변의 판정·단계·안내 문구를 담는 구조화 출력.

    보류 판정(`insufficient_evidence`)이면 `steps`는 빈 목록이다.

    Attributes:
        verdict: 답변됨·근거 부족 중 하나.
        steps: 조치 단계 목록. 보류 판정이면 `[]`.
        notice: `answered`가 아닐 때의 한국어 안내 문구. `answered`이면 `None`이다. 다만 신고 설비 자료만으로
            근거가 부족해 다른 설비 자료로 답한 `answered`는 그 사실을 알리는 문구를 담는다.
    """

    verdict: Verdict
    steps: list[AnswerStep]
    notice: str | None


# ---------------------------------------------------------------------------
# 신고
# ---------------------------------------------------------------------------


class ErrorCode(_Model):
    """에러코드 표 한 행. 경로 결정과 답변의 근거가 된다.

    Attributes:
        equipment: 설비. `(equipment, code)`가 키다.
        code: 에러코드. 설비 안에서 유일하다.
        name: 에러명.
        severity: 심각도(상·중·하).
        action_owner: 조치 구분(고객사 조치·엔지니어 필요).
        cause: 코드 표의 원인.
        action: 코드 표의 조치.
        manual_id: 출처 PDF 식별자.
        page: 출처 쪽(1 이상).
        section: 출처 절.
    """

    equipment: Equipment
    code: str
    name: str
    severity: Severity
    action_owner: ActionOwner
    cause: str
    action: str
    manual_id: str
    page: int = Field(ge=1)
    section: str


class CaseInput(_Model):
    """고객이 제출한 신고 입력값.

    Attributes:
        equipment: 신고 대상 설비.
        code: 신고한 에러코드. 비슷한 코드로 대체하지 않는다.
        memo: 증상 메모(선택). 2,000자 이하.
    """

    equipment: Equipment
    code: str
    memo: str | None = Field(max_length=2000)


class Resolution(_Model):
    """엔지니어가 쓴 원인·조치·결과. 입력과 확정 기록이 같은 구조다.

    각 필드는 앞뒤 공백을 제거한 뒤 1~5,000자여야 한다. 셀프 가이드 건에는 없다
    (셀프 가이드 내용은 `ServiceCase.guidance_answer`에 있다).

    Attributes:
        cause: 실제 원인. 고객에게 공개하지 않는다.
        action: 실제 조치. 고객에게 공개한다.
        result: 조치 결과. 고객에게 공개한다.
    """

    cause: Text5000
    action: Text5000
    result: Text5000


class ServiceCase(_Model):
    """신고 건 하나의 현재 상태. 시드 이력도 같은 형식이다.

    허용되는 `route`·`guide_failure`·`status`·`guidance_answer` 조합은 spec §2.10 '허용 조합'을 따른다.
    `completed_at`은 `created_at`보다 빠를 수 없다.

    Attributes:
        case_id: 접수번호. 신규는 `SC-<UUID 앞 8자리>`, 시드는 원본 ID를 보존한다.
        equipment: 설비.
        code: 에러코드.
        memo: 증상 메모(선택).
        source: 시드 이력인지 앱에서 접수한 건인지.
        status: 처리 상태.
        route: 처리 경로.
        guide_failure: 셀프 가이드 후보였으나 엔지니어 접수된 사유. 그 밖에는 `None`.
        guidance_answer: 셀프 가이드 내용·단계·출처. `route`가 `self_guide`일 때만 값이 있다.
        created_at: 신고 시각.
        completed_at: 완료 시각. 완료 전에는 `None`.
    """

    case_id: str
    equipment: Equipment
    code: str
    memo: str | None
    source: Source
    status: CaseStatus
    route: Route
    guide_failure: GuideFailure | None
    guidance_answer: Answer | None
    created_at: datetime
    completed_at: datetime | None


class CaseDetail(_Model):
    """신고·에러코드·조치 기록을 묶는다. 목록과 상세에서 함께 쓴다.

    Attributes:
        service_case: 신고 건.
        error_code: 신고한 에러코드 표의 행.
        resolution: 엔지니어가 확정한 조치 기록. 현장 조치로 완료된 건에만 있다.
    """

    service_case: ServiceCase
    error_code: ErrorCode
    resolution: Resolution | None


class CustomerCaseResult(_Model):
    """고객 목록·상세에 보여 주는 공개 필드만 담는다.

    내부 원인(`Resolution.cause`)과 `guide_failure`는 담지 않는다.

    Attributes:
        case_id: 접수번호.
        equipment: 설비.
        code: 에러코드.
        error_name: 에러명.
        memo: 증상 메모.
        status: 진행 상태.
        created_at: 신고 시각.
        guidance_answer: 셀프 가이드(단계·출처). 셀프 가이드 건에만 있다.
        action: 엔지니어가 등록한 실제 조치. 완료된 기술지원 건에만 있다.
        result: 엔지니어가 등록한 조치 결과. 완료된 기술지원 건에만 있다.
    """

    case_id: str
    equipment: Equipment
    code: str
    error_name: str
    memo: str | None
    status: CaseStatus
    created_at: datetime
    guidance_answer: Answer | None
    action: str | None
    result: str | None


class ChatTurn(_Model):
    """저장된 대화 한 턴.

    Attributes:
        turn_id: 대화 턴 번호(자동 증가). 조회는 이 순서다.
        case_id: 접수번호.
        question: 엔지니어의 질문.
        answer: 답변. 보류 판정도 저장한다.
        created_at: 저장 시각.
    """

    turn_id: int
    case_id: str
    question: str
    answer: Answer
    created_at: datetime


# ---------------------------------------------------------------------------
# RAG 전달
# ---------------------------------------------------------------------------


class CaseContext(_Model):
    """신고 맥락을 pipeline·generator에 전달한다.

    Attributes:
        error_code: 신고한 에러코드의 상세. 대상 코드는 이것으로 고정한다.
        memo: 증상 메모. 확인된 사실과 구분해 다룬다.
    """

    error_code: ErrorCode
    memo: str | None


class ChatRequest(_Model):
    """엔지니어의 현재 질문과 저장된 대화를 한 번에 전달한다.

    Attributes:
        question: 현재 질문. 앞뒤 공백 제거 후 1~2,000자.
        turns: 같은 신고 건의 저장된 대화 이력(오래된 순).
    """

    question: Text2000
    turns: list[ChatTurn]


class SearchHit(_Model):
    """검색 결과 한 건.

    Attributes:
        ref_id: 문서 ID(spec §3.4).
        kind: 문서가 속한 컬렉션(`manual` 또는 `history`).
        equipment: 문서의 설비.
        code: 문서의 에러코드. 코드와 무관한 문서면 `None`.
        text: 문서 원문.
        locator: 화면에 보이는 출처 표기.
        distance: 질의와의 벡터 거리. 거리가 없으면 `None`.
    """

    ref_id: str
    kind: CitationKind
    equipment: Equipment
    code: str | None
    text: str
    locator: str
    distance: float | None


class SearchSettings(_Model):
    """엔지니어 검색 설정.

    Attributes:
        manual_k: 매뉴얼 근거로 가져올 상위 건수. 처음에는 3으로 시작한다.
        history_k: 이력 근거로 가져올 상위 건수. 처음에는 3으로 시작한다.
    """

    manual_k: int
    history_k: int


class Chunk(_Model):
    """매뉴얼 청크 한 개와 출처 메타데이터.

    Attributes:
        manual_id: 출처 PDF 식별자.
        equipment: 매뉴얼의 설비.
        page: 출처 쪽(1 이상).
        section: 출처 절.
        chunk_no: 쪽 안의 청크 번호. 1부터 센다.
        version: 매뉴얼 버전.
        text: 청크 본문.
    """

    manual_id: str
    equipment: Equipment
    page: int = Field(ge=1)
    section: str
    chunk_no: int = Field(ge=1)
    version: str
    text: str


# ---------------------------------------------------------------------------
# 순수 업무 규칙
# ---------------------------------------------------------------------------


def is_self_guide_candidate(error_code: ErrorCode) -> bool:
    """심각도·조치 구분만으로 셀프 가이드 후보 여부를 판정한다(spec §2.10 경로 결정).

    위에서부터 순서대로 적용한다. 1) 심각도 `상`이면 아니오. 2) 조치 구분이 `고객사 조치`이면 예.
    3) 그 밖이면 아니오. 증상 메모·이력·LLM 판단은 쓰지 않는다.

    Args:
        error_code: 신고한 에러코드 표의 행. `severity`와 `action_owner`만 읽는다.

    Returns:
        bool: 셀프 가이드 후보이면 `True`, 엔지니어 처리(`support`)이면 `False`.
    """
    if error_code.severity == Severity.HIGH:
        return False
    return error_code.action_owner == ActionOwner.CUSTOMER


def raise_if_case_completed(case: ServiceCase) -> None:
    """완료된 건에 질문 저장·조치 결과 등록을 못 하게 막는다(spec §2.10 상태 전이).

    셀프 가이드 건은 접수 때 곧바로 `completed`가 되므로 같은 규칙으로 막힌다.

    Args:
        case: 상태를 확인할 신고 건. `status`만 읽는다.

    Returns:
        None: 완료되지 않은 건이면 아무 일도 하지 않는다.

    Raises:
        InvalidStateError: `case.status`가 `completed`일 때('이미 완료').
    """
    if case.status == CaseStatus.COMPLETED:
        raise InvalidStateError("이미 완료")
