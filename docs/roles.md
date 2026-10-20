# 역할 분담과 병렬 개발 규칙

- 근거 문서: [intent.md](intent.md)(SOT), [spec.md](spec.md)(계약). 이 문서는 팀 운영(누가, 어떤 파일을, 어떤 순서로)만 다루고 계약은 spec.md가 정한다.
- 표기: 장·절 번호만 쓴 참조(4장, 5.3, 6장, 7.1, 7.2, 7.4, 8장)는 spec.md의 것이다.
- 상태: 안(案). 개발 시작 전에 7명이 합의한다.

7명 = DB 3명(벡터 DB 2, MySQL 1) + RAG·UI 4명(고객 접수, 엔지니어 세션, RAG 검색, 서비스 이력)이다. 별도 테스트·평가 인원은 없다. 각 파트가 자기 스토리의 계약 테스트를 직접 쓰고, 평가와 통합 시나리오 점검은 서비스 이력 담당이 겸한다. 스토리 하나는 담당 파트 한 곳이 책임지고(7.1), 파트마다 소유 파일이 달라 같은 파일을 동시에 고치지 않는다. 서로의 모듈은 가짜 구현으로 대신해 기다리지 않고 개발한다.

제출물 4종(PM·발표, README, 시연 영상, 결과보고서)은 파트 배정과 별도로 rohdonggyun-lab이 맡는다(7명 중 한 명이 파트 업무와 겸한다).

## 1. 파트별 역할

| 그룹   | 파트           | 담당 스토리 | 하는 일                                                                                                                                                                               |
| ------ | -------------- | ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| DB     | 벡터 DB·데이터 | US-01       | 표 9-4와 에러코드 CSV를 병합해 `errorcodes.csv` 249개를 만들고 검증한다. 사양서 본문을 쪽·절 청크로 나눈다                                                                             |
| DB     | 벡터 DB·인덱싱 | US-02, 03   | Chroma `manual`·`history`를 만들고, 설비 필터 검색(`query`)과 인용 원문 조회(`get_source`), 확정 이력 추가(`add_history`, 복합 건은 쌍별 문서, 검색은 `case_id` 중복 제거)를 제공한다 |
| DB     | MySQL          | US-04, 05   | MySQL 환경, 접수 건·쌍(`case_errors`)·대화 저장과 상태 전이, 가상 이력(원본 200건 변환 + 보강분)                             |
| RAG·UI | 고객 접수      | US-06~11    | 신고 폼(사진 포함, 설비-에러코드 쌍 1~4개 입력), 접수번호 조회, 기술지원 대기 안내, 저위험 분기(`route`), 셀프 가이드, 배너, `app.py` Tab 조립                                       |
| RAG·UI | 엔지니어 세션  | US-12~16    | 접수 목록과 세션 화면, 요약·대응 방안(`brief`, 복합 건은 쌍별 `retrieve`), 질문 답변(`ask`, 인용 검증), 근거 부족·적용 대상 아님 안내                             |
| RAG·UI | RAG 검색       | US-18~21    | 질문 분석, 에러코드 키워드 기반 조회, 매뉴얼·이력 검색, 적용 대상 아님·근거 부족 판정(`decide_verdict`), τ                                                                                          |
| RAG·UI | 서비스 이력      | US-17, 22~24    | 가짜 전환(`registry`)과 저장소 공통 파일(`requirements.txt`, `pyproject.toml`, `.gitignore`, `.env.example`), 이력 작성 화면, 이력 구조화, 확정과 인덱스 추가(`confirm_case`), 평가 실행(`run_eval`)과 통합 시나리오 점검, 독립 조회·평가 화면(P3)                                                                                              |

파트별 소유와 인계:

- **벡터 DB·데이터**: 코드 `src/errorcodes.py`(타입 `ErrorCode`), `src/parsing.py`, `scripts/merge_errorcodes.py`. 데이터 `errorcodes.csv` 249개, `data/manuals/` 관리. 제공: `errorcodes` 로더, `errorcodes.csv`, 본문 청크. 의존: 없음(사양서 개정본은 수령 완료). 검수: MySQL의 `history_seed.csv`
- **벡터 DB·인덱싱**: 코드 `src/indexing.py`(`python -m indexing --reset`). 데이터 Chroma 컬렉션(저장소 제외, `build_index`로 재생성), 가짜 응답 `data/sample/indexing.json`. 제공: `indexing.query`, `get_source`, `add_history`. 의존: `errorcodes`, `parsing`, `history_seed.csv`
- **MySQL**: 코드 `src/db.py`(타입 `ServiceCase`, 대화 기록, `load_seed`, `transition`), `scripts/init_db.py`(`--reset`, `--check`), `scripts/convert_history.py`, `scripts/schema.sql`, `docker-compose.yml`(MySQL), `tests/conftest.py`(`real_db` 픽스처). 데이터 `history_seed.csv`(원본 200건 변환 + 복합·함정 보강분), `data/sample/`. 제공: MySQL 환경(`docker-compose.yml`), `db`(가장 먼저). 검수: `errorcodes.csv`(저위험 78개 문구의 안전 수칙 부합, 다른 절 참조·전문 용어). 보강: 복합 장애·함정 이력을 쓴다(5.3, 작성자는 MySQL 담당을 기본값으로 한다(팀 확인 필요))
- **고객 접수**: 코드 `app.py`(Tab 조립, 배너), `src/ui/customer.py`, `src/routing.py`. 의존: `db`, `errorcodes`
- **엔지니어 세션**: 코드 `src/ui/engineer.py`(목록·세션), `src/answer.py`(타입 `Answer` 등, 프롬프트 2종). 데이터 가짜 응답 `data/sample/answer.json`. 제공: `brief`, `ask`. 의존: `search`(`retrieve`, `decide_verdict`), `db`, `indexing.get_source`
- **RAG 검색**: 코드 `src/search.py`(타입 `QueryAnalysis`, `Evidence`, 질문 분석 프롬프트), τ 조정. 데이터 가짜 응답 `data/sample/search.json`. 제공: `retrieve`, `lookup_error_code`, `decide_verdict`. 의존: `indexing.query`, `errorcodes`
- **서비스 이력**: 서비스 조치 이력의 작성·확정이 본업이고, 평가·통합과 저장소 공통 장치(`registry` 등)도 맡는다. 코드 `src/registry.py`, `src/history.py`(프롬프트 1종), `src/ui/history_editor.py`(이력 작성, P3 독립 조회·평가 화면은 구현할 때 `src/ui/lookup.py`로 추가), `src/evaluate.py`, `tests/test_integration.py`, `.env.example`, `.gitignore`, `requirements.txt`, `pyproject.toml`. 데이터 `eval_questions.csv`, 가짜 응답 `data/sample/history.json`, 통합 시나리오 실행, 인용 샘플 확인. 제공: `registry`(가장 먼저), `structure_resolution`, `confirm_case`, `run_eval`. 의존: `db`, `indexing.add_history`, `search`·`answer`(`run_eval`과 US-24의 `retrieve`, `ask`). 평가 질문은 `expected_ref`를 벡터 DB·데이터와 MySQL 담당이 확인한다

## 2. 병렬 개발 규칙

- 계약: 6장 시그니처와 5장 타입이 계약이다. 타입은 소유 모듈 파일에 두고 소유자만 바꾼다. 소유자가 바꾸면 spec.md를 같이 고치고 의존하는 파트에 알린다.
- 가짜 먼저: 개발 초기에 `indexing`(벡터 DB·인덱싱), `search`(RAG 검색), `answer`(엔지니어 세션), `history`(서비스 이력)가 `data/sample/<모듈>.json`에 함수별 고정 응답을 올린다. JSON 형식은 5.4 타입을 그대로 옮긴 것이고, 바꾸면 타입과 같이 바꾼다. MySQL은 `docker-compose.yml`과 `db`의 첫 버전(접수·조회·목록), 샘플 데이터를, 서비스 이력 담당은 `registry`를 올린다.
- 사용 방법: `indexing`, `search`, `answer`, `history`는 `registry.get("search")`처럼 받아 쓰고, `MPILOT_FAKES=search,answer`처럼 모듈별로 가짜와 실제를 전환한다. `db`는 가짜 모듈이 없으므로 직접 import하되, 단위 테스트에서는 연결을 목으로 바꿔 DB 없이 돈다. 실제 MySQL(`docker compose up`)은 `init_db`, 시연, 통합 실행과 SQL 동작 테스트에만 쓴다.
- 테스트: 각 파트는 자기 스토리의 7.2 인수 기준을 `tests/test_<모듈>.py`로 가짜 위에서 먼저 쓴다. `db`는 연결을 목으로 바꿔 쓰고, 원자성·중복 거부·콜레이션·동시 접수처럼 SQL이 실제로 하는 일에 의존하는 AC는 `real_db` 마커를 달아 시연용과 분리된 테스트 DB에서 확인한다(테스트마다 비운다). `real_db`는 로컬에서는 선택 실행하고 GitHub Actions가 MySQL로 반드시 돌려 필수 체크로 머지를 막는다. 테스트 DB 접속 정보는 환경 변수(`.env.example`)로 받는다. CI에는 API 키가 없으므로 `indexing`, `search`, `answer`, `history`는 항상 가짜로 돌린다(워크플로 파일은 첫 구현이 올라온 뒤에 추가한다). 서비스 이력 담당은 통합 테스트(7.4)와 평가를 맡고, 평가 질문은 에러코드·이력 데이터를 만들지 않은 서비스 이력 담당이 쓴다. Gradio 화면에서만 확인되는 AC(렌더링·문구·배치: AC-06.1의 폼 제약, 07.2, 09.1, 13.3, 22.1)는 pytest 대신 수동 체크리스트로 점검하고, 핸들러가 부르는 함수 호출 순서와 저장값은 pytest로 쓴다.
- 파일 경계: 엔지니어 Tab은 `src/ui/engineer.py`(엔지니어 세션), `src/ui/history_editor.py`(서비스 이력)로 나눠 같은 화면 파일을 두 사람이 고치지 않는다. 각 화면은 `app.py`(고객 접수)가 Tab에 올린다.
- 실제 구현으로 교체하는 순서는 `db` → `errorcodes.csv` → `indexing` → `search` → `answer`·`history` → 화면 통합이다. 각 교체는 해당 스토리의 인수 기준 검증이 통과해야 한다.
- 상호 검수: 벡터 DB·데이터와 MySQL이 서로의 자료를 검수한다.

## 3. 리스크와 완충

- **병목은 MySQL과 서비스 이력의 첫 산출물이다.** MySQL 환경(`docker-compose.yml`), `db`(MySQL)와 `registry`(서비스 이력)가 늦으면 전원이 막힌다. 환경, 접수·조회, `registry`부터 먼저 올린다.
- **벡터 DB 두 파트의 인계.** 인덱싱은 데이터 파트의 `errorcodes.csv`와 청크를 기다린다. 인계 전까지 `data/sample/`로 개발하고, 인계 후 `build_index`만 다시 돌린다.
- **엔지니어 세션은 UI와 `answer`(US-12~16)에 집중한다.** 평가·통합(US-17)과 저장소 공통 파일은 P1 통합 전까지 가벼운 서비스 이력 담당이 맡고, 평가 질문은 데이터를 만들지 않은 서비스 이력 담당이 쓴다. 서비스 이력 담당이 P2 이력 작성과 겹쳐 밀리면 질문 작성만 엔지니어 세션이 돕는다(데이터를 만들지 않은 사람이라는 조건을 지킨다).
- **P1 범위가 크다**(intent 열린 질문 1). 두 화면은 가짜 위에서 먼저 끝까지 돌리고, P1 통합에서 실제 구현으로 바꾼다.
- **엔지니어 Tab 파일 경계.** `src/ui/engineer.py`(엔지니어 세션), `src/ui/history_editor.py`(서비스 이력)로 나뉘어 있다. 화면 조립은 `app.py`(고객 접수)가 맡으므로 화면 함수의 이름과 인자는 개발 시작 전에 합의한다.
- 팀원별 MySQL 상태 차이는 8장 스냅샷 절차(`--reset`)를 따른다.
- **이력 변환·보강은 MySQL 파트의 몫이 커진다.** 200건 변환은 `convert_history.py`로 자동화하고 복합·함정 보강분만 손으로 쓴다. 벡터 DB·데이터와 서로 검수한다.
