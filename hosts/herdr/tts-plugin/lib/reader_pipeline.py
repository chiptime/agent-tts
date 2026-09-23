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

import html
import re

# Public pinned-engine surface only (RF-HT-13 layer boundary audit).
from agent_tts import redact_secrets, strip_ansi

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
                line = "### " + line.strip()[:-1]  # drop the trailing ':'
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


def markdown_to_html(md: str, anchors=None) -> str:
    """Render the GFM subset: h1–h4, paragraphs, fenced code with language
    tag, blockquotes, tables, ul/ol, bold/italic/inline-code, links.

    All text and attribute values are html.escape'd unconditionally; no raw
    input HTML ever passes through. Links allow only http/https. ``anchors``
    (a tuple of SentenceAnchor from the same render) decorates blocks with
    ``data-sent-idx``/``data-para-idx`` anchors; None renders plain HTML.
    Malformed input (e.g. an unclosed fence) degrades to fully-escaped text.
    """
    lines = (md or "").split("\n")
    parts: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        # Fenced code (tolerates an unclosed fence: runs to EOF, escaped).
        m_fence = re.match(r"^\s*(```|~~~)\s*([\w+#.-]*)\s*$", line)
        if m_fence:
            fence = m_fence.group(1)
            lang = m_fence.group(2)
            i += 1
            body: list[str] = []
            while i < n and not lines[i].strip().startswith(fence):
                body.append(lines[i])
                i += 1
            if i < n:
                i += 1  # consume the closing fence
            attrs = f' class="language-{html.escape(lang, quote=True)}"' if lang else ""
            text = html.escape("\n".join(body), quote=True)
            parts.append(f"<pre><code{attrs}>{text}</code></pre>")
            continue
        # Heading (h1–h4; deeper headings degrade to paragraphs).
        m_h = _H_RE.match(line)
        if m_h:
            level = len(m_h.group(1))
            if level <= 4:
                parts.append(
                    f"<h{level}>{_inline(m_h.group(2).strip())}</h{level}>"
                )
                i += 1
                continue
        # Blockquote (consecutive ``>`` lines).
        m_q = _QUOTE_RE.match(line)
        if m_q:
            quoted: list[str] = []
            while i < n:
                m = _QUOTE_RE.match(lines[i])
                if not m:
                    break
                quoted.append(m.group(1))
                i += 1
            parts.append(f"<blockquote><p>{_inline(' '.join(quoted))}</p></blockquote>")
            continue
        # GFM table: header row + delimiter row.
        if "|" in line and i + 1 < n and _TABLE_DELIM_RE.match(lines[i + 1]) and _TABLE_ROW_RE.match(line):
            header = [c.strip() for c in line.strip().strip("|").split("|")]
            i += 2
            rows: list[list[str]] = []
            while i < n and _TABLE_ROW_RE.match(lines[i]):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            thead = "".join(f"<th>{_inline(c)}</th>" for c in header)
            tbody = "".join(
                "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>"
                for row in rows
            )
            parts.append(
                f"<table><thead><tr>{thead}</tr></thead><tbody>{tbody}</tbody></table>"
            )
            continue
        # Lists (ul / ol).
        if _UL_RE.match(line):
            items: list[str] = []
            while i < n and _UL_RE.match(lines[i]):
                items.append(f"<li>{_inline(_UL_RE.match(lines[i]).group(1))}</li>")
                i += 1
            parts.append(f"<ul>{''.join(items)}</ul>")
            continue
        if _OL_RE.match(line):
            items = []
            while i < n and _OL_RE.match(lines[i]):
                items.append(f"<li>{_inline(_OL_RE.match(lines[i]).group(1))}</li>")
                i += 1
            parts.append(f"<ol>{''.join(items)}</ol>")
            continue
        # Paragraph: consecutive non-blank, non-structural lines.
        para: list[str] = []
        while i < n and lines[i].strip() and not (
            _H_RE.match(lines[i])
            or _QUOTE_RE.match(lines[i])
            or _UL_RE.match(lines[i])
            or _OL_RE.match(lines[i])
            or re.match(r"^\s*(```|~~~)", lines[i])
            or ("|" in lines[i] and i + 1 < n and _TABLE_DELIM_RE.match(lines[i + 1]) and _TABLE_ROW_RE.match(lines[i]))
        ):
            para.append(lines[i].strip())
            i += 1
        parts.append(f"<p>{_inline(' '.join(para))}</p>")
    return "\n".join(parts)
