"""data/raw/에서 개발용 샘플(에러코드 10행, 이력 5건)을 뽑아 data/sample/에 쓴다(AC-05.1).

실제 데이터(US-01 errorcodes.csv, US-05 history_seed.csv)가 나오기 전에 인덱싱·검색 파트가 쓸 자료다.
에러코드는 설비 혼동 함정 6개와 설비별 저위험 코드 1개씩, 이력은 그 코드들의 이력에서 고른다.
cause·action은 표 9-4 병합(US-01) 전이라 비운다. 이력 변환은 spec 5.3 규칙을 따른다. 소유: 서비스 이력.
"""
import csv
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
RAW, SAMPLE = DATA_DIR / "raw", DATA_DIR / "sample"

TRAP_CODES = ["R206", "S115", "M406", "S121", "M414", "A308"]
LOW_RISK_CODES = ["M404", "S101", "A305", "R207"]
ERRORCODE_COLUMNS = ["equipment", "code", "name", "severity", "action_owner", "cause", "action", "extra"]
HISTORY_COLUMNS = ["case_id", "equipment", "code", "memo", "engineer_action", "cause_found",
                   "resolution", "resolution_note", "actor", "created_at", "diverged"]
UNRESOLVED = {"임시 조치(재발 감시)", "미해결"}


def read(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write(path: Path, columns: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def to_case(h: dict) -> dict:
    """spec 5.3 변환 규칙. 결과 6종은 해결/미해결로 줄이고 원문은 resolution_note에 남긴다."""
    return {
        "case_id": h["이력ID"], "equipment": h["모델명"], "code": h["에러 코드"],
        "memo": f'{h["현장 증상"]} {h["확인한 상태값"]}'.strip(),
        "engineer_action": h["조치 내용"], "cause_found": h["원인"],
        "resolution": "미해결" if h["결과"] in UNRESOLVED else "해결",
        "resolution_note": h["결과"], "actor": h["조치자"], "created_at": h["발생일시"], "diverged": "",
    }


def main() -> None:
    codes = TRAP_CODES + LOW_RISK_CODES
    table = {r["code"]: r for r in read(RAW / "error_code_table.csv")}
    errorcodes = [{**table[c], "cause": "", "action": "", "extra": ""} for c in codes]

    history = [h for f in sorted(RAW.glob("*_error_history_dummy.csv")) for h in read(f) if h["에러 코드"] in codes]
    picked = {}
    for h in sorted(history, key=lambda h: h["결과"] not in UNRESOLVED):  # 실패 이력 1건이 먼저 들어가게
        picked.setdefault(h["에러 코드"], h)
    cases = [to_case(h) for h in list(picked.values())[:5]]

    SAMPLE.mkdir(parents=True, exist_ok=True)
    write(SAMPLE / "errorcodes.csv", ERRORCODE_COLUMNS, errorcodes)
    write(SAMPLE / "history_seed.csv", HISTORY_COLUMNS, cases)
    print(f"errorcodes {len(errorcodes)}행, history {len(cases)}건 → {SAMPLE}")


if __name__ == "__main__":
    main()
