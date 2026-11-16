import json

import httpx
import pytest

from repopilot.agent import ask_agent
from repopilot.models import MODEL_OPTIONS, select_model
from repopilot.retrieval import chunk_file
from repopilot.stream import consume_provider_stream, read_sse

REPO = {"id": "test/repo", "demo": True, "sha": "test"}
FILES = [{"path": "README.md", "content": "# Install\nRun python -m app"}]
CHUNKS = chunk_file(**FILES[0])


def test_model_validation():
    assert len([m for m in MODEL_OPTIONS if m["id"].endswith("-sol")]) == 3
    assert select_model("gpt-6.1-astra", "ultra")["effort"] == "ultra"
    with pytest.raises(ValueError):
        select_model("gpt-6-luna", "ultra")
    with pytest.raises(ValueError):
        select_model("bad model")


async def test_extractive_never_calls_model():
    def blocked(_):
        raise AssertionError("Network called")
    result = await ask_agent(repo=REPO, files=FILES, chunks=CHUNKS, question="安装", force_extractive=True,
                             config={"key": "fake"}, transport=httpx.MockTransport(blocked))
    assert result["mode"] == "extractive" and "[S1]" in result["answer"]


async def test_sse_split_utf8_crlf_and_multiple_data_lines():
    raw = ': ping\r\ndata: {"text":\r\ndata: "中文"}\r\n\r\n'.encode()
    async def chunks():
        for byte in raw:
            yield bytes([byte])
    result = [json.loads(event) async for event in read_sse(chunks())]
    assert result == [{"text": "中文"}]


def sse_response(events):
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content="".join("data: " + json.dumps(e) + "\n\n" for e in events).encode())


@pytest.mark.parametrize("responses_api,events", [(True, [{"type": "response.output_text.delta", "delta": "partial"}]),
    (False, [{"choices": [{"delta": {"content": "partial"}}]}]),
    (True, [{"type": "response.incomplete"}])])
async def test_partial_stream_rejected(responses_api, events):
    with pytest.raises(ValueError):
        await consume_provider_stream(sse_response(events), responses_api, lambda _: None)


async def test_responses_tool_round_preserves_reasoning_and_citations():
    requests, events = [], []
    reasoning = {"type": "reasoning", "id": "r1", "encrypted_content": "opaque", "summary": []}
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert request.url.path == "/v1/responses"
        assert body["reasoning"] == {"effort": "high"}
        if len(requests) == 1:
            return sse_response([{"type": "response.completed", "response": {"status": "completed", "output": [reasoning,
                {"type": "function_call", "call_id": "c1", "name": "read_file", "arguments": json.dumps({"path": "README.md", "start": 1})}]}}])
        assert reasoning in body["input"]
        assert body["input"][-1]["type"] == "function_call_output"
        return sse_response([{"type": "response.output_text.delta", "delta": "安装 [S1] [S999]"},
            {"type": "response.completed", "response": {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "安装 [S1] [S999]"}]}]}}])
    result = await ask_agent(repo=REPO, files=FILES, chunks=CHUNKS, question="如何安装", config={"key": "fake", "base": "https://provider.test/v1", "model": "gpt-6.1-sol", "effort": "high"}, on_event=events.append, transport=httpx.MockTransport(handler))
    assert len(requests) == 2
    assert result["answer"] == "安装 [S1] [引用无效]"
    assert any(e["type"] == "delta" for e in events)


async def test_chat_completions_tool_argument_fragments():
    result = await consume_provider_stream(sse_response([
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call", "function": {"name": "search_code", "arguments": '{"query":'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"start"}'}}]}, "finish_reason": "tool_calls"}]},
    ]), False, lambda _: None)
    call = result["choices"][0]["message"]["tool_calls"][0]
    assert call["id"] == "call" and json.loads(call["function"]["arguments"]) == {"query": "start"}


async def test_chat_completions_json_fallback():
    def handler(request):
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(200, json={"choices": [{"message": {"content": "启动 [S1]"}, "finish_reason": "stop"}]})
    result = await ask_agent(repo=REPO, files=FILES, chunks=CHUNKS, question="安装", config={"key": "fake", "base": "https://provider.test/v1", "model": "gpt-4.1-mini"}, transport=httpx.MockTransport(handler))
    assert result["answer"] == "启动 [S1]"
