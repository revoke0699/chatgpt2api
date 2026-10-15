"""Private sentinel solver: one resident chatgpt.com Chromium page.

The process exits unless SENTINEL_SOLVER_KEY is set. Compose must not publish
the listen port. Callers send only the challenge fields `dx` and `p`.

Environment:
- SENTINEL_SOLVER_KEY: required Bearer key
- SENTINEL_SOLVER_PROXY: optional browser proxy, same egress as the app
- SENTINEL_SOLVER_USER_AGENT: optional override
- SENTINEL_SOLVER_TIMEZONE: optional IANA zone, same zone as the egress IP
- SENTINEL_SOLVER_REFRESH_SECONDS: page reopen interval, default 1800
- SENTINEL_SOLVER_HEADLESS: default true
- PORT: listen port inside the private network, default 8080
"""
from __future__ import annotations

import asyncio
import hmac
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Header, HTTPException
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright
from playwright_stealth import Stealth
from pydantic import BaseModel, ConfigDict

CHATGPT_URL = "https://chatgpt.com/"
CHALLENGE_TITLES = ("请稍候", "Just a moment", "Attention Required")
VM_SCRIPT = (Path(__file__).parent / "vm.js").read_text(encoding="utf-8")
BASE_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = window.chrome || { runtime: {} };
"""
PROFILE_JS = """
() => {
  const text = new Date().toString();
  const matched = text.match(/GMT([+-]\\d{4}) \\((.*)\\)$/);
  const scripts = Array.from(document.scripts || []).map((script) => script.src).filter(Boolean);
  const heap = performance.memory && performance.memory.jsHeapSizeLimit;
  return {
    user_agent: navigator.userAgent,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "",
    timezone_offset_min: new Date().getTimezoneOffset(),
    gmt: matched ? matched[1] : "+0000",
    timezone_name: matched ? matched[2] : "UTC",
    language: navigator.language || "",
    languages: (navigator.languages || []).join(","),
    screen_width: Number(screen.width || 0),
    screen_height: Number(screen.height || 0),
    screen_sum: Number(screen.width || 0) + Number(screen.height || 0),
    heap_limit: Number(heap || 0),
    hardware_concurrency: Number(navigator.hardwareConcurrency || 0),
    script_srcs: scripts,
    data_build: document.documentElement.getAttribute("data-build") || "",
  };
}
"""

SOLVER_KEY = os.getenv("SENTINEL_SOLVER_KEY", "").strip()
if not SOLVER_KEY:
    raise SystemExit("SENTINEL_SOLVER_KEY is required")

PROXY = os.getenv("SENTINEL_SOLVER_PROXY", "").strip()
USER_AGENT = os.getenv("SENTINEL_SOLVER_USER_AGENT", "").strip()
TIMEZONE = os.getenv("SENTINEL_SOLVER_TIMEZONE", "").strip()
REFRESH_SECONDS = max(60, int(os.getenv("SENTINEL_SOLVER_REFRESH_SECONDS", "1800") or "1800"))
HEADLESS = os.getenv("SENTINEL_SOLVER_HEADLESS", "true").strip().lower() not in {"0", "false", "no"}
READY_TIMEOUT_SECONDS = 60


class SolverPage:
    """Keep one chatgpt.com page that has passed Cloudflare and reopen it when stale."""

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._opened_at = 0.0
        self._lock = asyncio.Lock()
        self.user_agent = USER_AGENT
        self.last_error = ""

    async def start(self) -> None:
        self._playwright = await async_playwright().start()
        launch_kwargs: dict[str, object] = {
            "headless": HEADLESS,
            "channel": "chromium",
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        }
        if PROXY:
            launch_kwargs["proxy"] = {"server": PROXY}
        self._browser = await self._playwright.chromium.launch(**launch_kwargs)
        if not self.user_agent and self._browser is not None:
            major = str(self._browser.version).split(".")[0]
            self.user_agent = (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
            )
        try:
            await self.page()
        except Exception as exc:
            self.last_error = exc.__class__.__name__
            print(f"[sentinel-solver] initial page open failed: {exc.__class__.__name__}", flush=True)

    async def stop(self) -> None:
        if self._context is not None:
            await self._context.close()
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()

    async def page(self) -> Page:
        async with self._lock:
            stale = self._page is None or time.monotonic() - self._opened_at > REFRESH_SECONDS
            if not stale and not await self._is_ready(self._page):
                stale = True
            if stale:
                await self._reopen()
            if self._page is None:
                raise RuntimeError("sentinel page is not open")
            return self._page

    async def invalidate(self) -> None:
        async with self._lock:
            self._opened_at = 0.0

    async def _reopen(self) -> None:
        if self._context is not None:
            try:
                await self._context.close()
            except Exception:
                pass
        self._page = None
        if self._browser is None:
            raise RuntimeError("sentinel browser is not running")
        context_kwargs: dict[str, object] = {
            "locale": "zh-CN",
            "viewport": {"width": 1280, "height": 800},
            "screen": {"width": 1920, "height": 1080},
            "user_agent": self.user_agent,
        }
        if TIMEZONE:
            context_kwargs["timezone_id"] = TIMEZONE
        context = await self._browser.new_context(**context_kwargs)
        await context.add_init_script(BASE_INIT_SCRIPT)
        await context.add_init_script(VM_SCRIPT)
        await Stealth().apply_stealth_async(context)
        page = await context.new_page()
        await page.goto(CHATGPT_URL, wait_until="domcontentloaded", timeout=60000)
        deadline = time.monotonic() + READY_TIMEOUT_SECONDS
        while not await self._is_ready(page):
            if time.monotonic() > deadline:
                title = await page.title()
                await context.close()
                raise RuntimeError(f"chatgpt.com page not ready (title={title!r})")
            await asyncio.sleep(1)
        self._context = context
        self._page = page
        self._opened_at = time.monotonic()
        self.last_error = ""
        print("[sentinel-solver] chatgpt.com page ready", flush=True)

    @staticmethod
    async def _is_ready(page: Page | None) -> bool:
        if page is None or page.is_closed():
            return False
        try:
            title = await page.title()
            if any(marker in title for marker in CHALLENGE_TITLES):
                return False
            return bool(await page.evaluate(
                "location.hostname === 'chatgpt.com' && typeof window.__sentinelVM === 'object'"
            ))
        except Exception:
            return False

    async def evaluate(self, expression: str, arg: object) -> object:
        for attempt in range(2):
            page = await self.page()
            try:
                return await page.evaluate(expression, arg)
            except Exception as exc:
                self.last_error = exc.__class__.__name__
                if attempt:
                    raise
                await self.invalidate()
        raise RuntimeError("sentinel page evaluate failed")

    async def environment(self) -> dict[str, object]:
        page = self._page
        if page is None or page.is_closed():
            return {}
        try:
            payload = await page.evaluate(PROFILE_JS)
        except Exception as exc:
            self.last_error = exc.__class__.__name__
            return {}
        return payload if isinstance(payload, dict) else {}

    def status(self) -> dict[str, object]:
        ready = self._page is not None and not self._page.is_closed()
        return {
            "ready": ready,
            "page_age_seconds": int(time.monotonic() - self._opened_at) if self._opened_at else None,
            "browser_version": self._browser.version if self._browser else None,
            "user_agent": self.user_agent,
            "timezone": TIMEZONE or None,
            "last_error": self.last_error or None,
        }


solver = SolverPage()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await solver.start()
    yield
    await solver.stop()


app = FastAPI(title="chatgpt2api sentinel solver", lifespan=lifespan)


class TurnstileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dx: str
    p: str


def require_key(authorization: str | None) -> None:
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(supplied, SOLVER_KEY):
        raise HTTPException(status_code=401, detail="invalid solver key")


@app.get("/health")
async def health(authorization: str | None = Header(default=None)) -> dict[str, object]:
    require_key(authorization)
    payload = solver.status()
    if payload.get("ready"):
        payload.update(await solver.environment())
    return payload


@app.post("/v1/turnstile")
async def solve_turnstile(
    body: TurnstileRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    require_key(authorization)
    try:
        token = await solver.evaluate(
            "([dx, p]) => window.__sentinelVM.turnstile(dx, p)",
            [body.dx, body.p],
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="turnstile solve failed") from exc
    return {"token": str(token or "")}


if __name__ == "__main__":
    # Reachable only on the container network. Do not publish this port.
    uvicorn.run(
        app,
        host=os.getenv("SENTINEL_SOLVER_BIND", "0.0.0.0"),
        port=int(os.getenv("PORT", "8080")),
        access_log=False,
    )
