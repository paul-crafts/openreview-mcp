import os
import time
import threading
import pytest
from unittest.mock import MagicMock, patch
import requests

from openreview_mcp.rate_limiter import (
    RateLimiter,
    RateLimitingAdapter,
    OpenReviewIPBannedError,
    load_cached_token,
    save_cached_token,
    clear_cached_token,
)


def test_rate_limiter_pacing():
    """Test that RateLimiter enforces minimum delay between requests."""
    limiter = RateLimiter(min_interval=0.1)

    t0 = time.time()
    limiter.wait_for_slot()
    limiter.wait_for_slot()
    limiter.wait_for_slot()
    elapsed = time.time() - t0

    # 3 calls with 0.1s interval should take at least 0.2s
    assert elapsed >= 0.18


def test_rate_limiter_429_cooldown():
    """Test that RateLimiter pauses when a 429 is reported."""
    limiter = RateLimiter(min_interval=0.01)

    assert not limiter.is_paused()
    limiter.report_429(0.2)
    assert limiter.is_paused()

    t0 = time.time()
    limiter.wait_for_slot()
    elapsed = time.time() - t0

    # Should have waited for the 0.2s pause to expire
    assert elapsed >= 0.18
    assert not limiter.is_paused()


def test_rate_limiter_thread_safety():
    """Test that concurrent threads are properly paced."""
    limiter = RateLimiter(min_interval=0.05)
    call_times = []
    lock = threading.Lock()

    def worker():
        for _ in range(3):
            limiter.wait_for_slot()
            with lock:
                call_times.append(time.time())

    threads = [threading.Thread(target=worker) for _ in range(4)]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.time() - t0

    # 12 calls with 0.05s interval should take at least 11 * 0.05 = 0.55s
    assert len(call_times) == 12
    assert elapsed >= 0.50


def test_rate_limiting_adapter_429_retry():
    """Test that RateLimitingAdapter automatically retries on 429."""
    limiter = RateLimiter(min_interval=0.01)
    adapter = RateLimitingAdapter(rate_limiter=limiter, max_retries_429=3)

    mock_resp_429 = MagicMock()
    mock_resp_429.status_code = 429
    mock_resp_429.headers = {"Retry-After": "0.1"}
    mock_resp_429.json.return_value = {"message": "try again in 0 seconds"}

    mock_resp_200 = MagicMock()
    mock_resp_200.status_code = 200

    # First call returns 429, second returns 200
    with patch.object(
        requests.adapters.HTTPAdapter,
        "send",
        side_effect=[mock_resp_429, mock_resp_200],
    ) as mock_send:
        req = requests.Request("GET", "https://api2.openreview.net/notes").prepare()
        resp = adapter.send(req)

        assert resp.status_code == 200
        assert mock_send.call_count == 2


def test_rate_limiting_adapter_ip_ban_detection():
    """Test that RateLimitingAdapter raises OpenReviewIPBannedError on Cloudflare 1015 / 403."""
    limiter = RateLimiter(min_interval=0.01)
    adapter = RateLimitingAdapter(rate_limiter=limiter)

    mock_resp_403 = MagicMock()
    mock_resp_403.status_code = 403
    mock_resp_403.text = "Error 1015: You are being rate limited. IP temporarily banned."

    with patch.object(
        requests.adapters.HTTPAdapter, "send", return_value=mock_resp_403
    ):
        req = requests.Request("GET", "https://api2.openreview.net/notes").prepare()
        with pytest.raises(OpenReviewIPBannedError):
            adapter.send(req)

    # Limiter should now be paused
    assert limiter.is_paused()


def test_token_caching(tmp_path):
    """Test token saving, loading, and cache clearing."""
    with patch(
        "openreview_mcp.rate_limiter.get_token_cache_path",
        return_value=str(tmp_path / "token.json"),
    ):
        assert load_cached_token("user@example.com", "https://api2.openreview.net") is None

        save_cached_token(
            "user@example.com", "https://api2.openreview.net", "test-jwt-token"
        )
        loaded = load_cached_token(
            "user@example.com", "https://api2.openreview.net"
        )
        assert loaded == "test-jwt-token"

        # Check file permissions (user only read/write)
        mode = os.stat(str(tmp_path / "token.json")).st_mode & 0o777
        assert mode == 0o600

        # Different user/baseurl returns None
        assert load_cached_token("other@example.com", "https://api2.openreview.net") is None
        assert load_cached_token("user@example.com", "https://other.openreview.net") is None

        clear_cached_token()
        assert load_cached_token("user@example.com", "https://api2.openreview.net") is None


def test_get_client_token_caching():
    """Test that get_client uses cached token when available."""
    from openreview_mcp.server import get_client
    import openreview_mcp.server

    openreview_mcp.server._client_instance = None

    mock_client = MagicMock()
    mock_client.profile.id = "~TestUser1"
    mock_client.session = MagicMock()

    with patch.dict(
        os.environ,
        {"OPENREVIEW_USERNAME": "test_user", "OPENREVIEW_PASSWORD": "test_password"},
    ):
        with patch(
            "openreview_mcp.server.load_cached_token", return_value="cached-jwt-123"
        ):
            with patch(
                "openreview_mcp.server.OpenReviewClient", return_value=mock_client
            ) as MockClientClass:
                client = get_client()

                assert client == mock_client
                # Verify it initialized with token
                MockClientClass.assert_called_once_with(
                    baseurl="https://api2.openreview.net", token="cached-jwt-123"
                )
                # Verify RateLimitingAdapter was mounted
                assert mock_client.session.mount.call_count == 2
