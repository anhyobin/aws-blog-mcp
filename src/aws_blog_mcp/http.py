"""Polite, bounded HTTP access to aws.amazon.com.

All network I/O goes through :class:`Fetcher`, which

* identifies itself with a descriptive User-Agent (overridable via ``AWS_BLOG_MCP_USER_AGENT``),
* spaces out request starts (a few requests per second at most),
* retries transient failures (network errors, 429, 5xx) with backoff, honouring ``Retry-After``,
* follows at most a few redirects and only within ``https://aws.amazon.com``,
* caps response bodies so a misbehaving upstream cannot exhaust memory.
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

import anyio
import httpx2

from aws_blog_mcp import __version__
from aws_blog_mcp.blogs import HOST, ORIGIN
from aws_blog_mcp.errors import NotFoundError, UpstreamError

logger = logging.getLogger(__name__)

PROJECT_URL = "https://github.com/anhyobin/aws-blog-mcp"
USER_AGENT_ENV = "AWS_BLOG_MCP_USER_AGENT"
DEFAULT_TIMEOUT = httpx2.Timeout(20.0, connect=5.0)
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_REDIRECTS = 3
MAX_RETRY_AFTER_SECONDS = 10.0
ATTEMPT_DEADLINE_SECONDS = 45.0
"""Upper bound for one attempt (all redirects plus the body), on top of per-read timeouts, so a
server trickling bytes cannot hold a tool call indefinitely."""


def user_agent() -> str:
    """Return the User-Agent sent with every request."""
    default = f"aws-blog-mcp/{__version__} (+{PROJECT_URL})"
    override = os.environ.get(USER_AGENT_ENV, "").strip()
    if not override:
        return default
    if not (override.isascii() and override.isprintable()):
        logger.warning(
            "Ignoring %s: it must be printable ASCII without line breaks", USER_AGENT_ENV
        )
        return default
    return override


def create_http_client(transport: httpx2.AsyncBaseTransport | None = None) -> httpx2.AsyncClient:
    """Create the shared async client (redirects are handled by :class:`Fetcher`)."""
    return httpx2.AsyncClient(
        headers={"User-Agent": user_agent(), "Accept-Encoding": "gzip"},
        timeout=DEFAULT_TIMEOUT,
        follow_redirects=False,
        transport=transport,
    )


@dataclass(frozen=True, slots=True)
class FetchedResponse:
    """A fully read, successful (2xx) response."""

    url: str
    """Final URL after redirects."""
    status: int
    content: bytes
    encoding: str

    def text(self) -> str:
        """Decode the body, replacing undecodable bytes (unknown charsets fall back to UTF-8)."""
        try:
            return self.content.decode(self.encoding, errors="replace")
        except (LookupError, TypeError):
            return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        """Parse the body as JSON."""
        try:
            return json.loads(self.content)
        except ValueError as exc:
            raise UpstreamError(f"aws.amazon.com returned invalid JSON for {self.url}.") from exc


class Fetcher:
    """Rate-limited GET with retries, redirect allowlisting and a body size cap."""

    def __init__(
        self,
        client: httpx2.AsyncClient,
        *,
        min_interval: float = 0.25,
        max_attempts: int = 3,
        backoff_base: float = 0.5,
        attempt_deadline: float = ATTEMPT_DEADLINE_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._min_interval = min_interval
        self._max_attempts = max(1, max_attempts)
        self._backoff_base = backoff_base
        self._attempt_deadline = attempt_deadline
        self._sleep = sleep
        self._clock = clock
        self._lock = anyio.Lock()
        self._next_start = 0.0

    async def get(
        self,
        url: str,
        *,
        params: Mapping[str, str | int] | None = None,
        max_bytes: int,
    ) -> FetchedResponse:
        """GET ``url`` and return the body.

        Raises:
            NotFoundError: the final response was 404 or 410.
            UpstreamError: any other failure, after retries where sensible.
        """
        _check_allowed(url)
        last_error = "unknown error"
        for attempt in range(1, self._max_attempts + 1):
            try:
                with anyio.fail_after(self._attempt_deadline):
                    return await self._get_once(url, params=params, max_bytes=max_bytes)
            except TimeoutError:
                last_error = f"Fetching {url} took longer than {self._attempt_deadline:g}s."
                if attempt == self._max_attempts:
                    break
                await self._sleep(self._backoff_delay(attempt))
            except _RetryableError as exc:
                last_error = exc.message
                if attempt == self._max_attempts:
                    break
                delay = exc.retry_after
                if delay is None:
                    delay = self._backoff_delay(attempt)
                logger.info("Retrying %s in %.2fs (%s)", url, delay, exc.message)
                await self._sleep(delay)
        raise UpstreamError(
            f"{last_error} Gave up after {self._max_attempts} attempts; try again later."
        )

    def _backoff_delay(self, attempt: int) -> float:
        return self._backoff_base * 2 ** (attempt - 1) + random.uniform(0, 0.25)  # noqa: S311

    async def _throttle(self) -> None:
        async with self._lock:
            now = self._clock()
            wait = self._next_start - now
            if wait > 0:
                await self._sleep(wait)
            self._next_start = max(now, self._next_start) + self._min_interval

    async def _get_once(
        self, url: str, *, params: Mapping[str, str | int] | None, max_bytes: int
    ) -> FetchedResponse:
        current = url
        current_params = params
        for _ in range(MAX_REDIRECTS + 1):
            await self._throttle()
            try:
                async with self._client.stream("GET", current, params=current_params) as resp:
                    if resp.has_redirect_location:
                        current = _redirect_target(str(resp.url), resp.headers["location"])
                        current_params = None  # the Location already carries the query
                        continue
                    status = resp.status_code
                    if status in RETRY_STATUSES:
                        raise _RetryableError(
                            f"aws.amazon.com answered HTTP {status} for {resp.url}.",
                            retry_after=_retry_after_seconds(resp.headers.get("retry-after")),
                        )
                    if status in (404, 410):
                        raise NotFoundError(f"{resp.url} does not exist (HTTP {status}).")
                    if not 200 <= status < 300:
                        raise UpstreamError(
                            f"aws.amazon.com answered HTTP {status} for {resp.url}."
                        )
                    content = await _read_capped(resp, max_bytes)
                    return FetchedResponse(
                        url=str(resp.url),
                        status=status,
                        content=content,
                        encoding=resp.encoding or "utf-8",
                    )
            except httpx2.TimeoutException as exc:
                raise _RetryableError(f"Request to {current} timed out.") from exc
            except httpx2.DecodingError as exc:
                raise UpstreamError(f"Could not decode the response from {current}.") from exc
            except httpx2.RemoteProtocolError as exc:
                if "location" in str(exc).lower():
                    # httpx2 parses the redirect target itself and rejects malformed ones.
                    raise UpstreamError(f"{current} redirected to an invalid URL.") from exc
                raise _RetryableError(
                    f"Network error while fetching {current}: {type(exc).__name__}."
                ) from exc
            except httpx2.LocalProtocolError as exc:
                # Our own request was malformed (e.g. a bad header): retrying will not help.
                raise UpstreamError(f"Could not send the request to {current}: {exc}") from exc
            except httpx2.TransportError as exc:
                raise _RetryableError(
                    f"Network error while fetching {current}: {type(exc).__name__}."
                ) from exc
            except (httpx2.HTTPError, httpx2.InvalidURL) as exc:
                raise UpstreamError(f"Could not fetch {current}: {type(exc).__name__}.") from exc
        raise UpstreamError(f"Too many redirects while fetching {url}.")


class _RetryableError(Exception):
    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after


def _check_allowed(url: str) -> None:
    """Allow only plain ``https://aws.amazon.com`` URLs (default port, no credentials)."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        parts, port = None, -1
    if (
        parts is None
        or parts.scheme != "https"
        or (parts.hostname or "").lower() != HOST
        or port not in (None, 443)
        or parts.username is not None
        or parts.password is not None
        or "\\" in url
    ):
        raise UpstreamError(f"Refusing to fetch {url}: only {ORIGIN} is allowed.")


def _redirect_target(current: str, location: str) -> str:
    try:
        target = urljoin(current, location.strip())
    except ValueError as exc:
        raise UpstreamError(f"{current} redirected to an invalid URL.") from exc
    _check_allowed(target)
    return target


def _retry_after_seconds(value: str | None) -> float | None:
    """Parse a delta-seconds ``Retry-After``; refuse to wait unreasonably long."""
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None  # HTTP-date form: fall back to normal backoff
    if math.isnan(seconds) or seconds < 0:
        return None
    if seconds > MAX_RETRY_AFTER_SECONDS:
        shown = value.strip() if math.isinf(seconds) else f"{seconds:.0f}"
        raise UpstreamError(
            f"aws.amazon.com is rate limiting requests (Retry-After: {shown}s). Try again later."
        )
    return seconds


async def _read_capped(resp: httpx2.Response, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in resp.aiter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise UpstreamError(
                f"Response from {resp.url} exceeded the {_format_size(max_bytes)} size limit."
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _format_size(size: int) -> str:
    if size >= 1_000_000:
        return f"{size / 1_000_000:g} MB"
    if size >= 1_000:
        return f"{size / 1_000:g} KB"
    return f"{size} bytes"
