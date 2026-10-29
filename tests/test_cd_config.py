"""검증하지 않은 커밋·외부 PR이 배포 권한을 얻지 않는지 확인한다."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_publish_requires_all_tests_and_original_develop_push():
    jobs = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]
    publish = jobs["build_publish"]
    assert set(publish["needs"]) == {"unit", "db", "pdf"}
    for guard in ("github.repository == 'movebrain/m-pilot'", "github.event_name == 'push'", "github.ref == 'refs/heads/develop'"):
        assert guard in publish["if"]
    assert publish["permissions"]["packages"] == "write"
    assert jobs["deploy"]["needs"] == "build_publish"
    assert jobs["deploy"]["concurrency"]["cancel-in-progress"] is False
    assert jobs["deploy"]["permissions"] == {"contents": "read"}


def test_db_and_app_networks_and_volume_do_not_escape_release():
    config = yaml.safe_load((ROOT / "compose.demo.yml").read_text())
    services = config["services"]
    assert "ports" not in services["mysql"]
    assert config["volumes"]["mysql-data"]["name"] == "mpilot-demo-mysql-data"
    for role, port in (("customer", 7860), ("engineer", 7861)):
        assert services[role]["ports"] == [f"127.0.0.1:{port + 10000}:{port}"]
        assert services[role]["environment"]["GRADIO_ROOT_PATH"] == f"/mpilot/{role}"
        assert services[role]["depends_on"]["mysql"]["condition"] == "service_healthy"
