"""Sentence chunker unit tests (W3-P1, PRD-06 FR-05).

The chunker is the deterministic, TTS-safe boundary finder that turns
streamed GLM tokens into synthesis-ready sentences. Adversarial cases
come straight from the PRD's V1 list: abbreviations, decimals, code
spans, lists and Spanish punctuation — plus the streaming shape (text
arrives in token-sized pieces, so every property must hold under
incremental feeding, not only whole-text calls).
"""

from __future__ import annotations

import pytest

from herdr_brain.sentence_chunker import SentenceChunker


def chunks(text: str) -> list[str]:
    """Whole-text convenience: everything the chunker emits."""
    chunker = SentenceChunker()
    out = chunker.feed(text)
    tail = chunker.flush()
    if tail:
        out.append(tail)
    return out


def streamed(tokens: list[str]) -> list[str]:
    """Feeds token-sized pieces exactly as the LLM stream would."""
    chunker = SentenceChunker()
    out: list[str] = []
    for token in tokens:
        out.extend(chunker.feed(token))
    tail = chunker.flush()
    if tail:
        out.append(tail)
    return out


def test_basic_boundaries_split_at_punctuation_plus_space():
    assert chunks("Hola. ¿Cómo estás? Bien.") == ["Hola.", "¿Cómo estás?", "Bien."]


def test_spanish_punctuation_and_ellipsis_are_boundaries():
    assert chunks("¿Qué tal? ¡Genial!") == ["¿Qué tal?", "¡Genial!"]
    assert chunks("Esperá... ya volvió. Fin.") == ["Esperá...", "ya volvió.", "Fin."]


def test_feed_returns_only_completed_sentences_and_flush_the_tail():
    # A boundary closes only when the separator AFTER the punctuation is
    # already buffered — the tail stays flushable either way.
    chunker = SentenceChunker()
    assert chunker.feed("Hola") == []
    assert chunker.feed(" mundo.") == []
    assert chunker.feed(" Todo") == ["Hola mundo."]
    assert chunker.feed(" bien!") == []
    assert chunker.flush() == "Todo bien!"


def test_streamed_pieces_equal_whole_text_result():
    text = "Primera frase. Segunda frase! ¿Tercera? Cuarta sin punto final"
    tokens = [text[i:i + 7] for i in range(0, len(text), 7)]
    assert streamed(tokens) == chunks(text)


def test_decimal_numbers_never_split():
    assert chunks("El índice 3.14 subió. Luego bajó.") == [
        "El índice 3.14 subió.",
        "Luego bajó.",
    ]


@pytest.mark.parametrize(
    "text",
    [
        "Avisó el Dr. García. Luego fue.",
        "Compró pan, leche, etc. Y volvió.",
        "Mirá la pág. 3 del manual.",
        "Use e.g. this one. Done.",
        "El Sr. López vino. Temprano.",
        "John F. Kennedy dijo. Y más.",
        "Avisó el Dr",
    ],
)
def test_abbreviations_and_initials_do_not_split(text):
    sentences = chunks(text)
    assert len(sentences) >= 1
    assert not any(
        sentence.endswith(("Dr.", "etc.", "pág.", "e.g.", "Sr.", "F."))
        for sentence in sentences[:-1]
    )


def test_abbreviation_across_feed_boundary_is_guarded():
    # The abbreviation token arrived in an earlier feed; the guard must
    # still see it because it walks back over the accumulated buffer.
    assert streamed(["Avisó el Dr", ". García. Luego fue."]) == [
        "Avisó el Dr. García.",
        "Luego fue.",
    ]


def test_code_spans_do_not_split():
    sentences = chunks("Ejecuta `npm run dev. Luego` y sigue. Fin.")
    assert sentences == ["Ejecuta `npm run dev. Luego` y sigue.", "Fin."]


def test_newlines_and_bullet_lists_are_boundaries():
    assert chunks("- Uno\n- Dos\n") == ["- Uno", "- Dos"]
    assert chunks("una frase sin punto\notra") == ["una frase sin punto", "otra"]


def test_urls_stay_in_one_sentence():
    assert chunks("Mirá https://ejemplo.com/a.b. Después contó.") == [
        "Mirá https://ejemplo.com/a.b. Después contó."
    ]


def test_multiple_sentences_in_one_feed():
    assert chunks("Uno. Dos. Tres.") == ["Uno.", "Dos.", "Tres."]


def test_flush_returns_only_meaningful_remainder():
    chunker = SentenceChunker()
    chunker.feed("Hola.   ")
    assert chunker.flush() is None
    chunker2 = SentenceChunker()
    assert chunker2.feed("   ") == []
    assert chunker2.flush() is None


def test_flush_is_repeatable_after_close():
    chunker = SentenceChunker()
    chunker.feed("Texto sin límite")
    assert chunker.flush() == "Texto sin límite"
    assert chunker.flush() is None


def test_chunker_is_deterministic():
    text = "Aviso el Dr. García: 3.14 subió `a.b` hoy. Fin."
    assert chunks(text) == chunks(text)


def test_no_boundary_without_separator_keeps_accumulating():
    chunker = SentenceChunker()
    assert chunker.feed("Hola.") == []  # cannot know yet whether text follows
    assert chunker.feed(" x") == ["Hola."]  # separator arrived: boundary closes
    assert chunker.flush() == "x"
    chunker2 = SentenceChunker()
    assert chunker2.feed("Hola.") == []
    assert chunker2.feed("world.") == []  # no separator after the run, ever
    assert chunker2.flush() == "Hola.world."
