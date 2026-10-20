# Spec: M-Pilot 장애 대응 지원 서비스

- 근거 문서: [intent.md](intent.md)(SOT). 이 문서가 intent.md와 어긋나면 intent.md가 우선한다.
- 상태: draft. 개발 시작 전에 7명이 아래 계약(구조, 데이터 모델, 인터페이스, 수용 기준)을 합의한다. 파트별 역할, 제출물 담당, 리스크는 8장에 둔다.
- 표기: "(기본값)"은 팀 확인이 필요한 값이다. 모아서 9장에 둔다.

## 1. 범위

| 우선순위 | 기능                           | 한 줄                                                                                                                          |
| -------- | ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------ |
| P1       | US-01, 02, 04~09, 12~16, 18~21 | 신고 → 접수 → (P1은 모두 기술지원) 세션: AI 요약·대응 방안 자동 + 엔지니어 질문 답변(에러코드 표·사양서 본문 파싱·임베딩 포함) |
| P2       | US-03, 10, 11, 17, 22, 23      | 이력 작성·서비스 이력 추가, 저위험 분기·셀프 가이드(반복 에러 전환 포함), 정답률 평가                                                      |
| P3(선택) | US-24                          | 독립 조회 Q&A 화면, 평가 화면, 설비 추가                                                                                       |

비범위: 로그인·권한, 로그 첨부, 사진 AI 판독, 고객 서명·완료 확인 기록, 실시간 푸시, 다중 고객사 격리, 영어 지원.

## 2. 프로젝트 구조

```
├── app.py                         Gradio Blocks 조립(`src/ui/` 화면을 Tab에 올림), "모든 데이터는 가상" 배너
├── docker-compose.yml             MySQL 컨테이너(통합·시연, 팀원별 로컬)
├── .env.example                   API 키, MySQL 접속 정보 예시
├── .gitignore                     `.env`, `data/chroma/`, `data/photos/`, `__pycache__/`
├── requirements.txt               패키지 버전 고정
├── src/                           모듈 = 파일 하나, 타입·프롬프트는 모듈 안
│   ├── __init__.py                빈 파일
│   ├── registry.py                모듈별 실제/가짜 전환(`MPILOT_FAKES`), 가짜 응답 JSON 로더
│   ├── errorcodes.py              ErrorCode 타입, 에러코드 표 로더·검증, 설비 목록
│   ├── parsing.py                 사양서 PDF 본문 → 청크(쪽·절)
│   ├── indexing.py                Chroma 컬렉션 manual, history 생성·추가, 벡터 질의, 인용 원문 조회
│   ├── db.py                      ServiceCase 타입, 접수 건 저장·조회, 상태 전이, 24시간 건수, 시드 적재
│   ├── search.py                  QueryAnalysis, Evidence 타입; 질문 분석, 정확 조회, 검색, 판정(decide_verdict)
│   ├── answer.py                  Citation, Step, Answer, CaseBrief 타입; 요약·대응 방안(brief), 질문 답변(ask)
│   ├── history.py                 이력 구조화, 이력 확정
│   ├── routing.py                 분기(route), 셀프 가이드(guide_for)
│   ├── evaluate.py                평가 실행(`python -m src.evaluate`)
│   └── ui/
│       ├── customer.py            고객 Tab: 신고, 접수번호 조회, 기술지원 대기 안내, 해결됨/해결 안 됨
│       ├── engineer.py            엔지니어 Tab: 접수 목록, 세션
│       └── resolution.py          엔지니어 Tab: 이력 작성(초안 확인·확정), 독립 조회·평가 화면(P3)
├── scripts/
│   ├── build_index.py             Chroma 인덱스 생성(`reset`)
│   ├── merge_errorcodes.py        표 9-4 추출·CSV 병합·대조 리포트
│   ├── convert_history.py         장애 기록 더미 4종(`resources/`) → `history_seed.csv` 변환(4.3)
│   ├── init_db.py                 스키마·시드 적재, `--reset`, `--check`(이력 시드 검증)
│   └── schema.sql                 MySQL 스키마(`cases`, `case_errors`, `case_messages`)
├── tests/
│   ├── test_<모듈>.py             6.2 인수 기준
│   └── test_integration.py        6.4 통합 시나리오
├── data/
│   ├── errorcodes.csv, history_seed.csv, eval_questions.csv, synonyms.csv
│   ├── manuals/                   사양서 PDF 4종
│   ├── sample/                    개발용 샘플(에러코드 10행, 이력 5건), 가짜 응답 `<모듈>.json`
│   ├── chroma/                    Chroma 저장(생성, 저장소 제외)
│   └── photos/                    신고 사진(생성, 저장소 제외)
└── docs/                          intent.md, spec.md, issues.md
```

| 항목         | 결정                                                                                                                                                                                                                                                                                            |
| ------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 언어·UI      | Python, Gradio Blocks(Tab)                                                                                                                                                                                                                                                                      |
| LLM·임베딩   | OpenAI API(채팅 + 임베딩), 모델명은 환경 변수. LangChain으로 연결                                                                                                                                                                                                                               |
| 벡터DB       | Chroma(수업에서 쓰는 벡터DB 기준, 다르면 교체) (기본값)                                                                                                                                                                                                                                         |
| 접수 건 저장 | 개발·테스트·통합·시연 모두 MySQL(팀원별 로컬 Docker, PyMySQL). ORM 없음, SQL은 `src/db.py`에만 두고 연결은 호출마다 열고 닫는다. 정적 표·이력 시드는 CSV를 pandas로 읽는다 (기본값)                                                                                                             |
| PDF 파싱     | pdfplumber. 사양서 4종은 표 추출이 되는 텍스트 PDF다 (기본값)                                                                                                                                                                                                                                   |
| API 계층     | 두지 않는다(FastAPI 없음). Gradio 핸들러가 `registry.get(...)`로 받은 모듈과 `db`를 직접 호출한다. 필요해지면 `mount_gradio_app`으로 얹는다 (기본값)                                                                                                                                                 |
| 개발·실행    | `python -m scripts.init_db`로 누구나 같은 DB(스키마, 가상 이력 시드)를 만들고 `--reset`으로 되돌린다. 먼저 `docker compose up`으로 MySQL을 띄운다(DB 확인은 `docker compose exec`로 `mysql` CLI). 실행은 저장소 루트에서 `python app.py`, `python -m src.evaluate`, `python -m pytest`(그냥 `pytest`는 `src` import 실패) |
| 키·재현성    | `.env`에 API 키·MySQL 접속 정보(저장소에 올리지 않음, `.env.example` 제공). LLM `temperature=0`, 평가는 고정 스냅샷                                                                                                                                                                             |

## 3. 아키텍처

```
고객 Tab                          엔지니어 Tab
신고 폼 ─► create_case(쌍 1~4) ─► route(pairs)
                           ├ guide   ─► 셀프 가이드(guide_for) ─┬ 해결됨 ─► 완료
                           │                                    └ 해결 안 됨 ─► support 전환
                           └ support ─► 접수 목록 ─► 세션 열기 ─► brief(case)
                                                                  ├ 질문 ─► ask(case_id, q)
                                                                  └ 이력 작성 ─► confirm_case ─► history 인덱스에 추가
```

| 계층 | 구성요소                                                      | 모듈                            |
| ---- | ------------------------------------------------------------- | ------------------------------- |
| 화면 | 고객 Tab(신고, 조회), 엔지니어 Tab(목록, 세션, 이력)          | `app.py`, `src/ui/`             |
| 저장 | 접수 건(MySQL), 에러코드 표(CSV)                              | `db`, `errorcodes`              |
| 검색 | PDF 파싱, Chroma(manual, history), 질문 분석, 정확 조회, 판정 | `parsing`, `indexing`, `search` |
| 답변 | 요약·대응 방안, 질문 답변, 이력 구조화                        | `answer`, `history`             |
| 분기 | 저위험·반복 에러 규칙                                         | `routing`                       |
| 평가 | 엔진 직접 호출 평가                                           | `evaluate`                      |

설계 규칙:

- **분기**: `route()`는 DB를 읽지 않고 메모리의 에러코드 표만 본다. 신고 핸들러(`src/ui/customer.py`)가 `create_case` → 쌍마다 `count_recent`(이번 건 포함) → `route(pairs)` → `update_case`(`branch`, `repeat_flag`) 순서로 처리한다. 복합 신고는 쌍마다 판정해 모든 쌍이 `guide`일 때만 `guide`다. P1의 `route()`는 항상 `support`다. 신고 접수와 분기는 LLM 없이 동작한다.
- **저장**: DB 접속은 `src/db.py`에서만 하고 다른 모듈은 공개 함수만 호출한다. `case_id` 채번은 동시 접수에도 겹치지 않게 DB 안에서 처리한다.
- **검색**: Chroma 컬렉션은 `manual`(에러코드 행 1개 = 문서 1개, 사양서 본문은 절 단위 청크)과 `history`(확정된 접수 건의 쌍 1개 = 문서 1개, 내용은 건 전체)다. 모든 검색에 `equipment` 필터를 적용하고 `history` 결과는 `case_id`로 중복을 제거한다. 메타데이터는 `manual` 행 문서 `equipment`, `code`, `severity`, `action_owner`, `row_id`, 본문 청크 `equipment`, `section`, `page`, `history` `equipment`, `code`, `case_id`, `resolution`이다.
- **답변**: 요약·대응 방안은 접수 건당 한 번만 생성해 저장한다. 복합 신고의 `brief`는 쌍마다 `retrieve`해 쌍별 섹션으로 만든다. LLM 호출이 실패하면 세션 화면에 에러와 재시도 버튼을 보이고 접수 건은 바뀌지 않는다.
- **모듈**: 실제 모듈은 import할 때 DB·Chroma에 접속하지 않는다. `MPILOT_FAKES`는 `registry.get()`을 호출할 때 읽고, 데이터 경로는 `Path(__file__)` 기준이다.
- **화면**: 고객 Tab과 엔지니어 Tab은 한 앱의 Tab이다. 접근을 막지 않고 고객 Tab 코드가 고객 노출 필드만 렌더링한다.

상태 전이:

| 현재                 | 이벤트                             | 다음                                      |
| -------------------- | ---------------------------------- | ----------------------------------------- |
| (없음)               | 신고 제출                          | `접수` (`route`로 `branch` 결정)          |
| `접수`, `support`    | 엔지니어가 세션 열기(`brief` 실행) | `분석 중`                                 |
| `접수`, `guide`      | 고객 "해결됨"                      | `완료` (기본값)                           |
| `접수`, `guide`      | 고객 "해결 안 됨"                  | `접수`, `branch=support`                  |
| `분석 중`            | 엔지니어가 "조치 중"으로 변경      | `조치 중`                                 |
| `분석 중`, `조치 중` | 이력 확정, `resolution=해결`       | `완료`                                    |
| `분석 중`, `조치 중` | 이력 확정, `resolution=미해결`     | `조치 중` (이력은 "실패 이력"으로 남는다) |

## 4. 데이터 모델

### 4.1 ErrorCode (`data/errorcodes.csv`)

제공된 에러코드 CSV(5열, 249행)에 사양서 표 9-4의 원인·조치를 코드 기준으로 합친 표다.

| 컬럼           | 규칙                                                                        |
| -------------- | --------------------------------------------------------------------------- |
| `equipment`    | `Mini Loader` / `SRM` / `AGV` / `RTV`                                       |
| `code`         | 설비 접두 체계(`R201`, `S101`, `A301`, `M401`). 전역에서 유일               |
| `name`         | 명칭. 증상 검색은 `name`과 `cause`를 합친 텍스트로 한다. 표기는 표 9-4 기준 |
| `severity`     | `상` / `중` / `하`. 비면 로드 실패                                          |
| `action_owner` | `고객사 조치` / `엔지니어 필요`. 비면 로드 실패                             |
| `cause`        | 발생 조건·원인(표 9-4)                                                      |
| `action`       | 조치(표 9-4). 저위험 건의 셀프 가이드로도 쓴다                           |
| `extra`        | 설비별 부가 정보(JSON 문자열, 선택). 표 9-4의 `대상 모델`이 `전 모델`이 아니면 `{"target_models": "S10/L10/SY10"}`처럼 둔다                                         |

- 기본키는 (`equipment`, `code`)다. `grade`, `guide`, `symptom` 컬럼은 없다.
- 사양서 PDF 4종은 접두 체계의 최신 개정본이고 코드 집합이 `errorcodes.csv` 249개와 일치한다(2026-10-20 확인). `merge_errorcodes.py`는 표 9-4와 CSV를 코드 기준으로 합치고 코드 집합, 심각도, 조치 구분이 어긋난 행을 대조 리포트로 보고한다.
- `대상 모델`(표 9-4)이 `전 모델`이 아닌 행은 `extra.target_models`에 저장하고 검색 문서 본문에 포함하며, 요약·대응 방안·답변은 모델별 값을 병기한다. 신고에서 모델은 받지 않고 설비 4종 단위 원칙은 유지한다.

### 4.2 ServiceCase (MySQL `cases`)

| 컬럼                                           | 타입            | 설명                                                                   |
| ---------------------------------------------- | --------------- | ---------------------------------------------------------------------- |
| `case_id`                                      | TEXT PK         | `SC-YYYYMMDD-NNN`. 세션 키로도 쓴다. 시드는 원본 이력ID(`RTV-H-0001` 등)를 그대로 쓴다                                    |
| `company_id`                                   | TEXT            | 고객사 식별자. 상수 `COMP-DEMO`. 검증 없음                                            |
| `memo`                                         | TEXT null       | 증상 메모(선택)                                                        |
| `photo_path`                                   | TEXT null       | `data/photos/{case_id}.{ext}`(선택). jpg/jpeg/png, 5MB 이하            |
| `status`                                       | TEXT            | `접수` / `분석 중` / `조치 중` / `완료`                                |
| `branch`                                       | TEXT            | `support`(기술지원) / `guide`(셀프 가이드). 신고 직후 `route()`가 정한다 |
| `repeat_flag`                                  | BOOL            | 쌍 중 하나라도 24시간 내 5회 이상이라 기술지원으로 전환된 건이면 true  |
| `summary`, `proposal`                          | TEXT, JSON null | AI 요약, 대응 방안(`Answer`). 세션을 처음 열 때 생성·저장              |
| `cause_found`, `engineer_action`, `resolution` | TEXT null       | 구조화된 원인, 조치, `해결` / `미해결`                                 |
| `resolution_note`                              | TEXT null       | 결과 원문(예: `해결(부품 교체)`, `임시 조치(재발 감시)`). 가상 이력에서 온다 |
| `actor`                                        | TEXT null       | 조치자(서비스 엔지니어, 고객사 운영자, 자동 복구 등). 가상 이력에서 온다 |
| `diverged`                                     | BOOL null       | 답변이 "예상과 달랐는지" 체크박스                                      |
| `source`                                       | TEXT            | `authored`(팀이 만든 가상 이력) / `engineer`(엔지니어 입력)            |
| `confirmed`                                    | BOOL            | 이력으로 확정됐는지. 확정된 건만 검색 대상                             |
| `created_at`, `updated_at`                     | TEXT            | ISO 시각. 시드는 `created_at`이 장애 기록의 발생일시다                                                               |

- 타입은 MySQL 기준으로 옮긴다. 짧은 문자열은 `VARCHAR`, 긴 텍스트는 `TEXT`, `BOOL`은 `TINYINT(1)`, `proposal`은 `JSON`, 시각은 `DATETIME`(UTC)이고 문자셋은 `utf8mb4`다.
- `schema.sql`은 MySQL 문법으로 쓴다. 문자셋은 docker-compose의 DB 설정에 둔다.
- 설비-에러코드 쌍은 `case_errors`(`case_id`, `seq`, `equipment`, `code`)에 쌍당 1행(건당 1~4행)으로 저장한다. 기본키가 (`case_id`, `equipment`, `code`)라 한 건에 같은 쌍이 두 번 들어가지 않는다. 같은 설비의 다른 코드는 함께 들어갈 수 있고, `seq`는 입력 순서(1~4)다. 모든 쌍은 4.1의 행과 일치해야 한다.
- `cases`에는 `equipment`, `code` 컬럼이 없고 `ServiceCase`가 `errors: list[(equipment, code)]`를 가진다. 원인·조치·결과와 `diverged`는 복합 신고도 건 단위 한 벌이다.
- 대화 기록은 `case_messages`(`case_id`, `role`, `content`, `created_at`) 테이블에 저장한다. 세션 재열기, 후속 질문의 대화 기억, 이력 초안 채우기가 이를 쓴다.

### 4.3 가상 이력 (`data/history_seed.csv`)

- 원본: `resources/{agv,mini_loader,rtv,srm}_error_history_dummy.csv` 4종 200건(설비당 50건, 열 19~20개, RTV만 `OBC 버전` 열이 하나 더 있다). `scripts/convert_history.py`가 접수 건 형식으로 바꿔 `history_seed.csv`를 만들고, 팀이 보강한 이력(아래)을 더한다.
- 컬럼: `case_id`, `equipment`, `code`(4.1과 일치, 코드 없는 이력 없음. 복합 장애는 `;`로 구분해 같은 순서로 나열한다. 예: `RTV;SRM` / `R201;S101`. 같은 쌍은 중복 불가, 같은 설비의 다른 코드는 가능, 최대 4개), `memo`, `engineer_action`, `cause_found`, `resolution`, `resolution_note`, `actor`, `created_at`, `diverged`(선택).
- `load_seed`는 `equipment`, `code`를 `;`로 쪼개 `case_errors`에 쌍당 1행으로 적재한다(두 값의 개수가 다르면 예외).
- `source=authored`, `confirmed=true`로 적재하고 `summary`, `proposal`은 비워 둔다(기본값). `status`는 `resolution=해결`이면 `완료`, `미해결`이면 `조치 중`이다.

변환 규칙(원본 열 → 목적지):

| 원본 열 | 목적지 | 규칙 |
| --- | --- | --- |
| `이력ID` | `case_id` | 그대로(`RTV-H-0001`). 보강분은 아래 규칙 |
| `모델명` | `equipment` | 값이 설비 4종과 같다(`AGV`, `Mini Loader`, `RTV`, `SRM`). 세부 모델은 없다 |
| `에러 코드` | `code` | 그대로. 에러코드 표에 없으면 변환 실패 |
| `현장 증상`, `확인한 상태값` | `memo` | 현장 증상 뒤에 확인한 상태값을 이어 붙인다 (기본값) |
| `원인` | `cause_found` | 그대로 |
| `조치 내용` | `engineer_action` | 그대로 |
| `결과` | `resolution`, `resolution_note` | 아래 표. 원문은 `resolution_note`에 남긴다 |
| `조치자` | `actor` | 그대로 |
| `발생일시` | `created_at` | 그대로. 가장 최근이 2026-10-16이라 시연 때 `count_recent`의 24시간 창에 걸리지 않는다 |
| `에러명`, `심각도`, `조치 구분` | 적재하지 않음 | 에러코드 표와 모두 일치한다. 변환 검증에만 쓴다 |
| `호기`, `발생 위치`, `정지시간(분)`, `복구일시`, `참조 매뉴얼`, `비고`, `OBC 버전` | 적재하지 않음 | (기본값) 필요해지면 컬럼을 더한다 |

결과 대응(2026-10-20 기준 건수):

| 원본 `결과` | 건수 | `resolution` |
| --- | --- | --- |
| 해결 | 134 | 해결 |
| 해결(부품 교체) | 32 | 해결 |
| 자동 복구 | 9 | 해결 |
| 엔지니어 이관 후 해결 | 9 | 해결 |
| 임시 조치(재발 감시) | 12 | 미해결(실패 이력) |
| 미해결 | 4 | 미해결(실패 이력) |

더미에 없어 팀이 보강하는 것:

- **복합 장애**: 본문에 '동반'으로 다른 코드가 적힌 행(예: AGV-H-0011의 A381·A383, AGV-H-0021의 A309·A382)에서 일부를 골라 쌍 2~4개 이력으로 새로 만든다. 원본 단일 행은 지우지 않는다. `case_id`는 `<설비 약어>-H-C<NN>`(예: `AGV-H-C01`)이다 (기본값).
- **함정**: 더미에 이력이 없는 R206, S115, S121과 유사 증상 A308의 이력을 코드마다 1건 이상 추가 작성한다(M406, M414는 1건씩 있다). `case_id`는 `<설비 약어>-H-T<NN>`이다. 다른 설비의 비슷한 에러(주행 브레이크 이상 R206 / S115 / M406, 낙하 방지 장치 작동 S121 / M414)가 이력 검색에서 섞이지 않는지 시험하려는 것이다.
- 같은 설비 안의 비슷한 코드, 매뉴얼과 다른 실제 조치, 표기가 제각각인 이력은 변환 때 더미에 있는지 확인하고 부족하면 보강한다.

더미에 이미 있어 그대로 쓰는 것:

- 반복 에러 소재: 같은 코드가 24시간 안에 5건 나온 묶음이 A316, M420, R214, S135에 있다. 시드의 `created_at`이 과거라 시연 때 `count_recent`에 잡히지 않으므로, 시나리오 3은 새 신고로 5회를 채운다.
- 실패 이력: `미해결` 16건(원본 `임시 조치` 12 + `미해결` 4)으로 "실패 이력" 표시를 시연한다.
- 이력의 범위: 엔지니어뿐 아니라 고객사가 직접 조치한 기록도 이력이다(`actor`로 구분한다).
- 검증(`init_db.py --check`): 모든 코드가 4.1에 있고, `resolution`이 2종이며, `case_id`가 겹치지 않고, 복합은 `equipment`와 `code`의 `;` 개수가 같다.

### 4.4 답변·검색 타입 (Pydantic)

타입은 소유 모듈 파일에 두고 소유자만 바꾼다. `ErrorCode`는 `src/errorcodes.py`, `ServiceCase`는 `src/db.py`, `QueryAnalysis`·`Evidence`는 `src/search.py`, `Citation`·`Step`·`Answer`·`CaseBrief`는 `src/answer.py`에 둔다. 다른 담당은 import만 한다. `db`는 `proposal`을 JSON으로 저장하므로 `answer`를 import하지 않는다.

```
Citation:      kind("manual"|"history"), ref_id, locator
Step:          text, citations: list[Citation]          # 1개 이상
Answer:        steps: list[Step], verdict("answered"|"insufficient_evidence"|"not_applicable"), notice: str | None
CaseBrief:     summary: str, proposal: Answer
QueryAnalysis: equipment(Literal 4 또는 "other"), code | None, code_in_question: bool, symptom | None
Evidence:      exact_row: ErrorCode | None, manual_hits, history_hits, has_evidence: bool
```

- `ref_id`: 행은 `{equipment}/{code}`, 본문 청크는 `{equipment}/{section}`, 이력은 `case_id`. `locator`: 행은 CSV 행 번호, 본문은 사양서 쪽·절, 이력은 필드명(예: `engineer_action`).
- 인용 단위는 조치 단계다. 모든 `Step`에 `Citation`이 1개 이상 있어야 한다.
- `verdict`가 `insufficient_evidence` 또는 `not_applicable`이면 `steps`는 비우고 `notice`에 "근거 부족"(담당 엔지니어 확인 안내) 또는 "적용 대상 아님"(4개 설비 밖의 설비·장비)을 넣는다.

### 4.5 기타 데이터

- 설비 목록: 별도 파일 없이 `errorcodes.csv`의 `equipment` 고유값(4종)이며 질문 분석의 고정 목록 원천이다. 모델 단위는 두지 않는다. 설비 추가(P3)는 그 설비의 에러코드 행을 추가한다.
- `data/synonyms.csv`: 용어·동의어와 코드 표기 정규화. 질문 분석과 검색 전에 쓴다.
- `data/eval_questions.csv`: 7장.

## 5. 모듈 인터페이스

| 모듈         | 함수                                                                      | 입력 → 출력                                                                                                                                                                                       |
| ------------ | ------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `errorcodes` | `load_error_codes(path)`, `equipment_list(codes)`                         | CSV → `list[ErrorCode]` (검증 실패는 예외) / → 설비 목록(`equipment` 고유값)                                                                                                                      |
| `registry`   | `get(name)`                                                               | 모듈 이름(`indexing`, `search`, `answer`, `history`) → 실제 또는 가짜 모듈. 환경 변수 `MPILOT_FAKES`로 전환. 가짜는 `data/sample/<모듈>.json` 고정 응답이다. `db`는 가짜가 없어 registry를 거치지 않고 직접 import한다 |
| `parsing`    | `parse_body(dir)`                                                         | 사양서 PDF 4종 → 본문 청크(쪽·절). 표 9-4는 `scripts/merge_errorcodes.py`가 `errorcodes.csv`로 합친다                                                                                             |
| `indexing`   | `build_index(reset)`, `add_history(case)`                                 | CSV, 파싱 결과, 시드 → Chroma `manual`, `history` / 확정된 `ServiceCase` → `history`에 추가                                                                                                       |
| `indexing`   | `query(collection, text, equipment, k)`, `get_source(ref_id)`             | 설비 필터를 적용한 벡터 질의(히트: 거리, 메타데이터) / 인용 원문(표 행, 본문, 이력) 조회                                                                                                          |
| `db`         | `create_case(errors, memo, photo)`                                        | `errors`는 `(equipment, code)` 쌍 1~4개 → `ServiceCase`(접수번호 부여, `status=접수`, 쌍은 `case_errors`에 저장)                                                                                  |
| `db`         | `get_case`, `list_cases`, `update_case`, `append_message`, `get_messages` | 조회, 목록, 수정, 대화 기록 저장·조회                                                                                                                                                             |
| `db`         | `count_recent(equipment, code, hours=24)`                                 | → 한 쌍(설비·코드)이 들어 있는 최근 접수 건수(이번 건 포함). 복합 건은 건당 1회로 센다                                                                                                            |
| `db`         | `load_seed(path)`                                                         | `history_seed.csv` → `cases`(`source=authored`, `confirmed=true`)                                                                                                                                 |
| `db`         | `transition(case_id, event)`                                              | 3장 상태 전이 표대로 상태를 바꾼다. 허용되지 않는 전이는 예외                                                                                                                                     |
| `search`     | `analyze_query(text, default_equipment, default_code)`                    | → `QueryAnalysis`. 4개 설비 밖이면 `equipment=other`                                                                                                                                              |
| `search`     | `lookup_error_code(equipment, code)`                                      | → 정확히 1행 또는 `None`                                                                                                                                                                          |
| `search`     | `retrieve(analysis, k_manual=3, k_history=3)`                             | 정확 조회 + 매뉴얼·이력 검색을 병렬로 → `Evidence`                                                                                                                                                |
| `search`     | `decide_verdict(analysis, evidence)`                                      | → `"answered"` \| `"insufficient_evidence"` \| `"not_applicable"`. `analysis.equipment=other`이면 `evidence=None`(검색하지 않음)으로 불러 `not_applicable`, 아니면 `evidence.has_evidence`가 거짓일 때 `insufficient_evidence`, 아니면 `answered`. `brief`는 쌍마다, `ask`는 질문마다 부른다 |
| `answer`     | `brief(case)`                                                             | → `CaseBrief`. 저장하고 이미 있으면 재생성하지 않는다                                                                                                                                             |
| `answer`     | `ask(case_id, question)`                                                  | → `Answer`. 접수번호별 대화 기억을 반영한다                                                                                                                                                       |
| `routing`    | `route(pairs)`                                                            | `pairs`는 `(equipment, code, count_24h)` 1~4개. DB 비의존. → `"support"` \| `"guide"`(건 단위)                                                                                                    |
| `routing`    | `guide_for(errors)`                                                       | `errors`는 `(equipment, code)` 쌍 1~4개 → 모든 쌍이 저위험이면 쌍별 `action` 목록, 하나라도 아니면 `None`                                                                                         |
| `history`    | `structure_resolution(case_id, freeform, draft)`                          | → 구조화된 원인·조치·결과 초안(저장하지 않음)                                                                                                                                                     |
| `history`    | `confirm_case(case_id, fields, diverged)`                                 | 확인된 값 저장 → `confirmed=true`, `add_history`, 해결이면 `status=완료`                                                                                                                          |
| `evaluate`   | `run_eval(questions_csv)`                                                 | → 지표 리포트(7장)                                                                                                                                                                                |

모듈은 `src/<모듈>.py`다. `registry.get(name)`이 돌려주는 실제와 가짜는 시그니처가 같다. 상태가 있는 `db`는 가짜 없이 개발에서도 실제 `db.py`를 MySQL로 쓰고 `from src import db`로 직접 가져온다. `registry`는 `indexing`, `search`, `answer`, `history` 4개만 다루며, `data/sample/<모듈>.json`의 함수별 고정 응답을 4.4 타입으로 바꿔 돌려준다. 가짜의 쓰기 함수(`add_history`, `confirm_case`)는 저장하지 않는다. `registry`는 모듈 전환 장치이며 설비 목록과 다르다.

## 6. 기능 명세

기능은 사용자 스토리(US-번호)와 인수 기준(AC-스토리번호.순번)으로 정리한다. 스토리 하나는 담당 파트 한 곳이 끝까지 책임진다. 다른 파트의 기능이 필요하면 5장 인터페이스(가짜 구현 포함)를 호출하고, 그 파트의 파일은 고치지 않는다. 서비스 사용자는 엔지니어와 고객뿐이고, "팀원"은 개발·평가용 내부 스토리의 표기다. 인수 기준의 `검증` 열은 다른 파트의 가짜 위에서도, 실제 구현으로 바꾼 뒤에도 통과해야 한다.

### 6.1 사용자 스토리

| ID    | 사용자        | 하고 싶은 일                                                                           | 이유                                            | P   | 담당 파트      |
| ----- | ------------- | -------------------------------------------------------------------------------------- | ----------------------------------------------- | --- | -------------- |
| US-01 | 엔지니어      | 에러코드 표와 사양서 본문이 사양서 기준으로 정리되어 있다                              | 설비·코드마다 정확한 원인·조치를 믿고 보려고    | P1  | 벡터 DB·데이터 |
| US-02 | 팀원          | 원본 자료에서 검색 인덱스를 다시 만들고, 설비 필터가 걸린 검색과 인용 원문 조회를 쓴다 | 검색 파트가 인덱스를 기다리지 않고 개발하려고   | P1  | 벡터 DB·인덱싱 |
| US-03 | 엔지니어      | 방금 확정한 이력이 같은 코드의 새 신고에서 근거로 쓰인다                               | 조치 노하우가 개인에게 갇히지 않게              | P2  | 벡터 DB·인덱싱 |
| US-04 | 엔지니어      | 접수 건과 대화 기록이 저장되어 세션을 다시 열어도 이어진다                             | 작업을 이어가려고                               | P1  | MySQL          |
| US-05 | 엔지니어      | 가상 이력(제공 200건 변환 + 복합·함정 보강분, 미해결 포함)이 처음부터 적재되어 있다                         | 검색 근거와 시연에 쓰려고                       | P1  | MySQL          |
| US-06 | 고객          | 설비와 에러코드를 (한 건에 최대 4쌍) 골라 신고하고 접수번호를 받는다                   | 초동 대응을 빨리 받으려고                       | P1  | 고객 접수      |
| US-07 | 고객          | 접수번호로 상태와 서비스 조치 결과를 확인한다                                                 | 진행 상황을 알려고                              | P1  | 고객 접수      |
| US-08 | 고객          | 기술지원 건이면 기술지원 대기 안내를 본다                                                       | 설비를 안전하게 두고 기다리려고                 | P1  | 고객 접수      |
| US-09 | 고객·엔지니어 | 모든 화면에서 가상 데이터임을 알아본다                                                 | 가상 데이터를 실제로 오해하지 않으려고          | P1  | 고객 접수      |
| US-10 | 고객          | 저위험 에러는 셀프 가이드를 받아 직접 해결하고 결과를 알린다                             | 엔지니어를 기다리지 않으려고                    | P2  | 고객 접수      |
| US-11 | 고객          | 저위험이 아닌 에러는 바로 기술지원으로, 저위험 에러만 셀프 가이드로 분기된다                         | 위험한 조치를 직접 하지 않으려고                | P2  | 고객 접수      |
| US-12 | 엔지니어      | 새 신고를 접수 목록에서 본다                                                           | 처리할 건을 놓치지 않으려고                     | P1  | 엔지니어 세션  |
| US-13 | 엔지니어      | 세션 화면에서 요약·대응 방안, 인용, 사진을 보고 상태를 바꾼다                          | 한 화면에서 대응하려고                          | P1  | 엔지니어 세션  |
| US-14 | 엔지니어      | 세션을 열면 접수 건 요약과 대응 방안을 자동으로 받는다                                 | 원인 파악과 방향 결정을 앞당기려고              | P1  | 엔지니어 세션  |
| US-15 | 엔지니어      | 질문과 후속 질문에 출처가 붙은 답을 받는다                                             | 답을 믿고 확인하려고                            | P1  | 엔지니어 세션  |
| US-16 | 엔지니어      | 근거가 없으면 추측 대신 "근거 부족"·"적용 대상 아님" 안내를 받는다                     | 지어낸 답에 속지 않으려고                       | P1  | 엔지니어 세션  |
| US-17 | 팀원          | 평가 질문으로 정답률 등 지표를 재고 통합 시나리오로 전체 흐름을 점검한다               | 시연 전에 품질을 숫자로 보이려고                | P2  | 서비스 이력  |
| US-18 | 엔지니어      | 에러코드의 원인·조치를 설비를 혼동하지 않고 정확히 조회한다                            | 엉뚱한 설비의 조치를 따르지 않으려고            | P1  | RAG 검색       |
| US-19 | 엔지니어      | 증상만으로 관련 매뉴얼 본문과 과거 이력을 찾는다                                       | 매뉴얼을 직접 뒤지는 시간을 줄이려고            | P1  | RAG 검색       |
| US-20 | 엔지니어      | 4개 설비 밖의 질문은 "적용 대상 아님"으로 구분받는다                                   | 자료에 없는 것과 우리 설비가 아닌 것을 가르려고 | P1  | RAG 검색       |
| US-21 | 엔지니어      | 현장 용어·동의어·코드 표기가 달라도 같은 결과를 받는다                                 | 표기 때문에 답을 놓치지 않으려고                | P1  | RAG 검색       |
| US-22 | 엔지니어      | 이력 작성 화면에서 초안을 확인·수정하고 확정한다                                       | 조치 결과를 쉽게 남기려고                       | P2  | 서비스 이력      |
| US-23 | 엔지니어      | 조치 이력이 자동으로 구조화되어 확인 후 저장된다                                       | 다음 대응의 근거로 돌려주려고                   | P2  | 서비스 이력      |
| US-24 | 엔지니어      | 접수 없이 설비·질문만으로 바로 조회한다                                                | 현장에서 빠르게 확인하려고                      | P3  | 서비스 이력      |

### 6.2 인수 기준

| ID      | 요구사항                                                                                                                                                                                                                                          | 검증                              |
| ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------- |
| AC-01.1 | 249개를 `ErrorCode`로 로드한다. 심각도나 조치 구분이 비었거나 (설비, 코드)가 중복이면 실패한다. 동명 에러도 (설비, 코드)로 구분한다                                                                                                               | 불량 CSV, 동명 에러 조회          |
| AC-01.2 | `merge_errorcodes.py`가 표 9-4와 CSV를 코드 기준으로 합쳐 `errorcodes.csv`를 만들고, 심각도·조치 구분이 어긋난 행은 목록으로 보고한다. `equipment` 값은 4종뿐이다                                                        | 대조 리포트                       |
| AC-01.3 | 사양서 본문이 쪽·절 메타데이터가 붙은 청크로 나뉜다(`parse_body`). 표 9-4는 `merge_errorcodes.py`가 따로 합친다                                                                                                                                   | 청크 메타데이터                   |
| AC-02.1 | 검색 문서는 원본(`errorcodes.csv`, `data/manuals/`, 시드)에서만 생성한다. 원본을 고치고 `build_index`를 다시 돌리면 문서가 따라 바뀐다. 표 문서 1개 = 에러코드 1행이다. 설비 추가(P3)도 표를 추가하고 재인덱싱한다                                | 수정 후 재생성                    |
| AC-02.2 | `indexing.query`는 설비 필터를 적용한 히트(거리, 메타데이터)를 반환하고, `indexing.get_source`는 인용 원문을 반환한다                                                                                                                             | 필터 검색, 원문 조회              |
| AC-03.1 | `indexing.add_history`는 확정된 건만 `history`에 추가한다. 확인 전 초안은 검색되지 않는다. 복합 건은 3장 검색 규칙대로 쌍마다 추가한다                                                                                                            | 확정 전후 검색, 복합 건           |
| AC-03.2 | 같은 코드로 새 신고를 하면 방금 확정한 접수 건이 요약·대응 방안과 답변의 근거로 나타난다                                                                                                                                                          | 통합 시나리오 3                   |
| AC-04.1 | `create_case`는 `case_id`(`SC-YYYYMMDD-NNN`)를 부여하고 쌍 1~4개를 `case_errors`에 저장하며(0개, 5개 이상, 같은 쌍 중복은 거부. 같은 설비의 다른 코드는 허용) `status=접수`로 저장한다. 동시에 접수해도 `case_id`가 겹치지 않는다                 | DB 확인                           |
| AC-04.2 | 요약·대응 방안·대화 기록·상태를 저장하고 조회한다. `transition`이 상태 전이를 3장 표대로만 허용한다                                                                                                                                               | 저장 후 조회, 전이                |
| AC-04.3 | `count_recent`는 한 쌍(설비·코드)이 들어 있는 최근 24시간 접수 건수(이번 건 포함, 복합 건은 건당 1회)를 반환한다                                                                                                                                  | 경계 시각                         |
| AC-05.1 | 제공된 장애 기록 200건을 `convert_history.py`로 변환하고 복합·함정 보강분을 더해 가상 이력을 만든다(미해결 포함). `data/sample/`(샘플 에러코드 10행, 이력 5건)도 만든다. `init_db.py --check`가 이력의 코드·형식(4.3 검증 항목)을 검증한다                                                                                                     | `init_db.py --check`, 상호 검수   |
| AC-05.2 | `load_seed`가 `history_seed.csv`를 `cases`(`source=authored`, `confirmed=true`)로 적재한다. `case_id`는 원본 이력ID, `created_at`은 발생일시다                                                                                                                                                        | 건수                              |
| AC-06.1 | 설비와 에러코드는 필수이고 에러코드는 고른 설비의 목록에서만 고른다. 한 건에 쌍을 1~4개 고를 수 있고(복합 장애) 같은 쌍을 두 번 고르거나 5번째 쌍을 추가할 수 없다. 같은 설비의 다른 코드는 함께 고를 수 있다. 증상 메모와 사진은 비워도 접수된다 | 폼 테스트, 최소 입력              |
| AC-06.2 | 사진은 jpg/jpeg/png, 5MB 이하만 저장한다. 초과하면 거부 메시지를 보이되 사진 없이 접수할 수 있다                                                                                                                                                  | 큰 파일, 다른 형식                |
| AC-06.3 | 제출하면 접수번호가 보이고 접수 버튼은 없다. 신고 처리 순서는 `create_case` → `count_recent`(쌍마다) → `route` → `update_case`다                                                                                                                  | 화면, 호출 순서                   |
| AC-07.1 | 접수번호로 상태(접수 / 분석 중 / 조치 중 / 완료)와 서비스 조치 결과를 조회한다                                                                                                                                                                           | 조회 테스트                       |
| AC-07.2 | 고객 Tab은 접수번호, 설비-코드 쌍, 상태, 셀프 가이드, 기술지원 대기 안내, 확정된 서비스 조치 결과만 렌더링한다. AI 요약·대응 방안·이력 원문은 렌더링하지 않는다(접근 차단은 아님, 기본값)                                                                      | 코드 경로 점검                    |
| AC-08.1 | `branch=support`인 건의 고객 화면에 기술지원 대기 안내(안전 확보, 설비 정지 유지, 기술지원팀 연락)가 보인다                                                                                                                                                | 기술지원 건 조회                  |
| AC-09.1 | 모든 화면 상단에 "모든 데이터는 가상" 배너를 둔다. 화면·발표 문구는 "AI가 학습한다"가 아니라 "검색 근거가 늘어난다"로 쓴다                                                                                                                        | 화면·문구 점검                    |
| AC-10.1 | `branch=guide`이면 셀프 가이드(`guide_for`, 복합 신고는 쌍별 조치 문구)와 "해결됨 / 해결 안 됨" 버튼을 보인다                                                                                                                                       | 저위험 코드 신고                  |
| AC-10.2 | "해결됨"이면 곧바로 `완료`(기본값), "해결 안 됨"이면 `branch=support`로 바뀌어 세션을 열 수 있다                                                                                                                                                  | 두 경로                           |
| AC-11.1 | 쌍마다 `severity=상`이면 `support`, `action_owner=고객사 조치`이고 `severity`가 중·하이며 `count_24h`가 5 미만이면 `guide`, 그 밖에는 `support`다. 건 단위 합치기는 3장 분기 규칙을 따르고, P1에서는 항상 `support`다                             | 심각도 3 × 조치 구분 2, 복합 혼합 |
| AC-11.2 | 쌍 중 하나라도 `count_24h`(이번 건 포함)가 5 이상이면 저위험 코드도 `support`이고 `repeat_flag=true`다                                                                                                                                            | 4와 5 경계값                      |
| AC-11.3 | `route`는 쌍 목록(`equipment`, `code`, `count_24h`)만 입력받고 에러코드 표 외에는 읽지 않는다(DB 비의존). 증상 메모와 사진을 쓰지 않는다                                                                                                          | 시그니처                          |
| AC-11.4 | `guide_for`는 모든 쌍이 저위험이면 쌍별 행의 `action` 목록을, 하나라도 아니면 `None`을 반환한다(저위험 쌍의 셀프 가이드도 내보내지 않는다)                                                                                                               | 저위험 행, 상 행, 복합 혼합       |
| AC-12.1 | 새 신고가 목록 새로고침으로 나타나고 상태·분기·반복 에러가 표시된다. `source=authored` 시드 이력은 목록에 나타나지 않는다                                                                                                                                                                               | 신고 후 새로고침                  |
| AC-12.2 | `branch=guide`인 건은 세션을 만들지 않고 목록에 "셀프 가이드"로 표시한다                                                                                                                                                                            | 목록                              |
| AC-13.1 | 세션은 `branch=support`인 건에만 열린다. 처음 열면 `brief` 결과를 요약·대응 방안 카드로 보이고 `status=분석 중`이 된다                                                                                                                            | 세션 열기                         |
| AC-13.2 | 다시 열면 저장된 요약·대응 방안·대화를 그대로 보인다                                                                                                                                                                                              | 재열기                            |
| AC-13.3 | 인용을 누르면 `indexing.get_source`로 가져온 원문(표 행, 본문, 이력)이 보인다. 근거 부족과 적용 대상 아님은 눈에 띄게 표시된다                                                                                                                    | 화면                              |
| AC-13.4 | 접수 사진을 열람하고 상태를 `조치 중`으로 바꿀 수 있다                                                                                                                                                                                            | 사진 있는 건, 상태 변경           |
| AC-14.1 | `brief`는 요약(쌍마다 설비, 코드의 증상·원인과 쌍별 근거, 증상 메모, 사진 유무, 유사 이력 유무)과 대응 방안을 반환한다. 메모와 코드가 어긋나면 둘 다 보이고 코드를 우선한다                                                                       | 샘플 3건                          |
| AC-14.2 | 심각도가 상인 코드의 대응 방안에는 매뉴얼의 안전 수칙 단계가 포함된다                                                                                                                                                                             | 샘플 확인                         |
| AC-14.3 | 접수 건당 한 번만 생성해 저장하고, 이미 있으면 LLM을 다시 호출하지 않는다                                                                                                                                                                         | 재호출 횟수                       |
| AC-14.4 | 복합 신고에서 쌍 사이의 인과 관계는 근거가 있을 때만 말하고, 근거가 없으면 추측하지 않는다                                                                                                                                                        | 복합 샘플 확인                    |
| AC-14.5 | 행의 `extra.target_models`가 있으면 요약·대응 방안과 `ask` 답변이 모델별 값을 병기한다. 신고에서 모델은 받지 않는다 | 모델별 값이 있는 행(예: RTV 제동 저항) |
| AC-15.1 | 대응 방안과 `ask` 답변의 모든 `Step`에 `Citation`이 1개 이상 있다. 없으면 출력 검증이 실패한다                                                                                                                                                    | 파서 검증                         |
| AC-15.2 | 같은 `case_id`의 후속 질문은 앞의 질문·답변을 반영한다                                                                                                                                                                                            | 연속 질문                         |
| AC-16.1 | `verdict`가 `insufficient_evidence` 또는 `not_applicable`이면 `steps`가 비고 안내가 나온다. 추측 답변을 만들지 않는다                                                                                                                             | 근거 없는 질문, 4개 설비 밖 질문  |
| AC-17.1 | `run_eval`은 화면 없이 엔진을 직접 호출하고 고정된 스냅샷에서 같은 결과를 낸다. 7장 지표를 유형별로 계산하고 채택률은 정답률과 독립적으로 돈다                                                                                                    | 두 번 실행 비교, 리포트           |
| AC-17.2 | 6.4 통합 시나리오 1~4가 통과한다                                                                                                                                                                                                                  | `tests/test_integration.py`       |
| AC-18.1 | `analyze_query`의 `equipment`는 고정 목록 4개 중 하나다(4개 밖은 US-20)                                                                                                                                                                           | 구조화 출력                       |
| AC-18.2 | `lookup_error_code`는 정확히 1행 또는 `None`이고, 비슷한 코드나 다른 설비의 같은 이름 에러(R206 / S115 / M406)를 반환하지 않는다                                                                                                                  | 함정 케이스                       |
| AC-19.1 | `retrieve`는 정확 조회, 매뉴얼 검색, 이력 검색을 병렬로 실행하고 모두 설비 필터를 적용한다. 다른 설비의 문서가 나오지 않는다                                                                                                                      | 호출 인자, 필터 검색              |
| AC-19.2 | 매뉴얼 결과는 에러코드 행(원본 행 번호) 또는 본문 청크(사양서 파일·쪽·절)로 출처를 알 수 있다                                                                                                                                                     | 메타데이터                        |
| AC-19.3 | 이력은 확정된 건만 검색된다. `resolution=미해결` 이력은 제외하지 않고 "실패 이력"으로 표시한다                                                                                                                                                    | 진행 중 건, 미해결 이력 검색      |
| AC-19.4 | `Evidence.has_evidence`는 6.3의 근거 부족 규칙대로 계산하고, `decide_verdict`는 적용 대상 아님 → 근거 부족 순으로 판정한다                                                                                                                                                                                       | 근거 없는 질문                    |
| AC-20.1 | `analyze_query`가 4개 설비 밖의 설비·장비를 `equipment=other`로 분류하면 `decide_verdict`가 `not_applicable`로 판정하고 검색 없이 답하게 한다. LLM은 분류만 한다                                                                                              | 대형 AMR, 입출고 스테이션 질문    |
| AC-21.1 | `synonyms.csv`의 동의어·표기로 질문을 정규화한 뒤 분석한다                                                                                                                                                                                        | 동의어 질문                       |
| AC-22.1 | 이력 작성 화면이 세션 기록 기반 초안을 보여주고, 자유서술 입력과 "예상과 달랐는지" 체크박스를 제공한다                                                                                                                                            | 세션 후 화면                      |
| AC-22.2 | 엔지니어가 확인·수정해 확정 버튼을 누르기 전에는 저장하지 않는다                                                                                                                                                                                  | 저장 전 DB                        |
| AC-23.1 | `cause_found`, `engineer_action` 초안을 세션 기록으로 채우고 `structure_resolution`이 구조화한다. 확인 전에는 저장하지 않는다                                                                                                                     | 세션 후, 저장 전 DB               |
| AC-23.2 | `confirm_case`가 `confirmed=true`로 만들고 `history`에 추가를 요청한다                                                                                                                                                                            | 확정 전후 검색                    |
| AC-23.3 | `resolution=해결`일 때만 `status=완료`다. `diverged` 값을 저장한다                                                                                                                                                                                | 미해결 저장, DB                   |
| AC-24.1 | 독립 조회 화면(`ask`, `retrieve` 재사용, 설비 선택 필수, 임시 세션 키), 평가 화면, 설비 추가(표 추가 후 재인덱싱). 새 검색 로직은 만들지 않는다                                                                                                   | 화면                              |

### 6.3 판정 규칙 (US-16, US-19, US-20 공통)

판정 순서는 적용 대상 아님 → 근거 부족이다. 판정은 `search.decide_verdict`(코드)가 하고 LLM은 바꾸지 못한다. `answer`는 그 결과를 `Answer.verdict`에 옮길 뿐이다.

- **적용 대상 아님**: `analyze_query`가 질문이 가리키는 설비를 고정 목록 4개 중 하나 또는 `other`로 분류한다. `other`이면 코드가 `not_applicable`로 판정하고 검색 없이 답한다. LLM은 분류만 한다.
- **근거 부족**: 정확 조회가 근거로 인정되거나, 매뉴얼·이력의 상위 k 히트 중 하나라도 거리가 임계값 τ 이하이면 근거가 있다. 둘 다 아니면 `insufficient_evidence`다. 정확 조회는 `brief`에서는 접수 건의 각 쌍의 코드로(쌍마다 따로 판정), `ask`에서는 질문에 코드가 명시된 경우(`code_in_question`)에만 근거로 인정한다. 접수 건의 쌍으로 채운 경우를 빼서, 코드와 무관한 질문이 항상 근거 있음으로 판정되지 않게 한다. τ는 거리 로그로 정하고 평가 질문으로 한 번 조정한 뒤 고정한다.

### 6.4 통합 시나리오 (시연·통합 테스트)

1. 저위험 코드 신고(US-06, US-11) → 셀프 가이드(US-10) → "해결됨" → `완료`.
2. 심각도 상 코드 신고(US-06) → 기술지원, 세션(US-13, US-14, US-15) → 인용 확인 → 이력 확정(US-22, US-23).
3. 같은 코드로 재신고 → 방금 확정한 이력이 근거로 나타남(US-03). 같은 저위험 코드를 5회 신고 → 기술지원으로 전환(AC-11.2).
4. 복합 신고(저위험 쌍 1개 + 상 쌍 1개) → 건 전체가 기술지원, 저위험 쌍의 셀프 가이드는 고객에게 나가지 않음(AC-11.1, AC-11.4) → 세션에서 쌍별 근거가 구분되어 보임(AC-14.1) → 이력 확정 후 각 쌍의 코드로 재신고하면 근거로 나타남(AC-03.1).

## 7. 평가

`data/eval_questions.csv` (30~40문항, 데이터를 쓰지 않은 사람이 작성):

| 컬럼                             | 설명                                                                                                                                        |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `q_id`, `type`                   | `lookup` / `symptom` / `confusion`(설비 혼동) / `compound`(복합 신고, 3~5문항) / `none`(근거 없음) / `not_applicable` / `spec`(사양서 본문) |
| `subtype`                        | `spec` 세부 유형: `version` / `calc` / `xref` / `multipage`                                                                                 |
| `equipment`, `code`, `question`  | 입력. `compound`는 `equipment`, `code`를 `;`로 같은 순서로 나열한다(4.3과 같다)                                                             |
| `expected_ref`, `expected_value` | 정답 출처(`{equipment}/{code}`, `{equipment}/{section}`, `case_id`)와 정답 값. `none`, `not_applicable`은 비움                              |

| 지표                | 계산                                                                                                   | 판정                         |
| ------------------- | ------------------------------------------------------------------------------------------------------ | ---------------------------- |
| 정확 조회 정답률    | `lookup`에서 반환 행이 `expected_ref`와 일치한 비율                                                    | 자동                         |
| 설비 혼동           | `confusion`에서 다른 설비의 행이 나온 건수                                                             | 자동                         |
| 복합 신고 쌍별 근거 | `compound`에서 각 쌍의 근거가 해당 쌍의 `expected_ref`와 일치하고 다른 쌍·설비의 행이 섞이지 않은 비율 | 자동(인용은 사람 샘플)       |
| 증상 검색 적중      | `symptom`에서 `expected_ref`가 상위 k(=3)에 포함된 비율                                                | 자동                         |
| 근거 부족 처리      | `none`에서 `verdict=insufficient_evidence`인 비율(나머지는 지어낸 것)                                  | 자동                         |
| 적용 대상 아님      | `not_applicable`에서 `verdict=not_applicable`인 비율(근거 부족 처리는 오답)                            | 자동                         |
| 사양서 본문 정답률  | `spec`에서 답변이 `expected_value`와 일치하고 인용이 `expected_ref`를 가리키는 비율                    | 값은 자동, 인용은 사람 샘플  |
| 응답 시간           | 질문별 소요 시간의 평균·중앙·최대(API 지연 포함)                                                       | 자동                         |
| 인용 정확도         | 인용이 단계 내용을 뒷받침하는 비율                                                                     | 사람이 무작위 20개 단계 확인 |
| 채택률(시연용)      | `diverged=false` 비율                                                                                  | 자동                         |

- 목표치는 정하지 않고 측정한다. 이력 추가 전/후 비교는 평가와 별도 시연이다.
- 스냅샷: `data/`의 CSV·사양서 PDF와 `scripts/build_index.py`를 저장소에 두고 Chroma 디렉터리는 올리지 않고, DB는 `scripts/init_db.py`로 각자 만든다(평가·시연은 MySQL). 각자 `build_index`로 재생성하고, 평가 직전에 `build_index(reset=True)`와 `scripts/init_db.py --reset`으로 인덱스와 DB를 시드 상태로 되돌려 시연 중 확정된 이력의 영향을 없앤다.

## 8. 역할 분담 (안)

7명 = DB 3명(벡터 DB 2, MySQL 1) + RAG·UI 4명(고객 접수, 엔지니어 세션, RAG 검색, 서비스 이력)이다. 별도 테스트·평가 인원은 없다. 각 파트가 자기 스토리의 계약 테스트를 직접 쓰고, 평가와 통합 시나리오 점검은 서비스 이력 담당이 겸한다. 스토리 하나는 담당 파트 한 곳이 책임지고(6.1), 파트마다 소유 파일이 달라 같은 파일을 동시에 고치지 않는다. 서로의 모듈은 가짜 구현으로 대신해 기다리지 않고 개발한다.

제출물 4종(PM·발표, README, 시연 영상, 결과보고서)은 파트 배정과 별도로 rohdonggyun-lab이 맡는다(7명 중 한 명이 파트 업무와 겸한다).

### 8.1 파트별 역할

| 그룹   | 파트           | 담당 스토리 | 하는 일                                                                                                                                                                               |
| ------ | -------------- | ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| DB     | 벡터 DB·데이터 | US-01       | 표 9-4와 에러코드 CSV를 병합해 `errorcodes.csv` 249개를 만들고 검증한다. 사양서 본문을 쪽·절 청크로 나눈다                                                                             |
| DB     | 벡터 DB·인덱싱 | US-02, 03   | Chroma `manual`·`history`를 만들고, 설비 필터 검색(`query`)과 인용 원문 조회(`get_source`), 확정 이력 추가(`add_history`, 복합 건은 쌍별 문서, 검색은 `case_id` 중복 제거)를 제공한다 |
| DB     | MySQL          | US-04, 05   | MySQL 환경, 접수 건·쌍(`case_errors`)·대화 저장과 상태 전이, 24시간 건수, 가상 이력(원본 200건 변환 + 보강분)                             |
| RAG·UI | 고객 접수      | US-06~11    | 신고 폼(사진 포함, 설비-에러코드 쌍 1~4개 입력), 접수번호 조회, 기술지원 대기 안내, 저위험·반복 에러 분기(`route`), 셀프 가이드, 배너, `app.py` Tab 조립                                       |
| RAG·UI | 엔지니어 세션  | US-12~16    | 접수 목록과 세션 화면, 요약·대응 방안(`brief`, 복합 건은 쌍별 `retrieve`), 질문 답변(`ask`, 인용 검증), 근거 부족·적용 대상 아님 안내                             |
| RAG·UI | RAG 검색       | US-18~21    | 질문 분석, 에러코드 정확 조회, 매뉴얼·이력 검색, 적용 대상 아님·근거 부족 판정(`decide_verdict`), τ, 동의어 사전                                                                                          |
| RAG·UI | 서비스 이력      | US-17, 22~24    | 가짜 전환(`registry`)과 저장소 공통 파일(`requirements.txt`, `.gitignore`, `.env.example`), 이력 작성 화면, 이력 구조화, 확정과 인덱스 추가(`confirm_case`), 평가 실행(`run_eval`)과 통합 시나리오 점검, 독립 조회·평가 화면(P3)                                                                                              |

파트별 소유와 인계:

- **벡터 DB·데이터**: 코드 `src/errorcodes.py`(타입 `ErrorCode`), `src/parsing.py`, `scripts/merge_errorcodes.py`. 데이터 `errorcodes.csv` 249개, `data/manuals/` 관리. 제공: `errorcodes` 로더, `errorcodes.csv`, 본문 청크. 의존: 없음(사양서 개정본은 수령 완료). 검수: MySQL의 `history_seed.csv`
- **벡터 DB·인덱싱**: 코드 `src/indexing.py`, `scripts/build_index.py`. 데이터 Chroma 컬렉션(저장소 제외, `build_index`로 재생성), 가짜 응답 `data/sample/indexing.json`. 제공: `indexing.query`, `get_source`, `add_history`. 의존: `errorcodes`, `parsing`, `history_seed.csv`
- **MySQL**: 코드 `src/db.py`(타입 `ServiceCase`, 대화 기록, `count_recent`, `load_seed`, `transition`), `scripts/init_db.py`(`--reset`, `--check`), `scripts/convert_history.py`, `scripts/schema.sql`, `docker-compose.yml`(MySQL). 데이터 `history_seed.csv`(원본 200건 변환 + 복합·함정 보강분), `data/sample/`. 제공: MySQL 환경(`docker-compose.yml`), `db`(가장 먼저). 검수: `errorcodes.csv`(저위험 78개 문구의 안전 수칙 부합, 다른 절 참조·전문 용어). 보강: 복합 장애·함정 이력을 쓴다(4.3, 작성자는 intent Open questions 5)
- **고객 접수**: 코드 `app.py`(Tab 조립, 배너), `src/ui/customer.py`, `src/routing.py`. 의존: `db`, `errorcodes`
- **엔지니어 세션**: 코드 `src/ui/engineer.py`(목록·세션), `src/answer.py`(타입 `Answer` 등, 프롬프트 2종). 데이터 가짜 응답 `data/sample/answer.json`. 제공: `brief`, `ask`. 의존: `search`(`retrieve`, `decide_verdict`), `db`, `indexing.get_source`
- **RAG 검색**: 코드 `src/search.py`(타입 `QueryAnalysis`, `Evidence`, 질문 분석 프롬프트), τ 조정. 데이터 `synonyms.csv`, 가짜 응답 `data/sample/search.json`. 제공: `retrieve`, `lookup_error_code`, `decide_verdict`. 의존: `indexing.query`, `errorcodes`
- **서비스 이력**: 조치 이력의 작성·확정이 본업이고, 평가·통합과 저장소 공통 장치(`registry` 등)도 맡는다. 코드 `src/registry.py`, `src/history.py`(프롬프트 1종), `src/ui/resolution.py`(이력 작성, 독립 조회·평가 화면), `src/evaluate.py`, `tests/test_integration.py`, `.env.example`, `.gitignore`, `requirements.txt`. 데이터 `eval_questions.csv`, 가짜 응답 `data/sample/history.json`, 통합 시나리오 실행, 인용 샘플 확인. 제공: `registry`(가장 먼저), `structure_resolution`, `confirm_case`, `run_eval`. 의존: `db`, `indexing.add_history`, `search`·`answer`(`run_eval`과 US-24의 `retrieve`, `ask`). 평가 질문은 `expected_ref`를 벡터 DB·데이터와 MySQL 담당이 확인한다

### 8.2 병렬 개발 규칙

- 계약: 5장 시그니처와 4장 타입이 계약이다. 타입은 소유 모듈 파일에 두고 소유자만 바꾼다. 소유자가 바꾸면 spec.md를 같이 고치고 의존하는 파트에 알린다.
- 가짜 먼저: 개발 초기에 `indexing`(벡터 DB·인덱싱), `search`(RAG 검색), `answer`(엔지니어 세션), `history`(서비스 이력)가 `data/sample/<모듈>.json`에 함수별 고정 응답을 올린다. JSON 형식은 4.4 타입을 그대로 옮긴 것이고, 바꾸면 타입과 같이 바꾼다. MySQL은 `docker-compose.yml`과 `db`의 첫 버전(접수·조회·목록), 샘플 데이터를, 서비스 이력 담당은 `registry`를 올린다.
- 사용 방법: `indexing`, `search`, `answer`, `history`는 `registry.get("search")`처럼 받아 쓰고, `MPILOT_FAKES=search,answer`처럼 모듈별로 가짜와 실제를 전환한다. `db`는 가짜가 없으므로 직접 import한다. 모든 팀원이 로컬 Docker MySQL로 개발한다.
- 테스트: 각 파트는 자기 스토리의 6.2 인수 기준을 `tests/test_<모듈>.py`로 가짜와 로컬 MySQL 위에서 먼저 쓴다. DB 테스트는 시연용과 분리된 테스트 DB를 쓰고 테스트마다 비운다. 테스트 DB 접속 정보는 환경 변수(`.env.example`)로 받아, 로컬 Docker MySQL과 GitHub Actions의 MySQL 서비스 컨테이너에서 같은 테스트가 돌게 한다. CI에는 API 키가 없으므로 `indexing`, `search`, `answer`, `history`는 항상 가짜로 돌린다(워크플로 파일은 첫 구현이 올라온 뒤에 추가한다). 서비스 이력 담당은 통합 테스트(6.4)와 평가를 맡고, 평가 질문은 에러코드·이력 데이터를 만들지 않은 서비스 이력 담당이 쓴다. Gradio 화면에서만 확인되는 AC(렌더링·문구·배치: AC-06.1의 폼 제약, 07.2, 09.1, 13.3, 22.1)는 pytest 대신 수동 체크리스트로 점검하고, 핸들러가 부르는 함수 호출 순서와 저장값은 pytest로 쓴다.
- 파일 경계: 엔지니어 Tab은 `src/ui/engineer.py`(엔지니어 세션), `src/ui/resolution.py`(서비스 이력)로 나눠 같은 화면 파일을 두 사람이 고치지 않는다. 각 화면은 `app.py`(고객 접수)가 Tab에 올린다.
- 실제 구현으로 교체하는 순서는 `db` → `errorcodes.csv` → `indexing` → `search` → `answer`·`history` → 화면 통합이다. 각 교체는 해당 스토리의 인수 기준 검증이 통과해야 한다.
- 상호 검수: 벡터 DB·데이터와 MySQL이 서로의 자료를 검수한다.

### 8.3 리스크와 완충

- **병목은 MySQL과 서비스 이력의 첫 산출물이다.** MySQL 환경(`docker-compose.yml`), `db`(MySQL)와 `registry`(서비스 이력)가 늦으면 전원이 막힌다. 환경, 접수·조회, `registry`부터 먼저 올린다.
- **벡터 DB 두 파트의 인계.** 인덱싱은 데이터 파트의 `errorcodes.csv`와 청크를 기다린다. 인계 전까지 `data/sample/`로 개발하고, 인계 후 `build_index`만 다시 돌린다.
- **엔지니어 세션은 UI와 `answer`(US-12~16)에 집중한다.** 평가·통합(US-17)과 저장소 공통 파일은 P1 통합 전까지 가벼운 서비스 이력 담당이 맡고, 평가 질문은 데이터를 만들지 않은 서비스 이력 담당이 쓴다. 서비스 이력 담당이 P2 이력 작성과 겹쳐 밀리면 질문 작성만 엔지니어 세션이 돕는다(데이터를 만들지 않은 사람이라는 조건을 지킨다).
- **P1 범위가 크다**(intent 열린 질문 1). 두 화면은 가짜 위에서 먼저 끝까지 돌리고, P1 통합에서 실제 구현으로 바꾼다.
- **엔지니어 Tab 파일 경계.** `src/ui/engineer.py`(엔지니어 세션), `src/ui/resolution.py`(서비스 이력)로 나뉘어 있다. 화면 조립은 `app.py`(고객 접수)가 맡으므로 화면 함수의 이름과 인자는 개발 시작 전에 합의한다.
- 팀원별 MySQL 상태 차이는 7장 스냅샷 절차(`--reset`)를 따른다.
- **이력 변환·보강은 MySQL 파트의 몫이 커진다.** 200건 변환은 `convert_history.py`로 자동화하고 복합·함정 보강분만 손으로 쓴다. 벡터 DB·데이터와 서로 검수한다.

## 9. 기본값 (팀 확인 필요)

| 항목                      | 기본값                                                                                                                                                               |
| ------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 반복 에러 집계            | 같은 설비·코드, 24시간 슬라이딩 창, 이번 건 포함 5회째부터 전환. 복합 신고는 쌍마다 한 번씩 센다                                                                     |
| 상태 값                   | 4단계. 저위험 건은 "접수"에 "셀프 가이드" 표시. 세션을 열면 "분석 중"                                                                                                  |
| 고객 "해결됨"             | 엔지니어 확인 없이 곧바로 `완료`                                                                                                                                     |
| 요약·대응 방안 생성 시점  | 엔지니어가 세션을 처음 열 때 생성하고 저장                                                                                                                           |
| 검색 k                    | 매뉴얼 3, 이력 3                                                                                                                                                     |
| 청킹                      | 표 9-4는 행 단위, 산문은 절 단위. 1000자 분할 비교는 선택                                                                                                            |
| 사양서 PDF                | `data/manuals/`에 저장소 포함(가상 문서). 접두 체계의 최신 개정본이다      |
| 제품 카탈로그(E-Cat2025)  | 검색 인덱스에 넣지 않고 용어 근거로만 사용. 공개 저장소에 올리지 않음                                                                                                |
| 가상 이력의 요약·제안     | 비워 둔다                                                                                                                                                            |
| 가상 이력 변환                | 원본 200건 전부 사용. 결과 6종은 `해결`/`미해결`로 줄이고 원문은 `resolution_note`에 둔다. `case_id`는 원본 이력ID, `created_at`은 발생일시. `memo` 합치기와 적재하지 않는 열은 4.3 표를 따른다 |
| 복합·함정 이력 보강       | 복합은 '동반' 행에서 파생(`-H-C<NN>`), 함정은 R206·S115·S121·A308 이력을 추가 작성(`-H-T<NN>`). 작성자는 MySQL 담당이고 벡터 DB·데이터와 서로 검수한다 |
| 이력의 범위               | 엔지니어뿐 아니라 고객사가 직접 조치한 기록도 이력이다(`actor`로 구분) |
| 접수 목록                 | `source=authored` 시드는 엔지니어 Tab의 접수 목록에 나타나지 않는다 |
| 모델별 값                 | `대상 모델`은 `extra.target_models`에 보관하고 답변에 병기한다. 신고에서 모델은 받지 않는다 |
| 고객에게 보이는 서비스 조치 결과 | 확정된 `engineer_action`과 `resolution`                                                                                                                              |

외부 확인 사항(P1 범위·일정 합의, 평가 질문 작성자, S/W 버전·모델 병기 형식, 보강 이력 작성자)은 intent.md의 Open questions를 따른다.
