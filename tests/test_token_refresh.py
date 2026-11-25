from unittest.mock import patch

import pytest
from openreview import OpenReviewException

import openreview_mcp.server as server
from openreview_mcp.server import retry_on_429

EXPIRED = {"name": "TokenExpiredError", "message": "Token has expired. Please log in again.", "status": 401}


def test_expired_token_resets_client_and_retries_once():
    calls = {"n": 0}

    @retry_on_429()
    def f():
        calls["n"] += 1
        if calls["n"] == 1:
            raise OpenReviewException(EXPIRED)
        return "ok"

    server._client_instance = object()
    with patch("openreview_mcp.server.clear_cached_token") as clear:
        assert f() == "ok"
    assert calls["n"] == 2
    assert server._client_instance is None
    clear.assert_called_once()


def test_persistent_401_is_not_retried_forever():
    calls = {"n": 0}

    @retry_on_429()
    def f():
        calls["n"] += 1
        raise OpenReviewException(EXPIRED)

    with patch("openreview_mcp.server.clear_cached_token"):
        with pytest.raises(OpenReviewException):
            f()
    assert calls["n"] == 2  # original call + exactly one re-authenticated retry


def test_other_errors_do_not_reset_the_client():
    @retry_on_429()
    def f():
        raise OpenReviewException({"name": "NotFoundError", "status": 404})

    sentinel = object()
    server._client_instance = sentinel
    with patch("openreview_mcp.server.clear_cached_token") as clear:
        with pytest.raises(OpenReviewException):
            f()
    assert server._client_instance is sentinel
    clear.assert_not_called()
    server._client_instance = None
