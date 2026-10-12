"""Tests for :mod:`aws_blog_mcp.http` (Fetcher, user agent, FetchedResponse)."""

from __future__ import annotations

import logging
import math
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

import anyio
import anyio.lowlevel
import httpx2
import pytest

from aws_blog_mcp import __version__
from aws_blog_mcp.errors import NotFoundError, UpstreamError
from aws_blog_mcp.http import (
    ATTEMPT_DEADLINE_SECONDS,
    MAX_REDIRECTS,
    MAX_RETRY_AFTER_SECONDS,
    PROJECT_URL,
    USER_AGENT_ENV,
    FetchedResponse,
    Fetcher,
    create_http_client,
    user_agent,
)

pytestmark = pytest.mark.anyio

URL = "https://aws.amazon.com/ko/blogs/tech/some-post/"
OTHER = "https://aws.amazon.com/ko/blogs/tech/other-post/"

Step = Callable[[httpx2.Request], httpx2.Response]


# --------------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------------


@dataclass
class FakeTime:
    """Fake monotonic clock; ``sleep`` records the delay and advances the clock."""

    now: float = 1000.0
    sleeps: list[float] = field(default_factory=list)

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        await anyio.lowlevel.checkpoint()


class Upstream:
    """Scripted aws.amazon.com: per-URL steps, else a sequence (the last step repeats)."""

    def __init__(self, time: FakeTime, *steps: Step, routes: dict[str, Step] | None = None):
        self.time = time
        self.steps = list(steps)
        self.routes = routes or {}
        self.requests: list[httpx2.Request] = []
        self.started: list[float] = []

    @property
    def urls(self) -> list[str]:
        return [str(r.url) for r in self.requests]

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        self.started.append(self.time.now)
        url = str(request.url)
        if url in self.routes:
            return self.routes[url](request)
        step = self.steps.pop(0) if len(self.steps) > 1 else self.steps[0]
        return step(request)


def reply(
    status: int = 200,
    content: bytes = b"ok",
    headers: dict[str, str] | None = None,
) -> Step:
    def step(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, content=content, headers=headers)

    return step


def redirect(location: str, status: int = 301) -> Step:
    return reply(status, b"", {"location": location})


def fail(exc_type: type[httpx2.TransportError], message: str = "boom") -> Step:
    def step(request: httpx2.Request) -> httpx2.Response:
        raise exc_type(message, request=request)

    return step


def fail_with(exc: Exception) -> Step:
    def step(request: httpx2.Request) -> httpx2.Response:
        raise exc

    return step


@asynccontextmanager
async def fetcher_for(
    upstream: Upstream,
    *,
    min_interval: float = 0.0,
    max_attempts: int = 3,
    backoff_base: float = 0.5,
    attempt_deadline: float = ATTEMPT_DEADLINE_SECONDS,
) -> AsyncIterator[Fetcher]:
    async with create_http_client(httpx2.MockTransport(upstream)) as client:
        yield Fetcher(
            client,
            min_interval=min_interval,
            max_attempts=max_attempts,
            backoff_base=backoff_base,
            attempt_deadline=attempt_deadline,
            sleep=upstream.time.sleep,
            clock=upstream.time.clock,
        )


@pytest.fixture
def ft() -> FakeTime:
    return FakeTime()


@pytest.fixture
def jitter(monkeypatch: pytest.MonkeyPatch) -> list[tuple[float, float]]:
    """Make the backoff jitter deterministic (always the upper bound) and record its range."""
    calls: list[tuple[float, float]] = []

    def uniform(a: float, b: float) -> float:
        calls.append((a, b))
        return b

    monkeypatch.setattr("aws_blog_mcp.http.random.uniform", uniform)
    return calls


# --------------------------------------------------------------------------------------------
# success
# --------------------------------------------------------------------------------------------


async def test_success_returns_body_and_metadata(ft: FakeTime) -> None:
    up = Upstream(ft, reply(200, b'{"a": 1}', {"content-type": "application/json"}))
    async with fetcher_for(up) as fetcher:
        resp = await fetcher.get(URL, params={"q": "65", "size": 3}, max_bytes=1000)

    assert resp == FetchedResponse(
        url=URL + "?q=65&size=3", status=200, content=b'{"a": 1}', encoding="utf-8"
    )
    assert resp.json() == {"a": 1}
    assert up.urls == [URL + "?q=65&size=3"]
    assert up.requests[0].method == "GET"
    assert up.requests[0].headers["user-agent"] == user_agent()
    assert up.requests[0].headers["accept-encoding"] == "gzip"
    assert ft.sleeps == []


async def test_success_with_empty_2xx_body(ft: FakeTime) -> None:
    up = Upstream(ft, reply(204, b""))
    async with fetcher_for(up) as fetcher:
        resp = await fetcher.get(URL, max_bytes=10)
    assert (resp.status, resp.content, resp.text()) == (204, b"", "")


async def test_text_uses_declared_charset(ft: FakeTime) -> None:
    body = "한글 본문".encode("euc-kr")
    up = Upstream(ft, reply(200, body, {"content-type": "text/html; charset=euc-kr"}))
    async with fetcher_for(up) as fetcher:
        resp = await fetcher.get(URL, max_bytes=1000)
    assert resp.encoding == "euc-kr"
    assert resp.text() == "한글 본문"


async def test_text_replaces_undecodable_bytes(ft: FakeTime) -> None:
    up = Upstream(ft, reply(200, b"ok\xff", {"content-type": "text/plain; charset=utf-8"}))
    async with fetcher_for(up) as fetcher:
        resp = await fetcher.get(URL, max_bytes=1000)
    assert resp.text() == "ok�"


@pytest.mark.parametrize("charset", ["rot13", "x-made-up-charset", "base64"])
async def test_unusable_charset_falls_back_to_utf8(ft: FakeTime, charset: str) -> None:
    body = "한글 uryyb\n".encode() + b"\xff"
    up = Upstream(ft, reply(200, body, {"content-type": f"text/html; charset={charset}"}))
    async with fetcher_for(up) as fetcher:
        resp = await fetcher.get(URL, max_bytes=1000)
    assert resp.text() == "한글 uryyb\n�"


@pytest.mark.parametrize("encoding", ["rot13", "no-such-codec", "", "zlib"])
def test_fetched_response_text_with_unusable_encoding(encoding: str) -> None:
    resp = FetchedResponse(url=URL, status=200, content="본문 ok".encode(), encoding=encoding)
    assert resp.text() == "본문 ok"


# --------------------------------------------------------------------------------------------
# FetchedResponse.json
# --------------------------------------------------------------------------------------------


def test_json_parses_utf8_body() -> None:
    resp = FetchedResponse(
        url=URL, status=200, content='{"a": [1, "한"]}'.encode(), encoding="utf-8"
    )
    assert resp.json() == {"a": [1, "한"]}


@pytest.mark.parametrize("content", [b"{not json", b"", b"<html></html>", b"\xff\xfe\xfa"])
def test_json_invalid_raises_upstream_error(content: bytes) -> None:
    resp = FetchedResponse(url=URL, status=200, content=content, encoding="utf-8")
    with pytest.raises(UpstreamError) as info:
        resp.json()
    assert str(info.value) == f"aws.amazon.com returned invalid JSON for {URL}."


# --------------------------------------------------------------------------------------------
# status handling
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("status", [404, 410])
async def test_not_found_statuses(ft: FakeTime, status: int) -> None:
    up = Upstream(ft, reply(status))
    async with fetcher_for(up) as fetcher:
        with pytest.raises(NotFoundError) as info:
            await fetcher.get(URL, max_bytes=1000)
    assert str(info.value) == f"{URL} does not exist (HTTP {status})."
    assert len(up.requests) == 1
    assert ft.sleeps == []


@pytest.mark.parametrize("status", [400, 401, 403, 405, 418, 451, 501, 505])
async def test_other_errors_are_not_retried(ft: FakeTime, status: int) -> None:
    up = Upstream(ft, reply(status))
    async with fetcher_for(up) as fetcher:
        with pytest.raises(UpstreamError) as info:
            await fetcher.get(URL, max_bytes=1000)
    assert not isinstance(info.value, NotFoundError)
    assert str(info.value) == f"aws.amazon.com answered HTTP {status} for {URL}."
    assert len(up.requests) == 1
    assert ft.sleeps == []


@pytest.mark.parametrize("status", [300, 301, 304])
async def test_3xx_without_location_is_not_followed(ft: FakeTime, status: int) -> None:
    up = Upstream(ft, reply(status, b""))
    async with fetcher_for(up) as fetcher:
        with pytest.raises(UpstreamError) as info:
            await fetcher.get(URL, max_bytes=1000)
    assert len(up.requests) == 1
    assert f"HTTP {status}" in str(info.value)


@pytest.mark.parametrize("status", [300, 304, 305])
async def test_location_on_a_non_redirect_status_is_not_followed(ft: FakeTime, status: int) -> None:
    up = Upstream(ft, routes={URL: redirect(OTHER, status), OTHER: reply(200)})
    async with fetcher_for(up) as fetcher:
        with pytest.raises(UpstreamError) as info:
            await fetcher.get(URL, max_bytes=1000)
    assert str(info.value) == f"aws.amazon.com answered HTTP {status} for {URL}."
    assert up.urls == [URL]


async def test_2xx_with_location_is_a_success(ft: FakeTime) -> None:
    up = Upstream(ft, routes={URL: reply(201, b"created", {"location": OTHER})})
    async with fetcher_for(up) as fetcher:
        resp = await fetcher.get(URL, max_bytes=1000)
    assert (resp.url, resp.status, resp.content) == (URL, 201, b"created")
    assert up.urls == [URL]


# --------------------------------------------------------------------------------------------
# retries and backoff
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
async def test_retryable_status_then_success(
    ft: FakeTime, jitter: list[tuple[float, float]], status: int
) -> None:
    up = Upstream(ft, reply(status), reply(status), reply(200, b"finally"))
    async with fetcher_for(up) as fetcher:
        resp = await fetcher.get(URL, max_bytes=1000)
    assert resp.content == b"finally"
    assert len(up.requests) == 3
    # backoff_base * 2 ** (attempt - 1) + jitter (patched to its 0.25 upper bound)
    assert ft.sleeps == [0.75, 1.25]
    assert jitter == [(0, 0.25), (0, 0.25)]


async def test_retries_exhausted(ft: FakeTime, jitter: list[tuple[float, float]]) -> None:
    up = Upstream(ft, reply(503))
    async with fetcher_for(up) as fetcher:
        with pytest.raises(UpstreamError) as info:
            await fetcher.get(URL, max_bytes=1000)
    assert str(info.value) == (
        f"aws.amazon.com answered HTTP 503 for {URL}. Gave up after 3 attempts; try again later."
    )
    assert len(up.requests) == 3
    assert ft.sleeps == [0.75, 1.25]  # no sleep after the last attempt


async def test_backoff_doubles_with_custom_settings(
    ft: FakeTime, jitter: list[tuple[float, float]]
) -> None:
    up = Upstream(ft, reply(500))
    async with fetcher_for(up, max_attempts=5, backoff_base=1.0) as fetcher:
        with pytest.raises(UpstreamError, match="Gave up after 5 attempts"):
            await fetcher.get(URL, max_bytes=1000)
    assert len(up.requests) == 5
    assert ft.sleeps == [1.25, 2.25, 4.25, 8.25]


async def test_real_jitter_stays_in_range(ft: FakeTime) -> None:
    up = Upstream(ft, reply(503), reply(503), reply(200))
    async with fetcher_for(up) as fetcher:
        await fetcher.get(URL, max_bytes=1000)
    first, second = ft.sleeps
    assert 0.5 <= first <= 0.75
    assert 1.0 <= second <= 1.25


async def test_single_attempt_does_not_retry(ft: FakeTime) -> None:
    up = Upstream(ft, reply(503), reply(200))
    async with fetcher_for(up, max_attempts=1) as fetcher:
        with pytest.raises(UpstreamError, match="HTTP 503"):
            await fetcher.get(URL, max_bytes=1000)
    assert len(up.requests) == 1
    assert ft.sleeps == []


async def test_retry_then_not_found(ft: FakeTime, jitter: list[tuple[float, float]]) -> None:
    up = Upstream(ft, reply(502), reply(404))
    async with fetcher_for(up) as fetcher:
        with pytest.raises(NotFoundError):
            await fetcher.get(URL, max_bytes=1000)
    assert len(up.requests) == 2
    assert ft.sleeps == [0.75]


# --------------------------------------------------------------------------------------------
# Retry-After
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "header", "expected"),
    [
        (429, "2", 2.0),
        (503, "1.5", 1.5),
        (429, "0", 0.0),
        (429, " 3 ", 3.0),
        (503, str(int(MAX_RETRY_AFTER_SECONDS)), MAX_RETRY_AFTER_SECONDS),
    ],
)
async def test_numeric_retry_after_is_honoured(
    ft: FakeTime,
    jitter: list[tuple[float, float]],
    status: int,
    header: str,
    expected: float,
) -> None:
    up = Upstream(ft, reply(status, b"", {"retry-after": header}), reply(200))
    async with fetcher_for(up) as fetcher:
        await fetcher.get(URL, max_bytes=1000)
    assert ft.sleeps == [expected]
    assert jitter == []  # no backoff computed
    assert len(up.requests) == 2


@pytest.mark.parametrize(
    "header", ["Wed, 21 Oct 2026 07:28:00 GMT", "-5", "soon", "nan", "NaN", "-nan", "-inf", ""]
)
async def test_unusable_retry_after_falls_back_to_backoff(
    ft: FakeTime, jitter: list[tuple[float, float]], header: str
) -> None:
    up = Upstream(ft, reply(429, b"", {"retry-after": header}), reply(200))
    async with fetcher_for(up) as fetcher:
        await fetcher.get(URL, max_bytes=1000)
    assert ft.sleeps == [0.75]


@pytest.mark.parametrize(
    ("header", "shown"),
    [("11", "11"), ("120", "120"), ("3600.5", "3600"), ("1e9", "1000000000"), (" 10.6 ", "11")],
)
async def test_too_large_retry_after_fails_immediately(
    ft: FakeTime, header: str, shown: str
) -> None:
    up = Upstream(ft, reply(429, b"", {"retry-after": header}), reply(200))
    async with fetcher_for(up) as fetcher:
        with pytest.raises(UpstreamError) as info:
            await fetcher.get(URL, max_bytes=1000)
    assert str(info.value) == (
        f"aws.amazon.com is rate limiting requests (Retry-After: {shown}s). Try again later."
    )
    assert len(up.requests) == 1
    assert ft.sleeps == []


async def test_nan_retry_after_falls_back_to_backoff(ft: FakeTime) -> None:
    up = Upstream(ft, reply(429, b"", {"retry-after": "nan"}), reply(200))
    async with fetcher_for(up) as fetcher:
        await fetcher.get(URL, max_bytes=1000)
    assert len(ft.sleeps) == 1
    assert math.isfinite(ft.sleeps[0])


@pytest.mark.parametrize("header", ["inf", "Infinity", "1e999"])
async def test_infinite_retry_after_is_too_large(ft: FakeTime, header: str) -> None:
    up = Upstream(ft, reply(429, b"", {"retry-after": header}))
    async with fetcher_for(up) as fetcher:
        with pytest.raises(UpstreamError) as info:
            await fetcher.get(URL, max_bytes=1000)
    assert str(info.value).startswith(
        f"aws.amazon.com is rate limiting requests (Retry-After: {header}"
    )
    assert str(info.value).endswith("Try again later.")
    assert len(up.requests) == 1
    assert ft.sleeps == []


# --------------------------------------------------------------------------------------------
# network errors and timeouts
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc_type", [httpx2.ConnectError, httpx2.ReadError, httpx2.RemoteProtocolError]
)
async def test_network_error_retried_then_success(
    ft: FakeTime, jitter: list[tuple[float, float]], exc_type: type[httpx2.TransportError]
) -> None:
    up = Upstream(ft, fail(exc_type), fail(exc_type), reply(200, b"up again"))
    async with fetcher_for(up) as fetcher:
        resp = await fetcher.get(URL, max_bytes=1000)
    assert resp.content == b"up again"
    assert len(up.requests) == 3
    assert ft.sleeps == [0.75, 1.25]


async def test_network_error_exhausted(ft: FakeTime, jitter: list[tuple[float, float]]) -> None:
    up = Upstream(ft, fail(httpx2.ConnectError))
    async with fetcher_for(up) as fetcher:
        with pytest.raises(UpstreamError) as info:
            await fetcher.get(URL, max_bytes=1000)
    assert str(info.value) == (
        f"Network error while fetching {URL}: ConnectError. "
        "Gave up after 3 attempts; try again later."
    )
    assert len(up.requests) == 3


@pytest.mark.parametrize(
    "exc_type", [httpx2.ConnectTimeout, httpx2.ReadTimeout, httpx2.PoolTimeout]
)
async def test_timeout_exhausted(
    ft: FakeTime, jitter: list[tuple[float, float]], exc_type: type[httpx2.TransportError]
) -> None:
    up = Upstream(ft, fail(exc_type))
    async with fetcher_for(up) as fetcher:
        with pytest.raises(UpstreamError) as info:
            await fetcher.get(URL, max_bytes=1000)
    assert str(info.value) == (
        f"Request to {URL} timed out. Gave up after 3 attempts; try again later."
    )
    assert len(up.requests) == 3
    assert ft.sleeps == [0.75, 1.25]


async def test_timeout_then_success(ft: FakeTime, jitter: list[tuple[float, float]]) -> None:
    up = Upstream(ft, fail(httpx2.ReadTimeout), reply(200))
    async with fetcher_for(up) as fetcher:
        resp = await fetcher.get(URL, max_bytes=1000)
    assert resp.status == 200
    assert len(up.requests) == 2


async def test_mixed_failures_report_last_error(
    ft: FakeTime, jitter: list[tuple[float, float]]
) -> None:
    up = Upstream(ft, fail(httpx2.ConnectError), reply(503), fail(httpx2.ReadTimeout))
    async with fetcher_for(up) as fetcher:
        with pytest.raises(
            UpstreamError, match=r"^Request to .* timed out\. Gave up after 3 attempts"
        ):
            await fetcher.get(URL, max_bytes=1000)


async def test_decoding_error_is_not_retried(ft: FakeTime) -> None:
    async def garbage() -> AsyncIterator[bytes]:
        yield b"this is not gzip data"

    def step(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, headers={"content-encoding": "gzip"}, content=garbage())

    up = Upstream(ft, step)
    async with fetcher_for(up) as fetcher:
        with pytest.raises(UpstreamError) as info:
            await fetcher.get(URL, max_bytes=1000)
    assert str(info.value) == f"Could not decode the response from {URL}."
    assert len(up.requests) == 1
    assert ft.sleeps == []


async def test_local_protocol_error_is_not_retried(ft: FakeTime) -> None:
    up = Upstream(ft, fail(httpx2.LocalProtocolError, "Illegal header value b'x\\n'"))
    async with fetcher_for(up) as fetcher:
        with pytest.raises(UpstreamError) as info:
            await fetcher.get(URL, max_bytes=1000)
    assert str(info.value) == f"Could not send the request to {URL}: Illegal header value b'x\\n'"
    assert isinstance(info.value.__cause__, httpx2.LocalProtocolError)
    assert len(up.requests) == 1
    assert ft.sleeps == []


@pytest.mark.parametrize(
    ("exc", "name"),
    [
        (httpx2.InvalidURL("Invalid non-printable ASCII character in URL"), "InvalidURL"),
        (httpx2.TooManyRedirects("redirect loop"), "TooManyRedirects"),
    ],
    ids=["invalid-url", "other-http-error"],
)
async def test_other_client_errors_are_upstream_errors(
    ft: FakeTime, exc: Exception, name: str
) -> None:
    up = Upstream(ft, fail_with(exc))
    async with fetcher_for(up) as fetcher:
        with pytest.raises(UpstreamError) as info:
            await fetcher.get(URL, max_bytes=1000)
    assert type(info.value) is UpstreamError
    assert str(info.value) == f"Could not fetch {URL}: {name}."
    assert len(up.requests) == 1
    assert ft.sleeps == []


# --------------------------------------------------------------------------------------------
# per-attempt deadline (real time, kept tiny)
# --------------------------------------------------------------------------------------------


def stall(seconds: float = 30.0) -> Callable[[httpx2.Request], Any]:
    async def step(request: httpx2.Request) -> httpx2.Response:
        await anyio.sleep(seconds)
        return httpx2.Response(200, content=b"too late")

    return step


def test_default_attempt_deadline() -> None:
    assert ATTEMPT_DEADLINE_SECONDS == 45.0


async def test_attempt_deadline_is_retried_then_succeeds(
    ft: FakeTime, jitter: list[tuple[float, float]]
) -> None:
    up = Upstream(ft, stall(), reply(200, b"fast now"))
    async with fetcher_for(up, attempt_deadline=0.05) as fetcher:
        with anyio.fail_after(5):
            resp = await fetcher.get(URL, max_bytes=1000)
    assert resp.content == b"fast now"
    assert len(up.requests) == 2
    assert ft.sleeps == [0.75]  # normal backoff between the attempts


async def test_attempt_deadline_exhausted(ft: FakeTime, jitter: list[tuple[float, float]]) -> None:
    up = Upstream(ft, stall())
    async with fetcher_for(up, max_attempts=2, attempt_deadline=0.05) as fetcher:
        with anyio.fail_after(5), pytest.raises(UpstreamError) as info:
            await fetcher.get(URL, max_bytes=1000)
    assert re.fullmatch(
        rf"Fetching {re.escape(URL)} took longer than [0-9.]+s\. "
        r"Gave up after 2 attempts; try again later\.",
        str(info.value),
    ), str(info.value)
    assert len(up.requests) == 2
    assert ft.sleeps == [0.75]


async def test_attempt_deadline_covers_a_trickling_body(ft: FakeTime) -> None:
    async def trickle() -> AsyncIterator[bytes]:
        yield b"a"
        await anyio.sleep(30)
        yield b"b"

    def step(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=trickle())

    up = Upstream(ft, step)
    async with fetcher_for(up, max_attempts=1, attempt_deadline=0.05) as fetcher:
        with anyio.fail_after(5), pytest.raises(UpstreamError, match="took longer than"):
            await fetcher.get(URL, max_bytes=1000)
    assert len(up.requests) == 1


# --------------------------------------------------------------------------------------------
# redirects
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
async def test_redirect_within_host_is_followed(ft: FakeTime, status: int) -> None:
    up = Upstream(ft, routes={URL: redirect(OTHER, status), OTHER: reply(200, b"moved")})
    async with fetcher_for(up) as fetcher:
        resp = await fetcher.get(URL, max_bytes=1000)
    assert resp.url == OTHER
    assert resp.content == b"moved"
    assert up.urls == [URL, OTHER]


@pytest.mark.parametrize(
    ("location", "resolved"),
    [
        ("/ko/blogs/tech/other-post/", OTHER),
        ("../other-post/", OTHER),
        ("child/", URL + "child/"),
        ("?page=2", URL + "?page=2"),
    ],
)
async def test_relative_redirect_is_resolved(ft: FakeTime, location: str, resolved: str) -> None:
    up = Upstream(ft, routes={URL: redirect(location), resolved: reply(200, b"moved")})
    async with fetcher_for(up) as fetcher:
        resp = await fetcher.get(URL, max_bytes=1000)
    assert resp.url == resolved
    assert up.urls == [URL, resolved]


async def test_redirect_drops_original_params(ft: FakeTime) -> None:
    target = "https://aws.amazon.com/api/v2/search?b=2"
    up = Upstream(ft, routes={URL + "?a=1": redirect(target), target: reply(200)})
    async with fetcher_for(up) as fetcher:
        resp = await fetcher.get(URL, params={"a": "1"}, max_bytes=1000)
    assert up.urls == [URL + "?a=1", target]
    assert resp.url == target


@pytest.mark.parametrize(
    "location",
    [
        "https://evil.example/ko/blogs/tech/",
        "http://aws.amazon.com/ko/blogs/tech/other-post/",
        "https://docs.aws.amazon.com/",
        "https://aws.amazon.com.evil.example/",
        "https://aws.amazon.com@evil.example/",
        "//evil.example/x",
        "ftp://aws.amazon.com/file",
        "https://aws.amazon.com:8443/ko/blogs/tech/other-post/",
        "https://aws.amazon.com:80/ko/blogs/tech/other-post/",
        "https://aws.amazon.com:99999/x",
        "https://user:secret@aws.amazon.com/ko/blogs/tech/other-post/",
        "https://user@aws.amazon.com/x",
        "https://aws.amazon.com\\@evil.example/",
        "/\\evil.example/x",
        "/ko/blogs/tech\\other-post/",
    ],
)
async def test_redirect_outside_allowlist_is_rejected(ft: FakeTime, location: str) -> None:
    up = Upstream(ft, redirect(location))
    async with fetcher_for(up) as fetcher:
        with pytest.raises(UpstreamError) as info:
            await fetcher.get(URL, max_bytes=1000)
    assert str(info.value) == (
        f"Refusing to fetch {urljoin(URL, location)}: only https://aws.amazon.com is allowed."
    )
    assert len(up.requests) == 1  # not followed, not retried
    assert ft.sleeps == []


async def test_redirect_to_explicit_default_port_is_followed(ft: FakeTime) -> None:
    location = "https://aws.amazon.com:443/ko/blogs/tech/other-post/"
    up = Upstream(ft, routes={URL: redirect(location), OTHER: reply(200, b"moved")})
    async with fetcher_for(up) as fetcher:
        resp = await fetcher.get(URL, max_bytes=1000)
    assert resp.url == OTHER
    assert up.urls == [URL, OTHER]


async def test_location_is_stripped_before_resolving(ft: FakeTime) -> None:
    up = Upstream(ft, routes={URL: redirect("  /ko/blogs/tech/other-post/  "), OTHER: reply(200)})
    async with fetcher_for(up) as fetcher:
        resp = await fetcher.get(URL, max_bytes=1000)
    assert resp.url == OTHER


@pytest.mark.parametrize(
    "location",
    [
        "http://[bad",
        "https://[::1",  # rejected by httpx2 itself while building the redirect request
        "https://aws.amazon.com:abc/x",
    ],
)
async def test_malformed_location_is_an_upstream_error(ft: FakeTime, location: str) -> None:
    up = Upstream(ft, redirect(location))
    async with fetcher_for(up) as fetcher:
        with pytest.raises(UpstreamError) as info:
            await fetcher.get(URL, max_bytes=1000)
    assert str(info.value) == f"{URL} redirected to an invalid URL."
    assert len(up.requests) == 1  # deterministic: not retried
    assert ft.sleeps == []


async def test_exactly_max_redirects_is_allowed(ft: FakeTime) -> None:
    hops = [f"https://aws.amazon.com/hop/{i}/" for i in range(MAX_REDIRECTS + 1)]
    routes = {hops[i]: redirect(hops[i + 1]) for i in range(MAX_REDIRECTS)}
    routes[hops[-1]] = reply(200, b"end")
    up = Upstream(ft, routes=routes)
    async with fetcher_for(up) as fetcher:
        resp = await fetcher.get(hops[0], max_bytes=1000)
    assert resp.url == hops[-1]
    assert up.urls == hops


async def test_too_many_redirects(ft: FakeTime) -> None:
    hops = [f"https://aws.amazon.com/hop/{i}/" for i in range(MAX_REDIRECTS + 2)]
    routes = {hops[i]: redirect(hops[i + 1]) for i in range(MAX_REDIRECTS + 1)}
    routes[hops[-1]] = reply(200)
    up = Upstream(ft, routes=routes)
    async with fetcher_for(up) as fetcher:
        with pytest.raises(UpstreamError) as info:
            await fetcher.get(hops[0], max_bytes=1000)
    assert str(info.value) == f"Too many redirects while fetching {hops[0]}."
    assert up.urls == hops[: MAX_REDIRECTS + 1]  # never retried
    assert ft.sleeps == []


async def test_redirect_loop(ft: FakeTime) -> None:
    up = Upstream(ft, routes={URL: redirect(URL)})
    async with fetcher_for(up) as fetcher:
        with pytest.raises(UpstreamError, match="Too many redirects"):
            await fetcher.get(URL, max_bytes=1000)
    assert up.urls == [URL] * (MAX_REDIRECTS + 1)


async def test_redirect_then_not_found_names_final_url(ft: FakeTime) -> None:
    up = Upstream(ft, routes={URL: redirect(OTHER), OTHER: reply(404)})
    async with fetcher_for(up) as fetcher:
        with pytest.raises(NotFoundError) as info:
            await fetcher.get(URL, max_bytes=1000)
    assert str(info.value) == f"{OTHER} does not exist (HTTP 404)."


# --------------------------------------------------------------------------------------------
# initial URL allowlist
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://aws.amazon.com/ko/blogs/tech/",
        "https://example.com/",
        "https://docs.aws.amazon.com/",
        "https://aws.amazon.com.evil.example/",
        "https://aws.amazon.com@evil.example/",
        "ftp://aws.amazon.com/",
        "aws.amazon.com/ko/blogs/tech/",
        "",
        "https://aws.amazon.com:8443/ko/blogs/tech/",
        "https://aws.amazon.com:abc/ko/blogs/tech/",
        "https://aws.amazon.com:99999/",
        "https://user@aws.amazon.com/ko/blogs/tech/",
        "https://user:pw@aws.amazon.com/ko/blogs/tech/",
        "https://aws.amazon.com/ko/blogs/tech\\x/",
        "https://[aws.amazon.com/",
    ],
)
async def test_disallowed_initial_url(ft: FakeTime, url: str) -> None:
    up = Upstream(ft, reply(200))
    async with fetcher_for(up) as fetcher:
        with pytest.raises(UpstreamError) as info:
            await fetcher.get(url, max_bytes=1000)
    assert str(info.value) == f"Refusing to fetch {url}: only https://aws.amazon.com is allowed."
    assert up.requests == []


async def test_explicit_default_port_is_allowed(ft: FakeTime) -> None:
    up = Upstream(ft, reply(200))
    async with fetcher_for(up) as fetcher:
        resp = await fetcher.get("https://aws.amazon.com:443/ko/blogs/tech/", max_bytes=1000)
    assert resp.status == 200
    assert len(up.requests) == 1


async def test_host_check_is_case_insensitive(ft: FakeTime) -> None:
    up = Upstream(ft, reply(200))
    async with fetcher_for(up) as fetcher:
        resp = await fetcher.get("https://AWS.Amazon.COM/ko/blogs/tech/", max_bytes=1000)
    assert resp.status == 200
    assert len(up.requests) == 1


# --------------------------------------------------------------------------------------------
# body size cap
# --------------------------------------------------------------------------------------------


async def test_body_at_limit_is_accepted(ft: FakeTime) -> None:
    up = Upstream(ft, reply(200, b"x" * 100))
    async with fetcher_for(up) as fetcher:
        resp = await fetcher.get(URL, max_bytes=100)
    assert resp.content == b"x" * 100


async def test_body_over_limit_is_rejected(ft: FakeTime) -> None:
    up = Upstream(ft, reply(200, b"x" * 101))
    async with fetcher_for(up) as fetcher:
        with pytest.raises(UpstreamError) as info:
            await fetcher.get(URL, max_bytes=100)
    assert str(info.value) == f"Response from {URL} exceeded the 100 bytes size limit."
    assert len(up.requests) == 1  # not retried
    assert ft.sleeps == []


@pytest.mark.parametrize(
    ("max_bytes", "shown"),
    [(999, "999 bytes"), (1000, "1 KB"), (1500, "1.5 KB"), (64_000, "64 KB"), (2_000_000, "2 MB")],
)
async def test_size_limit_message_uses_readable_units(
    ft: FakeTime, max_bytes: int, shown: str
) -> None:
    up = Upstream(ft, reply(200, b"x" * (max_bytes + 1)))
    async with fetcher_for(up) as fetcher:
        with pytest.raises(UpstreamError) as info:
            await fetcher.get(URL, max_bytes=max_bytes)
    assert str(info.value) == f"Response from {URL} exceeded the {shown} size limit."


async def test_size_cap_stops_reading_a_stream(ft: FakeTime) -> None:
    produced: list[int] = []

    async def chunks() -> AsyncIterator[bytes]:
        for i in range(10):
            produced.append(i)
            yield b"y" * 40

    def step(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=chunks())

    up = Upstream(ft, step)
    async with fetcher_for(up) as fetcher:
        with pytest.raises(UpstreamError, match="size limit"):
            await fetcher.get(URL, max_bytes=100)
    assert produced == [0, 1, 2]  # 120 bytes > 100: stopped right there


async def test_streamed_body_within_limit_is_joined(ft: FakeTime) -> None:
    async def chunks() -> AsyncIterator[bytes]:
        yield b"ab"
        yield b"cd"

    def step(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=chunks())

    up = Upstream(ft, step)
    async with fetcher_for(up) as fetcher:
        resp = await fetcher.get(URL, max_bytes=4)
    assert resp.content == b"abcd"


# --------------------------------------------------------------------------------------------
# throttling
# --------------------------------------------------------------------------------------------


async def test_throttle_spaces_request_starts(ft: FakeTime) -> None:
    up = Upstream(ft, reply(200))
    async with fetcher_for(up, min_interval=0.25) as fetcher:
        for _ in range(3):
            await fetcher.get(URL, max_bytes=1000)
    assert up.started == [1000.0, 1000.25, 1000.5]
    assert ft.sleeps == [0.25, 0.25]


async def test_throttle_does_not_wait_when_enough_time_passed(ft: FakeTime) -> None:
    up = Upstream(ft, reply(200))
    async with fetcher_for(up, min_interval=0.25) as fetcher:
        await fetcher.get(URL, max_bytes=1000)
        ft.now += 0.3
        await fetcher.get(URL, max_bytes=1000)
        ft.now += 0.1
        await fetcher.get(URL, max_bytes=1000)
    assert ft.sleeps == [pytest.approx(0.15)]
    assert up.started == [1000.0, 1000.3, pytest.approx(1000.55)]


async def test_throttle_spaces_concurrent_requests(ft: FakeTime) -> None:
    up = Upstream(ft, reply(200))
    async with fetcher_for(up, min_interval=0.25) as fetcher, anyio.create_task_group() as tg:
        for _ in range(4):
            tg.start_soon(lambda: fetcher.get(URL, max_bytes=1000))
    assert sorted(up.started) == [1000.0, 1000.25, 1000.5, 1000.75]
    assert ft.sleeps == [0.25, 0.25, 0.25]


async def test_throttle_applies_to_redirect_hops(ft: FakeTime) -> None:
    up = Upstream(ft, routes={URL: redirect(OTHER), OTHER: reply(200)})
    async with fetcher_for(up, min_interval=0.5) as fetcher:
        await fetcher.get(URL, max_bytes=1000)
    assert up.started == [1000.0, 1000.5]


async def test_throttle_tops_up_short_backoff(
    ft: FakeTime, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("aws_blog_mcp.http.random.uniform", lambda a, b: 0.0)
    up = Upstream(ft, reply(503), reply(200))
    async with fetcher_for(up, min_interval=1.0, backoff_base=0.1) as fetcher:
        await fetcher.get(URL, max_bytes=1000)
    assert ft.sleeps == [0.1, pytest.approx(0.9)]
    assert up.started == [1000.0, pytest.approx(1001.0)]


async def test_zero_interval_never_sleeps(ft: FakeTime) -> None:
    up = Upstream(ft, reply(200))
    async with fetcher_for(up, min_interval=0.0) as fetcher:
        for _ in range(5):
            await fetcher.get(URL, max_bytes=1000)
    assert ft.sleeps == []


# --------------------------------------------------------------------------------------------
# user agent and client factory
# --------------------------------------------------------------------------------------------


def test_default_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(USER_AGENT_ENV, raising=False)
    assert user_agent() == f"aws-blog-mcp/{__version__} (+{PROJECT_URL})"
    assert PROJECT_URL == "https://github.com/anhyobin/aws-blog-mcp"


def test_user_agent_env_override_is_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(USER_AGENT_ENV, "  my-bot/1.0 (ops@example.com)  ")
    assert user_agent() == "my-bot/1.0 (ops@example.com)"


@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_blank_user_agent_override_is_ignored(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(USER_AGENT_ENV, value)
    assert user_agent() == f"aws-blog-mcp/{__version__} (+{PROJECT_URL})"


@pytest.mark.parametrize(
    "value",
    [
        "봇/1.0",
        "bot/1.0 (café)",
        "bot/1.0\r\nX-Injected: 1",
        "bot/1.0\nsecond line",
        "bot\t1.0",
        "bot\x7f",
        "bot\x1b[31m",
    ],
    ids=["hangul", "latin-1", "crlf", "lf", "tab", "del", "escape"],
)
def test_invalid_user_agent_override_is_ignored_with_a_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, value: str
) -> None:
    monkeypatch.setenv(USER_AGENT_ENV, value)
    with caplog.at_level(logging.WARNING, logger="aws_blog_mcp.http"):
        assert user_agent() == f"aws-blog-mcp/{__version__} (+{PROJECT_URL})"
    assert caplog.messages == [
        f"Ignoring {USER_AGENT_ENV}: it must be printable ASCII without line breaks"
    ]


def test_valid_user_agent_override_logs_nothing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(USER_AGENT_ENV, "ops-bot/1.0 (+https://example.com; ops@example.com)")
    with caplog.at_level(logging.WARNING, logger="aws_blog_mcp.http"):
        assert user_agent() == "ops-bot/1.0 (+https://example.com; ops@example.com)"
    assert caplog.messages == []


async def test_client_with_invalid_user_agent_override_still_works(
    ft: FakeTime, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(USER_AGENT_ENV, "bad\r\nX-Injected: 1")
    up = Upstream(ft, reply(200))
    async with fetcher_for(up) as fetcher:
        await fetcher.get(URL, max_bytes=1000)
    assert up.requests[0].headers["user-agent"] == f"aws-blog-mcp/{__version__} (+{PROJECT_URL})"
    assert "x-injected" not in up.requests[0].headers


async def test_client_sends_overridden_user_agent(
    ft: FakeTime, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(USER_AGENT_ENV, "custom-agent/2.0")
    up = Upstream(ft, reply(200))
    async with fetcher_for(up) as fetcher:
        await fetcher.get(URL, max_bytes=1000)
    assert up.requests[0].headers["user-agent"] == "custom-agent/2.0"


async def test_create_http_client_settings() -> None:
    async with create_http_client(httpx2.MockTransport(reply(200))) as client:
        assert client.follow_redirects is False
        assert client.timeout == httpx2.Timeout(20.0, connect=5.0)
        assert client.headers["accept-encoding"] == "gzip"
