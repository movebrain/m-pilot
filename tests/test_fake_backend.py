"""`--fake` 실행용 가짜 하위 함수 테스트."""

from __future__ import annotations

from mpilot import fake_backend
from mpilot.db import rdb
from mpilot.models import CaseStatus, Source


def test_fake_fetch_cases_filters_and_sorts():
    reported = fake_backend.fetch_cases(source=Source.REPORTED)
    assert reported and all(d.service_case.source == Source.REPORTED for d in reported)
    created = [d.service_case.created_at for d in reported]
    assert created == sorted(created, reverse=True)

    received = fake_backend.fetch_cases(CaseStatus.RECEIVED, Source.REPORTED)
    assert all(d.service_case.status == CaseStatus.RECEIVED for d in received)


def test_install_swaps_rdb(monkeypatch):
    monkeypatch.setattr(rdb, "fetch_cases", rdb.fetch_cases)  # 테스트가 끝나면 원래대로 되돌린다
    fake_backend.install()
    assert rdb.fetch_cases is fake_backend.fetch_cases
