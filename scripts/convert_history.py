"""장애 기록 더미 4종(data/raw/*_error_history_dummy.csv) → data/history_seed.csv(spec 5.3 변환 규칙).

일회성 스크립트이고 보강분은 손으로 쓴다. 소유: MySQL.
"""
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def convert(raw_dir: Path = DATA_DIR / "raw", out_csv: Path = DATA_DIR / "history_seed.csv") -> int:
    """변환한 이력 건수를 돌려준다. 에러코드 표에 없는 코드는 변환 실패다."""
    raise NotImplementedError


if __name__ == "__main__":
    print(convert())
