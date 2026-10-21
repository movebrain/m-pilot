# CLAUDE.md

이 파일은 Claude Code가 이 저장소에서 작업 전에 자동으로 읽는 규칙이다. 구성은 두 층이다.

1. **공식 규칙**: [AI_RULES.md](AI_RULES.md)(1차 프로젝트 공식 가이드 6장 원문)와 아래 "Git 규칙". 바로 아래 줄에서 불러온다.
2. **M-Pilot 추가 규칙**: 이 파일의 나머지. 공식 규칙을 구체화하거나 덧붙인 것이다. 공식 규칙과 어긋나면 공식 규칙이 우선한다.

다른 AI 도구(ChatGPT, Cursor 등)를 쓰는 팀원은 AI_RULES.md와 이 파일을 둘 다 읽게 한다.

@AI_RULES.md

## Git 규칙 (공식 가이드 원문)

```
# main      : 항상 실행되는 최종본 (직접 push 금지, PR로만 합치기)
# develop   : 기능을 모아 테스트하는 통합 브랜치
# feature/* : 기능별 작업 브랜치, 하나의 기능 = 하나의 브랜치
feature/table-extract
feature/hybrid-search
feature/model-filter
fix/multipage-table-split
```

```
# 형식: 타입: 무엇을 했는지 (한글 OK, 50자 이내)
feat: 사양표를 "항목: 값 단위" 문장으로 변환하는 기능 추가
fix: 여러 쪽에 걸친 표가 잘리는 오류 수정
docs: README 실행 방법 추가
refactor: 하이브리드 검색 함수 분리
test: 수치 정답률 테스트 케이스 20개 추가
```

타입 목록과 작업 흐름은 [.github/CONTRIBUTING.md](.github/CONTRIBUTING.md)에 있다. `.gitignore`는 공식 가이드 예시(`.env`, `venv/`, `__pycache__/`, `chroma_db/`, `.DS_Store`, `Thumbs.db`)에 M-Pilot 로컬 파일(`data/chroma/`, `data/photos/` 등)을 더한 것이다.

---

# M-Pilot 추가 규칙

## 프로젝트 한 줄

M-Pilot: 현대무벡스 자동차 물류 설비 4종(Mini Loader, SRM, AGV, RTV)의 장애 신고를 접수해 저위험 건은 고객 안내를 보내고, 기술지원 건은 에러코드 표·서비스 매뉴얼·이력을 근거로 엔지니어 질문에 답하는 Gradio PoC다. 모든 데이터는 가상이다.

## 기준 문서

| 문서 | 내용 |
| --- | --- |
| [docs/intent.md](docs/intent.md) | **기준 문서(SOT)**. 왜·무엇을, 공용어(Ubiquitous Language), 저위험 판정, 제약 |
| [docs/spec.md](docs/spec.md) | 프로젝트 구조(2장), 아키텍처·상태 전이(3장), 데이터 모델(4장), 모듈 인터페이스(5장), 기능 명세(6장: 사용자 스토리 US, 인수 기준 AC), 평가(7장), 역할 분담(8장) |

- spec.md가 intent.md와 어긋나면 intent.md가 우선한다. 코드가 문서와 어긋나면 문서가 기준이다. 어긋남을 발견하면 AI는 임의로 한쪽에 맞추지 말고 사용자에게 알린다.
- spec.md의 "(기본값)" 항목(9장)은 팀 확인 전이다. 기본값을 바꾸는 코드는 사용자 확인 후에 쓴다.

## A. AI 사용 범위: 어디까지 AI, 어디부터 사람

판단 기준: **틀리면 안전·평가 신뢰도가 무너지는 일**은 사람이 직접 하고, **틀려도 테스트로 바로 드러나는 일**은 AI에게 맡긴다. 담당 파트는 spec.md 8장을 따른다.

### A-1. 사람이 직접

| 일 | 담당 파트 | 이유 |
| --- | --- | --- |
| 에러코드 표의 **심각도(상/중/하)·조치 구분** 값 확인, 대조 리포트에서 어긋난 행 판단 | 벡터 DB·데이터 | 값의 원본은 사양서 표 9-4다. 심각도와 조치 구분이 곧 "고객이 직접 손대도 되는가"를 정한다 |
| 저위험 78개 코드의 **고객 안내(조치 문구)** 검수: 안전 수칙 부합, 다른 절 참조·전문 용어 | MySQL ↔ 벡터 DB·데이터 상호 검수 | 고객에게 그대로 나가는 문구다. 안전 판단이다 |
| **평가 질문 세트**(30~40문항) 작성 | 서비스 이력 (`expected_ref`는 벡터 DB·데이터, MySQL이 확인) | AI가 쓰면 AI가 푼 문제를 AI가 채점하는 꼴이 된다. 데이터를 만들지 않은 사람이 쓴다 |
| **인용 정확도** 샘플 판정(무작위 20개 단계), 사양서 본문·복합 신고 인용 확인 | 서비스 이력 | spec 7장에서 사람이 확인하기로 한 지표다 |
| 근거 부족 **임계값 τ** 결정 | RAG 검색 | AI는 거리 로그 집계까지만 하고, 값은 사람이 정한다 |
| **계약 변경**: 4장 타입, 5장 시그니처, `data/sample/` 가짜 응답 형식 | 소유 파트 | 다른 파트가 이 계약에 기대어 병렬 개발한다. 바꾸면 spec.md를 고치고 의존 파트에 알린다 |
| spec.md **기본값 확정**, 범위(P1~P3) 변경 | 전원 | 팀 합의 사항이다 |
| **PR 리뷰·머지**, 충돌 해결 시 어느 쪽을 살릴지 판단 | 리뷰어 | 기록 책임은 사람에게 있다 |
| 발표 자료의 **수치와 결론** | 제출물 담당(PM·발표) | 평가 결과에서 그대로 옮긴다. AI가 수치를 만들거나 다듬지 않는다 |

AI 행동 규칙: 위 항목을 요청받으면 AI는 **대신 결정하지 않는다.** 선택지와 근거(intent·spec 조항, 데이터)만 정리해 보여주고 사용자에게 결정을 넘긴다. 심각도·조치 구분 값과 저위험 코드의 조치 문구는 AI가 CSV에서 직접 바꾸지 않는다.

### A-2. AI가 초안, 사람이 검증 후 반영

| 일 | 담당 파트 | 사람이 확인할 것 |
| --- | --- | --- |
| 프롬프트: 질문 분석 / 요약·대응 방안·질문 답변 / 이력 구조화 | RAG 검색 / 엔지니어 세션 / 서비스 이력 | 샘플 접수 건 3개 이상으로 출력 확인, 근거 밖 내용이 없는지 |
| 분기·판정 핵심 로직(`route`, `guide_for`, `lookup_error_code`, `retrieve`, `decide_verdict`) | 고객 접수 / RAG 검색 | 인수 기준 AC-11.x, AC-18.x~20.x 테스트 통과, 함정 케이스(R206 / S115 / M406) 결과 |
| 변환·병합 스크립트(`convert_history.py`, `merge_errorcodes.py`) | MySQL / 벡터 DB·데이터 | `init_db.py --check`, 대조 리포트 결과 |
| 복합·함정 이력 보강분의 서술 | MySQL | 어떤 함정을 넣을지는 spec 4.3대로 사람이 정한다. 벡터 DB·데이터와 상호 검수 |
| 테스트 케이스(`tests/test_<모듈>.py`) | 각 파트 | 어떤 경계값·함정을 테스트할지는 사람이 정한다 |
| spec.md·README 수정 초안 | 변경한 사람 | 실제 코드와 일치하는지, intent.md와 어긋나지 않는지 |

### A-3. AI에게 맡겨도 됨

- Gradio 화면 배치와 이벤트 연결 같은 반복 코드
- spec 5장 인터페이스대로의 정형 코드: CSV 로드·검증, DB 저장·조회, 가짜 응답 JSON(`data/sample/<모듈>.json`)
- 동작이 바뀌지 않는 리팩터링, 타입 힌트, docstring
- 오류 메시지 해석과 디버깅 원인 찾기
- 커밋 메시지, PR 본문 초안

맡긴 일이어도 AI_RULES 3번대로 **커밋하는 사람이 설명할 수 있어야** 커밋한다.

## B. 공식 규칙 보충

AI_RULES.md의 1~4번을 이 프로젝트에 맞게 구체화한 것이다.

**1. 처리 권한 보충**
- `main`, `develop`은 GitHub 보호 브랜치라 직접 push가 막혀 있다. 작업 브랜치에서 PR로만 합치고, 머지에는 승인 1개 이상이 필요하다.
- `git push --force`, `git reset --hard`, 브랜치 삭제처럼 되돌리기 어려운 명령은 사용자에게 먼저 묻는다.
- **다른 파트의 소유 파일은 고치지 않는다**(spec 6장, 8.1). 다른 파트의 기능이 필요하면 5장 인터페이스나 가짜 구현을 호출하고, 고쳐야 할 것이 있으면 사용자가 담당 파트에 요청하도록 안내한다.

| 파트 | 소유 파일 |
| --- | --- |
| 벡터 DB·데이터 | `src/errorcodes.py`, `src/parsing.py`, `scripts/merge_errorcodes.py`, `data/errorcodes.csv`, `data/manuals/` |
| 벡터 DB·인덱싱 | `src/indexing.py`, `scripts/build_index.py`, `data/sample/indexing.json` |
| MySQL | `src/db.py`, `scripts/init_db.py`, `scripts/convert_history.py`, `scripts/schema.sql`, `docker-compose.yml`, `data/history_seed.csv`, `data/sample/`(샘플 데이터) |
| 고객 접수 | `app.py`, `src/ui/customer.py`, `src/routing.py` |
| 엔지니어 세션 | `src/ui/engineer.py`, `src/answer.py`, `data/sample/answer.json` |
| RAG 검색 | `src/search.py`, `data/synonyms.csv`, `data/sample/search.json` |
| 서비스 이력 | `src/registry.py`, `src/history.py`, `src/ui/resolution.py`, `src/evaluate.py`, `tests/test_integration.py`, `data/eval_questions.csv`, `data/sample/history.json`, `.env.example`, `.gitignore`, `requirements.txt` |

- `resources/`의 원본 자료와 `docs/`의 기준 문서는 요청이 있을 때만 수정한다.

**2. 코드 규칙 보충**
- 이름: 변수·함수 `lower_snake_case`, 클래스 `CapWords`. 영어 식별자는 intent.md 공용어 표와 spec 4·5장의 이름을 그대로 쓴다. 표에 없는 새 용어는 intent.md 표에 먼저 추가하고 쓴다.
- 공용어: 주석·화면 문구·답변에서 intent.md의 "쓰지 않는 말"을 쓰지 않는다(예: 알람·오류 → **에러**, 등급 → **심각도**, 가이드 문구 → **고객 안내**, 환류·학습 → **서비스 이력 추가**).
- 입출력 주석은 docstring으로 쓴다.

```python
def lookup_error_code(equipment: str, code: str) -> ErrorCode | None:
    """설비와 에러코드로 에러코드 표의 한 행을 정확히 찾는다.

    입력: equipment - 설비명(Mini Loader / SRM / AGV / RTV), code - 에러코드(예: R201)
    출력: 일치하는 ErrorCode 1개, 없으면 None (비슷한 코드는 돌려주지 않는다)
    """
```

- 타입은 소유 모듈 파일에 두고 다른 파트는 import만 한다(spec 4.4).
- DB 접속과 SQL은 `src/db.py`에만 둔다. ORM은 쓰지 않는다.
- 실제 모듈은 import할 때 DB·Chroma에 접속하지 않는다. `indexing`, `search`, `answer`, `history`는 `registry.get("...")`으로 받아 쓰고(`MPILOT_FAKES`로 가짜 전환), `db`는 `from src import db`로 직접 가져온다. 데이터 경로는 `Path(__file__)` 기준이다.
- 새 라이브러리는 실제 문제가 드러났고 이유를 설명할 수 있을 때만 추가하고, PR에 이유를 적는다(`requirements.txt`는 서비스 이력 소유).
- 화면 문구와 LLM 답변은 한국어만 지원한다.

**3. 반영 기준 보충**
- AI는 커밋 전에 무엇을 왜 바꿨는지 사용자에게 짧게 설명한다.
- 해당 스토리의 인수 기준(spec 6.2) 테스트를 `python -m pytest`로 돌리고, 확인하지 못한 항목은 못 했다고 밝힌다.
- 커밋 메시지·PR에 사용자 스토리·인수 기준 ID를 붙인다(예: `feat: 에러코드 정확 조회 추가 (AC-18.2)`).
- PR 본문 양식. "AI 사용"에는 A장의 어느 구분인지도 적는다.

```
## 변경 내용
## 관련 이슈 / 스토리·인수 기준 ID
## AI 사용
- 사용: 예 / 아니오
- 사용한 부분과 구분(A-2 초안·검증 / A-3 맡김): 예) route() 초안 — A-2
- 사람이 확인한 것: 예) AC-11.1, AC-11.2 테스트 통과
## 확인 방법
```

**4. 문서 규칙 보충**
- 이 프로젝트의 기능 명세서는 spec.md 5장(모듈 인터페이스)과 6장(기능 명세)이다. 함수 입력·출력이 바뀌면 5장을, 타입이 바뀌면 4장을, 동작이 바뀌면 6장을 같은 PR에서 고친다.
- 상태 전이나 분기 규칙이 바뀌면 spec 3장과 intent.md(저위험 판정)를 함께 확인한다.
- 팀 규칙이 바뀌면 AI_RULES.md·CLAUDE.md도 PR로 고친다.

## C. 깨뜨리면 안 되는 도메인 규칙

AI가 코드를 빨리 만들면서 놓치기 쉬운 규칙이다. 위반하는 코드는 만들지 않는다.

- **원본에서 생성**: 검색 문서는 `errorcodes.csv`, `data/manuals/`, 이력 시드에서만 만든다. 원본을 고치고 `build_index`를 다시 돌린다. 생성된 문서나 Chroma 데이터를 직접 고치지 않는다.
- **(설비, 코드)가 키**: 에러코드는 설비별 접두 체계(`R201`, `S101`, `A301`, `M401`)다. 모든 검색에 `equipment` 필터를 걸고, `history` 결과는 `case_id`로 중복을 제거한다. 정확 조회는 정확 일치만 하고 비슷한 코드나 다른 설비의 같은 이름 에러를 대신 돌려주지 않는다.
- **안전 분기**: 쌍마다 심각도 `상`이면 조치 구분과 무관하게 `support`. 조치 구분이 `고객사 조치`이고 심각도가 `중`·`하`이며 `count_24h`(이번 건 포함)가 5 미만일 때만 `guide`. 그 밖에는 `support`. 복합 신고는 **모든 쌍이 `guide`일 때만** 건 전체가 `guide`이고, 하나라도 아니면 저위험 쌍의 안내도 내보내지 않는다. `route()`는 에러코드 표만 읽고(DB 비의존) 증상 메모·사진을 쓰지 않는다. P1의 `route()`는 항상 `support`다.
- **판정은 코드가**: `decide_verdict`가 적용 대상 아님 → 근거 부족 순으로 판정하고 LLM은 바꾸지 못한다. 모든 `Step`에 `Citation`이 1개 이상이다. 근거가 없으면 추측하지 않는다.
- **확정 이력만 검색**: `confirmed=true`인 건만 `history`에 넣는다. `미해결` 이력은 빼지 않고 "실패 이력"으로 표시한다. 시드 이력(`source=authored`)은 접수 목록에 나타나지 않는다.
- **재현성·비용**: `temperature=0`, 모델명은 환경 변수. `brief`는 접수 건당 한 번 생성해 저장하고 재호출하지 않는다. 신고 접수와 분기는 LLM 없이 동작한다.
- **고객 Tab**: 고객 노출 필드(접수번호, 쌍, 상태, 고객 안내, 안전 안내, 확정된 서비스 조치 결과)만 렌더링한다. AI 요약·대응 방안·이력 원문은 보여주지 않는다. 기술지원 건에는 안전 안내를 보인다.
- **표현**: 모든 화면 상단에 "모든 데이터는 가상". "AI가 학습한다"가 아니라 "검색 근거가 늘어난다".

## D. 비밀 정보

- `.env`(API 키, MySQL 접속 정보), `data/chroma/`, `data/photos/`, `venv/`, `__pycache__/`는 커밋하지 않는다(`.gitignore` 등록). 키 이름은 `.env.example`에 값 없이 적는다.
- 제품 카탈로그(E-Cat2025) 원본은 저장소에 올리지 않는다.
- API 키를 코드·로그·커밋·PR 본문에 쓰지 않는다. AI 대화창에도 키를 붙여넣지 않는다.

## 명령

저장소 루트에서 실행한다(spec 2장). 코드가 올라오면 실제와 맞는지 확인해 고친다.

```
docker compose up                  # MySQL 컨테이너
python -m scripts.init_db          # 스키마·가상 이력 시드 적재 (--reset 초기화, --check 검증)
python -m scripts.build_index      # Chroma 인덱스 재생성
python app.py                      # Gradio 앱
python -m pytest                   # 테스트 (그냥 pytest는 src import 실패)
python -m src.evaluate             # 정답률 평가
```
