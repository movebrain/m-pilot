#!/usr/bin/env bash
# 실제 키 없이 배포 이미지의 두 진입점과 하위 경로 설정을 확인한다.
set -euo pipefail
image=${1:?usage: smoke.sh IMAGE}
container=
trap 'if [[ -n "$container" ]]; then docker rm -f "$container" >/dev/null; fi' EXIT
docker run --rm "$image" python -c 'from langchain_chroma import Chroma; from langchain_openai import ChatOpenAI, OpenAIEmbeddings; import pymysql'
for role in customer engineer; do
    port=7860
    [[ $role != engineer ]] || port=7861
    container=$(docker run -d --env "GRADIO_ROOT_PATH=/mpilot/$role" "$image" python "app_$role.py" --fake)
    ready=false
    for ((attempt=0; attempt<60; attempt++)); do
        if docker exec "$container" python -c "import json,urllib.request; d=json.load(urllib.request.urlopen('http://127.0.0.1:$port/config',timeout=3)); assert d['root'].endswith('/mpilot/$role')" >/dev/null 2>&1; then
            ready=true
            break
        fi
        sleep 2
    done
    if [[ $ready != true ]]; then
        docker logs "$container"
        exit 1
    fi
    docker rm -f "$container" >/dev/null
    container=
done
