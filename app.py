"""앱 조립·실행: 설정·모듈 초기화, 화면 생성.

역할:
    `python app.py`로 실행하는 진입점이다. 설정을 읽고 DB·검색·모델을 연결한 뒤 Gradio Blocks 화면을 만들어 띄운다.

책임:
    - 시작 순서: 설정 로드 → `service.set_config` → `rdb.connect` → 임베딩으로 `vectordb.connect` →
      모델로 `generator.set_chat_model` → `service.prepare_search_index`(끝난 뒤에야 로그인 화면이 열린다)
      → 화면 조립 → 실행.
    - 화면 상태 3개(`role`, `case_id`, `screen`)를 `gr.State`로 만들어 모든 화면 함수에 같게 넘긴다.
    - 모든 화면에 "모든 데이터는 가상" 배너를 보인다.
    - 업무 규칙은 두지 않는다. 규칙은 `models`·`service`에 있다.

의존: `ui/*`, `service`, `config`, `models`, `db.rdb`, `db.vectordb`, `rag.generator`(의존 규칙 §2.5).
소유: UI·총괄 파트
"""

from __future__ import annotations


def main() -> None:
    """앱을 조립해 실행한다.

    Returns:
        None: Gradio 서버를 띄우고, 종료될 때까지 돌려주지 않는다.

    Raises:
        ValidationError: 설정이 틀렸을 때.
        DependencyError: DB·임베딩·모델·검색 색인 준비에 실패했을 때. 앱은 시작하지 않는다.
    """
    ...
