"""Call the private Chromium sentinel solver.

The solver receives only the challenge fields. Account tokens, cookies, and
user prompts stay in this process. A missing solver is an error when Turnstile
is required; the old in-process VM is not a fallback.
"""
from __future__ import annotations

import threading
import time
from urllib.parse import urlsplit

from curl_cffi import requests

from services.config import config
from utils.log import logger
from utils.pow import SentinelEnvironment

SOLVER_TIMEOUT_SECONDS = 30
PROFILE_CACHE_SECONDS = 60
_FAILURE_CACHE_SECONDS = 5

_profile_lock = threading.Lock()
_profile_cache: dict[str, object] = {
    "url": "",
    "expires_at": 0.0,
    "profile": None,
    "error": "",
}


def _solver_base_url() -> str:
    raw = str(config.sentinel_solver_url or "").strip()
    if not raw:
        return ""
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("sentinel solver URL must use http or https")
    return raw.rstrip("/")


def _solver_key() -> str:
    return str(config.sentinel_solver_key or "").strip()


def _solver_headers() -> dict[str, str]:
    key = _solver_key()
    if not key:
        raise RuntimeError("sentinel solver key is not configured")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _environment_from_health(payload: dict[str, object]) -> SentinelEnvironment:
    scripts = payload.get("script_srcs")
    script_sources = tuple(
        str(item).strip()
        for item in scripts
        if str(item or "").strip()
    ) if isinstance(scripts, list) else ()
    language = str(payload.get("language") or "")
    languages = str(payload.get("languages") or language)
    cores = payload.get("hardware_concurrency")
    heap = payload.get("heap_limit")
    return SentinelEnvironment(
        user_agent=str(payload.get("user_agent") or ""),
        gmt=str(payload.get("gmt") or "+0000"),
        timezone_name=str(payload.get("timezone_name") or "UTC"),
        language=language,
        languages=languages,
        screen_sum=int(payload.get("screen_sum") or 0),
        screen_width=int(payload.get("screen_width") or 0),
        screen_height=int(payload.get("screen_height") or 0),
        heap_limit=int(heap) if heap not in (None, "") else 4294705152,
        hardware_concurrency=int(cores) if cores not in (None, "") else 8,
        script_sources=script_sources,
        data_build=str(payload.get("data_build") or ""),
        timezone=str(payload.get("timezone") or ""),
        timezone_offset_min=int(payload.get("timezone_offset_min") or 0),
        from_solver=True,
    )


def _cached_profile(url: str) -> SentinelEnvironment | None:
    now = time.monotonic()
    if _profile_cache.get("url") != url or now >= float(_profile_cache.get("expires_at") or 0):
        return None
    error = str(_profile_cache.get("error") or "")
    if error:
        raise RuntimeError(error)
    profile = _profile_cache.get("profile")
    if isinstance(profile, SentinelEnvironment):
        return profile
    return None


def _store_profile(url: str, profile: SentinelEnvironment | None, error: str, ttl: float) -> None:
    _profile_cache.update(
        url=url,
        profile=profile,
        error=error,
        expires_at=time.monotonic() + ttl,
    )


def fetch_solver_profile(*, force: bool = False) -> SentinelEnvironment | None:
    """Return the solver page environment, or None when no solver is configured."""
    url = _solver_base_url()
    if not url:
        return None
    if not _solver_key():
        raise RuntimeError("sentinel solver key is not configured")
    with _profile_lock:
        if not force:
            cached = _cached_profile(url)
            if cached is not None:
                return cached
        error = "sentinel solver is unavailable"
        try:
            response = requests.get(
                f"{url}/health",
                headers=_solver_headers(),
                timeout=SOLVER_TIMEOUT_SECONDS,
                allow_redirects=False,
            )
        except Exception as exc:
            logger.warning({
                "event": "sentinel_solver_health_error",
                "error_type": exc.__class__.__name__,
            })
            _store_profile(url, None, error, _FAILURE_CACHE_SECONDS)
            raise RuntimeError(error) from exc
        if response.status_code != 200:
            logger.warning({
                "event": "sentinel_solver_health_error",
                "status": int(response.status_code),
            })
            _store_profile(url, None, error, _FAILURE_CACHE_SECONDS)
            raise RuntimeError(error)
        payload = response.json() if response.content else {}
        if not isinstance(payload, dict) or not payload.get("ready"):
            _store_profile(url, None, error, _FAILURE_CACHE_SECONDS)
            raise RuntimeError(error)
        profile = _environment_from_health(payload)
        if not profile.user_agent:
            _store_profile(url, None, error, _FAILURE_CACHE_SECONDS)
            raise RuntimeError(error)
        _store_profile(url, profile, "", PROFILE_CACHE_SECONDS)
        return profile


def solve_turnstile(dx: str, p: str) -> str:
    """Solve Turnstile for one prepare challenge. `p` is the requirements token."""
    challenge_dx = str(dx or "").strip()
    challenge_p = str(p or "").strip()
    if not challenge_dx or not challenge_p:
        raise RuntimeError("sentinel turnstile challenge is incomplete")
    url = _solver_base_url()
    if not url:
        raise RuntimeError("sentinel solver is not configured")
    try:
        response = requests.post(
            f"{url}/v1/turnstile",
            headers=_solver_headers(),
            json={"dx": challenge_dx, "p": challenge_p},
            timeout=SOLVER_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
    except Exception as exc:
        logger.warning({
            "event": "sentinel_solver_error",
            "error_type": exc.__class__.__name__,
        })
        raise RuntimeError("sentinel solver is unavailable") from exc
    if response.status_code != 200:
        logger.warning({
            "event": "sentinel_solver_failed",
            "status": int(response.status_code),
        })
        raise RuntimeError("sentinel solver rejected the turnstile challenge")
    payload = response.json() if response.content else {}
    token = str((payload or {}).get("token") or "") if isinstance(payload, dict) else ""
    if not token:
        raise RuntimeError("sentinel solver returned an empty turnstile token")
    return token
