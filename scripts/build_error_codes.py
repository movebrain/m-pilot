"""에러코드 표 정본 생성 스크립트(`python -m scripts.build_error_codes`).

역할:
    원자료 에러코드 표(`data/raw/error_code_table.csv`, 5열)와 매뉴얼 4종의 에러 코드 일람 표(9.2절)를 합쳐
    `error_codes` 11열 정본(`data/errorcodes.csv`)을 만든다(spec §3.4).

책임:
    - `equipment`·`code`·`name`·`severity`·`action_owner`는 원자료 CSV 값을 그대로 쓴다.
      매뉴얼 표의 값과 다르면 고치지 않고 경고로만 알린다(심각도·조치 구분은 사람이 정한 값이다).
    - `cause`·`action`·`manual_id`·`page`·`section`은 `parser.parse_manuals`가 만든 9.2절 표 청크에서
      그 코드의 행을 찾아 채운다. 출처는 그 행이 들어 있는 청크의 쪽·절이다.
    - `required_safety_rules`는 사람이 쓴다. 이미 정본 파일이 있으면 그 값을 코드별로 이어받고, 없으면 `[]`다.
    - 원자료에 있는 코드가 매뉴얼 표에 없거나 표 행 모양이 다르면 파일을 쓰지 않고 중단한다.
    - 앱 예외(`MpilotError` 하위)는 안내 메시지를 출력하고 종료 코드 1로 바꾼다.

의존: `db.parser`, `models`(의존 규칙 §2.5).
소유: DB 파트
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

from mpilot.db.parser import parse_manuals
from mpilot.models import Chunk, DependencyError, ErrorCode, MpilotError, ValidationError

ROOT = Path(__file__).resolve().parent.parent
RAW_CODES_PATH = ROOT / "data" / "raw" / "error_code_table.csv"
MANUALS_DIR = ROOT / "data" / "manuals"
OUTPUT_PATH = ROOT / "data" / "errorcodes.csv"  # AppConfig.codes_path와 같은 위치

RAW_COLUMNS = ["equipment", "code", "name", "severity", "action_owner"]
OUTPUT_COLUMNS = RAW_COLUMNS + [
    "cause", "action", "manual_id", "page", "section", "required_safety_rules",
]
CODE_TABLE_SECTION = "9.2"  # 매뉴얼 4종 모두 9.2절이 에러 코드 일람이다
# 표 9-4 머리글: 코드 | 명칭 | 대상 모델 | 심각도 | 조치 구분 | 발생 조건 · 원인 | 조치
CODE_TABLE_WIDTH = 7
_CELL_SEP = re.compile(r"(?<!\\) \| ")  # parser가 셀 안의 `|`를 `\|`로 이스케이프한다


def read_raw_codes(path: Path) -> list[dict]:
    """원자료 에러코드 표 CSV를 읽는다.

    Args:
        path: `data/raw/error_code_table.csv` 경로.

    Returns:
        list[dict]: 행마다 `RAW_COLUMNS` 5개 키를 가진 사전. 파일 순서 그대로.

    Raises:
        ValidationError: 머리글이 다르거나 빈 값·중복 `(equipment, code)`가 있을 때.
        DependencyError: 파일을 읽을 수 없을 때.
    """
    try:
        with Path(path).open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != RAW_COLUMNS:
                raise ValidationError(f"{Path(path).name}: 머리글이 {RAW_COLUMNS}와 다릅니다.")
            rows = list(reader)
    except OSError as e:
        raise DependencyError(f"파일을 읽을 수 없습니다({type(e).__name__}): {path}") from None

    seen = set()
    for no, row in enumerate(rows, start=1):
        if not all(isinstance(row[c], str) and row[c].strip() for c in RAW_COLUMNS):
            raise ValidationError(f"{Path(path).name} {no}번째 행: 빈 값이 있습니다.")
        key = (row["equipment"], row["code"])
        if key in seen:
            raise ValidationError(f"{Path(path).name} {no}번째 행: (equipment, code) 중복 {key}")
        seen.add(key)
    return rows


def _split_markdown_row(line: str) -> list[str]:
    """parser가 만든 마크다운 표 한 줄(`| a | b |`)을 셀 목록으로 나눈다.

    Args:
        line: 표 한 줄.

    Returns:
        list[str]: 셀 글자 목록. 이스케이프된 `\\|`는 `|`로 되돌린다.
    """
    inner = line.strip()[2:-2]  # 앞의 "| "와 뒤의 " |"를 뗀다
    return [cell.replace("\\|", "|") for cell in _CELL_SEP.split(inner)]


def read_manual_code_rows(chunks: list[Chunk]) -> dict[tuple[str, str], dict]:
    """매뉴얼 청크에서 에러 코드 일람(9.2절) 표의 행을 코드별로 모은다.

    Args:
        chunks: `parse_manuals`가 만든 매뉴얼 4종의 청크.

    Returns:
        dict[tuple[str, str], dict]: `(equipment, code)` → 매뉴얼 행 값
            (`name`, `severity`, `action_owner`, `cause`, `action`, `manual_id`, `page`, `section`).

    Raises:
        ValidationError: 표 행의 칸 수가 다르거나 같은 코드가 두 번 나올 때.
    """
    rows: dict[tuple[str, str], dict] = {}
    for chunk in chunks:
        if not chunk.section.startswith(CODE_TABLE_SECTION + " "):
            continue
        ref_id = f"manual:{chunk.manual_id}:{chunk.page}:{chunk.chunk_no}"
        for line in chunk.text.splitlines():
            if not line.startswith("| "):
                continue  # 캡션·본문 줄
            cells = _split_markdown_row(line)
            if cells[0] in ("코드", "---"):
                continue  # 머리글·구분선
            if len(cells) != CODE_TABLE_WIDTH:
                raise ValidationError(f"{ref_id}: 에러 코드 표 행의 칸 수가 {len(cells)}개입니다: {cells[0]}")
            code, name, _models, severity, action_owner, cause, action = cells
            key = (chunk.equipment.value, code)
            if key in rows:
                raise ValidationError(f"매뉴얼 에러 코드 표에 같은 코드가 두 번 있습니다: {key}")
            rows[key] = {
                "name": name,
                "severity": severity,
                "action_owner": action_owner,
                "cause": cause,
                "action": action,
                "manual_id": chunk.manual_id,
                "page": chunk.page,
                "section": chunk.section,
            }
    return rows


def read_existing_safety_rules(path: Path) -> dict[tuple[str, str], list[str]]:
    """이미 있는 정본 파일에서 사람이 쓴 `required_safety_rules`를 코드별로 읽는다.

    Args:
        path: 정본 파일 경로(`data/errorcodes.csv`). 없으면 빈 사전.

    Returns:
        dict[tuple[str, str], list[str]]: `(equipment, code)` → 안전 문구 목록.

    Raises:
        ValidationError: 그 열의 값이 JSON 문자열 배열이 아닐 때.
    """
    path = Path(path)
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    rules = {}
    for row in rows:
        try:
            value = json.loads(row.get("required_safety_rules") or "[]")
        except ValueError:
            value = None
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValidationError(
                f"{path.name}: {row.get('equipment')}/{row.get('code')}의 required_safety_rules가 "
                "JSON 문자열 배열이 아닙니다."
            )
        rules[(row["equipment"], row["code"])] = value
    return rules


def _same_text(a: str, b: str) -> bool:
    """공백을 무시하고 같은 글자인지 본다(PDF 추출이 단어 중간에 공백을 넣는 경우가 있다)."""
    return "".join(a.split()) == "".join(b.split())


def build_rows(
    raw_rows: list[dict],
    manual_rows: dict[tuple[str, str], dict],
    safety_rules: dict[tuple[str, str], list[str]],
) -> tuple[list[ErrorCode], list[str]]:
    """원자료 행과 매뉴얼 행을 합쳐 에러코드 표 11열을 만든다.

    Args:
        raw_rows: `read_raw_codes` 결과.
        manual_rows: `read_manual_code_rows` 결과.
        safety_rules: `read_existing_safety_rules` 결과.

    Returns:
        tuple[list[ErrorCode], list[str]]: 원자료 순서의 에러코드 행과, 고치지 않고 알리기만 하는 경고 목록
            (원자료와 매뉴얼의 이름·심각도·조치 구분 불일치, 원자료에 없는 매뉴얼 코드).

    Raises:
        ValidationError: 원자료 코드가 매뉴얼 표에 없거나, 합친 값이 `ErrorCode` 규칙을 어길 때.
    """
    codes, warnings = [], []
    missing = [f"{r['equipment']}/{r['code']}" for r in raw_rows
               if (r["equipment"], r["code"]) not in manual_rows]
    if missing:
        raise ValidationError(f"매뉴얼 에러 코드 표에 없는 코드: {', '.join(missing)}")

    for raw in raw_rows:
        key = (raw["equipment"], raw["code"])
        manual = manual_rows[key]
        for column in ("name", "severity", "action_owner"):
            if not _same_text(raw[column], manual[column]):
                warnings.append(
                    f"{key[0]}/{key[1]} {column}: 원자료 '{raw[column]}', 매뉴얼 '{manual[column]}'(원자료 값을 씀)"
                )
        try:
            codes.append(ErrorCode(
                equipment=raw["equipment"],
                code=raw["code"],
                name=raw["name"],
                severity=raw["severity"],
                action_owner=raw["action_owner"],
                cause=manual["cause"],
                action=manual["action"],
                manual_id=manual["manual_id"],
                page=manual["page"],
                section=manual["section"],
                required_safety_rules=safety_rules.get(key, []),
            ))
        except ValueError as e:  # pydantic 검증 오류
            raise ValidationError(f"{key[0]}/{key[1]} 값이 올바르지 않습니다: {e}") from None

    raw_keys = {(r["equipment"], r["code"]) for r in raw_rows}
    for key in sorted(manual_rows.keys() - raw_keys):
        warnings.append(f"{key[0]}/{key[1]}: 매뉴얼 표에는 있지만 원자료에 없어 넣지 않음")
    return codes, warnings


def write_codes(path: Path, codes: list[ErrorCode]) -> None:
    """에러코드 행을 정본 CSV로 쓴다. `required_safety_rules` 열은 JSON 배열 문자열이다.

    Args:
        path: 쓸 경로.
        codes: `build_rows`가 만든 에러코드 행.

    Raises:
        DependencyError: 파일을 쓸 수 없을 때.
    """
    try:
        with Path(path).open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
            writer.writeheader()
            for c in codes:
                writer.writerow({
                    **c.model_dump(mode="json"),
                    "required_safety_rules": json.dumps(c.required_safety_rules, ensure_ascii=False),
                })
    except OSError as e:
        raise DependencyError(f"파일을 쓸 수 없습니다({type(e).__name__}): {path}") from None


def main() -> int:
    """원자료와 매뉴얼로 `data/errorcodes.csv`를 만들고 성공 0·실패 1을 반환한다.

    Returns:
        int: 파일을 썼으면 `0`, 앱 예외가 났으면 `1`. 경고가 있어도 파일은 쓰고 `0`이다.
    """
    try:
        raw_rows = read_raw_codes(RAW_CODES_PATH)
        manual_rows = read_manual_code_rows(parse_manuals(MANUALS_DIR))
        safety_rules = read_existing_safety_rules(OUTPUT_PATH)
        codes, warnings = build_rows(raw_rows, manual_rows, safety_rules)
        write_codes(OUTPUT_PATH, codes)
    except MpilotError as e:
        print(f"에러코드 표를 만들지 못했습니다({type(e).__name__}): {e}", file=sys.stderr)
        return 1

    for warning in warnings:
        print(f"[경고] {warning}")
    print(f"{OUTPUT_PATH.name} 생성: {len(codes)}건(경고 {len(warnings)}건)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
