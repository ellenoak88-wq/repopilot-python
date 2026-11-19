from repopilot.chunking import chunk_file


def test_local_arrow_and_assertions_share_the_same_test_case():
    code = """test('hidden panel', () => {
  const display = (panel) => panel.display;
  assert.equal(display(panel), 'none');
});
test('second', () => {});"""
    chunks = chunk_file("panel.test.mjs", code)
    assert [(c["start"], c["end"]) for c in chunks] == [(1, 4), (5, 5)]
    assert "hidden panel" in chunks[0]["scope"]
