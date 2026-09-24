import json
import logging
import os
import re
import threading
import time
from typing import Optional
import requests
from requests.adapters import HTTPAdapter

logger = logging.getLogger("openreview_mcp.rate_limiter")


class OpenReviewIPBannedError(Exception):
    """Raised when an IP-level block or Cloudflare 1015 error is detected."""

    pass


class RateLimiter:
    """
    Thread-safe client-side rate limiter for OpenReview API requests.
    Enforces minimum delay between requests and coordinates global backoff on 429.
    """

    def __init__(self, min_interval: Optional[float] = None):
        if min_interval is None:
            env_val = os.environ.get("OPENREVIEW_MIN_REQUEST_INTERVAL", "0.5")
            try:
                min_interval = float(env_val)
            except ValueError:
                min_interval = 0.5

        self.min_interval = max(0.1, min_interval)
        self._lock = threading.Lock()
        self._last_request_time = 0.0
        self._pause_until = 0.0

    def wait_for_slot(self) -> None:
        """Wait until it is safe to issue an HTTP request."""
        while True:
            sleep_needed = 0.0
            with self._lock:
                now = time.time()

                # 1. Global pause due to active 429 backoff
                if now < self._pause_until:
                    sleep_needed = self._pause_until - now
                else:
                    # 2. Inter-request pacing
                    time_since_last = now - self._last_request_time
                    if time_since_last < self.min_interval:
                        sleep_needed = self.min_interval - time_since_last
                    else:
                        self._last_request_time = now
                        return

            if sleep_needed > 0:
                time.sleep(sleep_needed)

    def report_429(self, wait_seconds: float) -> None:
        """Pause all requests globally until wait_seconds has elapsed."""
        with self._lock:
            target_time = time.time() + max(1.0, wait_seconds)
            if target_time > self._pause_until:
                self._pause_until = target_time
                logger.warning(
                    f"OpenReview 429 encountered. All requests paused for {wait_seconds:.1f}s."
                )

    def is_paused(self) -> bool:
        """Check if rate limiter is currently in a 429 pause window."""
        with self._lock:
            return time.time() < self._pause_until


# Global singleton instance
GLOBAL_RATE_LIMITER = RateLimiter()


class RateLimitingAdapter(HTTPAdapter):
    """
    Requests HTTPAdapter that enforces client-side rate limiting,
    automatic per-request retries on 429 with backoff, and IP-ban detection.
    """

    def __init__(
        self,
        rate_limiter: Optional[RateLimiter] = None,
        max_retries_429: int = 5,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.rate_limiter = rate_limiter or GLOBAL_RATE_LIMITER
        self.max_retries_429 = max_retries_429

    def send(
        self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None
    ):
        for attempt in range(self.max_retries_429 + 1):
            # Throttle before sending
            self.rate_limiter.wait_for_slot()

            try:
                response = super().send(
                    request,
                    stream=stream,
                    timeout=timeout,
                    verify=verify,
                    cert=cert,
                    proxies=proxies,
                )
            except requests.exceptions.RequestException:
                raise

            # Detect Cloudflare 1015 / WAF IP bans
            if response.status_code == 403:
                body_text = response.text or ""
                if (
                    "1015" in body_text
                    or "rate limit" in body_text.lower()
                    or "access denied" in body_text.lower()
                ):
                    self.rate_limiter.report_429(300.0)  # Pause for 5 minutes
                    raise OpenReviewIPBannedError(
                        f"OpenReview IP ban detected (HTTP 403 / Cloudflare 1015). "
                        f"All requests paused. Message: {body_text[:200]}"
                    )
                return response

            # Handle 429 Too Many Requests
            if response.status_code == 429:
                if attempt >= self.max_retries_429:
                    logger.error("Max 429 retries exhausted.")
                    return response

                wait_time = self._parse_retry_delay(response, attempt)
                self.rate_limiter.report_429(wait_time)
                logger.info(
                    f"Rate limited (429). Retrying in {wait_time:.1f}s (attempt {attempt + 1}/{self.max_retries_429})..."
                )
                time.sleep(wait_time)
                continue

            return response

        return response

    def _parse_retry_delay(
        self, response: requests.Response, attempt: int
    ) -> float:
        """Parse retry delay from headers, body, or fallback to exponential backoff."""
        # Check standard Retry-After header
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return float(retry_after) + 0.5
            except ValueError:
                pass

        # Check OpenReview JSON body message: "try again in X seconds"
        try:
            data = response.json()
            msg = data.get("message", "") if isinstance(data, dict) else str(data)
            match = re.search(r"try again in (\d+) seconds", msg)
            if match:
                return float(match.group(1)) + 1.0
        except Exception:
            pass

        # Fallback exponential backoff: 5s, 10s, 20s, 40s...
        return float(5 * (2**attempt))


# --- Token Caching Utilities ---

def get_token_cache_path() -> str:
    """Return secure path to token cache file."""
    cache_dir = os.path.expanduser(
        os.environ.get("OPENREVIEW_TOKEN_CACHE_DIR", "~/.cache/openreview_mcp")
    )
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, "token.json")


def load_cached_token(
    username: Optional[str] = None,
    baseurl: str = "https://api2.openreview.net",
) -> Optional[str]:
    """Retrieve cached JWT token if valid for user and baseurl."""
    path = get_token_cache_path()
    if not os.path.exists(path):
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if (
            isinstance(data, dict)
            and (username is None or data.get("username") == username)
            and data.get("baseurl") == baseurl
            and data.get("token")
        ):
            return str(data["token"])
    except Exception as e:
        logger.debug(f"Failed to read token cache: {e}")

    return None


def save_cached_token(username: str, baseurl: str, token: str) -> None:
    """Save JWT token to cache file with user-only permissions."""
    path = get_token_cache_path()
    try:
        data = {
            "username": username,
            "baseurl": baseurl,
            "token": token,
            "saved_at": time.time(),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.chmod(path, 0o600)
    except Exception as e:
        logger.debug(f"Failed to write token cache: {e}")


def clear_cached_token() -> None:
    """Remove cached token file."""
    path = get_token_cache_path()
    if os.path.exists(path):
        try:
            os.remove(path)
        except Exception as e:
            logger.debug(f"Failed to clear token cache: {e}")
