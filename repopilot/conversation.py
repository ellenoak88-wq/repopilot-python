"""Compact older turns for use alongside the recent conversation window."""
import re

from .retrieval import search

RECENT_TURNS = 4
HISTORY_LIMIT = 104
SUMMARY_TURNS = 6
RECENCY_TURNS = 2

CITATION = re.compile(r"\[[SM]\d+\]")
PARAGRAPH = re.compile(r"\n\s*\n|(?<=[。！？!?])\s*")


def excerpt(text, limit):
    normalized = " ".join(CITATION.sub("", text).split())
    return normalized if len(normalized) <= limit else normalized[:limit - 1].rstrip() + "…"


def summarize(turns, question):
    """Return the selected turns and a concise, chronologically ordered snapshot."""
    if not turns:
        return [], None

    candidates = [
        {"path": turn["question"], "text": turn["answer"], "turn": turn}
        for turn in turns[:-RECENCY_TURNS]
    ]
    ranked = search(candidates, question, SUMMARY_TURNS - RECENCY_TURNS)
    selected = {hit["turn"]["id"]: hit["turn"] for hit in ranked}
    # Reserve space for recent context even when a follow-up has few search terms.
    for turn in reversed(turns):
        if len(selected) >= SUMMARY_TURNS:
            break
        selected[turn["id"]] = turn

    chosen = sorted(selected.values(), key=lambda turn: turn["id"])
    topics = []
    for turn in chosen:
        passages = [
            {"path": "", "text": part.strip()}
            for part in PARAGRAPH.split(turn["answer"])
            if part.strip()
        ]
        matching = search(passages, question, 1)
        answer = matching[0]["text"] if matching else turn["answer"]
        topics.append({"question": excerpt(turn["question"], 240), "answer": excerpt(answer, 420)})
    return chosen, {"olderTurns": len(turns), "topics": topics}
