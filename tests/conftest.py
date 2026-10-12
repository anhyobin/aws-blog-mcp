from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from mcp import Client

from aws_blog_mcp.server import create_server
from tests.fakes import FakeAws


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="run tests that call the real aws.amazon.com",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-live"):
        return
    skip_live = pytest.mark.skip(reason="needs --run-live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def fake_aws() -> FakeAws:
    return FakeAws()


@pytest.fixture(params=["auto", "legacy"])
async def client(request: pytest.FixtureRequest, fake_aws: FakeAws) -> AsyncIterator[Client]:
    """An in-process MCP client, once per protocol mode (2026 discovery and legacy handshake)."""
    server = create_server(transport=fake_aws.transport(), request_interval=0)
    async with Client(server, raise_exceptions=True, mode=request.param) as c:
        yield c
