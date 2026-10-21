"""표 9-4 추출 + data/raw/error_code_table.csv 병합 → data/errorcodes.csv와 대조 리포트(AC-01.2).

일회성 스크립트로 앱이 부르지 않는다. 소유: 벡터 DB·데이터.
"""
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def merge(raw_csv: Path = DATA_DIR / "raw" / "error_code_table.csv",
          manuals_dir: Path = DATA_DIR / "manuals",
          out_csv: Path = DATA_DIR / "errorcodes.csv") -> list[str]:
    """코드 기준으로 합쳐 out_csv를 쓰고, 코드 집합·심각도·조치 구분이 어긋난 행을 리포트 줄로 돌려준다."""
    raise NotImplementedError


if __name__ == "__main__":
    for line in merge():
        print(line)
