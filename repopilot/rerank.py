"""Second-stage scoring over a bounded pool of repository search results."""
import re


IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_./-]*")
IMPLEMENTATION = re.compile(r"实现|定义|校验|保存|写入|调用|调用链|源码|implement|defin(?:e|ition)", re.I)
DOCUMENTATION = re.compile(r"文档|使用说明|上手|安装|怎么用|readme|documentation|quickstart", re.I)
TESTS = re.compile(r"测试|用例|\btests?\b", re.I)


def rerank(query, candidates, limit):
    # Exact path and symbol matches carry more information than prose words.
    tokens = [token.rstrip(".") for token in IDENTIFIER.findall(query)]
    names = {t.lower() for t in tokens if any(c in t for c in "._/") or re.search(r"[a-z][A-Z]", t)}
    names.update(t.lower() for t in re.findall(r"`([\w./-]{1,160})`", query))
    wants_docs = bool(DOCUMENTATION.search(query))
    wants_code = bool(IMPLEMENTATION.search(query)) and not wants_docs
    wants_tests = bool(TESTS.search(query))
    if wants_code or len(tokens) == 1:
        names.update(token.lower() for token in tokens if len(token) > 2)
    symbols = sorted((n for n in names if re.fullmatch(r"[a-z_]\w*", n)), key=len, reverse=True)
    definition = None
    if symbols:
        definition = re.compile(
            r"\b(?:def|class|function|fn|func|struct|interface|type|const|let)\s+("
            + "|".join(map(re.escape, symbols)) + r")\b", re.I)
    ranked = []
    for rank, chunk in enumerate(candidates, 1):
        path = chunk["path"].lower()
        basename = path.rsplit("/", 1)[-1]
        body = chunk["text"]
        is_doc = path.endswith((".md", ".mdx", ".rst", ".txt"))
        is_test = bool(re.search(r"(?:^|/)(?:tests?/|test_)|[._](?:test|spec)\.", path))
        # The rank prior keeps vector-only hits competitive when there are no exact clues.
        score = 1 / (1 + 0.12 * (rank - 1))
        if path in names or basename in names:
            score += 1.0
        if definition and not wants_docs and not wants_tests and not is_doc and definition.search(body):
            score += 0.8
        if wants_code and not is_doc and (not is_test or wants_tests):
            score += 0.28
        if (wants_docs and is_doc) or (wants_tests and is_test):
            score += 0.3
        ranked.append({**chunk, "retrievalScore": chunk["score"], "retrievalRank": rank, "score": score})
    return sorted(ranked, key=lambda chunk: -chunk["score"])[:limit]
