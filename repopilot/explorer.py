"""Read-only searches over indexed repository files; no model or filesystem I/O."""
from .retrieval import chunk_file, search, tokenize


def _preview(lines, start, end, focus, query):
    """A bounded line-numbered preview near the best match, retaining its context."""
    first = max(start, focus - 2)
    last = min(end, first + 7)
    result = []
    for number in range(first, last + 1):
        text = lines[number - 1]
        position = text.lower().find(query.lower())
        offset = max(0, position - 80) if position >= 0 else 0
        excerpt = text[offset:offset + 320]
        result.append({"number": number, "text": ("…" if offset else "") + excerpt + ("…" if offset + 320 < len(text) else "")})
    return result


def search_files(files, query, *, mode="literal", path_filter="", limit=20):
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 200:
        raise ValueError("搜索内容需为 1–200 字。")
    if mode not in ("literal", "ranked"):
        raise ValueError("不支持的搜索模式。")
    if not isinstance(path_filter, str) or len(path_filter) > 200:
        raise ValueError("路径筛选最多 200 字。")
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("结果数量需为 1–50。")
    query, path_filter = query.strip(), path_filter.strip()
    scoped = sorted((f for f in files if path_filter.lower() in f["path"].lower()), key=lambda f: f["path"])
    lines_by_path = {f["path"]: f["content"].split("\n") for f in scoped}
    hits = []
    if mode == "literal":
        for file in scoped:
            lines = lines_by_path[file["path"]]
            matching = [i + 1 for i, line in enumerate(lines) if query.lower() in line.lower()]
            for number in matching:
                # Merge nearby matching lines instead of repeating overlapping snippets.
                if hits and hits[-1]["path"] == file["path"] and number - hits[-1]["focusEnd"] <= 4:
                    hits[-1]["focusEnd"] = number
                    hits[-1]["end"] = min(len(lines), number + 2)
                    hits[-1]["matchedLines"].append(number)
                else:
                    hits.append({"path": file["path"], "start": max(1, number - 2), "end": min(len(lines), number + 2),
                                 "focusStart": number, "focusEnd": number, "matchedLines": [number]})
    else:
        chunks = [c for f in scoped for c in chunk_file(f["path"], f["content"])]
        candidates = search(chunks, query, len(chunks))
        for chunk in candidates:
            # Overlapping chunks of one file should not dominate the results.
            if any(h["path"] == chunk["path"] and max(h["start"], chunk["start"]) <= min(h["end"], chunk["end"]) for h in hits):
                continue
            hits.append({k: chunk[k] for k in ("path", "start", "end", "score")})
    results = []
    for hit in hits[:limit]:
        lines = lines_by_path[hit["path"]]
        terms = set(tokenize(query))
        focus = hit.get("focusStart") or max(range(hit["start"], hit["end"] + 1), key=lambda n: len(terms.intersection(tokenize(lines[n - 1]))))
        results.append({**hit, "focusStart": hit.get("focusStart", focus), "focusEnd": hit.get("focusEnd", focus),
                        "preview": _preview(lines, hit["start"], hit["end"], focus, query)})
    return {"query": query, "mode": mode, "pathFilter": path_filter, "results": results, "total": len(hits),
            "fileCount": len({h["path"] for h in hits}), "searchedFiles": len(scoped), "truncated": len(hits) > limit}
