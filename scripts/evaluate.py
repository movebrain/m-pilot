"""RAG 품질 평가 실행·기록 스크립트(수동 실행, CI에서는 돌리지 않는다).

역할:
    `data/eval_questions.csv`의 문항을 `rag.pipeline`으로 실행하고 결과를 JSON 한 개로 남긴다.

책임(spec §2.7):
    - 목적(`customer_guidance`, `engineer_chat`)별로 따로 집계한다. 문항은 목적별 10문항 이상이며
      정상 답변·근거 부족(신고 설비 자료에 근거가 없는 질문)을 모두 포함하고 4종 설비에 나눈다.
      다른 설비·컨베이어를 언급해도 신고 설비 자료에 근거가 있으면 정상 답변 대상이다.
      문항은 데이터를 만든 사람이 아닌 다른 사람이 쓴다.
    - 지표와 합격선: 인용 검증 100%(공개된 답변의 인용이 모두 확보한 근거에 있고 설비가 일치),
      정상 답변율 80% 이상(초기 목표이며 실측값이 아니다), 보류 정확도 90% 이상.
      정상 문항의 분모가 0이면 평가 미완료로 처리한다.
    - 기록 항목: `versions`(데이터·모델·프롬프트 버전), `settings`(검색 설정), `results`(질문별 판정·인용),
      `summary`(목적별 지표). 합격선을 바꾸면 이유를 평가 결과에 남긴다.
    - 인용 검증은 이 스크립트가 다시 검사한다. 고객 안내의 안전 수칙 준수는 자동 검증하지 않아 지표로 세지 않는다
      (`passes_check`가 실행 때마다 막는다).
    - 사람 검수(생성된 고객 안내 전부, 엔지니어 `answered` 답변 최소 5건)는 이 스크립트 밖에서 한다.

의존: `config`, `db.rdb`, `models`, `rag.pipeline`을 쓸 수 있다.
소유: RAG 파트
"""

from __future__ import annotations

from pathlib import Path


def run_eval(questions_path: Path, output_dir: Path) -> dict[str, object]:
    """평가 문항을 실행하고 결과를 기록한다.

    Args:
        questions_path: 평가 문항 CSV 경로(`data/eval_questions.csv`).
        output_dir: 결과 JSON을 쓸 폴더.

    Returns:
        dict[str, object]: `versions`, `settings`, `results`, `summary` 키를 가진 평가 결과 한 개.
            같은 내용이 `output_dir`에 JSON으로 저장된다.

    Raises:
        ValidationError: 문항 파일의 형식·필수 열이 틀렸거나 목적별 문항 수·구성이 기준에 못 미칠 때.
        DependencyError: 모델·검색·DB 장애나 파일 쓰기 실패일 때.
    """
    ...
