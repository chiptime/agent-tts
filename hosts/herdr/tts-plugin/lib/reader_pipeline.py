#!/usr/bin/env python3
"""
reader_pipeline.py — HT-15 Markdown/HTML reader pipeline (host layer).

Turns raw agent scrollback into sanitized, structure-preserving Markdown and
GFM-subset HTML whose sentence anchors are INDEX-IDENTICAL to the enumeration
the pinned agent_tts engine drives during playback (scroll-info/highlight).

Design invariants (openspec/changes/ht-15-markdown-html-pipeline/design.md):

* Composition order is fixed: strip_ansi -> redact_secrets -> structure-
  preserving chrome filter. The speech mutations of clean_agent_text (code
  omission, ``|`` -> " — ", URL -> "enlace web", acronym expansion) MUST NOT
  reach reader output; clean_agent_text is only ever used to PROJECT text for
  oracle alignment, never to produce it.
* Sentence/paragraph indices are taken VERBATIM from
  ``estimate_boundaries_from_text`` over the normalized text; they are never
  recomputed on the raw side. A sidecar JSON map carries the raw-span
  correspondence for consumers (HT-16).
* Total escaping: every text node and attribute value passes through
  html.escape; raw input HTML is never passed through. Link schemes are
  restricted to http/https; anything else renders as plain text.
* Stdlib + the pinned agent_tts public API only (no new dependencies).

Fail-closed rule: if redact_secrets raises, the error propagates so callers
(render_to_files) can refuse to write any output file.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
from dataclasses import dataclass

# Public pinned-engine surface only (RF-HT-13 layer boundary audit).
from agent_tts import (
    clean_agent_text,
    estimate_boundaries_from_text,
    extract_last_turn,
    redact_secrets,
    strip_ansi,
)

__all__ = [
    "SentenceAnchor",
    "RenderResult",
    "sanitize",
    "plain_to_markdown",
    "markdown_to_html",
    "build_anchors",
    "render",
    "mapping_json",
    "render_to_files",
]


# ---------------------------------------------------------------------------
# Public data contracts (design Module Layout / Data Contracts)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SentenceAnchor:
    """One engine sentence and where it lives in the rendered document.

    ``sent_idx``/``para_idx`` come VERBATIM from the pinned engine's
    boundary enumeration (never recomputed locally). ``text`` mirrors the
    primary span (raw REDACTED text). ``fragments`` are char ranges over
    the markdown text produced by ``plain_to_markdown``.
    """

    sent_idx: int
    para_idx: int
    text: str
    block_ids: tuple[str, ...]
    fragments: tuple[tuple[int, int], ...]
    exact: bool
    # R2 superset over the design schema: the raw text of every fragment,
    # so the sidecar can prove redaction even when the secret sat in a
    # non-primary fragment (e.g. a fenced block).
    fragment_texts: tuple[str, ...] = ()


@dataclass(frozen=True)
class RenderResult:
    html: str
    anchors: tuple[SentenceAnchor, ...]
    alignment: str  # "exact" | "coverage"
    normalized: str  # N = clean_agent_text(R) the oracle enumerated
    engine: dict  # staleness tuple: lang/max_chars/summarize/lexicon_fp

# ---------------------------------------------------------------------------
# Chrome filtering (structure-preserving)
# ---------------------------------------------------------------------------
# Lines made ONLY of box-drawing/dash glyphs are terminal borders, not
# content. Pipes are deliberately excluded: GFM table rows and delimiter
# lines (``|---|---|``) must survive verbatim (R1).
_BORDER_LINE_RE = re.compile(r"^[\s▏▎▌▐┃┆╹▀▄■⬝█━─═┌┐└┘├┤┬┴┼╭╮╯╰—=]+$")
_SPINNER_RE = re.compile(r"[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]{2,}")
# Whole lines that are unambiguous TUI footer/sidebar chrome. Line-anchored
# and content-anchored on purpose so real prose can never match.
_CHROME_LINE_RES = (
    re.compile(r"(?i)^\s*ctrl\+\w+\s+(commands?|shortcuts?|to\s+\w+)\s*$"),
    re.compile(r"(?i)^\s*esc\s+(to\s+)?interrupt\s*$"),
    re.compile(r"(?i)\bopen ?code\s+v?\d+(\.\d+)+"),
    re.compile(r"(?i)^\s*click to expand\s*$"),
    re.compile(r"(?i)^\s*tokens?\s*[:=]\s*[\d.,\s/kKmM]+"),
    re.compile(r"(?i)^\s*cost\s*:\s*\$[\d.]+"),
    re.compile(r"(?i)^\s*elapsed\s*:\s*[\d.]+s"),
    re.compile(r"(?i)^\s*\d[\d,.]*\s*[kKmM]?\s*tokens\b"),
    re.compile(r"(?i)^\s*\d+(\.\d+)?%\s*used\s*$"),
    re.compile(r"(?i)^\s*\$\d+(\.\d+)?\s*spent\s*$"),
)
# Decoration glyphs stripped wherever they appear outside fences. Bullet
# characters are NOT here: they are list markers and must survive (R1).
_DECO_GLYPH_RE = re.compile(r"[✓✔✕✗▼▲●↳]")

_FENCE_OPEN_RE = re.compile(r"^\s*(```|~~~)")


def sanitize(raw: str) -> str:
    """ANSI-strip, redact, then drop terminal chrome — structure preserved.

    Fences (```/~~~), table pipes, heading/list markers and URLs survive
    verbatim; fence interiors are never touched by the chrome filter.
    ``redact_secrets`` runs BEFORE any structural decision and its exceptions
    propagate (fail closed).
    """
    text = strip_ansi(raw or "")
    # Fail-closed redaction: happens before any markdown/html transformation
    # so no downstream output can ever carry a raw credential.
    text = redact_secrets(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    kept: list[str] = []
    in_fence = False
    for line in text.split("\n"):
        if _FENCE_OPEN_RE.match(line):
            in_fence = not in_fence
            kept.append(line)
            continue
        if in_fence:
            kept.append(line)  # fence interiors are untouchable
            continue
        if _BORDER_LINE_RE.match(line):
            continue
        if any(rx.search(line) for rx in _CHROME_LINE_RES):
            continue
        line = _SPINNER_RE.sub("", line)
        line = _DECO_GLYPH_RE.sub("", line).rstrip()
        kept.append(line)
    return "\n".join(kept)


# ---------------------------------------------------------------------------
# Deterministic plain-to-Markdown heuristics (R3)
# ---------------------------------------------------------------------------
# Projection neutrality rule: heuristics must never create or remove
# sentence-splitting punctuation (anything the engine's splitter
# ``(?<=[.!?])\s+`` would treat differently). Markers like ``###`` or ``-``
# are safe; rewriting ``2)`` into ``2.`` would NOT be (CommonMark accepts
# both marker forms natively, so markers stay verbatim).
_BULLET_RE = re.compile(r"^(\s*)[•◦▪▸‣]\s+")
_HEADING_CANDIDATE_RE = re.compile(r"^([^{}#>*\-+`|\d\n]{1,60}):$")

_BLANK_CLUSTER_RE = re.compile(r"\n{3,}")


def plain_to_markdown(text: str) -> str:
    """Deterministically convert unformatted text toward Markdown.

    * Unicode bullets (• ◦ ▪ ▸ ‣) become ``- `` list markers.
    * Numbered markers (``1.``, ``2)``) are already valid Markdown and stay
      verbatim — no renumbering, no marker rewriting.
    * Short section lines ending in ``:`` become ``###`` headings.
    * Blank-line clusters collapse to a single blank line (paragraphs).
    """
    out_lines: list[str] = []
    in_fence = False
    for line in (text or "").split("\n"):
        if _FENCE_OPEN_RE.match(line):
            in_fence = not in_fence
            out_lines.append(line)
            continue
        if not in_fence:
            line = _BULLET_RE.sub(r"\1- ", line)
            if (
                line.strip()
                and len(line.strip()) <= 60
                and _HEADING_CANDIDATE_RE.match(line.strip())
                and not line.startswith((" ", "\t", "#", ">", "|"))
            ):
                # Keep the trailing ':' so the projected block text stays
                # identical to the oracle's normalized text (gate exactness).
                line = "### " + line.strip()
        out_lines.append(line)
    md = "\n".join(out_lines)
    md = _BLANK_CLUSTER_RE.sub("\n\n", md)
    return md


# ---------------------------------------------------------------------------
# GFM-subset HTML with total escaping (R4)
# ---------------------------------------------------------------------------

_H_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_UL_RE = re.compile(r"^\s*[-*+]\s+(.*)$")
_OL_RE = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_QUOTE_RE = re.compile(r"^\s*>\s?(.*)$")
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_DELIM_RE = re.compile(r"^\s*\|?\s*:?-{2,}.*\|.*$")
_FENCE_LINE_RE = re.compile(r"^\s*(```|~~~)\s*([\w+#.-]*)\s*$")

_CODE_SPAN_RE = re.compile(r"`([^`\n]+)`")
_BOLD_RE = re.compile(r"\*\*([^*\n]+)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")
_SAFE_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)


def _inline(md_text: str) -> str:
    """Escape first, then apply inline Markdown on the escaped text."""
    esc = html.escape(md_text, quote=True)
    esc = _CODE_SPAN_RE.sub(r"<code>\1</code>", esc)
    esc = _BOLD_RE.sub(r"<strong>\1</strong>", esc)
    esc = _ITALIC_RE.sub(r"<em>\1</em>", esc)

    def _link_sub(match: re.Match) -> str:
        text_part, url = match.group(1), match.group(2)
        if _SAFE_SCHEME_RE.match(url):
            href = html.escape(url, quote=True)
            return f'<a href="{href}">{text_part}</a>'
        return text_part  # unsafe scheme: demoted to plain (escaped) text

    esc = _LINK_RE.sub(_link_sub, esc)
    return esc


@dataclass(frozen=True)
class _Block:
    """One structural markdown element with its spans in the md text.

    ``e0``/``e1`` delimit the whole element (fence markers included);
    ``c0``/``c1`` delimit the content an anchor can cover (for a fence,
    the code body). For oracle projection, a fence uses the FULL element
    slice so the engine's code-block rules fire exactly as they do when
    the engine cleans the whole text at once.
    """

    kind: str  # fence | heading | quote | table | ul | ol | p
    e0: int
    e1: int
    c0: int
    c1: int
    lang: str = ""
    level: int = 0


def _parse_blocks(md: str) -> list[_Block]:
    """Parse md into ordered structural blocks with char spans.

    Mirrors the emission logic of markdown_to_html one-to-one so anchors
    built over these spans render exactly where computed. An unclosed
    fence consumes the rest of the input (safe degradation).
    """
    lines = (md or "").split("\n")
    offs = []
    pos = 0
    for ln in lines:
        offs.append(pos)
        pos += len(ln) + 1
    n = len(lines)
    blocks: list[_Block] = []
    i = 0
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        m_fence = _FENCE_LINE_RE.match(line)
        if m_fence:
            lang = m_fence.group(2)
            e0 = offs[i]
            i += 1
            body_start = i
            while i < n and not _FENCE_LINE_RE.match(lines[i]):
                i += 1
            if i > body_start:
                c0, c1 = offs[body_start], offs[i - 1] + len(lines[i - 1])
            elif body_start < n:  # empty fence body
                c0 = c1 = offs[body_start]
            else:
                c0 = c1 = e0
            if i < n:
                e1 = offs[i] + len(lines[i])  # include the closing fence line
                i += 1
            else:
                e1 = c1
            blocks.append(_Block("fence", e0, e1, c0, c1, lang=lang))
            continue
        m_h = _H_RE.match(line)
        if m_h:
            text = m_h.group(2)
            c0 = offs[i] + len(line) - len(text)
            e1 = offs[i] + len(line)
            blocks.append(
                _Block("heading", offs[i], e1, c0, e1, level=len(m_h.group(1)))
            )
            i += 1
            continue
        m_q = _QUOTE_RE.match(line)
        if m_q:
            e0 = offs[i]
            first_text = m_q.group(1)
            c0 = offs[i] + len(line) - len(first_text)
            i += 1
            while i < n and _QUOTE_RE.match(lines[i]):
                i += 1
            e1 = offs[i - 1] + len(lines[i - 1])
            blocks.append(_Block("quote", e0, e1, c0, e1))
            continue
        if (
            "|" in line
            and i + 1 < n
            and _TABLE_DELIM_RE.match(lines[i + 1])
            and _TABLE_ROW_RE.match(line)
        ):
            e0 = offs[i]
            i += 2
            while i < n and _TABLE_ROW_RE.match(lines[i]):
                i += 1
            e1 = offs[i - 1] + len(lines[i - 1])
            blocks.append(_Block("table", e0, e1, e0, e1))
            continue
        m_ul, m_ol = _UL_RE.match(line), _OL_RE.match(line)
        if m_ul or m_ol:
            kind = "ul" if m_ul else "ol"
            rx = _UL_RE if m_ul else _OL_RE
            e0 = offs[i]
            i += 1
            while i < n and rx.match(lines[i]):
                i += 1
            e1 = offs[i - 1] + len(lines[i - 1])
            blocks.append(_Block(kind, e0, e1, e0, e1))
            continue
        # paragraph: consecutive plain lines
        e0 = offs[i]
        i += 1
        while i < n and lines[i].strip() and not (
            _H_RE.match(lines[i])
            or _QUOTE_RE.match(lines[i])
            or _UL_RE.match(lines[i])
            or _OL_RE.match(lines[i])
            or _FENCE_LINE_RE.match(lines[i])
            or (
                "|" in lines[i]
                and i + 1 < n
                and _TABLE_DELIM_RE.match(lines[i + 1])
                and _TABLE_ROW_RE.match(lines[i])
            )
        ):
            i += 1
        e1 = offs[i - 1] + len(lines[i - 1])
        blocks.append(_Block("p", e0, e1, e0, e1))
    return blocks


def _span_attrs(sent_idx: int, para_idx: int, primary: bool) -> str:
    if primary:
        return (
            f' class="tts-sent" data-sent-idx="{sent_idx}" '
            f'data-para-idx="{para_idx}" id="tts-sent-{sent_idx}"'
        )
    return f' class="tts-sent-cont" data-sent-idx="{sent_idx}"'


def _render_events(md: str, block: _Block, events) -> str:
    """Render a prose-like block with anchored fragments.

    ``events``: ordered (sent_idx, para_idx, primary, (a, b)) absolute md
    ranges tiling part of the block content; gaps render un-anchored.
    """
    out: list[str] = []
    cur = block.c0
    for sent, para, primary, (a, b) in events:
        a = max(a, block.c0)
        b = min(b, block.c1)
        if a > cur:
            out.append(_inline(md[cur:a]))
        if b > a:
            out.append(f"<span{_span_attrs(sent, para, primary)}>{_inline(md[a:b])}</span>")
        cur = max(cur, b)
    if cur < block.c1:
        out.append(_inline(md[cur:block.c1]))
    return "".join(out)


def _virtual_span(sent: int, para: int, primary: bool = True) -> str:
    return f"<span{_span_attrs(sent, para, primary)}></span>"


def markdown_to_html(md: str, anchors=None) -> str:
    """Render the GFM subset: h1-h4, paragraphs, fenced code with language
    tag, blockquotes, tables, ul/ol, bold/italic/inline-code, links.

    All text and attribute values are html.escape'd unconditionally; no
    raw input HTML ever passes through. Links allow only http/https.
    ``anchors`` (tuple of SentenceAnchor computed over this same md text)
    decorates the output with ``data-sent-idx``/``data-para-idx`` anchors;
    None renders plain HTML. Container elements (``pre``/``table``/lists)
    carry anchor attributes directly (a span cannot legally wrap flow
    content); fragments beyond the first on a container degrade to empty
    spans after the element so ``count(.tts-sent)`` always equals the
    sentence count. Malformed input (e.g. an unclosed fence) degrades to
    fully-escaped text with anchors unaffected (engine-sourced).
    """
    md = md or ""
    blocks = _parse_blocks(md)
    per_block: dict[int, list] = {}
    virtuals: list[tuple[int, int]] = []
    if anchors is not None:
        pending = []
        for bi, blk in enumerate(blocks):
            for a in anchors:
                for frag in a.fragments:
                    if frag[0] < blk.c1 and frag[1] > blk.c0:
                        pending.append((bi, frag[0], a.sent_idx, a.para_idx, frag))
        pending.sort(key=lambda t: (t[0], t[1]))
        seen_primary: set[int] = set()
        for bi, _fs, sent, para, frag in pending:
            primary = sent not in seen_primary
            if primary:
                seen_primary.add(sent)
            per_block.setdefault(bi, []).append((sent, para, primary, frag))
        anchored = {t[2] for t in pending}
        virtuals = sorted(
            (a.sent_idx, a.para_idx) for a in anchors if a.sent_idx not in anchored
        )

    parts: list[str] = []
    for bi, blk in enumerate(blocks):
        events = per_block.get(bi, [])
        single = len(events) == 1
        if blk.kind == "fence":
            body = html.escape(md[blk.c0:blk.c1], quote=True)
            code_open = (
                f'<code class="language-{html.escape(blk.lang, quote=True)}">'
                if blk.lang
                else "<code>"
            )
            if single:
                attrs = _span_attrs(events[0][0], events[0][1], events[0][2]).strip()
                parts.append(f"<pre {attrs}>{code_open}{body}</code></pre>")
            else:
                parts.append(f"<pre>{code_open}{body}</code></pre>")
            parts.extend(_virtual_span(s, p, prim) for (s, p, prim, _f) in events[1:])
        elif blk.kind == "table":
            rows = md[blk.c0:blk.c1].split("\n")
            header = [c.strip() for c in rows[0].strip().strip("|").split("|")]
            body_rows = [
                [c.strip() for c in r.strip().strip("|").split("|")] for r in rows[2:]
            ]
            thead = "".join(f"<th>{_inline(c)}</th>" for c in header)
            tbody = "".join(
                "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>"
                for row in body_rows
            )
            if single:
                attrs = _span_attrs(events[0][0], events[0][1], events[0][2]).strip()
                parts.append(
                    f"<table {attrs}><thead><tr>{thead}</tr></thead>"
                    f"<tbody>{tbody}</tbody></table>"
                )
            else:
                parts.append(
                    f"<table><thead><tr>{thead}</tr></thead><tbody>{tbody}</tbody></table>"
                )
            parts.extend(_virtual_span(s, p, prim) for (s, p, prim, _f) in events[1:])
        elif blk.kind in ("ul", "ol"):
            rx = _UL_RE if blk.kind == "ul" else _OL_RE
            items = "".join(
                f"<li>{_inline(m.group(1))}</li>"
                for m in map(rx.match, md[blk.c0:blk.c1].split("\n"))
                if m
            )
            if single and events[0][3][0] <= blk.c0 and events[0][3][1] >= blk.c1:
                attrs = _span_attrs(events[0][0], events[0][1], events[0][2]).strip()
                parts.append(f"<{blk.kind} {attrs}>{items}</{blk.kind}>")
            else:
                parts.append(f"<{blk.kind}>{items}</{blk.kind}>")
                parts.extend(_virtual_span(s, p, prim) for (s, p, prim, _f) in events)
        elif blk.kind == "heading":
            inner = _render_events(md, blk, events) if events else _inline(md[blk.c0:blk.c1])
            if blk.level <= 4:
                parts.append(f"<h{blk.level}>{inner}</h{blk.level}>")
            else:
                parts.append(f"<p>{inner}</p>")
        elif blk.kind == "quote":
            inner = _render_events(md, blk, events) if events else _inline(md[blk.c0:blk.c1])
            parts.append(f"<blockquote><p>{inner}</p></blockquote>")
        else:  # paragraph
            inner = _render_events(md, blk, events) if events else _inline(md[blk.c0:blk.c1])
            parts.append(f"<p>{inner}</p>")
    if virtuals:
        joined = "".join(_virtual_span(s, p) for (s, p) in virtuals)
        parts.append(f"<p>{joined}</p>" if not blocks else joined)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Engine-oracle anchors (R5) — indices are NEVER recomputed locally
# ---------------------------------------------------------------------------
# The engine's enumeration is the ONLY authority for sentence/paragraph
# indices. The regexes below mirror the pinned engine's splitting rules
# strictly for SPAN GEOMETRY (character ranges over texts the engine has
# already enumerated); counts, indices and paragraph mapping are always
# taken from estimate_boundaries_from_text objects.

_PARA_SPLIT_RE = re.compile(r"\n\s*\n+")
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _acc_sentence_spans(text: str, a: int, b: int, spans: list[tuple[int, int]]) -> None:
    seg = text[a:b]
    s0 = a + (len(seg) - len(seg.lstrip()))
    s1 = b - (len(seg) - len(seg.rstrip()))
    if s0 >= s1:
        return
    cur = s0
    for m in _SENT_SPLIT_RE.finditer(text, s0, s1):
        e = m.start()
        if text[cur:e].strip():
            spans.append((cur, e))
        cur = m.end()
    if text[cur:s1].strip():
        spans.append((cur, s1))


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    """Character ranges of sentences using the engine's split rules."""
    spans: list[tuple[int, int]] = []
    last = 0
    for m in _PARA_SPLIT_RE.finditer(text):
        _acc_sentence_spans(text, last, m.start(), spans)
        last = m.end()
    _acc_sentence_spans(text, last, len(text), spans)
    return spans


def _block_projection_text(md: str, blk: _Block) -> str:
    if blk.kind == "fence":
        return md[blk.e0:blk.e1]  # full element: engine fence rules must fire
    return md[blk.c0:blk.c1]


def _lexicon_fp() -> str:
    paths = [
        os.environ.get("AGENT_TTS_LEXICON", ""),
        os.path.expanduser("~/.config/agent-tts/lexicon.json"),
    ]
    for p in paths:
        if p and os.path.isfile(p):
            try:
                with open(p, "rb") as fh:
                    return "sha256:" + hashlib.sha256(fh.read()).hexdigest()
            except OSError:
                continue
    return ""


_MAX_PROJECTION_BLOCKS = 2000  # oversized input guard (design Failure Modes)


def _build(redacted: str, lang: str, max_chars: int):
    """Anchor construction. Returns (anchors, alignment, normalized)."""
    R = redacted
    md = plain_to_markdown(R)
    blocks = _parse_blocks(md)
    if len(blocks) > _MAX_PROJECTION_BLOCKS:  # beyond cap: no projections
        blocks = blocks[:_MAX_PROJECTION_BLOCKS]

    N = clean_agent_text(R, max_chars=max_chars, lang=lang, pre_extracted=True)
    oracle = estimate_boundaries_from_text(N, 1.0)
    S = [(s.index, s.paragraph_index, s.text) for s in oracle.sentences]

    # P: per-block projections joined by the ORIGINAL md separators, so P
    # reproduces the global shape the engine saw when cleaning R.
    P_parts: list[str] = []
    branges: list[tuple[int, int]] = []
    prev_end = 0
    for blk in blocks:
        if P_parts:
            P_parts.append(md[prev_end:blk.e0])
        proj = clean_agent_text(
            _block_projection_text(md, blk), lang=lang, pre_extracted=True
        )
        branges.append((len("".join(P_parts)), len("".join(P_parts)) + len(proj)))
        P_parts.append(proj)
        prev_end = blk.e1
    P = "".join(P_parts)

    Sp = estimate_boundaries_from_text(P, 1.0)
    # Whitespace-insensitive signature: per-block projections come back
    # .strip()'d from clean_agent_text while the global N keeps boundary
    # spaces (e.g. around the code-omission placeholder). Indices and word
    # content are what parity means here — internal whitespace is not.
    _norm = lambda t: " ".join(t.split())
    Ssig = [(s.index, s.paragraph_index, _norm(s.text)) for s in Sp.sentences]
    gate = Ssig == [(i, p, _norm(t)) for (i, p, t) in S] and len(S) > 0

    sent_ranges: list[tuple[int, int] | None]
    if gate:
        alignment = "exact"
        spans = _sentence_spans(P)
        if len(spans) == len(S):
            sent_ranges = spans
        else:  # defensive: geometry disagrees with the oracle objects
            gate = False
            alignment = "coverage"
            sent_ranges = []
    if not gate:
        alignment = "coverage"
        # Deterministic monotone two-pointer over word tokens: oracle
        # sentence j consumes P tokens [c_j, c_{j+1}) of the token stream.
        toks = [(m.start(), m.end()) for m in re.finditer(r"\S+", P)]
        cums = [0]
        for (_, _, t) in S:
            cums.append(cums[-1] + len(t.split()))
        sent_ranges = []
        for j in range(len(S)):
            a_t = min(cums[j], len(toks))
            b_t = min(cums[j + 1], len(toks))
            if b_t > a_t:
                sent_ranges.append((toks[a_t][0], toks[b_t - 1][1]))
            else:
                sent_ranges.append(None)

    # ── block/fragment assignment (document order, monotone) ──
    events: list[tuple[int, int, tuple[int, int]]] = []  # (block, sent, md range)
    for bi, blk in enumerate(blocks):
        b0, b1 = branges[bi]
        overlaps = [
            j
            for j, rng in enumerate(sent_ranges)
            if rng is not None and rng[0] < b1 and rng[1] > b0
        ]
        if not overlaps:
            continue
        if len(overlaps) == 1:
            j = overlaps[0]
            events.append((bi, j, (blk.c0, blk.c1)))
            continue
        k_raw = len(_sentence_spans(md[blk.c0:blk.c1]))
        if k_raw == len(overlaps):
            local = _sentence_spans(md[blk.c0:blk.c1])
            for (l0, l1), j in zip(local, overlaps):
                events.append((bi, j, (blk.c0 + l0, blk.c0 + l1)))
        else:  # counts disagree: whole block to the sentence covering its start
            j = overlaps[0]
            for cand in overlaps:
                if sent_ranges[cand][0] <= b0:
                    j = cand
            events.append((bi, j, (blk.c0, blk.c1)))

    # sentence -> fragments, in document order
    by_sent: dict[int, list[tuple[int, tuple[int, int]]]] = {}
    for bi, j, rng in events:
        by_sent.setdefault(j, []).append((bi, rng))
    # virtual anchors for sentences with no fragment, inserted in order
    anchors: list[SentenceAnchor] = []
    exact = alignment == "exact"
    for j, (idx, para, _text) in enumerate(S):
        frags = by_sent.get(j, [])
        block_ids = tuple(f"b{bi}" for (bi, _r) in frags)
        if frags:
            ranges = tuple(rng for (_b, rng) in frags)
            ftexts = tuple(md[r0:r1] for (_b, (r0, r1)) in frags)
            primary_slice = ranges[0]
            text = md[primary_slice[0]:primary_slice[1]]
        else:
            ranges = ()
            ftexts = ()
            text = ""
        anchors.append(
            SentenceAnchor(
                sent_idx=idx,
                para_idx=para,
                text=text,
                block_ids=block_ids,
                fragments=ranges,
                exact=exact,
                fragment_texts=ftexts,
            )
        )
    return tuple(anchors), alignment, N


def build_anchors(redacted: str, *, lang: str = "es", max_chars: int = 0) -> tuple[SentenceAnchor, ...]:
    """Anchors for already-sanitized text; engine indices, verbatim."""
    return _build(redacted, lang, max_chars)[0]


def render(raw: str, *, lang: str = "es", max_chars: int = 0, pre_extracted: bool = False) -> RenderResult:
    """Full pipeline: sanitize → markdown → anchored HTML + sidecar data.

    Anchors are valid only against a speech invocation with the same
    (lang, max_chars, lexicon) tuple — recorded in the sidecar for
    staleness detection. ``summarize``/--tldr is unsupported for
    anchoring (the summarizer rewrites text).
    """
    if not pre_extracted:
        try:
            raw = extract_last_turn(raw)
        except Exception:
            pass  # malformed scrollback: treat as final message text
    R = sanitize(raw)  # redaction failure propagates (fail closed)
    md = plain_to_markdown(R)
    anchors, alignment, N = _build(R, lang, max_chars)
    doc_html = markdown_to_html(md, anchors)
    engine = {
        "lang": lang,
        "max_chars": max_chars,
        "summarize": False,
        "lexicon_fp": _lexicon_fp(),
    }
    return RenderResult(doc_html, anchors, alignment, N, engine)


def mapping_json(result: RenderResult) -> str:
    """Sidecar JSON map (contract reader-pipeline/anchors@1) for HT-16."""
    sentences = [
        {
            "sent_idx": a.sent_idx,
            "para_idx": a.para_idx,
            "text": a.text,
            "selector": f"#tts-sent-{a.sent_idx}",
            "block_ids": list(a.block_ids),
            "fragments": [[s, e] for (s, e) in a.fragments],
            "fragment_texts": list(a.fragment_texts),
            "exact": a.exact,
        }
        for a in result.anchors
    ]
    total_paras = (max((a.para_idx for a in result.anchors), default=-1) + 1) if result.anchors else 0
    doc = {
        "version": 1,
        "contract": "reader-pipeline/anchors@1",
        "alignment": result.alignment,
        "total_sents": len(result.anchors),
        "total_paras": total_paras,
        "engine": result.engine,
        "sentences": sentences,
    }
    return json.dumps(doc, ensure_ascii=False, indent=2)


def render_to_files(raw: str, html_path: str, map_path: str | None = None, **kw) -> int:
    """Render to an HTML file (+ optional sidecar map). Exit-code contract:
    0 success (including coverage / empty / malformed), 2 render failure
    (redaction fail-closed — no file is written). Engine import failures
    surface at module import (the bridge maps them to exit 3).
    """
    try:
        result = render(raw, **kw)
    except Exception as exc:  # fail closed: never emit unredacted output
        print(f"reader-pipeline: render failed: {exc}", file=__import__("sys").stderr)
        return 2
    try:
        with open(html_path, "w", encoding="utf-8") as fh:
            fh.write(result.html)
        if map_path:
            with open(map_path, "w", encoding="utf-8") as fh:
                fh.write(mapping_json(result))
    except OSError as exc:
        print(f"reader-pipeline: cannot write output: {exc}", file=__import__("sys").stderr)
        return 2
    return 0
