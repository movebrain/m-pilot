"""앱에서 접수한 신고의 저장(셀프 가이드·지원 요청)과 신고 목록·상세 조회.

역할:
    고객이 제출한 신고를 처리 경로에 맞는 상태로 저장하고, 고객·엔지니어 화면이 볼 신고를 읽어 준다.

책임:
    - 신고를 상태 전이 규칙(spec §2.10)에 맞춰 저장한다. 셀프 가이드 건은 곧바로 `completed`,
      엔지니어 처리 건은 `received`로 저장한다. 접수번호(PK)가 충돌하면 재시도 없이 오류로 알린다.
    - 목록은 최신순으로 돌려준다. 고객에게 어떤 필드를 보일지는 `service`가 정한다.
    - 모델 필드 `memo`·`guidance_answer`를 DB 컬럼 `customer_memo`·`self_guide_answer`로 바꿔 저장한다(spec §3.3).

의존: `rdb._common`, `schemas`, `models`만 import한다(의존 규칙 §2.5).
호출: `service`(패키지 `rdb`를 거쳐).
소유: 손재호
"""

from __future__ import annotations

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from mpilot.db.rdb._common import (
    _get_engine,
    _load_case_detail,
    _now_for_db,
    _row_to_error_code,
    _row_to_resolution,
    _row_to_service_case,
)
from mpilot.db.schemas import error_codes, resolutions, service_cases
from mpilot.models import (
    Answer,
    CaseDetail,
    CaseInput,
    CaseStatus,
    DependencyError,
    GuideFailure,
    Route,
    Source,
    ValidationError,
    Verdict,
)

MEMO_MAX_CHARS = 2000  # 증상 메모 최대 글자 수(spec §3.3 customer_memo)


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
    # 1) 필터 값을 확인한다. 틀린 값을 조용히 무시하면 엉뚱한 목록이 나가므로 오류로 알린다.
    try:
        status = CaseStatus(status) if status is not None else None
        source = Source(source) if source is not None else None
    except ValueError:
        raise ValidationError(f"허용되지 않은 조회 조건입니다: status={status}, source={source}") from None

    # 2) 신고 건에 조치 기록을 외부 조인한다(조치 기록이 없는 건도 나오게).
    query = (
        select(service_cases, resolutions.c.cause, resolutions.c.action, resolutions.c.result)
        .outerjoin(resolutions, resolutions.c.case_id == service_cases.c.case_id)
        # 최신순. 신고 시각이 같으면 접수번호 역순으로 순서를 고정한다.
        .order_by(service_cases.c.created_at.desc(), service_cases.c.case_id.desc())
    )
    if status is not None:
        query = query.where(service_cases.c.status == status.value)
    if source is not None:
        query = query.where(service_cases.c.source == source.value)

    try:
        with _get_engine().connect() as conn:
            rows = conn.execute(query).all()
            code_rows = conn.execute(select(error_codes)).all()
    except SQLAlchemyError as e:
        raise DependencyError(f"신고 목록을 조회하지 못했습니다({type(e).__name__}).") from None

    # 3) 에러코드 행을 (설비, 코드)로 붙인다. 외래키가 있어 반드시 있다.
    codes = {(r.equipment, r.code): _row_to_error_code(r) for r in code_rows}
    return [
        CaseDetail(
            service_case=_row_to_service_case(row),
            error_code=codes[(row.equipment, row.code)],
            # 외부 조인이라 조치 기록이 없으면 세 컬럼이 모두 NULL이다.
            resolution=_row_to_resolution(row) if row.cause is not None else None,
        )
        for row in rows
    ]


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
    try:
        with _get_engine().connect() as conn:
            return _load_case_detail(conn, case_id)
    except SQLAlchemyError as e:
        raise DependencyError(f"신고 상세를 조회하지 못했습니다({type(e).__name__}).") from None


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
    # 보류 판정 안내를 고객 가이드로 남기면 근거 없는 안내가 되므로 DB에 가기 전에 막는다.
    if guidance_answer.verdict != Verdict.ANSWERED:
        raise ValidationError("셀프 가이드로 저장할 수 있는 안내는 answered 판정뿐입니다.")
    return _insert_reported_case(
        case_id,
        data,
        route=Route.SELF_GUIDE,
        status=CaseStatus.COMPLETED,
        guide_failure=None,
        guidance_answer=guidance_answer,
    )


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
    try:
        guide_failure = GuideFailure(guide_failure) if guide_failure is not None else None
    except ValueError:
        raise ValidationError(f"허용되지 않은 guide_failure 값입니다: {guide_failure}") from None
    return _insert_reported_case(
        case_id,
        data,
        route=Route.SUPPORT,
        status=CaseStatus.RECEIVED,
        guide_failure=guide_failure,
        guidance_answer=None,
    )


def _insert_reported_case(
    case_id: str,
    data: CaseInput,
    *,
    route: Route,
    status: CaseStatus,
    guide_failure: GuideFailure | None,
    guidance_answer: Answer | None,
) -> CaseDetail:
    """앱에서 접수한 신고 한 건을 저장하고 다시 읽어 돌려준다(두 저장 함수의 공통 부분).

    `source`는 `reported`로 고정한다. 완료 상태로 저장하면 `completed_at`을 신고 시각과 같게 둔다.

    Args:
        case_id: 새 접수번호.
        data: 고객이 제출한 설비·에러코드·증상 메모.
        route: 처리 경로.
        status: 접수 직후 상태(`self_guide`면 `completed`, `support`면 `received`).
        guide_failure: 가이드 실패 사유. 해당이 없으면 `None`.
        guidance_answer: 셀프 가이드 안내. `support`면 `None`.

    Returns:
        CaseDetail: 저장한 신고 건과 신고한 에러코드 행(조치 기록은 없다).

    Raises:
        ValidationError: 미등록 코드나 메모 길이 초과일 때.
        DependencyError: DB 저장에 실패했을 때(접수번호 충돌 포함).
    """
    # 1) 메모 길이를 확인한다. CaseInput이 이미 검사하지만 저장 직전에 한 번 더 본다.
    if data.memo is not None and len(data.memo) > MEMO_MAX_CHARS:
        raise ValidationError(f"증상 메모는 {MEMO_MAX_CHARS:,}자 이하여야 합니다.")

    engine = _get_engine()
    now = _now_for_db()
    try:
        with engine.begin() as conn:
            # 2) 신고한 코드가 에러코드 표에 있어야 한다. 외래키 오류로 두면 DB 장애처럼 보이므로 먼저 확인한다.
            known = conn.execute(
                select(error_codes.c.code).where(
                    error_codes.c.equipment == data.equipment.value,
                    error_codes.c.code == data.code,
                )
            ).first()
            if known is None:
                raise ValidationError(f"에러코드 표에 없는 코드입니다: {data.equipment}/{data.code}")

            # 3) 신고 건을 저장한다. 모델 필드 memo·guidance_answer를 DB 컬럼 이름으로 바꿔 넣는다.
            conn.execute(
                insert(service_cases).values(
                    case_id=case_id,
                    equipment=data.equipment.value,
                    code=data.code,
                    customer_memo=data.memo,
                    source=Source.REPORTED.value,
                    status=status.value,
                    route=route.value,
                    guide_failure=guide_failure.value if guide_failure is not None else None,
                    self_guide_answer=(
                        guidance_answer.model_dump_json() if guidance_answer is not None else None
                    ),
                    created_at=now,
                    completed_at=now if status == CaseStatus.COMPLETED else None,
                )
            )

            # 4) 저장한 결과를 다시 읽어 돌려준다.
            detail = _load_case_detail(conn, case_id)
    except IntegrityError:
        # 접수번호(PK) 충돌. 재시도는 하지 않는다(spec §2.3.1).
        raise DependencyError(f"접수번호가 이미 있어 저장하지 못했습니다: {case_id}") from None
    except SQLAlchemyError as e:
        raise DependencyError(f"신고를 저장하지 못했습니다({type(e).__name__}).") from None
    return detail
