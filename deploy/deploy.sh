#!/usr/bin/env bash
# DB는 유지하고 두 앱의 성공 release만 원자적으로 기록한다.
set -Eeuo pipefail
umask 077
root=$(realpath "${1:?usage: deploy.sh ROOT SHA IMAGE_DIGEST}")
sha=${2:?commit SHA required}
image=${3:?image digest required}
[[ $sha =~ ^[a-f0-9]{40}$ && $image =~ ^ghcr\.io/movebrain/m-pilot@sha256:[a-f0-9]{64}$ ]] || exit 2
[[ -s $root/.env && -s $root/probe.curl ]] || { echo '서버 .env/probe.curl 준비가 필요합니다.' >&2; exit 2; }
# .env는 비밀 값을 포함하므로 source하지 않고 공개 주소 한 줄만 읽는다.
public_url=$(sed -n 's/^MPILOT_PUBLIC_URL=//p' "$root/.env" | tail -n 1)
[[ $public_url =~ ^https://[a-zA-Z0-9.-]+(:[0-9]+)?$ ]] || { echo '서버 .env에 MPILOT_PUBLIC_URL=https://<도메인> 설정이 필요합니다.' >&2; exit 2; }
exec 9>"$root/deploy.lock"
flock -w 1800 9
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
release="$root/releases/$sha-${image##*:}"
mkdir -p "$root/releases" "$root/backups"
if [[ ! -d $release ]]; then
    staging=$(mktemp -d "$root/releases/.incoming.XXXXXX")
    cp "$source_dir/compose.demo.yml" "$staging/compose.demo.yml"
    printf '%s\n' "$image" >"$staging/image"
    mv "$staging" "$release"
fi

compose() {
    local directory=$1 selected_image=$2
    shift 2
    MPILOT_IMAGE="$selected_image" \
        docker compose --project-name mpilot-demo --env-file "$root/.env" -f "$directory/compose.demo.yml" "$@"
}

probe() {
    local role status
    for role in customer engineer; do
        status=$(curl -q --silent --show-error --max-time 15 --output /dev/null --write-out '%{http_code}' \
            "$public_url/mpilot/$role/config") || return 1
        [[ $status == 401 ]] || return 1
        curl -q --config "$root/probe.curl" --fail --silent --show-error --max-time 15 --output /dev/null \
            "$public_url/mpilot/$role/config" || return 1
    done
}

head_sha() {
    git ls-remote https://github.com/movebrain/m-pilot.git refs/heads/develop | cut -f1
}

head=$(head_sha)
[[ $head =~ ^[a-f0-9]{40}$ ]] || exit 1
if [[ $head != "$sha" ]]; then
    echo '최신 develop이 아닌 배포는 건너뜁니다.'
    exit 0
fi
compose "$release" "$image" config --quiet
docker pull "$image"
previous=
previous_image=
if [[ -L $root/current ]]; then
    previous=$(readlink -f "$root/current")
elif [[ -s $root/bootstrap/image ]]; then
    # 최초 CD 전환: CD 이전에 수동 배포한 앱을 복구 대상으로 쓴다.
    previous=$root/bootstrap
fi
if [[ -n $previous ]]; then
    previous_image=$(cat "$previous/image")
    if [[ $previous == "$root/bootstrap" ]]; then
        # 로컬 이미지는 pull할 수 없으므로 앱을 바꾸기 전에 호스트에 있는지 확인한다.
        [[ $previous_image =~ ^mpilot-demo-local:[a-f0-9]{7,40}$ ]] || exit 2
        docker image inspect "$previous_image" >/dev/null || exit 2
    else
        [[ $previous_image =~ ^ghcr\.io/movebrain/m-pilot@sha256:[a-f0-9]{64}$ ]] || exit 2
    fi
    compose "$previous" "$previous_image" config --quiet
fi
if [[ $previous == "$release" ]]; then
    probe
    exit 0
fi
backup=$(mktemp "$root/backups/$sha.XXXXXX.sql")
if ! compose "$release" "$image" exec -T mysql sh -c \
    'MYSQL_PWD="$MYSQL_PASSWORD" exec mysqldump -u "$MYSQL_USER" --single-transaction --no-tablespaces "$MYSQL_DATABASE"' >"$backup"; then
    rm -f "$backup"
    echo 'DB 백업 실패: 앱은 교체하지 않았습니다.' >&2
    exit 1
fi
[[ -s $backup ]] || { echo 'DB 백업이 비어 있습니다.' >&2; exit 1; }
head=$(head_sha)
[[ $head =~ ^[a-f0-9]{40}$ ]] || exit 1
if [[ $head != "$sha" ]]; then
    echo '준비 중 develop이 변경되어 배포를 건너뜁니다.'
    exit 0
fi

rollback() {
    trap - ERR INT TERM HUP
    set +e
    if [[ -n $previous ]]; then
        if compose "$previous" "$previous_image" up -d --no-deps --wait --wait-timeout 1200 customer engineer && probe; then
            echo '새 배포 실패: 이전 release로 복구했습니다.' >&2
        else
            echo '새 배포 및 이전 release 복구 실패: 수동 확인이 필요합니다.' >&2
        fi
    else
        compose "$release" "$image" stop customer engineer
        echo '최초 배포 실패: 이전 release가 없습니다.' >&2
    fi
    exit 1
}
trap rollback ERR INT TERM HUP
compose "$release" "$image" up -d --no-deps --wait --wait-timeout 1200 customer engineer
probe
if [[ -n $previous ]]; then
    ln -sfn "$previous" "$root/previous.new"
    mv -Tf "$root/previous.new" "$root/previous"
fi
ln -sfn "$release" "$root/current.new"
mv -Tf "$root/current.new" "$root/current"
trap - ERR INT TERM HUP
echo "배포 성공: $sha"
