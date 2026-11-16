"""Incremental UTF-8 SSE parsing and OpenAI-compatible stream assembly."""
import codecs
import json

MAX_EVENT = 2_000_000


async def read_sse(chunks):
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    buffer, lines, event_size = "", [], 0
    async for chunk in chunks:
        buffer += decoder.decode(chunk)
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.removesuffix("\r")
            event_size += len(line)
            if event_size > MAX_EVENT:
                raise ValueError("模型流式事件过大。")
            if not line:
                if lines:
                    yield "\n".join(lines)
                lines, event_size = [], 0
            elif line.startswith("data:"):
                lines.append(line[5:].removeprefix(" "))
        if len(buffer) + event_size > MAX_EVENT:
            raise ValueError("模型流式事件过大。")
    decoder.decode(b"", final=True)
    # A partial event at EOF must not be treated as a completed answer.


async def consume_provider_stream(response, responses_api, on_delta):
    text, calls, completed, final_response = "", {}, False, None
    async for event in read_sse(response.aiter_bytes()):
        if event == "[DONE]":
            break
        data = json.loads(event)
        if data.get("error") or data.get("type") in ("error", "response.failed"):
            raise ValueError("模型流式响应失败，请重试或更换模型。")
        if responses_api:
            if data.get("type") in ("response.output_text.delta", "response.refusal.delta"):
                on_delta(data.get("delta", ""))
            if data.get("type") == "response.incomplete":
                raise ValueError("模型输出达到预算上限，请降低思考强度或缩小问题。")
            if data.get("type") == "response.completed":
                final_response, completed = data.get("response"), True
        else:
            choice = (data.get("choices") or [{}])[0]
            delta = choice.get("delta") or {}
            if delta.get("content"):
                text += delta["content"]
                on_delta(delta["content"])
            for part in delta.get("tool_calls", []):
                call = calls.setdefault(part["index"], {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                if part.get("id"):
                    call["id"] = part["id"]
                for name in ("name", "arguments"):
                    call["function"][name] += part.get("function", {}).get(name, "")
            if choice.get("finish_reason") in ("length", "content_filter"):
                raise ValueError("模型输出未完成，请缩小问题或更换模型。")
            if choice.get("finish_reason"):
                completed = True
    if not completed or (responses_api and not final_response):
        raise ValueError("模型连接提前中断，部分回答未保存，请重试。")
    return final_response if responses_api else {"choices": [{"message": {"content": text, "tool_calls": list(calls.values())}}]}
