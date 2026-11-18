import asyncio
import os
import re

import httpx

DEEPSEEK_FLASH = "deepseek-flash"
DEEPSEEK_BASE = "https://api.deepseek.com"
FAMILY_EFFORTS = ["low", "medium", "high", "xhigh", "max", "ultra"]
# These are editable provider presets, not a claim of availability in an account.
MODEL_OPTIONS = [
    {"id": "gpt-6.2-sol", "label": "GPT-6.2 Sol · 预设",
     "efforts": ["low", "medium", "high", "xhigh", "max"], "releaseDate": "2026-11-17"},
    {"id": "gpt-6.2-astra", "label": "GPT-6.2 Astra · 预设",
     "efforts": ["low", "medium", "high", "xhigh", "max"], "releaseDate": "2026-11-17"},
    {"id": DEEPSEEK_FLASH, "label": "DeepSeek V4.1 Flash · 预设",
     "efforts": ["none", "low", "high", "max"], "releaseDate": "2026-09-10"},
] + [
    {"id": f"gpt-{version}-{family}", "label": f"GPT-{version} {family} · 预设",
     "efforts": FAMILY_EFFORTS[:-1] if family == "luna" else FAMILY_EFFORTS[:]}
    for version in ("6.1", "6", "5.6") for family in ("sol", "astra", "luna", "terra")
] + [
    {"id": "gpt-5.4-mini", "label": "GPT-5.4 mini", "efforts": ["none", "low", "medium", "high", "xhigh"]},
    {"id": "gpt-5.4", "label": "GPT-5.4", "efforts": ["none", "low", "medium", "high", "xhigh"]},
    {"id": "gpt-4.1-mini", "label": "GPT-4.1 mini", "efforts": []},
]


def model_config():
    key = os.getenv("OPENAI_API_KEY", "")
    deepseek_key = os.getenv("DEEPSEEK_API_KEY", "")
    default_model = DEEPSEEK_FLASH if deepseek_key and not key else "gpt-5.4-mini"
    return {"key": key, "base": os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            "model": os.getenv("LLM_MODEL") or default_model, "deepseek_key": deepseek_key}


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
    config = {**base, "model": selected, "effort": reasoning}
    if selected == DEEPSEEK_FLASH and base.get("deepseek_key"):
        config.update(base=DEEPSEEK_BASE, key=base["deepseek_key"])
    return config


async def discover_models(config=None):
    config = config or model_config()
    providers = []
    if config["key"]:
        providers.append((config["base"], config["key"], None))
    if config.get("deepseek_key"):
        providers.append((DEEPSEEK_BASE, config["deepseek_key"], DEEPSEEK_FLASH))
    if not providers:
        raise ValueError("请先配置模型 API Key。")

    async with httpx.AsyncClient(timeout=15) as client:
        results = await asyncio.gather(
            *(_list_provider_models(client, base, key) for base, key, _ in providers),
            return_exceptions=True,
        )
    if all(isinstance(result, Exception) for result in results):
        raise results[0]
    models = {}
    for (_, _, only_model), result in zip(providers, results):
        if isinstance(result, list):
            models.update({model["id"]: model for model in result
                           if only_model is None or model["id"] == only_model})
    return sorted(models.values(), key=lambda m: (-(m["created"] or 0), m["id"]))


async def _list_provider_models(client, base, key):
    try:
        response = await client.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError as e:
        raise ValueError("无法读取服务商模型列表。预设及手动输入仍可使用。") from e
    if not response.is_success:
        raise ValueError(f"读取模型列表失败 ({response.status_code})。预设不代表当前账号已开通。")
    data = response.json().get("data")
    if not isinstance(data, list):
        raise ValueError("服务商返回了无效的模型列表。")
    models = []
    for item in data:
        if not isinstance(item, dict) or not valid_model(item.get("id")):
            continue
        mid = item["id"]
        if re.search(r"image|realtime|live", mid, re.I):
            continue
        created = item.get("created")
        # Keep provider creation time; do not present it as an official release date.
        created = created if type(created) in (int, float) and 0 < created < 253402300800 else None
        spec = model_spec(mid)
        label = spec["label"].removesuffix(" · 预设") if spec.get("releaseDate") else mid
        models.append({**spec, "label": label + " · 接口返回", "discovered": True, "created": created})
    return models
