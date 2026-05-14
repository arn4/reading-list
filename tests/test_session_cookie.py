"""
Tests for the session cookie Secure flag.

The `--https` CLI flag (or `USE_HTTPS` env var) sets the module-level
`HTTPS_ENABLED` flag in `app.py`. When that flag is on, every session cookie
issued by the auth endpoints carries the `Secure` attribute, so browsers will
only send it over HTTPS. When it's off, the attribute is omitted (development
on plain HTTP works).

We test the cookie helper directly rather than spinning up the full ASGI app
— the helper is the single point that owns this behavior.
"""
import pytest
from fastapi.responses import JSONResponse

import app


@pytest.fixture
def reset_https():
    original = app.HTTPS_ENABLED
    yield
    app.HTTPS_ENABLED = original


def _cookie_header(resp: JSONResponse) -> str:
    header = resp.headers.get("set-cookie")
    assert header is not None, "expected a Set-Cookie header"
    return header


def test_cookie_has_no_secure_flag_when_https_disabled(reset_https):
    app.HTTPS_ENABLED = False
    resp = JSONResponse({})
    app._set_session_cookie(resp, "tok-plain")

    header = _cookie_header(resp)
    assert "rl_session=tok-plain" in header
    assert "HttpOnly" in header
    assert "SameSite=lax" in header
    assert "Secure" not in header, "Secure must NOT be set when HTTPS is off"


def test_cookie_has_secure_flag_when_https_enabled(reset_https):
    app.HTTPS_ENABLED = True
    resp = JSONResponse({})
    app._set_session_cookie(resp, "tok-tls")

    header = _cookie_header(resp)
    assert "rl_session=tok-tls" in header
    assert "HttpOnly" in header
    assert "SameSite=lax" in header
    assert "Secure" in header, "Secure must be set when HTTPS is on"


def test_https_flag_can_be_toggled_at_runtime(reset_https):
    app.HTTPS_ENABLED = False
    resp_off = JSONResponse({})
    app._set_session_cookie(resp_off, "tok")
    assert "Secure" not in _cookie_header(resp_off)

    app.HTTPS_ENABLED = True
    resp_on = JSONResponse({})
    app._set_session_cookie(resp_on, "tok")
    assert "Secure" in _cookie_header(resp_on)
