"""Shared pytest fixtures."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("MAXAPI_TOKEN", "test-token")

from api.main import create_app  # noqa: E402

DEMO_ACCOUNT_ID = "acc_DEMO0000000000000000000000"
DEMO_CHANNEL_ID = "-1001111111111"
AUTH_HEADER = {"Authorization": "Bearer test-token"}


@pytest.fixture()
def client() -> Iterator[TestClient]:
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def auth_headers() -> dict[str, str]:
    return dict(AUTH_HEADER)
