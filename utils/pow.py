import json
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any, Sequence
from zoneinfo import ZoneInfo

import pybase64

from utils.helper import new_uuid

DEFAULT_POW_SCRIPT = "https://chatgpt.com/backend-api/sentinel/sdk.js"
FNV_OFFSET = 2166136261
FNV_PRIME = 16777619
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

CORES = [8, 16, 24, 32]
DOCUMENT_KEYS = ["__reactContainer$fzelfjyxej8", "_reactListening5dehydibo78", "location"]
SCREEN_RESOLUTIONS = [[1920, 1080], [1440, 900], [2560, 1440], [3840, 2160]]
NAVIGATOR_KEYS = [
    "registerProtocolHandler−function registerProtocolHandler() { [native code] }",
    "storage−[object StorageManager]",
    "locks−[object LockManager]",
    "appCodeName−Mozilla",
    "permissions−[object Permissions]",
    "share−function share() { [native code] }",
    "webdriver−false",
    "managed−[object NavigatorManagedData]",
    "canShare−function canShare() { [native code] }",
    "vendor−Google Inc.",
    "mediaDevices−[object MediaDevices]",
    "vibrate−function vibrate() { [native code] }",
    "storageBuckets−[object StorageBucketManager]",
    "mediaCapabilities−[object MediaCapabilities]",
    "cookieEnabled−true",
    "virtualKeyboard−[object VirtualKeyboard]",
    "product−Gecko",
    "presentation−[object Presentation]",
    "onLine−true",
    "mimeTypes−[object MimeTypeArray]",
    "credentials−[object CredentialsContainer]",
    "serviceWorker−[object ServiceWorkerContainer]",
    "keyboard−[object Keyboard]",
    "gpu−[object GPU]",
    "doNotTrack",
    "serial−[object Serial]",
    "pdfViewerEnabled−true",
    "language−zh-CN",
    "geolocation−[object Geolocation]",
    "userAgentData−[object NavigatorUAData]",
    "getUserMedia−function getUserMedia() { [native code] }",
    "sendBeacon−function sendBeacon() { [native code] }",
    "hardwareConcurrency−32",
    "windowControlsOverlay−[object WindowControlsOverlay]",
]
WINDOW_KEYS = [
    "0",
    "window",
    "self",
    "document",
    "name",
    "location",
    "customElements",
    "history",
    "navigation",
    "innerWidth",
    "innerHeight",
    "scrollX",
    "scrollY",
    "visualViewport",
    "screenX",
    "screenY",
    "outerWidth",
    "outerHeight",
    "devicePixelRatio",
    "screen",
    "chrome",
    "navigator",
    "onresize",
    "performance",
    "crypto",
    "indexedDB",
    "sessionStorage",
    "localStorage",
    "scheduler",
    "alert",
    "atob",
    "btoa",
    "fetch",
    "matchMedia",
    "postMessage",
    "queueMicrotask",
    "requestAnimationFrame",
    "setInterval",
    "setTimeout",
    "caches",
    "__NEXT_DATA__",
    "__BUILD_MANIFEST",
    "__NEXT_PRELOADREADY",
]


@dataclass(frozen=True)
class SentinelEnvironment:
    """Page values that requirements `p` and the proof must share with the solver."""

    user_agent: str = ""
    gmt: str = "+0000"
    timezone_name: str = "UTC"
    language: str = "en-US"
    languages: str = "en-US"
    screen_sum: int = 0
    screen_width: int = 0
    screen_height: int = 0
    heap_limit: int = 4294705152
    hardware_concurrency: int = 8
    script_sources: tuple[str, ...] = ()
    data_build: str = ""
    timezone: str = ""
    timezone_offset_min: int = 0
    from_solver: bool = False


class ScriptSrcParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.script_sources: list[str] = []
        self.data_build = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "script":
            return
        attrs_dict = dict(attrs)
        src = attrs_dict.get("src")
        if not src:
            return
        self.script_sources.append(src)
        match = re.search(r"c/[^/]*/_", src)
        if match:
            self.data_build = match.group(0)


def parse_pow_resources(html_content: str) -> tuple[list[str], str]:
    parser = ScriptSrcParser()
    parser.feed(html_content)
    script_sources = parser.script_sources or [DEFAULT_POW_SCRIPT]
    data_build = parser.data_build
    if not data_build:
        match = re.search(r'<html[^>]*data-build="([^"]*)"', html_content)
        if match:
            data_build = match.group(1)
    return script_sources, data_build


def _imul32(left: int, right: int) -> int:
    """Low 32 bits of a multiply, matching `Math.imul(...) >>> 0`."""
    return (int(left) * int(right)) & 0xFFFFFFFF


def fnv1a32_hex(text: str) -> str:
    """FNV-1a 32-bit plus the page avalanche. Input is a JS string, not raw bytes."""
    hash_value = FNV_OFFSET
    for char in text:
        hash_value ^= ord(char)
        hash_value = _imul32(hash_value, FNV_PRIME)
    hash_value ^= hash_value >> 16
    hash_value = _imul32(hash_value, 2246822507)
    hash_value ^= hash_value >> 13
    hash_value = _imul32(hash_value, 3266489909)
    hash_value ^= hash_value >> 16
    return f"{hash_value & 0xFFFFFFFF:08x}"


def format_js_date(moment: datetime, gmt: str, timezone_name: str) -> str:
    """English `Date.toString()`, with a space-padded day."""
    weekday = _WEEKDAYS[moment.weekday()]
    month = _MONTHS[moment.month - 1]
    offset = gmt if str(gmt).startswith(("+", "-")) else f"+{gmt}"
    return (
        f"{weekday} {month} {moment.day:2d} {moment.year:04d} "
        f"{moment:%H:%M:%S} GMT{offset} ({timezone_name})"
    )


def _gmt_timedelta(gmt: str) -> timedelta:
    match = re.fullmatch(r"([+-])(\d{2})(\d{2})", str(gmt or "+0000"))
    if match is None:
        return timedelta(0)
    sign = 1 if match.group(1) == "+" else -1
    minutes = int(match.group(2)) * 60 + int(match.group(3))
    return timedelta(minutes=sign * minutes)


def _moment_for_environment(environment: SentinelEnvironment) -> datetime:
    if environment.timezone:
        try:
            return datetime.now(ZoneInfo(environment.timezone))
        except Exception:
            pass
    return datetime.now(timezone(timedelta(0)) + _gmt_timedelta(environment.gmt))


def local_sentinel_environment(user_agent: str) -> SentinelEnvironment:
    """Fallback fingerprint when the solver page is not configured."""
    moment = datetime.now().astimezone()
    offset = moment.utcoffset() or timedelta(0)
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    absolute_minutes = abs(total_minutes)
    gmt = f"{sign}{absolute_minutes // 60:02d}{absolute_minutes % 60:02d}"
    zone = getattr(moment.tzinfo, "key", "") if moment.tzinfo is not None else ""
    return SentinelEnvironment(
        user_agent=user_agent,
        gmt=gmt,
        timezone_name=moment.tzname() or "UTC",
        language="en-US",
        languages="en-US",
        screen_sum=sum(SCREEN_RESOLUTIONS[0]),
        screen_width=SCREEN_RESOLUTIONS[0][0],
        screen_height=SCREEN_RESOLUTIONS[0][1],
        heap_limit=4294705152,
        hardware_concurrency=8,
        timezone=str(zone or ""),
        timezone_offset_min=-total_minutes,
        from_solver=False,
    )


def _encode_config(config: Sequence[Any]) -> str:
    payload = json.dumps(list(config), separators=(",", ":"), ensure_ascii=False).encode()
    return pybase64.b64encode(payload).decode()


def build_pow_config(
    user_agent: str,
    script_sources: Sequence[str] | None = None,
    data_build: str = "",
    environment: SentinelEnvironment | None = None,
) -> list[Any]:
    """25-field config. Requirements keep index 3 at 1 and index 9 as a small float."""
    environment = environment or local_sentinel_environment(user_agent)
    sources = list(environment.script_sources) or list(script_sources or [])
    script_source = random.choice(sources) if sources else DEFAULT_POW_SCRIPT
    build_id = environment.data_build or data_build
    agent = environment.user_agent or user_agent
    if environment.from_solver:
        screen_sum = int(environment.screen_sum)
        cores = int(environment.hardware_concurrency)
    else:
        screen_sum = int(environment.screen_sum) or sum(random.choice(SCREEN_RESOLUTIONS))
        cores = int(environment.hardware_concurrency) or random.choice(CORES)
    moment = _moment_for_environment(environment)
    gmt = moment.strftime("%z") or environment.gmt
    if len(gmt) == 5:
        gmt = f"{gmt[:3]}{gmt[3:]}"
    return [
        int(screen_sum),
        format_js_date(moment, gmt, environment.timezone_name or "UTC"),
        int(environment.heap_limit),
        1,
        agent,
        script_source,
        build_id,
        environment.language or "en-US",
        environment.languages or environment.language or "en-US",
        round((time.perf_counter() % 3) + 0.1, 10),
        random.choice(NAVIGATOR_KEYS),
        random.choice(DOCUMENT_KEYS),
        random.choice(WINDOW_KEYS),
        time.perf_counter() * 1000,
        new_uuid(),
        "",
        int(cores),
        time.time() * 1000 - (time.perf_counter() * 1000),
        0,
        0,
        0,
        0,
        0,
        0,
        0,
    ]


def _normalize_difficulty(difficulty: str) -> str:
    normalized = str(difficulty or "").strip().lower()
    return normalized or "0"


def _pow_generate(
    seed: str,
    difficulty: str,
    config: list[Any],
    limit: int = 500000,
) -> tuple[str, bool]:
    target = _normalize_difficulty(difficulty)
    started = time.perf_counter()
    for attempt in range(limit):
        current = list(config)
        current[3] = attempt
        current[9] = round((time.perf_counter() - started) * 1000)
        encoded = _encode_config(current)
        digest = fnv1a32_hex(f"{seed}{encoded}")
        if digest[: len(target)] <= target:
            return encoded, True
    fallback = "wQ8Lk5FbGpA2NcR9dShT6gYjU7VxZ4D" + pybase64.b64encode(f'"{seed}"'.encode()).decode()
    return fallback, False


def build_legacy_requirements_token(
    user_agent: str,
    script_sources: Sequence[str] | None = None,
    data_build: str = "",
    environment: SentinelEnvironment | None = None,
) -> str:
    """Requirements `p`: `gAAAAAC` plus the 25-field config. No `~S` suffix."""
    config = build_pow_config(
        user_agent,
        script_sources=script_sources,
        data_build=data_build,
        environment=environment,
    )
    return "gAAAAAC" + _encode_config(config)


def build_proof_token(
    seed: str,
    difficulty: str,
    user_agent: str,
    script_sources: Sequence[str] | None = None,
    data_build: str = "",
    environment: SentinelEnvironment | None = None,
) -> str:
    """Proof token: `gAAAAAB` plus base64 config plus `~S`."""
    config = build_pow_config(
        user_agent,
        script_sources=script_sources,
        data_build=data_build,
        environment=environment,
    )
    answer, solved = _pow_generate(seed, difficulty, config)
    if not solved:
        raise RuntimeError(f"failed to solve proof token: difficulty={difficulty}")
    return "gAAAAAB" + answer + "~S"
