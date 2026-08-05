"""Regression tests for the containerised live-harness truthfulness guards."""

from __future__ import annotations

import pytest

from tests.integration import conftest as live_harness


def test_missing_live_dependency_skips_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REQUIRE_LIVE", raising=False)
    skip_reasons: list[str] = []
    monkeypatch.setattr(live_harness.pytest, "skip", skip_reasons.append)

    live_harness._unavailable_live_dependency("Qdrant unavailable")

    assert skip_reasons == ["Qdrant unavailable"]


def test_missing_live_dependency_fails_when_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REQUIRE_LIVE", "1")

    with pytest.raises(pytest.fail.Exception, match="Qdrant unavailable"):
        live_harness._unavailable_live_dependency("Qdrant unavailable")


def test_matching_minio_secret_pair_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIO_TEST_SECRET_KEY", "same-secret")
    monkeypatch.setenv("TEST_MINIO_SECRET_KEY", "same-secret")

    live_harness._validate_minio_secret_pair()


def test_mismatched_minio_secret_pair_fails_without_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provisioning_value = "provisioning-secret-value"
    test_value = "test-secret-value"
    monkeypatch.setenv("MINIO_TEST_SECRET_KEY", provisioning_value)
    monkeypatch.setenv("TEST_MINIO_SECRET_KEY", test_value)

    with pytest.raises(pytest.fail.Exception) as caught:
        live_harness._validate_minio_secret_pair()

    message = str(caught.value)
    assert "MINIO_TEST_SECRET_KEY" in message
    assert "TEST_MINIO_SECRET_KEY" in message
    assert provisioning_value not in message
    assert test_value not in message
