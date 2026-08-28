from __future__ import annotations

import pytest

from roborock_mcp.fake_gateway import FakeGateway


@pytest.fixture
def fake_gateway() -> FakeGateway:
    return FakeGateway()
