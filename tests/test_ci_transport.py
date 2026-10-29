"""CI SSH 전송의 호스트 검증과 비밀 값 비노출을 확인한다."""

import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40
IMAGE = "ghcr.io/movebrain/m-pilot@sha256:" + "b" * 64


@pytest.fixture
def transport(tmp_path):
    commands = tmp_path / "bin"
    commands.mkdir()
    mock = '''#!/usr/bin/env python3
import os, pathlib, sys
assert 'MPILOT_SSH_KEY' not in os.environ
assert 'MPILOT_SSH_KNOWN_HOSTS' not in os.environ
with open(os.environ['TRANSPORT_LOG'],'a') as f: f.write(pathlib.Path(sys.argv[0]).name+' '+' '.join(sys.argv[1:])+'\\n')
if pathlib.Path(sys.argv[0]).name=='ssh' and 'mktemp' in sys.argv[-1]: print(os.environ['STAGING'])
'''
    for name in ("ssh", "tar"):
        path = commands / name
        path.write_text(mock)
        path.chmod(0o755)
    log = tmp_path / "log"
    env = {
        **os.environ, "PATH": str(commands) + ":" + os.environ["PATH"],
        "MPILOT_SSH_HOST": "demo.invalid", "MPILOT_SSH_USER": "mpilot",
        "MPILOT_SSH_KEY": "validation-only-private-key", "MPILOT_SSH_KNOWN_HOSTS": "validation-only-host-key",
        "TRANSPORT_LOG": str(log), "STAGING": "/opt/mpilot-demo/incoming/release.abc123",
    }
    return env, log


def run_transport(env, image=IMAGE):
    """외부 SSH 호출 없이 전송 스크립트를 실행한다."""
    return subprocess.run(["bash", "deploy/ci-deploy.sh", SHA, image], cwd=ROOT, env=env, capture_output=True, text=True)


def test_transport_enforces_known_host_and_does_not_log_secret(transport):
    env, log = transport
    result = run_transport(env)
    assert result.returncode == 0, result.stderr
    output = log.read_text() + result.stdout + result.stderr
    for option in ("StrictHostKeyChecking=yes", "UserKnownHostsFile=", "IdentitiesOnly=yes", "BatchMode=yes"):
        assert option in output
    assert "validation-only-private-key" not in output
    assert "validation-only-host-key" not in output
    assert IMAGE in output


def test_transport_rejects_remote_staging_injection(transport):
    env, log = transport
    result = run_transport({**env, "STAGING": "/tmp/staging; touch /tmp/injected"})
    assert result.returncode != 0
    assert len(log.read_text().splitlines()) == 1


def test_transport_rejects_mutable_image_before_connecting(transport):
    env, log = transport
    result = run_transport(env, "ghcr.io/movebrain/m-pilot:latest")
    assert result.returncode != 0
    assert not log.exists()
