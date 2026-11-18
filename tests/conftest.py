import httpx
import pytest

from repopilot.agent import ask_agent


@pytest.fixture
def offline_agent():
    def provider(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "测试回答 [S1]"}}]})

    async def agent(**kwargs):
        kwargs["config"] = {"key": "fixture", "model": "gpt-4.1-mini", "base": "https://model.test"}
        return await ask_agent(**kwargs, transport=httpx.MockTransport(provider))

    return agent

