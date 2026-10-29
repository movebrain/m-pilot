<!--
  [올리기 전에 확인할 곳] (이 블록은 화면에 보이지 않습니다)
  1. 시작하기의 `python -m scripts.init_db`, `python -m scripts.evaluate` 실행 방법이 실제와 맞는지 확인해 주세요.
  2. .env 항목 이름은 spec의 AppConfig 필드 이름입니다. 실제 .env.example의 변수 이름과 맞는지 확인해 주세요.
  3. 슬로건("설비가 멈춘 순간, 근거 있는 첫 대응")은 제안입니다. images/*.svg를 바꾸면 이미지 문구도 바뀝니다.
  4. 이 README의 기능·범위·규칙은 docs/spec.md 기준입니다. spec이 바뀌면 같이 고쳐 주세요.
  5. fake/ 폴더의 사용법은 fake/README.md를 읽고, 시작하기의 `python -m fake.run` 설명이 맞는지 확인해 주세요.
-->

<p align="center">
  <img src="images/logo.png" alt="MOVEBRAIN 로고" width="320">
</p>

<p align="center">
  <img src="images/hero.svg" alt="M-Pilot: 설비가 멈춘 순간, 근거 있는 첫 대응" width="100%">
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.12-002C5F?logo=python&logoColor=white">
  <img alt="Gradio" src="https://img.shields.io/badge/UI-Gradio-00AAD2">
  <img alt="MySQL" src="https://img.shields.io/badge/DB-MySQL-002C5F?logo=mysql&logoColor=white">
  <img alt="Chroma" src="https://img.shields.io/badge/Vector%20DB-Chroma-00AAD2">
  <a href="https://github.com/movebrain/m-pilot/actions"><img alt="CI" src="https://github.com/movebrain/m-pilot/actions/workflows/ci.yml/badge.svg"></a>
</p>

> 이 저장소의 모든 데이터(매뉴얼, 에러코드, 서비스 이력)는 **가상**입니다. 앱의 모든 화면에도 “모든 데이터는 가상”이 표시됩니다.

<img src="images/h_team.svg" alt="우리 팀은요" width="100%">

## 설비 장애 대응 지원, 근거 있는 첫 대응

설비가 멈추면 현장은 "무엇부터 해야 하지?"에서 시간을 씁니다. **M-Pilot**은 고객이 **설비와 에러코드를 골라 신고**하면 에러코드 표의 심각도와 조치 구분으로 경로를 나눕니다. 고객이 직접 조치할 수 있는 고장은 **셀프 가이드**를 받고, 위험하거나 고객이 조치할 수 없는 고장은 **엔지니어에게 접수**됩니다. 엔지니어에게는 **같은 설비의 매뉴얼과 확정된 과거 조치**에 근거한 답을 단계마다 출처와 함께 보여 줍니다. 근거를 찾지 못하면 지어내지 않고 **조치 없이 그 사실만 알립니다.**

<img src="images/h_members.svg" alt="MEMBERS" width="100%">

| 이름 | 파트 | GitHub |
|:---:|:---:|:---:|
| 노동균 | UI·총괄 | [@rohdonggyun-lab](https://github.com/rohdonggyun-lab) |
| 김성일 | DB | [@tjddlf112015-jpg](https://github.com/tjddlf112015-jpg) |
| 손재호 | DB | [@thswogh](https://github.com/thswogh) |
| 이재서 | DB | [@8113jsl-ui](https://github.com/8113jsl-ui) |
| 도예진 | RAG | [@yejjini](https://github.com/yejjini) |
| 정혜윤 | RAG | [@hyey00n](https://github.com/hyey00n) |
| 한시온 | RAG | [@zionhann](https://github.com/zionhann) |

| 파트 | 맡은 일 | 담당 폴더 |
|---|---|---|
| **UI·총괄** | 고객·엔지니어 화면, 앱 조립·실행, 화면과 DB·RAG를 잇는 서비스 함수 | `app.py` `app_customer.py` `app_engineer.py` `mpilot/ui/` `mpilot/service.py` `fake/` |
| **DB** | 매뉴얼 PDF 파싱·청킹, 초기 자료 적재, 신고·대화·조치 저장·조회, 검색 색인 | `mpilot/db/` `scripts/init_db.py` |
| **RAG** | 근거 검색, 답변 생성·인용 검증, 고객 안내·엔지니어 답변 흐름, 품질 평가 (도예진: `pipeline`, 정혜윤: `generator`) | `mpilot/rag/` `prompts/` `scripts/evaluate.py` |

<img src="images/h_project.svg" alt="PROJECT" width="100%">

## 근거가 있을 때만 답합니다

현장에서 필요한 건 그럴듯한 문장이 아니라 **믿고 따라 할 수 있는 조치**입니다. 그래서 모든 답변은 매뉴얼의 쪽·절, 확정된 이력 같은 **출처에 연결**되어야 하고, 출처가 근거와 다르거나 안전 수칙이 빠진 답은 보여 주지 않습니다. 고객이 위험한 조치를 하지 않도록, 고객 안내는 **매뉴얼에서 지정한 고객 수행 절차만** 사용합니다.

고객 앱(포트 7860)과 엔지니어 앱(포트 7861)은 **서로 분리된 두 개의 앱**입니다.

| 영역 | 기능 | 내용 |
|---|---|---|
| 고객 | 신고 작성 | 설비와 에러코드를 고르고 제출하면 접수번호가 발급됩니다. 심각도가 높거나 고객이 조치할 수 없는 고장은 엔지니어에게 접수되고, 나머지는 셀프 가이드를 받습니다. |
| 고객 | 신고 목록·상세 | 내가 접수한 신고를 최신순·상태별로 봅니다. 셀프 가이드 건은 단계별 안내와 출처를, 엔지니어 처리 건은 완료 뒤 조치 결과를 봅니다. 내부 분석은 보이지 않습니다. |
| 엔지니어 | 대시보드 | 기존 서비스 이력과 신규 신고를 상태별로 보고, 기술지원 건을 누르면 대화창으로 이동합니다. 셀프 가이드 건은 ‘고객 안내’로만 표시됩니다. |
| 엔지니어 | 장애 분석 대화 | 신고 건에서 질문하면 신고한 에러코드를 기준으로, 같은 설비의 매뉴얼·과거 이력에서 찾은 근거로 출처가 붙은 답을 줍니다. 첫 질문에 ‘조치 중’이 됩니다. |
| 엔지니어 | 이력 저장 | 원인·실제 조치·결과를 입력해 완료하면 ‘조치 완료’가 되고, 확정된 조치는 다음 질문의 근거가 됩니다. |
| 공통 | 가상 데이터 고지 | 모든 화면에 “모든 데이터는 가상”이 보이고, 오류는 고정 문구로만 안내합니다. |

### 경로 결정과 상태

에러코드 표의 **심각도와 조치 구분만** 써서 셀프 가이드 후보를 정합니다.

| 순서 | 조건 | 경로 |
|:---:|---|---|
| 1 | 심각도 `상` | 기술지원(`support`) |
| 2 | 조치 구분 `고객사 조치` | 셀프 가이드(`self_guide`) 후보 |
| 3 | 그 밖 | 기술지원(`support`) |

```mermaid
flowchart TD
    A["고객: 설비·에러코드 선택 후 신고"] --> B["접수번호 발급"]
    B --> C{"경로 결정<br/>심각도·조치 구분"}
    C -->|"고객사 조치 후보"| G["셀프 가이드 생성<br/>허용 절차 조회 → 생성 → 검증"]
    C -->|"심각도 상 또는 그 밖"| S["기술지원 접수 (received)"]
    G -->|"answered"| D["셀프 가이드 완료 (completed)"]
    G -->|"보류(held) 또는 실패(generation_failed)"| S
    S --> I["엔지니어 질문 (in_progress)"]
    I --> K["조치 결과 등록 (completed)"]
    K -.->|"확정 이력을 검색 근거로 추가"| I
```

- 셀프 가이드가 `completed`가 되는 것은 **안내 제공의 종료**이지 실제 해결 확인이 아닙니다. 완료 뒤 추가 지원은 새 신고로 접수합니다.
- 이미 완료된 건에는 질문도 조치 결과도 더 저장하지 않고 ‘이미 완료’로 안내합니다.

<img src="images/h_scope.svg" alt="구현 범위" width="100%">

## 구현 범위

### 포함

- 앱을 시작할 때마다 Chroma 색인을 새로 생성
- 역할별 앱(고객·엔지니어) 분리
- 고객 신고 목록·작성·상세, RAG 셀프 가이드
- 엔지니어 전체 이력 보기와 RAG 채팅, 현장 조치 결과 저장과 검색 반영
- 앱 실행·시연·자동 테스트에서 MySQL 지원(로컬 Docker, CI)

### 범위 밖

- 실제 인증·권한, 다중 고객사
- 복합 신고(한 건에 설비·에러코드는 하나)
- 미해결·다회 조치, 완료 후 채팅·수정·재개, 임시 저장, 고객 확인 단계
- 새 답변·새 신고 표시(N/NEW), 반복 에러 강조, 긴급 강조, LLM 위험도 분류
- 사진 첨부·판독, 로그 첨부, 실시간 푸시
- 중복 제출·동시 수정 방지, 목록 페이지 이동, 기간·원본 필터

### 후속 개선

상세 진입 시 AI 요약, 조치 결과 AI 초안, 작성 글의 이력 형식 구조화, 사진 첨부·열람

<img src="images/h_structure.svg" alt="시스템 구조" width="100%">

## 시스템 구조

```text
.
├── app.py                  # [UI] 앱 조립·실행 공통부
├── app_customer.py         # [UI] 고객 앱 진입점 (7860)
├── app_engineer.py         # [UI] 엔지니어 앱 진입점 (7861)
├── mpilot/
│   ├── config.py           # [공통] 앱 설정 로드·검증
│   ├── models.py           # [공통] 업무 데이터 정의와 순수 업무 규칙
│   ├── service.py          # [UI] 화면과 DB·RAG를 잇는 유즈케이스(접수·질문·완료)
│   ├── db/                 # [DB]
│   │   ├── parser/         #   매뉴얼 PDF를 출처가 붙은 검색 단위로 분해(types·text·tables·chunks·ocr)
│   │   ├── schemas.py      #   관계형 DB 구조·연결 설정
│   │   ├── rdb.py          #   신고·대화·조치의 저장·조회
│   │   └── vectordb.py     #   검색 자료 저장소 관리(색인·이력 반영·원문 조회)
│   ├── rag/                # [RAG]
│   │   ├── retriever.py    #   근거 검색과 근거 충분성 판단
│   │   ├── generator.py    #   모델 호출과 답변 형식·인용 검증
│   │   └── pipeline.py     #   고객 안내·엔지니어 답변의 단계 조립
│   └── ui/                 # [UI] 고객 신고 목록·작성·상세, 엔지니어 대시보드·대화·조치 저장
├── fake/                   # [UI] 수동 테스트·시연용 가짜 구현 (사용법·재현 절차는 fake/README.md)
│   ├── fake_service.py     #   --fake 실행용 가짜 service(원자료 CSV + 메모리)
│   ├── fake_db.py          #   가짜 rdb·vectordb·parser(메모리 + 진짜 Chroma). service·rag는 진짜 코드로 돈다
│   ├── run.py              #   가짜 DB 위에서 고객·엔지니어 앱 실행 (python -m fake.run)
│   └── test_*.py           #   위 가짜들과 실행 진입점의 테스트
├── scripts/
│   ├── init_db.py          # [DB] DB 초기화·초기 자료 적재
│   ├── build_error_codes.py   # [DB] 에러코드 정본 data/errorcodes.csv 생성
│   ├── build_history_seed.py  # [DB] 시드 이력 data/history_seed.csv 생성
│   └── evaluate.py         # [RAG] RAG 품질 평가 실행·기록
├── prompts/                # [RAG] 모델 지시문(고객 안내·엔지니어 대화)
├── tests/                  # 테스트
├── .github/workflows/      # GitHub CI
├── data/
│   ├── manuals/            # 설비 매뉴얼 PDF 4종(원자료)
│   └── raw/                # 에러코드 표, 설비별 조치 이력 더미 CSV(원자료)
└── docs/                   # 의도·스펙 문서
```

**의존 방향**은 한쪽으로만 흐릅니다: `app*.py` → `ui/*`·`service` → `db/*`·`rag/pipeline` → `rag/retriever`·`rag/generator` → `models`. `rag`는 `db/rdb`를 부르지 않고, 저장은 `service`가 맡습니다.

### 검색 자료(Chroma)

| 자료 | 컬렉션 | 문서 ID |
|---|---|---|
| 에러코드 표 행 | `manual` | `error:<equipment>:<code>` |
| 매뉴얼 본문 | `manual` | `manual:<manual_id>:<page>:<chunk_no>` |
| 확정 현장 조치 | `history` | `history:<case_id>` |

- **모든 검색은 설비 필터가 필수**입니다. 질문에 다른 설비를 말해도 신고한 설비의 자료에서만 근거를 찾습니다.
- 고객 안내는 허용된 ID로 `manual` 원문을 **직접 조회**하고, `history`는 엔지니어 대화에서만 검색합니다.
- 근거가 없으면 `steps=[]`인 보류 답변을 돌려주고, 모델·검색 장애와 검증 실패는 `DependencyError`로 올립니다.

### 데이터

- **가상 매뉴얼 4종**(Mini Loader·SRM·AGV·RTV)과 **설비별 완료 이력 50건(총 200건)**
- `data/errorcodes.csv`가 에러코드 표의 정본이고, 필수값·중복·설비 4종·출처를 검증하며 자동 보정하지 않습니다.

<img src="images/h_start.svg" alt="시작하기" width="100%">

## 시작하기

저장소 맨 위 폴더에서 실행합니다.

```bash
python -m pip install -r requirements.txt
cp .env.example .env                    # 값을 채웁니다. .env는 커밋하지 않습니다
docker compose up -d                    # 로컬 MySQL이 필요할 때
python -m scripts.build_error_codes     # 원자료 에러코드 표 + 매뉴얼 표 → data/errorcodes.csv (init_db 전에)
python -m scripts.build_history_seed    # 원자료 이력 4종 → data/history_seed.csv (init_db 전에)
python -m scripts.init_db               # DB 초기화·초기 자료 적재 (시연 전 한 번만)
python app_customer.py                  # 고객 앱     http://127.0.0.1:7860
python app_engineer.py                  # 엔지니어 앱 http://127.0.0.1:7861
python app_customer.py --fake           # DB·모델 없이 가짜 데이터로 화면만 확인
python -m fake.run                      # 가짜 DB 위에서 고객·엔지니어 앱 실행 (fake/README.md 참고)
python -m pytest                        # 전체 테스트
```

- `init_db`는 **실행할 때마다 DB를 비우고 다시 만듭니다.** 시연 중에 다시 실행하면 새로 확정한 이력이 사라지니, 시연 전에 한 번만 실행하세요.
- 앱은 시작할 때 DB에서 검색 색인을 새로 만듭니다. 색인이 끝난 뒤에 첫 화면이 열립니다.

### 환경 설정 (`.env`)

앱은 `.env`의 설정만 읽습니다. 항목은 `.env.example`을 참고하세요.

| 항목 | 내용 |
|---|---|
| `db_url` | MySQL 접속 주소 (비밀번호 포함) |
| `openai_api_key` | 임베딩·답변 생성에 쓰는 OpenAI API 키 |
| `chat_model_name`, `embedding_model_name` | 답변·임베딩 모델 |
| `manuals_dir`, `codes_path`, `history_seed_path` | 매뉴얼 폴더, 에러코드 표, 서비스 이력 시드 경로 |
| 검색 설정 | `manual_k`, `history_k`(처음엔 각 3건), `evidence_threshold` |
| `llm_timeout_seconds` | 모델 호출 제한 시간 (기본 60초) |
| 데모 계정 | 고객·엔지니어 데모 계정 2개 |

- 역할 분리는 **데모용**이며 인증·권한 보안이 아닙니다. 고객은 항상 같은 데모 고객입니다.
- API 키와 접속 정보는 코드·로그·커밋·PR 본문에 쓰지 않습니다. `.env.example`에는 키 이름만 값 없이 적습니다.

## 테스트와 품질

| 구분 | 내용 |
|---|---|
| 단위·통합 테스트 | `python -m pytest`. 코드를 만들면 그 코드의 테스트를 함께 작성합니다. |
| 자동 검사(CI) | `main`·`develop`을 향한 PR과 push에서 GitHub Actions가 MySQL 컨테이너와 함께 같은 테스트를 돌립니다. |
| RAG 평가 | `scripts/evaluate.py`를 **수동으로** 실행합니다. `data/eval_questions.csv`로 고객 안내와 엔지니어 답변을 따로 채점하고 결과를 JSON으로 남깁니다. |

| 지표 | 정의 | 합격선 |
|---|---|:---:|
| 인용 검증 | 공개된 답변의 인용이 모두 확보한 근거에 있고 설비가 일치하는 비율 | 100% |
| 정상 답변율 | 정상 답변 대상 문항 중 `answered`인 비율 (보류·실패도 분모에 포함) | ≥ 80% |
| 보류 정확도 | 근거 부족 문항에서 조치 단계 없이 맞는 판정을 낸 비율 | ≥ 90% |

정상 답변율 80%는 초기 목표이며 실측 결과가 아닙니다. 사람 검수는 생성된 고객 안내 전부와 엔지니어 `answered` 답변 최소 5건을 대상으로 하고, 필수 안전 문구 자동 검증을 통과해도 생략하지 않습니다.

## 개발 규칙

- **브랜치**: `main` ← `develop` ← `<type>/<설명>` 순서로 병합합니다. 작업 브랜치는 `develop`으로 squash 병합하고, `develop`은 `main`으로 merge commit합니다. `main`·`develop`에는 직접 push하지 않고 force push도 하지 않습니다.
- **리뷰**: PR은 리뷰 승인을 받은 뒤 병합합니다.
- **커밋**: conventional commit을 따릅니다. type은 영어, 제목과 본문은 한국어이고, 제목은 50자 이내, 본문에는 변경 의도를 씁니다.
- **PR 본문**: `## 변경 사항` 불릿과 `>` 의도로 쓰고, 끝에 `## 머지 커밋 메시지`를 붙입니다.
- **코드**: 코드와 테스트를 함께 올리고, PR 전에 로컬에서 `python -m pytest`를 돌립니다.
- **계약**: 함수 시그니처나 `models.py` 필드를 바꾸면 같은 PR에서 spec도 고치고 `계약 변경` 라벨을 붙입니다.

<img src="images/h_stack.svg" alt="TECH STACK" width="100%">

| 구분 | 사용 기술 |
|---|---|
| 언어·화면 | Python, Gradio Blocks |
| LLM·임베딩 | OpenAI API, LangChain |
| 벡터 DB | Chroma (메모리) |
| 관계형 DB | MySQL (PyMySQL), SQLAlchemy Core |
| 자료 읽기 | pdfplumber (PDF) |
| 패키지 관리 | pip |
| 협업·검증 | GitHub(이슈·PR·리뷰), GitHub Actions, pytest |

전공자가 아닌 팀원들이 **AI 도구와 함께** 개발했고, 커밋하는 사람이 설명할 수 있는 코드만 반영합니다.

## 문서와 토론

- [기능·아키텍처 명세 `docs/spec.md`](docs/spec.md) · [기획 의도 `docs/intent.md`](docs/intent.md)
- [Discussion #105: `pipeline`은 무엇을 돌려주나](https://github.com/movebrain/m-pilot/discussions/105)
- [Discussion #100: `_extract_page_texts`는 무엇을 돌려주나](https://github.com/movebrain/m-pilot/discussions/100)
