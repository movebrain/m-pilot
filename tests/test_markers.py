"""`db`·`pdf` 마커 자동 부착(`tests/conftest.py`의 `pytest_collection_modifyitems`) 테스트.

CI는 이 마커로 job을 나누므로, 마커가 빠지면 DB 테스트가 MySQL 없는 job에서 실패하고
PDF 테스트가 느린 채로 `unit` job에 섞인다.
"""

from __future__ import annotations

import conftest
import pytest


class FakeItem:
    """수집된 테스트 항목 대역. fixture 이름을 갖고 붙은 마커 이름을 모은다."""

    def __init__(self, *fixturenames: str) -> None:
        self.fixturenames = list(fixturenames)
        self.markers: list[str] = []

    def add_marker(self, marker) -> None:
        self.markers.append(marker.name)


@pytest.mark.parametrize(
    ("fixturenames", "expected"),
    [
        (["tmp_path"], []),
        (["rdb_db"], ["db"]),
        (["real_tables"], ["pdf"]),
        (["real_page_texts"], ["pdf"]),
        (["disk_parse"], ["pdf"]),
        (["rdb_db", "disk_parse"], ["db", "pdf"]),
    ],
)
def test_markers_follow_fixtures(fixturenames, expected):
    item = FakeItem(*fixturenames)

    conftest.pytest_collection_modifyitems([item])

    assert item.markers == expected


def test_markers_are_registered(pytestconfig):
    # 등록이 빠지면 `-m`으로 골라도 경고만 나고 넘어가 오타를 알아채지 못한다
    registered = {line.split(":")[0] for line in pytestconfig.getini("markers")}

    assert {"db", "pdf"} <= registered
