from repopilot.query_rewrite import rewrite_query


def test_preserves_original_symbols_and_adds_search_vocabulary():
    query = "src/store.js 的 `addNote` 在哪里校验标题？"
    result = rewrite_query(query)
    assert result.semantic == query
    assert result.lexical.startswith(query)
    assert {"validate", "title"} <= set(result.terms)


def test_context_only_for_followups_and_bounded():
    previous = "HybridRetrieval 的缓存在哪里？"
    followup = rewrite_query("那它什么时候过期？", previous)
    assert followup.contextual and previous in followup.semantic
    assert {"cache", "stale"} <= set(followup.terms)
    standalone = rewrite_query("如何配置新的仓库？", previous)
    assert not standalone.contextual and previous not in standalone.lexical
    assert rewrite_query("它在哪？", "x" * 300).semantic.endswith("x" * 240)
