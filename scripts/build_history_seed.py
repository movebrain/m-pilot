"""시드 이력 CSV 생성 스크립트(`python -m scripts.build_history_seed`).

역할:
    원자료 장애 이력 4종(`data/raw/*_error_history_dummy.csv`, 한글 열 이름)을 합쳐
    `init_db`가 적재하는 시드 이력(`data/history_seed.csv`, 9열)을 만든다(spec §3.4).

책임:
    - 필요한 원자료 열 9개를 새 열 이름으로 옮기고, 나머지 열(정지시간, 호기, 에러명 등)은 버린다.
    - 값은 그대로 옮긴다. 시각만 `2026-07-01 08:35`(한국 시간)를 `2026-07-01T08:35:00+09:00`으로 바꾼다.
    - 필요한 열이 없거나 시각 형식이 다르면 보정하지 않고 파일을 쓰지 않은 채 중단한다.
      그 밖의 값 검증(필수값, 코드 존재, 설비당 50건 등)은 적재 함수(`rdb.load_seed_history_from_csv`)가 맡는다.
    - 행은 `case_id` 순으로 정렬해, 같은 원자료로 다시 돌리면 같은 파일이 나오게 한다.
    - 원자료 파일은 수정하지 않는다.
    - 앱 예외(`MpilotError` 하위)는 안내 메시지를 출력하고 종료 코드 1로 바꾼다.

의존: `models`(의존 규칙 §2.5).
소유: DB 파트
"""

from __future__ import annotations

import csv
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mpilot.models import DependencyError, MpilotError, ValidationError

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
RAW_PATTERN = "*_error_history_dummy.csv"
OUTPUT_PATH = ROOT / "data" / "history_seed.csv"  # AppConfig.history_seed_path와 같은 위치

# 새 열 → 원자료 열(spec §3.4 history_seed.csv)
COLUMN_MAP = {
    "case_id": "이력ID",
    "equipment": "모델명",
    "code": "에러 코드",
    "memo": "현장 증상",
    "cause": "원인",
    "action": "조치 내용",
    "result": "결과",
    "created_at": "발생일시",
    "completed_at": "복구일시",
}
OUTPUT_COLUMNS = list(COLUMN_MAP)
TIME_COLUMNS = ("created_at", "completed_at")
RAW_TIME_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")  # 예: 2026-07-01 08:35
KST = timezone(timedelta(hours=9))  # 원자료 시각은 한국 시간이다


def to_iso_kst(value: str) -> str:
    """원자료 시각(`YYYY-MM-DD HH:MM`, 한국 시간)을 시간대가 붙은 ISO 8601 문자열로 바꾼다.

    Args:
        value: 원자료 시각 문자열.

    Returns:
        str: 예) `2026-07-01T08:35:00+09:00`.

    Raises:
        ValidationError: 형식이 다르거나 없는 날짜·시각일 때(짐작해서 고치지 않는다).
    """
    if not RAW_TIME_PATTERN.fullmatch(value):
        raise ValidationError(f"시각 형식이 'YYYY-MM-DD HH:MM'이 아닙니다: {value!r}")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M")
    except ValueError:
        raise ValidationError(f"없는 날짜·시각입니다: {value!r}") from None
    return parsed.replace(tzinfo=KST).isoformat()


def read_raw_history(path: Path) -> list[dict]:
    """원자료 이력 CSV 한 개를 읽어 시드 이력 9열 행으로 바꾼다.

    Args:
        path: 원자료 이력 CSV 경로.

    Returns:
        list[dict]: `OUTPUT_COLUMNS` 키를 가진 행 목록. 파일 순서 그대로.

    Raises:
        ValidationError: 필요한 열이 없거나 시각 형식이 다를 때.
        DependencyError: 파일을 읽을 수 없을 때.
    """
    name = Path(path).name
    try:
        with Path(path).open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            missing = [raw for raw in COLUMN_MAP.values() if raw not in (reader.fieldnames or [])]
            if missing:
                raise ValidationError(f"{name}: 필요한 열이 없습니다: {missing}")
            raw_rows = list(reader)
    except (OSError, UnicodeDecodeError) as e:
        raise DependencyError(f"파일을 읽을 수 없습니다({type(e).__name__}): {path}") from None

    rows = []
    for no, raw in enumerate(raw_rows, start=1):
        row = {new: raw[old] for new, old in COLUMN_MAP.items()}
        if any(value is None for value in row.values()):  # 열이 모자란 행
            raise ValidationError(f"{name} {no}번째 행: 열 개수가 머리글보다 적습니다.")
        try:
            for column in TIME_COLUMNS:
                row[column] = to_iso_kst(row[column])
        except ValidationError as e:
            raise ValidationError(f"{name} {no}번째 행 {COLUMN_MAP[column]}: {e}") from None
        rows.append(row)
    return rows


def build_rows(paths: list[Path]) -> list[dict]:
    """원자료 이력 여러 개를 합쳐 `case_id` 순으로 정렬한다.

    Args:
        paths: 원자료 이력 CSV 경로 목록.

    Returns:
        list[dict]: 시드 이력 9열 행 목록(`case_id` 오름차순).

    Raises:
        ValidationError: 원자료 파일이 없거나, 어느 파일에 필요한 열이 없거나 시각 형식이 다를 때.
        DependencyError: 파일을 읽을 수 없을 때.
    """
    if not paths:
        raise ValidationError(f"원자료 이력 파일({RAW_PATTERN})이 없습니다.")
    rows = []
    for path in paths:
        rows.extend(read_raw_history(path))
    return sorted(rows, key=lambda row: row["case_id"])


def write_rows(path: Path, rows: list[dict]) -> None:
    """시드 이력 행을 UTF-8 CSV로 쓴다.

    Args:
        path: 쓸 경로.
        rows: `build_rows` 결과.

    Raises:
        DependencyError: 파일을 쓸 수 없을 때.
    """
    try:
        with Path(path).open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
    except OSError as e:
        raise DependencyError(f"파일을 쓸 수 없습니다({type(e).__name__}): {path}") from None


def main() -> int:
    """원자료 이력 4종으로 `data/history_seed.csv`를 만들고 성공 0·실패 1을 반환한다.

    Returns:
        int: 파일을 썼으면 `0`, 앱 예외가 났으면 `1`(파일은 쓰지 않는다).
    """
    try:
        rows = build_rows(sorted(RAW_DIR.glob(RAW_PATTERN)))
        write_rows(OUTPUT_PATH, rows)
    except MpilotError as e:
        print(f"시드 이력을 만들지 못했습니다({type(e).__name__}): {e}", file=sys.stderr)
        return 1

    print(f"{OUTPUT_PATH.name} 생성: {len(rows)}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
