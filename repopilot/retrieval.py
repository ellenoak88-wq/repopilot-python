"""Deterministic BM25 retrieval with Chinese query expansion and line citations."""
import math
import re
from collections import Counter

ALIASES = [
    (r"启动|安装|运行|上手|入门", "readme install installation setup getting started quickstart usage scripts start"),
    (r"架构|结构|模块|入口", "architecture structure entry main index app server routes modules"),
    (r"测试|验证", "test tests testing spec assert"),
    (r"贡献|修改|开发", "contributing development guidelines pull request"),
    (r"路由|请求", "router routing request handle middleware"),
    (r"配置|环境", "configuration config environment env settings"),
]


def tokenize(text: str) -> list[str]:
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text).lower()
    return re.findall(r"[a-z0-9_]+|[\u3400-\u9fff\U00020000-\U000323af]", text)


def chunk_file(path: str, content: str, size: int = 65, overlap: int = 12) -> list[dict]:
    if not 0 <= overlap < size:
        raise ValueError("overlap must be smaller than chunk size")
    lines, chunks = content.split("\n"), []
    for i in range(0, len(lines), size - overlap):
        chunks.append(dict(path=path, start=i + 1, end=min(i + size, len(lines)), text="\n".join(lines[i:i + size])))
        if i + size >= len(lines):
            break
    return chunks


def search(chunks: list[dict], query: str, limit: int = 6) -> list[dict]:
    expanded = query + " " + " ".join(words for pattern, words in ALIASES if re.search(pattern, query))
    terms = set(tokenize(expanded))
    docs = [tokenize(c["path"] + " " + c["path"] + " " + c["text"]) for c in chunks]
    avg = sum(map(len, docs)) / (len(docs) or 1)
    counts = [Counter(d) for d in docs]
    dfs = {t: sum(t in c for c in counts) for t in terms}
    results = []
    for chunk, doc, freq in zip(chunks, docs, counts):
        score = sum(math.log(1 + (len(docs) - dfs[t] + .5) / (dfs[t] + .5)) * freq[t] * 2.2 /
                    (freq[t] + 1.2 * (.25 + .75 * len(doc) / (avg or 1))) for t in terms)
        if score > 0:
            results.append({**chunk, "score": score})
    return sorted(results, key=lambda c: -c["score"])[:limit]
