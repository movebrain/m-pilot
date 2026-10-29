#!/usr/bin/env bash
# CI의 비밀 값은 임시 파일에만 보관하고 검증한 SSH 호스트에 전달한다.
set -euo pipefail
umask 077
sha=${1:?commit SHA required}
image=${2:?image digest required}
[[ $sha =~ ^[a-f0-9]{40}$ && $image =~ ^ghcr\.io/movebrain/m-pilot@sha256:[a-f0-9]{64}$ ]] || exit 2
: "${MPILOT_SSH_HOST:?}" "${MPILOT_SSH_USER:?}" "${MPILOT_SSH_KEY:?}" "${MPILOT_SSH_KNOWN_HOSTS:?}"
[[ $MPILOT_SSH_HOST =~ ^[a-zA-Z0-9.-]+$ && $MPILOT_SSH_USER =~ ^[a-z_][a-z0-9_-]*$ ]] || exit 2
temporary=$(mktemp -d)
trap 'rm -rf -- "$temporary"' EXIT
printf '%s\n' "$MPILOT_SSH_KEY" >"$temporary/key"
printf '%s\n' "$MPILOT_SSH_KNOWN_HOSTS" >"$temporary/known_hosts"
unset MPILOT_SSH_KEY MPILOT_SSH_KNOWN_HOSTS
ssh_options=(-i "$temporary/key" -o "UserKnownHostsFile=$temporary/known_hosts" -o StrictHostKeyChecking=yes \
    -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=30 -o ServerAliveCountMax=6)
target="$MPILOT_SSH_USER@$MPILOT_SSH_HOST"
staging=$(ssh "${ssh_options[@]}" "$target" 'umask 077; mkdir -p /opt/mpilot-demo/incoming; mktemp -d /opt/mpilot-demo/incoming/release.XXXXXX')
[[ $staging =~ ^/opt/mpilot-demo/incoming/release\.[a-zA-Z0-9]+$ ]] || exit 2
tar -cf - compose.demo.yml deploy/deploy.sh | ssh "${ssh_options[@]}" "$target" "tar -xf - -C '$staging'"
ssh "${ssh_options[@]}" "$target" "bash '$staging/deploy/deploy.sh' /opt/mpilot-demo '$sha' '$image'"
ssh "${ssh_options[@]}" "$target" "rm -- '$staging/deploy/deploy.sh' '$staging/compose.demo.yml'; rmdir -- '$staging/deploy' '$staging'"
