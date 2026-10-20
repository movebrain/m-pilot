---
name: branch-strategy
description: 이 저장소(mpilot) 전용 브랜치 전략 스킬. main ← develop ← type/설명 브랜치 구조에서 브랜치 생성·이름 규칙, 작업 브랜치→develop은 Squash and merge, develop→main은 Merge commit(릴리스) 규칙, hotfix는 develop 경유 규칙과 머지 명령·머지 커밋 메시지 형식을 다룬다. 새 작업 브랜치를 만들거나 머지 방식·머지 명령을 정하거나 develop을 main으로 릴리스할 때 사용한다. PR 제목·본문 작성과 PR 생성은 이 스킬이 아니라 create-pr 스킬이 맡는다. 불분명한 부분은 가이드 질문으로 확인한다.
---

# 브랜치 전략

> 이 저장소(mpilot) 전용 규칙이다. 다른 저장소의 브랜치 전략과 섞어 쓰지 않는다.

> **정본**: 브랜치 이름과 커밋·PR 제목 규칙은 이 스킬과 `conventional-commit` 스킬이 정본이며, `origin/develop`의 `.github/CONTRIBUTING.md`(`feature/*`·`fix/*`, `타입: 설명`, type 6개)보다 우선한다. `docs/issues.md`의 "브랜치는 기여 가이드대로"도 이 스킬 기준으로 읽는다. CONTRIBUTING.md는 팀이 별도 PR로 이 규칙에 맞춰 고친다 (`feature/*` → `feat/*`, type 10개와 scope·`!` 허용, 제목 50자).

## 구조

```
main      ──●───────────────●──────────  시연·발표 가능한 안정 상태만 둔다
             ▲ merge commit  ▲ merge commit
develop   ──●──●──●──●───●──●──●────────  기능이 통합되는 브랜치
                ▲ squash    ▲ squash
type/설명      ●─●─●        ●─●            작업 브랜치 (develop에서 분기)
```

| 브랜치 | 역할 | 직접 push |
| --- | --- | --- |
| `main` | 시연·발표 가능한 안정 상태 | 금지 |
| `develop` | 기능 통합 | 금지 |
| `<type>/<설명>` | 한 가지 작업 단위 | 허용 |

## 머지 규칙

| 흐름 | 방식 | 비고 |
| --- | --- | --- |
| 작업 브랜치 → `develop` | **Squash and merge** | PR 하나가 develop에 커밋 하나로 남는다 |
| `develop` → `main` | **Merge commit** (Create a merge commit) | squash·rebase 금지 |
| hotfix | `develop` 경유 | 별도 경로 없이 일반 작업 브랜치로 처리한다 |

- **develop → main을 squash나 rebase로 머지하지 않는다.** develop과 main의 이력이 갈라져 다음 릴리스 때 같은 충돌이 반복된다.
- **작업 브랜치 → develop을 merge commit이나 rebase로 머지하지 않는다.** 한 작업이 develop에 커밋 하나로 남는다는 전제가 깨진다.
- 모든 PR은 **팀원 1명 이상의 승인** 후 머지한다.
- `main`, `develop`에는 직접 commit·push 하지 않고 force push도 하지 않는다.

## 브랜치 이름

`<type>/<kebab-case 영어 설명>` 형식이다. `type`은 `conventional-commit` 스킬의 type과 같은 값을 쓴다.

- 허용 type: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`
- 설명은 소문자 영어와 하이픈으로 2~4단어 안팎으로 쓴다.
- 예: `feat/self-guide-routing`, `fix/search-equipment-filter`, `docs/spec-routing-rule`
- 브랜치 type은 그 작업을 대표하는 하나를 고른다. 이 type이 PR 제목의 type과 일치하는 것이 자연스럽다.

## 작업 브랜치 생성과 유지

1. 항상 최신 `develop`에서 분기한다.

   ```bash
   git switch develop
   git pull --ff-only
   git switch -c feat/self-guide-routing
   ```

   작업 트리에 커밋되지 않은 변경이 있어 `git switch develop`이 실패하면 **중단하고** `AskUserQuestion`으로 처리 방법(커밋, stash, 별도 `git worktree`)을 묻는다. 사용자의 변경을 임의로 stash·reset 하지 않는다.

2. 현재 브랜치가 `main`이나 `develop`이면 **커밋하기 전에** 위 방식으로 작업 브랜치를 만든다.
3. 작업 브랜치는 작게 유지하고, 한 PR에는 한 가지 목적만 담는다.
4. develop의 새 변경을 가져올 때는 `git merge origin/develop`을 쓴다. force push가 필요 없고, squash되므로 브랜치 안의 머지 커밋은 develop 이력에 남지 않는다.
5. **머지된 작업 브랜치는 삭제한다.** squash된 브랜치를 재사용하거나 그 위에 새 브랜치를 쌓으면 이미 squash된 변경이 충돌로 다시 나타난다. 이어서 작업하려면 최신 develop에서 새로 분기한다.

## 작업 브랜치 → develop (Squash and merge)

- PR의 base는 `develop`이다. PR 작성은 `create-pr` 스킬을 따른다.
- squash 커밋의 **제목은 PR 제목(Conventional 형식, 50자 이내), 본문은 PR의 `## 머지 커밋 메시지` 코드 블록**이다. PR 본문 전체(마크다운)를 쓰지 않는다. 작업 브랜치 안의 개별 커밋 메시지는 develop 이력에 남지 않으므로, 이 메시지가 곧 최종 커밋 메시지다.
- 본문은 **일반 텍스트**다. `##` 제목, `>` 인용 같은 마크다운은 쓰지 않는다. `git log`에서 기호가 그대로 보이고, `#`로 시작하는 줄이 편집기에서 주석으로 지워질 수 있기 때문이다.

  ```
  <PR 제목>

  - <변경 (개조식)>
    <의도 한두 줄, 마크다운 없이>
  - <변경 (개조식)>
    <의도 한두 줄, 마크다운 없이>

  BREAKING CHANGE: <영향과 대응 방법. 있을 때만>
  Closes #<이슈 번호. 있을 때만>
  ```

- 의도는 PR 본문의 `>`에서 **한 번만** 쓴다. 이 본문은 그 내용을 줄여 옮긴 것이고 새로 쓰지 않는다.
- 머지할 때 제목과 본문을 직접 지정한다. 저장소 설정의 **Squash merging 기본 메시지는 "Default to pull request title"** 로 맞춰, 화면에서 머지하더라도 PR 본문의 마크다운이 자동으로 들어가지 않게 한다 (본문 칸에는 `## 머지 커밋 메시지` 블록을 붙여 넣는다).
- squash 커밋 메시지에도 Claude `Co-authored-by` 트레일러가 붙지 않았는지 확인한다.

```bash
gh pr merge <PR 번호> --squash --delete-branch --subject "<PR 제목>" --body "$(cat <<'EOF'
<`## 머지 커밋 메시지` 블록의 본문>
EOF
)"
```

## develop → main (Merge commit, 릴리스)

`main`은 시연·발표 가능한 상태만 담는다. 팀이 시연 가능하다고 합의한 시점에 올린다.

1. `develop`이 최신이고 시연 가능한 상태인지 확인한다.
2. 릴리스 PR은 **`create-pr` 스킬의 "릴리스 PR" 절차**로 만든다 (base `main`, head `develop`, 제목 `chore(release): <한국어 제목>`). 본문 구성과 작성 규칙도 그 스킬을 따른다.
3. 승인 후 **merge commit**으로 머지한다. 메시지는 아래 "Merge commit 메시지" 형식으로 직접 지정한다 (GitHub 기본 `Merge pull request #N from ...` 문구를 쓰지 않는다).

   ```bash
   gh pr merge <PR 번호> --merge --subject "chore(release): P1 신고·접수·세션 흐름 시연 가능 상태 반영" --body "$(cat <<'EOF'
   <본문>
   EOF
   )"
   ```

4. 릴리스 태그는 쓰지 않는다. 필요해지면 이 스킬에 규칙을 추가한다.
5. 머지 후 `develop` 브랜치는 삭제하지 않는다.

### Merge commit 메시지

릴리스 PR 본문(GitHub 화면용 마크다운)과 **별도로** 아래 형식의 일반 텍스트를 쓴다.

```
chore(release): <한국어 제목>

<릴리스 목적: 왜 지금 main에 올리는지 한두 문장>

- <포함된 squash 커밋 제목>
- <포함된 squash 커밋 제목>

BREAKING CHANGE: <영향과 대응 방법. 있을 때만>
Closes #<이슈 번호. 있을 때만>
```

- **제목**: 릴리스 PR 제목과 같다 (50자 이내).
- **본문 구성**: 릴리스 목적 + 변경 목록만 쓴다. 변경별 의도는 쓰지 않는다. 의도는 main에서도 보이는 develop의 squash 커밋 본문에 이미 있으므로 반복하지 않는다.
- **변경 목록**: `origin/main..origin/develop`의 squash 커밋 제목을 **그대로** (`feat(routing): ...` 형태) `- `로 한 줄씩, 오래된 순으로 옮긴다. 다듬거나 type을 빼지 않는다.

  ```bash
  git log --reverse --format='- %s' origin/main..origin/develop
  ```

- **서식**: 일반 텍스트다. `##` 제목, `>` 인용 같은 마크다운 서식은 쓰지 않는다. `git log`에서 기호가 그대로 보이고, `#`로 시작하는 줄이 편집기에서 주석으로 지워질 수 있기 때문이다.
- **footer**: 포함된 squash 커밋 본문의 `BREAKING CHANGE:`와 `Closes #N`을 **모두 모아** 맨 아래에 쓴다 (같은 `Closes #N`은 한 번만). 없으면 해당 줄을 생략한다. `BREAKING CHANGE:`는 여러 줄일 수 있으므로 첫 줄만이 아니라 다음 빈 줄 전까지 옮긴다.

  ```bash
  git log origin/main..origin/develop --format=%B | awk '/^BREAKING CHANGE:/{p=1} /^Closes #/{print; p=0; next} /^$/{p=0} p'
  ```

- 이 메시지는 릴리스 PR 본문의 `## 머지 커밋 메시지` 코드 블록에도 실어 두어, 누가 머지하든 같은 메시지를 쓰게 한다 (`create-pr` 스킬 참조).

## Hotfix

main에서 따로 분기하지 않는다. 일반 작업 브랜치와 같은 흐름이다.

1. `develop`에서 `fix/<설명>` 브랜치를 만들어 수정한다.
2. `develop`으로 squash 머지한다.
3. 급하면 곧바로 develop → main 릴리스 PR을 올린다.

이력이 develop → main 한 방향으로만 흐르므로 main → develop 역머지는 필요 없다.

## 저장소 설정 체크리스트

팀 관리자가 GitHub 저장소 설정에서 맞춰야 하는 항목이다. 코드로 강제되지 않는다.

- `main`, `develop`: Branch protection (직접 push 금지, force push 금지, PR 필수, 승인 1명 이상)
- Allow squash merging: 켬 / 기본 메시지는 "Default to pull request title" (본문은 머지할 때 `## 머지 커밋 메시지` 블록으로 채운다)
- Allow merge commits: 켬 (develop → main에 필요)
- Allow rebase merging: 끔
- Automatically delete head branches: 켬
- `main`의 **"Require branches to be up to date before merging"은 켜지 않는다.** merge commit 이후 main이 develop에 없는 커밋(머지 커밋)을 갖게 되어 다음 develop → main PR이 "뒤처짐"으로 막힌다.

## 의도 확인 (가이드 질문)

아래 상황에서 사용자의 의도가 분명하지 않으면 추측하지 않고 `AskUserQuestion`으로 묻는다. 이미 분명하면 묻지 않는다.

- **브랜치 type이 불분명할 때**: 변경이 여러 성격에 걸쳐 있을 때. 예: "이 작업을 대표하는 type은 무엇인가요?" (후보 2~3개를 선택지로 제시)
- **릴리스 시점이 불분명할 때**: "develop을 main으로 올리는 이유와 시점은 무엇인가요?" (시연 준비, 최종 발표, 일정 마일스톤 등)
- **규칙과 다른 머지 방식을 요청받았을 때**: 예를 들어 develop → main을 squash로 하자는 요청이면, 이력이 갈라지는 문제를 설명하고 진행 여부를 묻는다.
- **main에 바로 반영하자는 요청일 때**: "hotfix인가요, 일반 릴리스인가요?"를 확인하고 develop 경유로 안내한다.

## 하지 말 것

- `main`이나 `develop`에 직접 commit·push
- develop → main을 squash 또는 rebase로 머지
- 작업 브랜치 → develop을 merge commit 또는 rebase로 머지
- `main`, `develop`에 force push
- 머지된 작업 브랜치 재사용, 머지 전 작업 브랜치 위에 새 브랜치 쌓기
- 릴리스 머지 커밋에 GitHub 기본 제목(`Merge pull request #N ...`) 사용
- squash·merge 커밋 본문에 PR 본문의 마크다운(`##`, `>` 인용)을 그대로 복사 (릴리스 머지 커밋에는 변경별 의도도 넣지 않는다)
- squash·merge 커밋 메시지에 Claude `Co-authored-by` 또는 `🤖 Generated with Claude Code` 포함
