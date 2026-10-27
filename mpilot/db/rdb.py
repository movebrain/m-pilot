"""신고·대화·조치의 저장·조회와 초기 자료 적재(MySQL, SQLAlchemy Core).

역할:
    관계형 DB가 신고 건·조치 기록·대화·에러코드 표의 정본이다. 이 모듈이 그 저장과 조회를 맡는다.

책임:
    - 에러코드 CSV와 시드 이력 CSV를 검증한 뒤 적재한다. 값을 자동으로 보정하지 않고, 오류가 있으면 중단한다.
    - 신고를 상태 전이 규칙(spec §2.10)에 맞춰 저장한다. 완료된 건에는 질문·조치 결과를 더 저장하지 않는다.
      완료 처리는 조건부 UPDATE로 보호한다(중복 제출·동시 수정 방지는 이것뿐이다).
    - 목록은 최신순으로 돌려준다. 고객에게 어떤 필드를 보일지는 `service`가 정한다.
    - 시각 컬럼(DATETIME)에는 UTC 기준, 시간대 없는 값으로 저장하고, 꺼낼 때 UTC 시간대를 붙여
      datetime으로 돌려준다. `completed_at`은 `created_at`보다 빠를 수 없다.
    - DB 컬럼 `customer_memo`·`self_guide_answer`와 모델 필드 `memo`·`guidance_answer`를
      서로 변환한다(spec §3.3). 컬럼 이름은 이 모듈 밖으로 내보내지 않는다.
    - SQL과 DB 접속은 이 모듈(과 `schemas`)에만 둔다. 모듈을 import할 때 DB에 접속하지 않는다.
    - Chroma 검색 색인은 다루지 않는다. 색인은 `vectordb`가 맡는다.

의존: `schemas`, `models`만 import한다(의존 규칙 §2.5). RAG 모듈은 부르지 않는다.
호출: `service`, `app`, `scripts.init_db`.
소유: DB 파트
"""

from __future__ import annotations

from pathlib import Path

from mpilot.models import (
    Answer,
    CaseDetail,
    CaseInput,
    CaseStatus,
    ChatTurn,
    ErrorCode,
    Equipment,
    GuideFailure,
    Resolution,
    Source,
)


def connect(db_url: str) -> None:
    """관계형 DB 연결을 준비한다.

    Args:
        db_url: SQLAlchemy 접속 URL(예: `mysql+pymysql://...`, 비밀번호 포함).
            `AppConfig.db_url`의 값이며 로그에 남기지 않는다.

    Returns:
        None: 모듈 안에 엔진을 보관한다. 이후 함수는 이 연결을 쓴다.

    Raises:
        ValidationError: URL 형식이 틀렸을 때.
        DependencyError: DB에 연결할 수 없을 때.
    """
    ...


def reset_schema() -> None:
    """모든 테이블을 삭제하고 다시 만든다.

    `scripts.init_db`가 실행할 때마다 부른다. 시연 중에 다시 부르면 새로 확정한 이력이 사라진다.

    Returns:
        None: 빈 테이블 4개(`error_codes`, `service_cases`, `resolutions`, `chat_turns`)가 만들어진다.

    Raises:
        DependencyError: 테이블 삭제·생성에 실패했을 때.
    """
    ...


def load_error_codes_from_csv(path: Path) -> list[ErrorCode]:
    """에러코드 CSV를 검증해 적재한다.

    필수값, `(equipment, code)` 중복, 설비 4종, 출처(`manual_id`, `page`, `section`)를 검증한다.
    두 목록 열은 JSON 배열 문자열이며 비어 있으면 `[]`다. 틀린 값은 고치지 않고 적재를 멈춘다.

    Args:
        path: 에러코드 CSV 경로(`AppConfig.codes_path`, `data/errorcodes.csv`).

    Returns:
        list[ErrorCode]: 적재한 에러코드 표의 행 목록.

    Raises:
        ValidationError: 필수값 누락, 중복 키, 허용 밖 설비, 출처 누락 등 CSV 오류가 있을 때.
        DependencyError: 파일을 읽거나 DB에 쓰는 데 실패했을 때.
    """
    ...


def fetch_error_codes(equipment: Equipment) -> list[ErrorCode]:
    """설비별 에러코드를 조회한다.

    Args:
        equipment: 조회할 설비.

    Returns:
        list[ErrorCode]: 그 설비의 에러코드 표 행 목록. 없으면 빈 목록.

    Raises:
        ValidationError: 허용되지 않은 설비일 때.
        DependencyError: DB 조회에 실패했을 때.
    """
    ...


def load_seed_history_from_csv(path: Path) -> int:
    """시드 이력 CSV를 검증해 적재하고 적재 건수를 반환한다.

    설비당 50건, 총 200건이어야 한다. 시드는 `source=seed`, `route=support`, `guide_failure=NULL`,
    `self_guide_answer=NULL`, `status=completed`로 고정해 `service_cases`와 `resolutions`에 쓴다.
    CSV `memo` 열은 `service_cases.customer_memo`에 넣는다. 시간은 ISO 8601(시간대 포함)을
    UTC로 바꾼 뒤 시간대 없는 DATETIME으로 저장한다. 코드·필수값 오류면 중단한다.

    Args:
        path: 시드 이력 CSV 경로(`AppConfig.history_seed_path`, `data/history_seed.csv`).

    Returns:
        int: 적재한 이력 건수(정상이면 200).

    Raises:
        ValidationError: 에러코드 표에 없는 코드, 필수값 누락, 건수 불일치, 완료 시각이 신고 시각보다 빠를 때.
        DependencyError: 파일을 읽거나 DB에 쓰는 데 실패했을 때.
    """
    ...


def fetch_cases(
    status: CaseStatus | None = None, source: Source | None = None
) -> list[CaseDetail]:
    """신고 목록을 최신순으로 조회한다.

    Args:
        status: 지정하면 그 상태의 신고만 돌려준다. `None`이면 모든 상태.
        source: 지정하면 시드 이력(`seed`) 또는 앱 접수 건(`reported`)만 돌려준다. `None`이면 모두.

    Returns:
        list[CaseDetail]: `created_at` 내림차순 신고 목록. 조치 기록이 있으면 `resolution`이 채워진다.

    Raises:
        ValidationError: 허용되지 않은 `status`·`source` 값일 때.
        DependencyError: DB 조회에 실패했을 때.
    """
    ...


def fetch_case_detail(case_id: str) -> CaseDetail:
    """신고 상세를 조회한다.

    Args:
        case_id: 접수번호.

    Returns:
        CaseDetail: 신고 건, 신고한 에러코드 행, 조치 기록(있으면).

    Raises:
        NotFoundError: 그 접수번호의 신고가 없을 때.
        DependencyError: DB 조회에 실패했을 때.
    """
    ...


def save_self_guide_case(
    case_id: str, data: CaseInput, guidance_answer: Answer
) -> CaseDetail:
    """셀프 가이드 건을 완료 상태로 저장한다.

    `source=reported`, `route=self_guide`, `status=completed`, `guide_failure=NULL`로 저장하고
    가이드의 내용·단계·출처를 `service_cases.self_guide_answer`에 JSON으로 남긴다.
    `resolutions` 행은 만들지 않는다.

    Args:
        case_id: 새 접수번호(`SC-<UUID 앞 8자리>`). PK가 충돌하면 재시도 없이 오류로 알린다.
        data: 고객이 제출한 설비·에러코드·증상 메모.
        guidance_answer: 생성·검증을 통과한 고객 안내. `verdict`는 `answered`여야 한다.

    Returns:
        CaseDetail: 저장된 신고 건(상태 `completed`)과 신고한 에러코드 행.

    Raises:
        ValidationError: 미등록 코드, 메모 길이 초과, `answered`가 아닌 안내일 때.
        DependencyError: DB 저장에 실패했을 때(접수번호 충돌 포함).
    """
    ...


def save_support_case(
    case_id: str, data: CaseInput, guide_failure: GuideFailure | None
) -> CaseDetail:
    """엔지니어 처리 건을 접수 상태로 저장한다.

    `source=reported`, `route=support`, `status=received`로 저장한다.

    Args:
        case_id: 새 접수번호(`SC-<UUID 앞 8자리>`). PK가 충돌하면 재시도 없이 오류로 알린다.
        data: 고객이 제출한 설비·에러코드·증상 메모.
        guide_failure: 셀프 가이드 후보였다가 넘어온 사유(`held`, `generation_failed`).
            처음부터 엔지니어 처리 대상이면 `None`.

    Returns:
        CaseDetail: 저장된 신고 건(상태 `received`)과 신고한 에러코드 행.

    Raises:
        ValidationError: 미등록 코드나 메모 길이 초과일 때.
        DependencyError: DB 저장에 실패했을 때(접수번호 충돌 포함).
    """
    ...


def fetch_chat_turns(case_id: str) -> list[ChatTurn]:
    """신고의 대화 이력을 조회한다.

    Args:
        case_id: 접수번호.

    Returns:
        list[ChatTurn]: `turn_id` 순(오래된 순) 대화 목록. 대화가 없으면 빈 목록.

    Raises:
        DependencyError: DB 조회에 실패했을 때.
    """
    ...


def save_chat_turn(case_id: str, question: str, answer: Answer) -> ChatTurn:
    """질문·답변을 저장하고 첫 질문이면 조치 중으로 바꾼다.

    보류 판정(`insufficient_evidence`, `not_applicable`)의 답변도 저장한다. 상태가 `received`이면
    `in_progress`로 바꾸고, 이미 `in_progress`이면 상태는 그대로 둔다. 완료된 건은 저장하지 않는다.

    Args:
        case_id: 접수번호.
        question: 엔지니어의 질문. 앞뒤 공백을 제거한 뒤 1~2,000자.
        answer: 그 질문에 대한 답변(보류 판정 포함).

    Returns:
        ChatTurn: 저장된 대화 한 턴(자동 증가한 `turn_id` 포함).

    Raises:
        ValidationError: 질문이 비었거나 길이 범위를 벗어났을 때.
        NotFoundError: 그 접수번호의 신고가 없을 때.
        InvalidStateError: 이미 완료된 건이거나 셀프 가이드 건일 때('이미 완료').
        DependencyError: DB 저장에 실패했을 때.
    """
    ...


def save_confirmed_resolution(case_id: str, record: Resolution) -> CaseDetail:
    """조치 결과를 저장하고 조치 완료로 확정한다.

    `resolutions` 행을 만들고 `status`를 `completed`로 바꾸며 `completed_at`을 기록한다.
    등록과 완료 처리는 한 동작이다. 같은 건을 동시에 완료하는 일은 조건부 UPDATE로 막는다.
    확정 뒤에는 수정하지 않는다.

    Args:
        case_id: 접수번호.
        record: 엔지니어가 쓴 원인·실제 조치·결과. 각 필드는 앞뒤 공백 제거 후 1~5,000자.

    Returns:
        CaseDetail: 완료된 신고 건과 확정된 조치 기록.

    Raises:
        ValidationError: 필드가 비었거나 길이 범위를 벗어났을 때.
        NotFoundError: 그 접수번호의 신고가 없을 때.
        InvalidStateError: 이미 완료된 건이거나 셀프 가이드 건일 때('이미 완료').
        DependencyError: DB 저장에 실패했을 때.
    """
    ...


def fetch_resolved_cases() -> list[CaseDetail]:
    """조치 기록이 있는 신고를 조회한다.

    검색 색인의 `history` 컬렉션을 새로 만들 때 쓴다. 시드 이력도 포함한다.
    셀프 가이드 건에는 `resolutions` 행이 없어 포함되지 않는다.

    Returns:
        list[CaseDetail]: `resolution`이 채워진 신고 목록.

    Raises:
        DependencyError: DB 조회에 실패했을 때.
    """
    ...
