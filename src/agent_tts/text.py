"""Shared text segmentation helpers for synthesis orchestration and providers."""

import re
from typing import List


def split_sentence_groups(text: str, max_chars: int = 250) -> List[str]:
    """Splits text into greedy sentence groups of at most max_chars characters for pipelined synthesis."""
    if not text or not text.strip():
        return []

    raw_sentences: List[str] = []
    for paragraph in text.strip().splitlines():
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        raw_sentences.extend(s.strip() for s in re.split(r"(?<=[.!?])\s+", paragraph) if s.strip())
    if not raw_sentences:
        raw_sentences = [text.strip()]

    # Very long single sentences may be split on commas when they exceed twice the budget.
    sentences: List[str] = []
    for sent in raw_sentences:
        if len(sent) > max_chars * 2:
            pieces = [p.strip() for p in sent.split(", ") if p.strip()]
            sentences.extend(pieces if pieces else [sent])
        else:
            sentences.append(sent)

    groups: List[str] = []
    current = ""
    for sent in sentences:
        if current and len(current) + 1 + len(sent) <= max_chars:
            current = f"{current} {sent}"
        else:
            if current:
                groups.append(current)
            current = sent
    if current:
        groups.append(current)
    return groups
