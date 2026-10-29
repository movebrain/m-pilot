# fake/: 가짜 DB로 수동 테스트하기

MySQL·docker 없이, 로컬에서 고객·엔지니어 앱을 띄워 UI부터 RAG까지 눈으로 확인하는 절차다. 모든 데이터는 가상이다.

## 빠른 시작 (OpenAI 키 없이 5분)

Python 3.12와 sqlite3 3.35 이상이 필요하다(macOS·Ubuntu 22.04 이상은 대개 충족, 아니면 7번의 "sqlite3가 낮은 환경"을 본다). 저장소 루트에서 실행한다.

```bash
git fetch origin && git switch feat/fake-db           # PR #76 머지 뒤에는 develop
python3.12 -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
python -m fake.run --offline --both
```

`initialized: offline, …`이 나오면 브라우저로 고객 앱 `http://127.0.0.1:7860`, 엔지니어 앱 `http://127.0.0.1:7861`을 연다. 첫 기동은 매뉴얼 PDF 파싱으로 2~3분 걸린다(합본 PDF 기준, 4코어에서 약 170초). 무엇을 눌러 볼지는 4번, 막히면 7번을 본다. 셀프 가이드와 엔지니어 답변까지 보려면 실제 모드(3번)가 필요하다.

## 무엇이 가짜이고 무엇이 진짜인가

| 구분 | 내용 |
| --- | --- |
| 가짜 | `rdb`(에러코드 표·시드 이력 200건·신고·대화·조치를 메모리에 둔다), `vectordb`의 `connect`·`get_store`·`get_document`, `parser.parse_manuals` |
| 진짜 | `service`, `rag`(`pipeline`·`retriever`·`generator`), `ui`, 색인 쓰기(`rebuild_search_index`, `index_history`), 벡터 저장소(메모리 Chroma) |
| 모드 | 실제 모드: OpenAI 임베딩·LLM을 쓴다(키 필요, 호출 비용 발생). `--offline`: 키 없이 해시 임베딩만 쓰고 LLM은 연결하지 않는다 |

`service` 전체를 가짜로 바꾸는 `app_customer.py --fake`와 다르다. 그쪽은 RAG를 거치지 않고 화면만 띄운다(`fake/fake_service.py`).

## 1. 준비물

| 항목 | 요구 | 비고 |
| --- | --- | --- |
| Python | 3.12 | CI와 같다 |
| 패키지 | `pip install -r requirements.txt` | 새 의존성은 없다 |
| sqlite3 | 3.35 이상 | Chroma 요구 사항. 아래 "문제 해결" 참고 |
| MySQL, docker | 불필요 | |
| OpenAI 키 | 실제 모드만 | `--offline`은 불필요 |

```bash
python3.12 -c "import sqlite3; print(sqlite3.sqlite_version)"   # 3.35 이상이어야 한다
python3.12 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

## 2. 코드 받기

`fake/`가 `develop`에 머지된 뒤에는 `develop`을 받으면 된다. 머지 전에는 PR #76의 브랜치를 받는다.

```bash
git fetch origin
git switch develop && git pull --ff-only     # 머지된 뒤
git switch feat/fake-db                       # 머지 전(PR #76)
```

## 3. 실행

저장소 루트에서 실행한다(`app`·`fake`를 import하려면 루트가 작업 폴더여야 한다).

```bash
python -m fake.run --offline --both          # 키 없이 고객(7860)·엔지니어(7861) 앱
python -m fake.run --offline                 # 고객 앱만
python -m fake.run --both                    # 실제 모드(루트 .env 필요)
python -m fake.run --both --evidence-threshold 1.2   # 엔지니어 근거 임계값을 바꿔 본다(기본 1.0, 5번 참고)
```

- 첫 기동에 매뉴얼 PDF 파싱으로 2~3분이 걸린다. `initialized: offline, evidence_threshold=1.0`(또는 `initialized: real OpenAI models, …`)이 출력되면 브라우저에서 `http://127.0.0.1:7860`을 연다.
- 종료는 `Ctrl+C`다. 접수 건·대화·조치는 메모리에만 있어 종료하면 사라지고, 시드 이력 200건은 기동 때마다 다시 올라온다.

### 실제 모드 설정

```bash
cp .env.example .env     # .env는 커밋하지 않는다(.gitignore)
```

`.env`에서 `OPENAI_API_KEY`만 채우면 된다. 모델명은 예시 값이 들어 있다. `MYSQL_*`와 `DEMO_*`는 비워 둬도 `fake.run`이 더미를 채운다. `EVIDENCE_THRESHOLD`는 `.env`가 아니라 `--evidence-threshold`(기본 1.0)로 정한다. 환경변수가 `.env`보다 우선하기 때문이다. 키는 코드·로그·PR·대화창에 쓰지 않는다.

시작할 때 매뉴얼 청크를 임베딩하고 신고마다 LLM을 부르므로 OpenAI 호출 비용이 든다.

## 4. 수동 테스트: 고객 앱(7860)

신고하기 → 설비 선택 → 에러코드(입력해서 검색) → 증상 메모(선택) → 신고하기 → 상세 화면 → 뒤로 → 목록에서 확인한다.

경로는 에러코드 표의 값만으로 정해진다(CLAUDE.md §5). 아래 값은 `data/raw/error_code_table.csv`의 것이다.

| 설비 | 코드 | 에러명 | 심각도 | 조치 구분 | 기대 경로 |
| --- | --- | --- | --- | --- | --- |
| AGV | A302 | 구동 모터 과열 | 중 | 고객사 조치 | 셀프 가이드 후보 |
| AGV | A305 | 휠 슬립 감지 | 하 | 고객사 조치 | 셀프 가이드 후보 |
| RTV | R207 | 휠 슬립 검출 | 하 | 고객사 조치 | 셀프 가이드 후보 |
| Mini Loader | M404 | 주행 휠 슬립 검출 | 하 | 고객사 조치 | 셀프 가이드 후보 |
| Mini Loader | M402 | 주행 서보 과부하 | 상 | 고객사 조치 | 심각도 `상`이라 엔지니어 접수 |
| SRM | S102 | 과속도 트립 | 상 | 엔지니어 필요 | 엔지니어 접수 |

### 모드별로 보이는 결과

| 모드 | 셀프 가이드 후보 | 엔지니어 접수 건 |
| --- | --- | --- |
| `--offline` | 안내를 만들 LLM이 없어 엔지니어 접수로 떨어진다. 오류가 아니라 정해진 동작이다 | 상세에 "엔지니어가 확인하고 있습니다. 조치가 끝나면 이 화면에서 결과를 볼 수 있습니다." |
| 실제 모드 | 조치 안내와 `출처를 고르면 원문을 봅니다` 선택란이 나올 수 있다. 안내를 못 만들면(`held`, `generation_failed`) 엔지니어 접수로 떨어진다. 후보여도 흔히 그렇게 된다(아래 참고) | 위와 같다 |

실제 모드에서 후보 코드의 결과는 코드마다 갈린다. 우리가 본 결과는 A302·R207이 안내까지 나왔고 A305·M404는 `generation_failed`로 엔지니어 접수가 됐다(`gpt-4o-mini`, 실행마다 다를 수 있고 원인은 조사하지 않았다). 후보 78개 코드 모두 가짜 DB가 허용 절차(목)를 채우므로 경로 판정은 맞고, 생성·검증 단계에서 갈린다. 후보 코드가 접수로 떨어지는 것만으로 버그라고 보지 않는다.

그 밖에 확인할 것:

- 설비·에러코드 없이 `신고하기`를 누르면 "입력값을 확인해 주세요." 경고가 나온다.
- 접수 직후 목록에 새 건이 `접수` 상태로 맨 위에 보이고, 상태 필터(전체·접수·조치 중·조치 완료)가 동작한다.
- 모든 화면 위에 "모든 데이터는 가상입니다" 배너가 보인다.

## 5. 수동 테스트: 엔지니어 앱(7861)

`--both`로 띄운 뒤 `http://127.0.0.1:7861`을 연다. 대시보드(기술지원 건 선택) → 장애 분석 대화(질문 보내기, 출처 선택) → 조치 결과 작성 → 조치 완료로 확정 순서다.

- 질문과 안내 생성은 LLM이 필요하므로 실제 모드에서 확인한다.
- **`근거 부족`이 나올 수 있다.** 신고 설비의 매뉴얼 3건과 이력 3건 중 검색 거리가 `EVIDENCE_THRESHOLD` 이하인 근거가 하나도 없으면 답변을 만들지 않는다. 임계값 기본값 1.0에서는 에러코드를 넣은 질문(예: `M402 주행 서보 과부하가 뜨면 어떻게 조치하나요?`)이 잘 통과하고, 에러코드 없는 자연어 질문(예: `주행 중 과부하 경고 원인이 뭔가요?`)은 `근거 부족`이 될 수 있다. 오류가 아니라 정해진 동작이다.
- **임계값은 평가로 정한 값이 아니다.** 1.0은 가짜 DB 청크로 질문 19개의 거리를 잰 수동 테스트용 임시 값이다. 관련 질문 12개는 거리가 0.75~1.22(에러코드를 넣으면 0.75~0.98)이고 무관·다른 설비 질문 7개는 1.00 이상이었다. 값에 따른 통과 수는 아래와 같다. 자연어 질문까지 보려면 `--evidence-threshold 1.2`로 올려 보되, 올릴수록 관련 없는 질문도 통과한다.

  | 임계값 | 관련 질문 통과(12개) | 무관·다른 설비 질문 통과(7개) |
  | --- | --- | --- |
  | 0.9 | 5 | 0 |
  | 1.0 | 7 | 0 |
  | 1.1 | 9 | 2 |
  | 1.2 | 11 | 4 |

  가짜 파서의 청크 기준이라 실제 `parser`와 다를 수 있다. 값은 평가(#49) 결과를 보고 사람이 정한다.
- 조치를 확정한 건만 `history` 색인에 들어가고, 그 뒤 같은 건에는 질문·조치를 더 저장할 수 없어야 한다(`InvalidStateError`, CLAUDE.md §5).

## 6. 알려진 한계

- **`(목)` 표시 값은 근거가 아니다.** 에러코드 표에 없는 허용 절차 ID와 안전 문구는 가짜 DB가 같은 설비 매뉴얼에서 그 코드가 가장 많이 나온 청크를 골라 채운다. 절차 본문이 아닌 오류 목록 절을 가리킬 수 있다. 배선 확인용이며 RAG 품질 판단에 쓰지 않는다.
- **해시 임베딩은 의미를 모른다.** `--offline`의 검색은 겹치는 글자 묶음 수만 반영한다. 검색 품질 평가에 쓰지 않는다.
- **출처 쪽 번호가 실제 매뉴얼과 1 어긋난다.** 가짜 파서는 PDF 순서로 쪽을 센다. `develop`의 `parser` 규약은 꼬리글에 인쇄된 번호(PDF 순서보다 1 작음)이고 `manual_id`도 꼬리글 문서번호다. DB 파트가 구현되면 달라질 값이다.
- **결과가 실행마다 달라질 수 있다.** `app.py`는 `ChatOpenAI`를 temperature 없이 만들어 모델 기본값이 쓰인다(`generator.py` docstring은 0이라고 적혀 있어 어긋난다). 같은 신고도 안내가 나오거나 `generation_failed`가 될 수 있다. 모델에 따라서는 temperature를 지정할 수도 없으므로 `.env`의 `OPENAI_CHAT_MODEL`을 바꿀 때 알아 둔다.
- **재시작하면 초기화된다.** 접수·대화·조치는 메모리에만 있다.

## 7. 문제 해결

앱은 비밀 정보 보호를 위해 초기화 실패 때 예외 원문을 숨기고 아래 고정 문구와 예외 이름만 stderr에 출력한 뒤 종료 코드 1로 끝난다.

| 증상 | 원인과 대응 |
| --- | --- |
| `앱을 시작하지 못했습니다. … (DependencyError)` | `--offline`이면 대개 sqlite3가 3.35 미만이다(Chroma를 못 불러온다). 1번의 확인 명령으로 버전을 보고, 새 sqlite를 쓰는 환경(macOS, Ubuntu 22.04 이상 등)에서 실행한다. Oracle Linux·RHEL처럼 낮은 배포판이면 아래 "sqlite3가 낮은 환경" 절차로 venv를 다시 만든다. 실제 모드면 OpenAI 키 오류나 네트워크도 확인한다 |
| `앱을 시작하지 못했습니다. … (ValidationError)` | `.env`의 필수 값이 비었거나 형식이 틀렸다. 실제 모드에서 `OPENAI_API_KEY`가 비어 있는 경우가 흔하다. 키를 채우거나 `--offline`으로 실행한다 |
| `ModuleNotFoundError: No module named 'app'`(또는 `fake`) | 저장소 루트가 아닌 곳에서 실행했다. 루트에서 `python -m fake.run`으로 실행한다 |
| 포트 사용 중 | 7860·7861을 쓰는 이전 프로세스를 종료한다 |

### sqlite3가 낮은 환경: 새 sqlite를 쓰는 Python으로 venv 다시 만들기

시스템 Python의 sqlite가 3.35 미만이면, sqlite 3.35 이상을 쓰는 Python 3.12를 따로 설치한 뒤(OS 패키지, 공식 설치 파일, Homebrew 등 방법은 자유) 그 Python으로 `venv`를 다시 만든다. 이미 만든 `venv`가 있으면 `--clear`로 덮어쓴다. 시스템 Python은 바꾸지 않는다.

```bash
deactivate 2>/dev/null                                      # 켜져 있는 venv가 있으면 끈다
/path/to/python3.12 -m venv --clear venv && source venv/bin/activate   # 새 sqlite를 쓰는 python3.12의 경로
pip install -r requirements.txt
python -c "import sqlite3; print(sqlite3.sqlite_version)"   # 3.35 이상인지 확인
```

## 8. 테스트

```bash
python -m pytest fake/        # 가짜 DB·가짜 service·fake.run 테스트. PDF 파싱으로 몇 분 걸린다
python -m pytest              # 전체(CI와 같다)
```

`mpilot/`은 `fake/`를 import하지 않는다. 루트 `app.py`만 `--fake`에서 `fake/fake_service.py`를 부른다.
