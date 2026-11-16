import os
import re

import httpx

FAMILY_EFFORTS = ["low", "medium", "high", "xhigh", "max", "ultra"]
# These are editable provider presets, not a claim of availability in an account.
MODEL_OPTIONS = [
    {"id": f"gpt-{version}-{family}", "label": f"GPT-{version} {family} · 预设",
     "efforts": FAMILY_EFFORTS[:-1] if family == "luna" else FAMILY_EFFORTS[:]}
    for version in ("6.1", "6", "5.6") for family in ("sol", "astra", "luna", "terra")
] + [
    {"id": "gpt-5.4-mini", "label": "GPT-5.4 mini", "efforts": ["none", "low", "medium", "high", "xhigh"]},
    {"id": "gpt-5.4", "label": "GPT-5.4", "efforts": ["none", "low", "medium", "high", "xhigh"]},
    {"id": "gpt-4.1-mini", "label": "GPT-4.1 mini", "efforts": []},
]


def model_config():
    return {"key": os.getenv("OPENAI_API_KEY", ""), "base": os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"), "model": os.getenv("LLM_MODEL", "gpt-5.4-mini")}


def valid_model(model):
    return isinstance(model, str) and bool(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,127}", model))


def uses_responses(model):
    match = re.match(r"^gpt-(\d+)(?:[.-]|$)", model)
    return bool(match and int(match[1]) >= 5)


def model_spec(model):
    return next((m for m in MODEL_OPTIONS if m["id"] == model), {"id": model, "label": model, "efforts": FAMILY_EFFORTS[:] if uses_responses(model) else []})


def select_model(model=None, effort=None, base=None):
    base = base or model_config()
    if model is not None and not isinstance(model, str):
        raise ValueError("模型 ID 格式无效。")
    if effort is not None and not isinstance(effort, str):
        raise ValueError("思考强度格式无效。")
    selected = model or base["model"]
    if not valid_model(selected):
        raise ValueError("模型 ID 格式无效。")
    reasoning = effort or "default"
    if reasoning != "default" and reasoning not in model_spec(selected)["efforts"]:
        raise ValueError("该模型不支持所选思考强度。")
    return {**base, "model": selected, "effort": reasoning}


async def discover_models(config=None):
    config = config or model_config()
    if not config["key"]:
        raise ValueError("请先配置模型 API Key。")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(f"{config['base']}/models", headers={"Authorization": f"Bearer {config['key']}"})
    except httpx.HTTPError as e:
        raise ValueError("无法读取服务商模型列表。预设及手动输入仍可使用。") from e
    if not response.is_success:
        raise ValueError(f"读取模型列表失败 ({response.status_code})。预设不代表当前账号已开通。")
    data = response.json().get("data")
    if not isinstance(data, list):
        raise ValueError("服务商返回了无效的模型列表。")
    models = {}
    for item in data:
        if not isinstance(item, dict) or not valid_model(item.get("id")):
            continue
        mid = item["id"]
        if re.search(r"image|realtime|live", mid, re.I):
            continue
        created = item.get("created")
        # Keep provider creation time; do not present it as an official release date.
        created = created if type(created) in (int, float) and 0 < created < 253402300800 else None
        models[mid] = {**model_spec(mid), "label": mid + " · 接口返回", "discovered": True, "created": created}
    return sorted(models.values(), key=lambda m: (-(m["created"] or 0), m["id"]))
