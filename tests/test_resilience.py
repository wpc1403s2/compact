import pytest

from src.resilience import resilient_call


@pytest.mark.asyncio
async def test_resilient_call_does_not_retry_by_default():
    calls = 0

    async def fail():
        nonlocal calls
        calls += 1
        raise ValueError("failure")

    with pytest.raises(ValueError, match="failure"):
        await resilient_call(fail)

    assert calls == 1
