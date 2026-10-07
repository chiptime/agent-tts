"""Reader-pipeline coverage tail anchoring (GSR-06 regression).

Regression input minimized from a real Brain reader-cache envelope
(alignment ``coverage``): after a markdown table, the global engine clean
glues the table region into one paragraph while per-block projections do
not, so cumulative word-count windows drift right past block boundaries.
The final paragraph then overlaps two sentence windows while containing a
single raw sentence (the closing ``?**`` shields the raw split), and the
block/fragment assignment handed the WHOLE final paragraph to the earlier
sentence's drift sliver, leaving the last oracle sentence an empty virtual
anchor. The reader frontend then highlighted both tail paragraphs at once
("all remaining text turns green" after clicking the protected-session
sentence).
"""

from __future__ import annotations

import reader_pipeline as rp
from agent_tts import estimate_boundaries_from_text

# Minimized standalone regression input: keeps the reported trigger
# sentence, the glue-producing table, and the bold-question markup
# condition on the final paragraph, without persisting the private
# conversation.
REGRESSION_INPUT = (
    "Resumen del inventario de sesiones y memoria que pediste.\n\n"
    "| PID | Terminal | RAM |\n"
    "|---|---|---:|\n"
    "| `1925774` | `pts/22` | 984 MiB |\n"
    "| `1123424` | `pts/8` | 947 MiB |\n"
    "| `307418` | `pts/44` | 822 MiB |\n\n"
    "Esta sesión queda protegida, junto con Whisper y el servidor de síntesis.\n"
    "Las terminales antiguas de las pruebas consumen muy poco; cerrarlas apenas ayudaría.\n\n"
    "**¿Cuáles de estas sesiones puedes cerrar sin interrumpir trabajo que quieras conservar?** "
    "Indícame sus PID o terminales; si todas siguen trabajando, no cerraremos ninguna."
)

TAIL_SENTENCE = (
    "Las terminales antiguas de las pruebas consumen muy poco; "
    "cerrarlas apenas ayudaría."
)


def _span_inner_by_id(html: str, sent_idx: int) -> str:
    """Inner text of the primary span ``id="tts-sent-<idx>"`` (no nested
    spans exist in this fixture's primary spans)."""
    marker = f'id="tts-sent-{sent_idx}"'
    start = html.index(marker)
    open_end = html.index(">", start) + 1
    return html[open_end:html.index("</span>", open_end)]


def test_coverage_tail_sentences_get_distinct_nonempty_anchors():
    result = rp.render(REGRESSION_INPUT, lang="es", pre_extracted=True)
    assert result.alignment == "coverage"  # glue is real; fix must not fake exact
    anchors = result.anchors
    assert len(anchors) == 4

    # No oracle sentence may be left an empty virtual anchor when its
    # content exists in the document (last sentence owned the final
    # paragraph under drift).
    assert all(a.fragments for a in anchors), [
        (a.sent_idx, a.fragments) for a in anchors
    ]

    # The tail sentence owns exactly its own text — not the whole final
    # paragraph as an extra continuation fragment.
    tail = next(a for a in anchors if a.text.startswith("Las terminales"))
    assert len(tail.fragments) == 1
    assert tail.fragment_texts == (TAIL_SENTENCE,)

    # The final oracle sentence owns the bold-question paragraph.
    last = anchors[-1]
    assert len(last.fragments) == 1
    assert last.text.startswith("**¿Cuáles")
    assert last.text.endswith("no cerraremos ninguna.")


def test_rendered_html_anchors_tail_paragraphs_separately():
    result = rp.render(REGRESSION_INPUT, lang="es", pre_extracted=True)
    html = result.html
    tail = next(a for a in result.anchors if a.text.startswith("Las terminales"))
    last = result.anchors[-1]

    tail_html = _span_inner_by_id(html, tail.sent_idx)
    last_html = _span_inner_by_id(html, last.sent_idx)

    assert "Las terminales antiguas" in tail_html
    assert "¿Cuáles" not in tail_html  # whole-answer green defect
    assert "¿Cuáles" in last_html
    assert "no cerraremos ninguna." in last_html


def test_table_glue_continuation_span_preserved():
    # Legitimate coverage continuation: the glued table sentence keeps its
    # table fragment PLUS the following prose sentence fragment.
    anchors = rp.build_anchors(REGRESSION_INPUT, lang="es")
    glue = anchors[1]
    assert len(glue.fragments) == 2
    assert glue.fragment_texts[0].startswith("| PID")
    assert "Esta sesión queda protegida" in glue.fragment_texts[1]


def test_coverage_oracle_parity_preserved():
    # Spec contract: count, order, and paragraph mapping stay verbatim
    # from the oracle enumeration even after the assignment fix.
    result = rp.render(REGRESSION_INPUT, lang="es", pre_extracted=True)
    oracle = estimate_boundaries_from_text(result.normalized, 1.0)
    assert [(a.sent_idx, a.para_idx) for a in result.anchors] == [
        (s.index, s.paragraph_index) for s in oracle.sentences
    ]


def test_exact_prose_alignment_unchanged():
    result = rp.render(
        "Primera frase del parrafo uno. Segunda frase del parrafo uno.\n\n"
        "Tercera frase del parrafo dos.",
        lang="es",
        pre_extracted=True,
    )
    assert result.alignment == "exact"
    assert [a.sent_idx for a in result.anchors] == [0, 1, 2]
    assert all(a.fragments and a.exact for a in result.anchors)


def test_exact_code_span_keeps_start_owner_fallback():
    # Verifier blocker regression: the raw line has ONE sentence (the "."
    # inside `foo.` is followed by a backtick, so `(?<=[.!?])\s+` cannot
    # split there) while the cleaned projection splits into two exact
    # spans. The counts-disagree fallback must keep the historical exact
    # behavior: the block belongs to the sentence covering its start
    # (sent 0 whole fragment, sent 1 virtual), NOT the largest-overlap
    # window — that rule is scoped to coverage alignment only.
    result = rp.render("Read `foo.` Then test done.", lang="es", pre_extracted=True)
    assert result.alignment == "exact"
    first, second = result.anchors[0], result.anchors[1]
    assert first.fragments == ((0, 27),)
    assert first.text == "Read `foo.` Then test done."
    assert second.fragments == ()


def test_build_is_deterministic():
    first = rp.build_anchors(REGRESSION_INPUT, lang="es")
    second = rp.build_anchors(REGRESSION_INPUT, lang="es")
    assert first == second
