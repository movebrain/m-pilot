"""수동 테스트·시연용 가짜 구현 모음. 운영 코드(`mpilot/`)는 이 패키지를 import하지 않는다.

역할:
    DB·RAG가 구현되기 전에도 UI를 확인할 수 있게, 바깥 의존(service 전체, 또는 MySQL·벡터 저장소·매뉴얼 파싱)을
    메모리 구현으로 바꿔 끼우는 도구를 둔다. 사용법과 재현 절차는 `fake/README.md`를 따른다.

책임:
    - `fake_service`: `service` 전체의 가짜. RAG를 거치지 않고 화면만 띄운다(`app.py --fake`).
    - `fake_db`: `rdb`·`vectordb`·`parser`의 가짜와 `install()`. `service`·`rag`는 진짜 코드로 돈다.
    - `run`: 가짜 DB 위에서 고객·엔지니어 앱을 띄우는 진입점(`python -m fake.run`).
    - `test_*`: 위 가짜들과 실행 진입점의 테스트(`python -m pytest`가 함께 모은다).

의존: `mpilot`(바꿔 끼울 대상). 거꾸로 `mpilot/`이 `fake`를 부르지 않는다. 루트 `app.py`만 `--fake`에서
    `fake_service`를 부른다.
소유: UI·총괄 파트
"""
