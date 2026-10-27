# CLAUDE.md: M-Pilot 컨텍스트 맵

현대무벡스 물류 설비 4종(Mini Loader, SRM, AGV, RTV)의 장애 신고를 받아, 고객사 조치 건은 매뉴얼 근거의 셀프 가이드로, 그 밖은 엔지니어 RAG 채팅으로 처리하는 Gradio PoC다. 모든 데이터는 가상이다.

이 파일은 규칙을 복사하지 않고 **어디를 읽을지** 가리킨다. 작업 전에 해당 칸의 원문을 읽는다. 문서끼리 어긋나면 `intent.md` > `spec.md` > 코드 순이며, 임의로 한쪽에 맞추지 말고 사용자에게 알린다.

## 1. 문서 지도

| 알고 싶은 것 | 읽을 곳 |
| --- | --- |
| 왜·무엇을, 범위 원칙(SOT) | [docs/intent.md](docs/intent.md) |
| 파트별 기능 이벤트 | [docs/spec.md](docs/spec.md) §1 |
| 기술 스택, 프로젝트 트리 | spec §2.1, §2.2 |
| 모듈 함수 시그니처(계약) | spec §2.3, §2.4, 각 `mpilot/` 파일의 시그니처 |
| 모듈 의존 방향 | spec §2.5 |
| RAG 규칙, 평가 기준 | spec §2.6, §2.7 |
| 경로 결정, 상태 전이, 허용 조합, 이력 취급 | spec §2.10 |
| DB 테이블, `Answer`, 검색 문서 ID | spec §3.3, §3.4 |
| 하지 않는 것 | spec §4 |

## 2. 코드 지도

모든 `.py` 맨 위 docstring에 **역할·책임·의존·소유 파트**가 있다. 파일을 고치기 전에 그것부터 읽는다. 공통 함수 4개(`load_config`, `models`의 규칙 함수 3개)를 뺀 함수 본문은 아직 `...`이고 시그니처·docstring이 계약이다.

| 파트 | 파일 |
| --- | --- |
| 공통 | `mpilot/config.py`(`AppConfig`, `.env` 로드), `mpilot/models.py`(예외·열거형·업무 객체·순수 업무 규칙) |
| DB | `mpilot/db/`(`parser`, `schemas`, `rdb`, `vectordb`), `scripts/init_db.py`, `docker-compose.yml` |
| RAG | `mpilot/rag/`(`retriever`, `generator`, `pipeline`), `prompts/`, `scripts/evaluate.py` |
| UI·총괄 | `app.py`(공통부), `app_customer.py`, `app_engineer.py`, `mpilot/service.py`, `mpilot/fake_backend.py`(--fake용), `mpilot/ui/`(화면 6개와 `common.py`) |
| 자료 | `data/manuals/`(사양서 PDF 4종), `data/raw/`(에러코드 표 CSV, 장애 이력 더미 4종), `data/chroma/`·`data/photos/`(로컬 전용) |

- 소유 파트가 아닌 파일은 고치지 않는다. 고쳤다면 담당자에게 알린다. 필요한 기능은 spec §2.3의 함수를 호출한다.
- 의존 방향(spec §2.5)을 거스르지 않는다. 예: `ui`가 `db`를 직접 부르지 않고 `service`를 거친다.
- 계약(시그니처, `models.py` 필드)을 바꾸면 같은 PR에서 spec §2.3·§2.4를 고치고 `계약 변경` 라벨을 붙인다. 바꾸기 전에 사용자에게 알리고, 그 함수·모델을 쓰는 파트 담당이 확인하게 한다.

## 3. 작업별 스킬 (AI_RULES.md 대체)

팀 공식 AI 규칙은 아래 스킬([#23](https://github.com/movebrain/m-pilot/pull/23), `.claude/skills/`)이 나눠 맡는다. 이 저장소에서는 전역 커밋·PR 스킬 대신 이 스킬을 쓴다.

| 상황 | 스킬 | 이 스킬이 정하는 것 |
| --- | --- | --- |
| 브랜치 생성, 머지, 릴리스 | `branch-strategy` | `main` ← `develop` ← `<type>/<설명>`, 작업→develop은 squash, develop→main은 merge commit, main·develop 직접 push·force push 금지, 승인 1명 이상 |
| 커밋 | `conventional-commit` | type은 영어·제목과 본문은 한국어, 제목 50자, 본문에 변경 **의도**, Claude `Co-authored-by` 금지 |
| PR 작성·생성 | `create-pr` | `## 변경 사항` 불릿 + `>` 의도, 끝에 `## 머지 커밋 메시지`, Claude 생성 문구 금지 |

스킬이 다루지 않아 이 파일에 남기는 공식 규칙:

- 함수·변수명은 영어 `snake_case`, 주석·docstring은 한글이다. 식별자는 spec의 이름을 그대로 쓴다. 함수마다 받는 것과 돌려주는 것을 docstring에 쓴다(`Args`/`Returns`/`Raises`).
- AI가 만든 코드는 커밋하는 사람이 설명할 수 있을 때만 커밋한다. 커밋 전에 무엇을 왜 바꿨는지 사용자에게 짧게 설명한다.
- 작성한 코드에는 그 코드의 테스트 코드를 함께 작성한다. PR을 올리기 전에 로컬에서 `python -m pytest`를 돌려 통과를 확인한다(GitHub Actions `CI`가 같은 검사를 MySQL 컨테이너와 함께 돌린다).
- `git push --force`, `git reset --hard`, 브랜치 삭제처럼 되돌리기 어려운 명령은 사용자에게 먼저 묻는다.

## 4. 사람이 정한다 (AI는 선택지와 근거만 정리)

- 에러코드 표의 심각도·조치 구분 값, 두 목록 열(`customer_procedure_ref_ids`, `required_safety_rules`): 매뉴얼 원문과 사람이 대조해 쓴다(spec §3.4). AI가 CSV에서 바꾸지 않는다.
- 평가 문항은 데이터를 만든 사람이 쓴다. 인용·고객 안내 사람 검수와 `evidence_threshold` 확정도 사람이 한다(spec §2.7).
- 계약 변경의 최종 판단, 범위(spec §4) 변경, PR 리뷰·머지, 충돌 해결 시 어느 쪽을 살릴지.

## 5. 깨뜨리면 안 되는 도메인 규칙

원문은 spec §2.6, §2.10과 intent의 "지킬 범위와 원칙"이다.

- **경로는 표만으로**: 심각도 `상`이면 조치 구분과 무관하게 `support`. 조치 구분이 `고객사 조치`면 `self_guide` 후보. 그 밖은 `support`. 가이드를 못 만들면(`held`, `generation_failed`) 조치를 추측하지 않고 `support`로 접수한다.
- **고객 안내는 허용 절차만**: 매뉴얼에서 고객이 수행하도록 명시한 절차 ID를 직접 조회해 인용한다. 과거 이력은 고객 안내에 쓰지 않는다.
- **출처 없는 답은 없다**: 모든 단계에 인용이 있고 설비가 일치해야 한다. 근거 부족·적용 대상 아님은 조치 단계 없이 그 사실만 알린다.
- **설비 필터 필수**: 모든 검색에 `equipment` 필터를 건다. `(설비, 코드)`가 키다.
- **이력은 확정분만**: `resolutions`가 있는 건만 `history` 색인에 넣는다. 셀프 가이드는 조회용으로만 두고 근거로 재사용하지 않는다. DB가 정본이고 Chroma는 재생성한다.
- **완료는 끝이다**: 완료된 건은 질문·조치를 더 저장하지 않는다(`InvalidStateError`).
- **표현**: 모든 화면에 "모든 데이터는 가상"을 보인다. "AI가 학습한다"가 아니라 "검색 근거가 늘어난다"고 쓴다. 시간 단축 효과는 실측 없이 주장하지 않는다.

## 6. 비밀 정보

- `.env`, `data/chroma/`, `data/photos/`, `venv/`, `__pycache__/`, 제품 카탈로그(`*E-Cat2025*`)는 커밋하지 않는다(`.gitignore`). 키 이름은 `.env.example`에 값 없이 적는다.
- API 키와 접속 정보를 코드·로그·커밋·PR 본문·AI 대화창에 쓰지 않는다.

## 7. 명령

저장소 루트에서 실행한다.

```
docker compose up -d           # 로컬 MySQL
python -m scripts.init_db      # 시연 전 한 번. 모든 테이블을 지우고 다시 적재
python app_customer.py         # 고객 앱(7860). --fake면 DB·RAG 없이 가짜 데이터
python app_engineer.py         # 엔지니어 앱(7861). --fake 동일
python -m pytest               # 테스트(그냥 pytest는 쓰지 않는다)
python -m scripts.evaluate     # RAG 평가(수동, CI 아님)
```

## 8. 아직 어긋나 있는 곳 (PR #26 리뷰 포인트, 정리되면 지운다)

- 위 spec 절 번호는 이 PR의 개정본 기준이다. 이 PR이 머지되기 전까지 `develop`의 spec은 개정 전 `src/` 구조다.
- `load_config`는 `MYSQL_*` 변수로 `db_url`을 조립한다(`.env.example`·`docker-compose.yml`과 같은 이름). `EVIDENCE_THRESHOLD`는 사람이 정할 때까지 비워 두며, 비어 있으면 앱이 시작되지 않는다.
