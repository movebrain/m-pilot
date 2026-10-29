"""시연 배포의 선행 실패·복구·DB 보존을 외부 서비스 없이 검증한다."""

import os
from pathlib import Path
import subprocess

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "deploy.sh"
SHA = "a" * 40
IMAGE = "ghcr.io/movebrain/m-pilot@sha256:" + "b" * 64
OLD_IMAGE = "ghcr.io/movebrain/m-pilot@sha256:" + "c" * 64
LOCAL_IMAGE = "mpilot-demo-local:cf1cbc2"


@pytest.fixture
def deployment(tmp_path):
    root = tmp_path / "server"
    root.mkdir()
    (root / ".env").write_text("MYSQL_DATABASE=test\nMPILOT_PUBLIC_URL=https://demo.example.test\n")
    (root / "probe.curl").write_text("silent\n")
    commands = tmp_path / "bin"
    commands.mkdir()
    mock = '''#!/usr/bin/env python3
import os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ['TEST_LOG'], 'a') as f:
    f.write(name + ' ' + ' '.join(args) + ' IMAGE=' + os.getenv('MPILOT_IMAGE', '') + '\\n')
if name == 'git':
    if os.getenv('FAIL_HEAD'): sys.exit(1)
    print(os.environ['TEST_HEAD'] + '\\trefs/heads/develop')
elif name == 'docker':
    if 'pull' in args and os.getenv('FAIL_PULL'): sys.exit(1)
    if 'inspect' in args and os.getenv('FAIL_INSPECT'): sys.exit(1)
    if 'exec' in args:
        if os.getenv('FAIL_BACKUP'): sys.exit(1)
        print('-- backup')
    if 'up' in args and os.getenv('MPILOT_IMAGE') == os.environ['NEW_IMAGE'] and os.getenv('FAIL_NEW'):
        sys.exit(1)
elif name == 'curl' and '--write-out' in args:
    print('401', end='')
'''
    for name in ("docker", "git", "curl"):
        path = commands / name
        path.write_text(mock)
        path.chmod(0o755)
    env = {
        **os.environ, "PATH": str(commands) + ":" + os.environ["PATH"],
        "TEST_LOG": str(tmp_path / "log"), "TEST_HEAD": SHA, "NEW_IMAGE": IMAGE,
    }
    return root, env


def previous_release(root):
    """직전 성공 release의 불변 이미지와 Compose를 만든다."""
    previous = root / "releases" / "previous"
    previous.mkdir(parents=True)
    (previous / "image").write_text(OLD_IMAGE + "\n")
    (previous / "compose.demo.yml").write_text("services: {}\n")
    (root / "current").symlink_to(previous)
    return previous


def bootstrap_release(root, image=LOCAL_IMAGE):
    """CD 이전에 수동 배포해 둔 앱의 로컬 이미지와 Compose를 만든다."""
    bootstrap = root / "bootstrap"
    bootstrap.mkdir()
    (bootstrap / "image").write_text(image + "\n")
    (bootstrap / "compose.demo.yml").write_text("services: {}\n")
    return bootstrap


def run_deploy(root, env, image=IMAGE):
    """배포 결과와 비밀 값 없는 명령 기록을 반환한다."""
    result = subprocess.run(["bash", str(SCRIPT), str(root), SHA, image], env=env, capture_output=True, text=True)
    log = Path(env["TEST_LOG"])
    return result, log.read_text() if log.exists() else ""


@pytest.mark.parametrize("failure", ["FAIL_PULL", "FAIL_BACKUP"])
def test_preflight_failure_preserves_previous(deployment, failure):
    root, env = deployment
    previous = previous_release(root)
    result, log = run_deploy(root, {**env, failure: "1"})
    assert result.returncode != 0
    assert (root / "current").resolve() == previous
    assert " up " not in log


def test_failed_app_rolls_back_both_without_restoring_db(deployment):
    root, env = deployment
    previous = previous_release(root)
    result, log = run_deploy(root, {**env, "FAIL_NEW": "1"})
    assert result.returncode != 0
    assert (root / "current").resolve() == previous
    starts = [line for line in log.splitlines() if " up " in line]
    assert len(starts) == 2
    assert IMAGE in starts[0] and OLD_IMAGE in starts[1]
    assert all("customer engineer" in line for line in starts)
    assert "down" not in log and "init_db" not in log
    assert "mysqldump" in log


def test_success_records_digest_and_keeps_previous(deployment):
    root, env = deployment
    previous = previous_release(root)
    result, log = run_deploy(root, env)
    assert result.returncode == 0, result.stderr
    assert (root / "current" / "image").read_text().strip() == IMAGE
    assert (root / "previous").resolve() == previous
    assert "--project-name mpilot-demo" in log
    assert str(root / ".env") in log
    assert "--no-deps" in log and "--wait-timeout 1200" in log
    for role in ("customer", "engineer"):
        assert f"https://demo.example.test/mpilot/{role}/config" in log


@pytest.mark.parametrize("line", ["", "MPILOT_PUBLIC_URL=http://insecure.example.test\n", "MPILOT_PUBLIC_URL='https://quoted.example.test'\n"])
def test_missing_or_invalid_public_url_blocks_before_any_command(deployment, line):
    root, env = deployment
    (root / ".env").write_text("MYSQL_DATABASE=test\n" + line)
    result, log = run_deploy(root, env)
    assert result.returncode == 2
    assert not log


def test_stale_commit_skips_without_touching_apps(deployment):
    root, env = deployment
    result, log = run_deploy(root, {**env, "TEST_HEAD": "d" * 40})
    assert result.returncode == 0
    assert " up " not in log and "mysqldump" not in log


def test_first_deploy_failure_stops_apps_and_reports_no_previous(deployment):
    root, env = deployment
    result, log = run_deploy(root, {**env, "FAIL_NEW": "1"})
    assert result.returncode != 0
    assert not (root / "current").exists()
    assert "stop customer engineer" in log


def test_first_cd_failure_restores_bootstrap_apps_without_stopping(deployment):
    root, env = deployment
    bootstrap = bootstrap_release(root)
    result, log = run_deploy(root, {**env, "FAIL_NEW": "1"})
    assert result.returncode != 0
    assert not (root / "current").exists()
    starts = [line for line in log.splitlines() if " up " in line]
    assert len(starts) == 2
    assert IMAGE in starts[0] and LOCAL_IMAGE in starts[1]
    assert str(bootstrap / "compose.demo.yml") in starts[1]
    assert all("customer engineer" in line for line in starts)
    assert "stop" not in log and "down" not in log and "init_db" not in log


def test_first_cd_success_keeps_bootstrap_as_previous(deployment):
    root, env = deployment
    bootstrap = bootstrap_release(root)
    result, _ = run_deploy(root, env)
    assert result.returncode == 0, result.stderr
    assert (root / "current" / "image").read_text().strip() == IMAGE
    assert (root / "previous").resolve() == bootstrap


def test_bootstrap_with_missing_local_image_blocks_before_touching_apps(deployment):
    root, env = deployment
    bootstrap_release(root)
    result, log = run_deploy(root, {**env, "FAIL_INSPECT": "1"})
    assert result.returncode != 0
    assert " up " not in log and "mysqldump" not in log


@pytest.mark.parametrize("image", ["mpilot-demo-local:latest", "nginx:1", OLD_IMAGE.replace("ghcr", "evil")])
def test_bootstrap_rejects_unrecognized_image(deployment, image):
    root, env = deployment
    bootstrap_release(root, image)
    result, log = run_deploy(root, env)
    assert result.returncode != 0
    assert " up " not in log and "mysqldump" not in log


def test_invalid_image_rejected_before_any_command(deployment):
    root, env = deployment
    result, log = run_deploy(root, env, "ghcr.io/movebrain/m-pilot:latest")
    assert result.returncode != 0
    assert not log


def test_remote_head_failure_is_not_reported_as_stale_success(deployment):
    root, env = deployment
    previous = previous_release(root)
    result, log = run_deploy(root, {**env, "FAIL_HEAD": "1"})
    assert result.returncode != 0
    assert (root / "current").resolve() == previous
    assert " up " not in log
