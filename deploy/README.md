# M-Pilot 시연 배포

`develop`의 unit·MySQL·PDF 테스트가 모두 성공하면 ARM64 앱 이미지를 비공개 GHCR에 발행하고 SSH로 배포 서버에 반영한다. PR·main push에서는 발행/배포하지 않는다. 고객·엔지니어 앱은 같은 image digest를 사용한다.

서버 준비(계정·네트워크·리버스 프록시·인증·레지스트리 로그인)는 이 저장소에서 다루지 않는다. 비밀 값은 코드·Git·로그·대화에 붙이지 않는다.

## 필요한 설정

| 위치 | 항목 |
| --- | --- |
| GitHub Actions repository secrets | `MPILOT_SSH_HOST`, `MPILOT_SSH_USER`, `MPILOT_SSH_KEY`, `MPILOT_SSH_KNOWN_HOSTS` |
| 서버 배포 루트의 `.env` | 저장소의 `.env.example`을 복사해 쓰되 `MYSQL_ROOT_PASSWORD`와 `MPILOT_PUBLIC_URL`(`https://<도메인>` 형식, 따옴표·경로 없음)을 추가하고 `MYSQL_PASSWORD`는 반드시 교체한다 |
| 서버 배포 루트의 `probe.curl` | 배포 후 인증 확인용 curl 설정. 권한 600 |

CI 발행은 `GITHUB_TOKEN`의 job 한정 `packages: write`를 사용한다. 기존 GHCR 패키지가 공개 상태이면 발행 전에 중단한다. SSH는 고정된 known_hosts로 호스트를 검증하며 검증을 끄거나 CI에서 확인 없이 `ssh-keyscan` 결과를 신뢰하지 않는다.

## 일반 배포 동작

- 소스를 원격 `git pull`하지 않는다. CI가 정확한 SHA의 Compose·배포 스크립트를 임시 디렉터리에 전달한다.
- `deploy.sh ROOT SHA IMAGE_DIGEST`는 flock으로 직렬화한다. SHA·digest·`MPILOT_PUBLIC_URL` 형식이 잘못되면 어떤 명령도 실행하기 전에 중단한다.
- 서버 `.env`·probe 파일 확인 → Compose 구문 확인 → 이미지 pull → DB `mysqldump --single-transaction` 백업 → 최신 develop SHA 재확인 → 두 앱만 교체 순서다. pull·백업 실패는 기존 앱을 유지한다.
- release는 `releases/<SHA>-<digest>`이며 `current`, `previous` symlink로 성공 이력을 보관한다. Compose 프로젝트명, `mpilot-demo-mysql-data` 볼륨, `.env` 절대 경로는 release와 무관하게 고정한다.
- 앱 교체는 MySQL을 재생성하지 않는 `up -d --no-deps --wait`로 실행한다. 두 앱의 `/config`가 응답할 때까지 최대 20분 기다린 뒤 공개 주소로 미인증 401과 인증 성공을 확인한다.
- 배포 중 새 develop 커밋이 생기면 교체 직전 확인에서 오래된 배포를 건너뛴다. 실행 중인 배포는 취소하지 않는다.
- 기동 실패 시 이전 Compose·digest로 두 앱 모두 되돌리고 다시 확인한다. 복구 성공과 무관하게 새 배포 job은 실패다. 이전 release가 전혀 없는 최초 배포가 실패하면 앱을 중지하고 수동 확인을 요청한다.
- `current`가 없고 배포 루트에 `bootstrap/{image,compose.demo.yml}`이 있으면 이를 직전 release로 취급한다(CD 이전 수동 배포 앱). 이때 `image`는 `mpilot-demo-local:<커밋 SHA 7~40자리>` 형식이어야 하며, 로컬 이미지는 pull할 수 없으므로 앱을 바꾸기 전에 호스트에 있는지와 Compose 구문을 확인하고 없으면 중단한다. 첫 CD 배포가 성공하면 `previous`가 `bootstrap`을 가리킨다. 로컬 태그를 GHCR digest로 기록하지 않는다.
- DB 내용은 자동 복원하지 않는다. 앱 기동 장애를 이유로 정상 저장 데이터를 과거 백업으로 덮어쓰지 않는다.

배포 중 스크립트를 강제 종료하지 않는다. 종료 신호의 복구는 처리하지만 SIGKILL·서버 전원 장애에는 수동 점검이 필요하다.

## 복구·초기화·변경 관리

수동 앱 복구는 `current`/`previous`가 가리키는 release의 Compose와 `image`를 확인한 뒤 같은 고정 프로젝트·`.env`로 두 앱을 교체한다. 오래된 커밋은 `deploy.sh`의 최신 SHA 검사에서 건너뛰므로 수동 복구에서는 이전 Compose의 `up`을 직접 실행한다.

`scripts/init_db.py`는 모든 테이블을 지우므로 일반 배포·재시작·복구 경로에 포함하지 않는다. 신규 전용 DB의 최초 적재가 아니면 실행하지 않고, 필요하면 앱을 중단하고 백업을 확보한 뒤 삭제 범위를 확인하고 실행한다. `down -v`, 볼륨 삭제, 무차별 Docker prune은 하지 않는다.

DB 스키마를 바꾸는 커밋에는 별도 호환성 검토와 migration/복구 절차가 필요하다. 현재 workflow에는 스키마 변경 자동 판정이 없으므로 해당 커밋은 develop에 넣기 전에 자동 배포를 보류하고 검토한다. 이번 CD는 앱 코드 교체만 담당한다.

백업에는 채팅 등 데이터가 포함되므로 외부에 노출하지 않고, 현재·직전 이미지와 복구에 필요한 백업은 지우지 않는다.

## 검증

```bash
python -m pytest
bash -n deploy/deploy.sh deploy/ci-deploy.sh deploy/smoke.sh
docker build -t mpilot-demo:test .
bash deploy/smoke.sh mpilot-demo:test
```

ARM64 smoke는 실제 키 없이 두 앱의 기동과 `root_path`를 검사한다. 실제 OpenAI 답변·고객 신고→엔지니어 완료 흐름·재배포 데이터 유지·실제 장애 복구는 최초 서버 적용 후 별도로 확인해야 한다.

의존성 갱신은 Python 3.12에서 `uv pip compile requirements.txt --python-version 3.12 --no-annotate --output-file requirements.lock` 후 전체 CI·ARM64 빌드를 통과시킨다. CI와 Dockerfile 모두 같은 lock을 설치한다. RAG 평가 기준이나 검색 임계값은 이 작업에서 임의로 바꾸지 않는다.
