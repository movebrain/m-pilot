# M-Pilot 실행 가이드

팀원이 자기 PC에서 고객 앱·엔지니어 앱을 띄워 직접 테스트하는 절차다. 모든 데이터는 가상이다.
모든 명령은 **저장소 루트**(`m-pilot/`)에서 실행한다. 다른 폴더에서 실행하면 `ModuleNotFoundError`가 난다.

## 0. 어떤 방법으로 띄울까

| 방법 | 필요한 것 | 보이는 것 | 명령 |
| --- | --- | --- | --- |
| **A. 실제 실행** | MySQL, OpenAI 키 | 실제 DB 저장 + RAG(셀프 가이드·엔지니어 답변) | `python app_customer.py` |
| **B. 가짜 DB** | (선택) OpenAI 키 | 실제 service·RAG, DB만 메모리 | `python -m fake.run --both` |
| **C. 화면만** | 없음 | 가짜 데이터로 화면 흐름만 | `python app_customer.py --fake` |

DB 파트 기능(신고 저장·조회, 이력 적재)을 확인하려면 **A**를 쓴다. B의 자세한 내용은 [fake/README.md](../fake/README.md)를 본다.

## 1. 처음 한 번: 환경 준비

### 1-1. Python 가상환경과 패키지

Python 3.12를 쓴다(CI와 같다).

```powershell
# Windows (PowerShell)
py -3.12 -m venv venv
venv\Scripts\activate
python -m pip install -r requirements.txt
```

```bash
# macOS / Linux
python3.12 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
```

이후 새 터미널을 열 때마다 `venv\Scripts\activate`(macOS·Linux는 `source venv/bin/activate`)부터 실행한다.

### 1-2. `.env` 만들기

```powershell
copy .env.example .env      # macOS·Linux: cp .env.example .env
```

`.env`에서 채울 값:

| 항목 | 값 |
| --- | --- |
| `OPENAI_API_KEY` | 각자 받은 OpenAI 키 |
| `MYSQL_*` | 기본값(`mpilot`/`mpilot`) 그대로 두면 된다. 직접 설치한 MySQL에 다른 계정을 만들었다면 그 값 |
| `DEMO_CUSTOMER_ID`·`DEMO_CUSTOMER_PASSWORD` | 고객 앱 로그인 계정(아무 값). 엔지니어 ID와 달라야 한다 |
| `DEMO_ENGINEER_ID`·`DEMO_ENGINEER_PASSWORD` | 엔지니어 앱 로그인 계정(아무 값) |

> `.env`는 커밋하지 않는다. 키를 코드·채팅·PR·스크린샷에 남기지 않는다.

### 1-3. MySQL 띄우기 (방법 A만)

둘 중 하나를 고른다.

**Docker가 있으면**

```powershell
docker compose up -d
```

`.env`의 `MYSQL_*` 값으로 DB와 계정이 자동으로 만들어진다.

**Docker 없이 MySQL 8을 직접 설치했으면**

MySQL 서비스가 켜져 있는지 확인한 뒤(Windows: `Get-Service MySQL80`), root로 접속해 DB와 계정을 한 번 만든다. 값은 `.env`와 맞춘다.

```sql
CREATE DATABASE mpilot CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'mpilot'@'%' IDENTIFIED BY 'mpilot';
GRANT ALL PRIVILEGES ON mpilot.* TO 'mpilot'@'%';
FLUSH PRIVILEGES;
```

## 2. 데이터 적재 (방법 A)

순서대로 실행한다.

```powershell
python -m scripts.build_error_codes     # 원자료 표 + 매뉴얼 표 → data/errorcodes.csv
python -m scripts.build_history_seed    # 원자료 이력 4종 → data/history_seed.csv
python -m scripts.init_db               # 모든 테이블을 지우고 에러코드·시드 이력 200건을 다시 적재
```

**매번 할 필요는 없다.** 한 번 적재했으면 바로 3번으로 가도 된다. 다시 실행하는 경우는 아래뿐이다.

- 처음 실행할 때
- `git pull`로 `data/raw/`·`data/manuals/`나 DB 스키마가 바뀌었을 때
- 테스트하며 쌓인 신고·대화·조치를 지우고 시드 상태로 되돌리고 싶을 때

> ⚠️ `init_db`는 실행할 때마다 **DB를 통째로 비운다.** 시연 도중에는 실행하지 않는다.

검색 색인(Chroma)은 따로 만들지 않는다. 앱이 시작할 때마다 매뉴얼 PDF와 DB로 새로 만든다.

## 3. 앱 실행

터미널을 두 개 열어 각각 실행한다(둘 다 venv 활성화).

```powershell
python app_customer.py      # 고객 앱      → http://127.0.0.1:7860
python app_engineer.py      # 엔지니어 앱  → http://127.0.0.1:7861
```

로그인은 `.env`의 `DEMO_*` 값으로 한다. 종료는 `Ctrl+C`.

### 시작할 때 보이는 것 (정상)

- 첫 화면이 열리기까지 **몇 분** 걸린다. 매뉴얼 PDF 4종을 파싱하고 OpenAI로 임베딩하기 때문이다.
- 그동안 아래 경고가 여러 번 찍힌다. **PDF 글꼴 정보에 대한 pdfminer 경고이며 무시해도 된다.**

  ```
  Could not get FontBBox from font descriptor because None cannot be parsed as 4 floats
  ```

- 준비가 끝나면 `* Running on local URL:  http://127.0.0.1:7860`이 나온다. 그때 브라우저로 연다.
- 앱을 띄울 때마다 OpenAI 임베딩·LLM 호출 비용이 든다.

## 4. 다른 실행 방법

```powershell
# B. 가짜 DB(MySQL 불필요). service·RAG는 진짜 코드
python -m fake.run --both                # 실제 OpenAI 사용(.env의 키 필요)
python -m fake.run --offline --both      # 키 없이. LLM이 없어 셀프 가이드·엔지니어 답변은 안 나온다

# C. 화면만(DB·RAG 모두 가짜)
python app_customer.py --fake
python app_engineer.py --fake
```

B·C는 접수한 건이 메모리에만 있어서 종료하면 사라진다.

## 5. 무엇을 눌러 볼까

### 고객 앱(7860)

신고하기 → 설비 선택 → 에러코드 입력 → (증상 메모) → 신고하기 → 상세 → 목록 순서로 본다.

| 설비 / 코드 | 기대 경로 |
| --- | --- |
| AGV A302, RTV R207 | 셀프 가이드(조치 안내 + 출처) |
| AGV A305, Mini Loader M404 | 셀프 가이드 후보. 안내 생성에 실패하면 엔지니어 접수로 넘어간다 |
| Mini Loader M402 | 심각도 `상` → 엔지니어 접수 |
| SRM S102 | 엔지니어 필요 → 엔지니어 접수 |

- 셀프 가이드 후보가 엔지니어 접수로 넘어가는 것(`held`, `generation_failed`)은 정해진 동작이다. 버그가 아니다.
- 설비·에러코드 없이 신고하면 "입력값을 확인해 주세요." 경고가 나와야 한다.
- 모든 화면 위에 "모든 데이터는 가상입니다" 배너가 보여야 한다.

### 엔지니어 앱(7861)

대시보드에서 기술지원 건 선택 → 장애 분석 대화에서 질문 → 조치 결과 작성 → 조치 완료 순서로 본다.

- 질문에 에러코드를 넣으면 근거가 잘 잡힌다. 예: `M402 주행 서보 과부하가 뜨면 어떻게 조치하나요?`
- 에러코드 없는 질문은 `근거 부족`이 나올 수 있다. 오류가 아니다.
- 조치 완료한 건에는 질문·조치를 더 저장할 수 없어야 한다.

## 6. 문제 해결

| 증상 | 대응 |
| --- | --- |
| `Could not get FontBBox ...`만 계속 나온다 | 정상이다. PDF 파싱 중이다. 몇 분 기다린다 |
| `앱을 시작하지 못했습니다. … (ValidationError)` | `.env` 값이 비었거나 형식이 틀렸다. `OPENAI_API_KEY`, `DEMO_*`를 확인한다 |
| `앱을 시작하지 못했습니다. … (DependencyError)` | MySQL이 꺼져 있거나 접속 정보가 틀렸다. `init_db`를 안 했거나, OpenAI 키·네트워크 문제일 수도 있다 |
| `init_db`가 접속 오류로 실패 | MySQL 서비스가 켜져 있는지, `.env`의 `MYSQL_*`와 1-3의 계정이 맞는지 확인한다 |
| `ModuleNotFoundError: No module named 'mpilot'`(또는 `app`, `fake`) | 저장소 루트에서 실행하지 않았거나 venv가 꺼져 있다 |
| `OSError: Cannot find empty port in range: 7860-7860` | 이전에 띄운 앱이 아직 켜져 있다. 그 터미널에서 `Ctrl+C`로 끄거나, `Get-NetTCPConnection -LocalPort 7860,7861 -State Listen`으로 PID를 찾아 `Stop-Process -Id <PID>`로 종료한 뒤 다시 띄운다. `init_db` 뒤에는 색인을 새로 만들도록 반드시 앱을 다시 띄운다 |
| Chroma·sqlite 관련 오류 | sqlite3가 3.35 미만이다. [fake/README.md](../fake/README.md) 7번을 본다 |

앱은 비밀 정보 보호를 위해 오류 원문을 숨기고 예외 이름만 출력한다. 막히면 터미널 마지막 몇 줄(키·비밀번호는 지우고)을 팀 채널에 공유한다.

## 7. 테스트

```powershell
python -m pytest            # 전체(CI와 같다). 그냥 pytest는 쓰지 않는다
```
