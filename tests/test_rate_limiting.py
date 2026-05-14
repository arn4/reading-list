import pytest
from fastapi import HTTPException

import app


@pytest.fixture
def reset_rate_limits():
    original_auth_limit = app.RATE_LIMIT_AUTH_LOGIN_BEGIN
    original_prepare_limit = app.RATE_LIMIT_LINKS_PREPARE
    app._rate_limit_hits.clear()
    yield
    app.RATE_LIMIT_AUTH_LOGIN_BEGIN = original_auth_limit
    app.RATE_LIMIT_LINKS_PREPARE = original_prepare_limit
    app._rate_limit_hits.clear()


class _DummyClient:
    def __init__(self, host: str):
        self.host = host


class _DummyRequest:
    def __init__(self, host: str):
        self.client = _DummyClient(host)


def test_login_begin_rate_limited(reset_rate_limits):
    app.RATE_LIMIT_AUTH_LOGIN_BEGIN = 2
    req = _DummyRequest("10.0.0.10")

    app._enforce_rate_limit(req, "auth/login/begin", app.RATE_LIMIT_AUTH_LOGIN_BEGIN)
    app._enforce_rate_limit(req, "auth/login/begin", app.RATE_LIMIT_AUTH_LOGIN_BEGIN)
    with pytest.raises(HTTPException) as err:
        app._enforce_rate_limit(req, "auth/login/begin", app.RATE_LIMIT_AUTH_LOGIN_BEGIN)

    assert err.value.status_code == 429


def test_links_prepare_rate_limited(reset_rate_limits):
    app.RATE_LIMIT_LINKS_PREPARE = 2
    req = _DummyRequest("10.0.0.11")

    app._enforce_rate_limit(req, "links/prepare", app.RATE_LIMIT_LINKS_PREPARE)
    app._enforce_rate_limit(req, "links/prepare", app.RATE_LIMIT_LINKS_PREPARE)
    with pytest.raises(HTTPException) as err:
        app._enforce_rate_limit(req, "links/prepare", app.RATE_LIMIT_LINKS_PREPARE)

    assert err.value.status_code == 429
