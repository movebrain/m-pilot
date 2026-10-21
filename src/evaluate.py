"""화면 없이 엔진을 직접 호출해 평가 지표를 계산한다(spec 8장). 소유: 서비스 이력.

베이스라인 스텁이다. 담당이 같은 시그니처의 실제 구현으로 덮어쓴다(spec 6장).
"""
from pathlib import Path

from errorcodes import DATA_DIR

QUESTIONS_CSV = DATA_DIR / "eval_questions.csv"


def run_eval(questions_csv: Path = QUESTIONS_CSV) -> dict[str, float]:
    """평가 질문 → 지표 리포트(지표 이름 → 값). 고정 스냅샷에서 같은 결과를 낸다(AC-17.1)."""
    return {}


if __name__ == "__main__":
    for metric, value in run_eval().items():
        print(f"{metric}: {value}")
