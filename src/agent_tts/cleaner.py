"""Text sanitization, normalization, and terminal cleaner for agent speech."""

import json
import os
import re
from typing import Dict, List, Optional, Tuple

from agent_tts.redact import redact_secrets

ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

# Comprehensive developer technical replacements for natural speech
BASE_TECHNICAL_LEXICON: List[Tuple[str, str]] = [
    # Git & workflows
    (r"\bPR\b", "pull request"),
    (r"\bPRs\b", "pull requests"),
    (r"\brepo\b", "repositorio"),
    (r"\brepos\b", "repositorios"),
    (r"\bgit rebase\b", "git rebase"),
    (r"\bgit merge\b", "git merge"),
    (r"\bCI/CD\b", "C I C D"),
    (r"\bCI\b", "integración continua"),
    (r"\bCD\b", "despliegue continuo"),
    (r"\bsemver\b", "versionado semántico"),
    # Architecture & infra
    (r"\bCLI\b", "C L I"),
    (r"\bAPI\b", "A P I"),
    (r"\bAPIs\b", "A P Is"),
    (r"\bURL\b", "U R L"),
    (r"\bURLs\b", "U R Ls"),
    (r"\bDB\b", "base de datos"),
    (r"\bDBs\b", "bases de datos"),
    (r"\bconfig\b", "configuración"),
    (r"\benv\b", "entorno"),
    (r"\bK8s\b", "Kubernetes"),
    (r"\bPostgreSQL\b", "Postgres"),
    (r"\bPostgres\b", "Postgres"),
    (r"\bUUID\b", "U U I D"),
    (r"\bUUIDs\b", "U U I Ds"),
    (r"\bJWT\b", "J W T"),
    (r"\bJWTs\b", "J W Ts"),
    (r"\bSSH\b", "S S H"),
    (r"\bTLS\b", "T L S"),
    (r"\bSSL\b", "S S L"),
    (r"\bHTTP\b", "H T T P"),
    (r"\bHTTPS\b", "H T T P S"),
    (r"\bJSON\b", "Jeison"),
    (r"\bYAML\b", "Yamel"),
    (r"\bSQL\b", "S Q L"),
    (r"\bSDK\b", "S D K"),
    (r"\bSDKs\b", "S D Ks"),
    (r"\bGUI\b", "G U I"),
    (r"\bIDE\b", "I D E"),
    (r"\bIDEs\b", "I D Es"),
    (r"\bOS\b", "sistema operativo"),
    (r"\bIPC\b", "I P C"),
    (r"\bstdout\b", "salida estándar"),
    (r"\bstderr\b", "error estándar"),
    (r"\bstdin\b", "entrada estándar"),
    (r"\bregex\b", "expresión regular"),
    (r"\bregexp\b", "expresión regular"),
    (r"\bSHA\b", "S H A"),
    (r"\bMD5\b", "M D 5"),
    (r"\basync/await\b", "async await"),
]

# Hardware & performance unit normalizations
UNIT_NORMALIZATIONS: List[Tuple[str, str]] = [
    (r"\b(\d+)\s*ms\b", r"\1 milisegundos"),
    (r"\b(\d+(?:\.\d+)?)\s*s\b", r"\1 segundos"),
    (r"\b(\d+(?:\.\d+)?)\s*KB\b", r"\1 kilobytes"),
    (r"\b(\d+(?:\.\d+)?)\s*MB\b", r"\1 megabytes"),
    (r"\b(\d+(?:\.\d+)?)\s*GB\b", r"\1 gigabytes"),
    (r"\b(\d+(?:\.\d+)?)\s*GHz\b", r"\1 gigahercios"),
    (r"\b(\d+(?:\.\d+)?)\s*MHz\b", r"\1 megahercios"),
    (r"\b(\d+(?:\.\d+)?)\s*kHz\b", r"\1 kilohercios"),
    (r"\b(\d+(?:\.\d+)?)\s*kbps\b", r"\1 kilobits por segundo"),
    (r"\b(\d+(?:\.\d+)?)\s*Mbps\b", r"\1 megabits por segundo"),
    # Version tags: v1.2.3 -> versión 1.2.3
    (r"\bv(\d+\.\d+(?:\.\d+)?)\b", r"versión \1"),
]


# Common Spanish abbreviations
COMMON_ABBREVIATIONS_ES: List[Tuple[str, str]] = [
    (r"\bp\.?\s*ej\.", "por ejemplo"),
    (r"\bej\.", "ejemplo"),
    (r"\baprox\.", "aproximadamente"),
    (r"\betc\.", "etcétera"),
    (r"\bDr\.", "doctor"),
    (r"\bDra\.", "doctora"),
    (r"\bSr\.", "señor"),
    (r"\bSra\.", "señora"),
    (r"\bpág\.", "página"),
    (r"\bpágs\.", "páginas"),
    (r"\bvs\.?\b", "versus"),
    (r"\bcap\.", "capítulo"),
    (r"\bart\.", "artículo"),
    (r"\bref\.", "referencia"),
    (r"\btel\.", "teléfono"),
    (r"\btfno\.", "teléfono"),
    (r"\bEE\.?\s*UU\.?", "Estados Unidos"),
    (r"\b(núm|nro|n°|Nº)\.?\s*(\d+)", r"número \2"),
]

# Common English abbreviations
COMMON_ABBREVIATIONS_EN: List[Tuple[str, str]] = [
    (r"\be\.?\s*g\.", "for example"),
    (r"\bi\.?\s*e\.", "that is"),
    (r"\bapprox\.", "approximately"),
    (r"\betc\.", "etcetera"),
    (r"\bDr\.", "Doctor"),
    (r"\bMr\.", "Mister"),
    (r"\bMrs\.", "Missus"),
    (r"\bMs\.", "Miz"),
    (r"\bvs\.?\b", "versus"),
    (r"\bp\.\s*(\d+)", r"page \1"),
    (r"\bpp\.\s*(\d+)", r"pages \1"),
    (r"\b(no|num)\.?\s*(\d+)", r"number \2"),
]

COMMON_ABBREVIATIONS = COMMON_ABBREVIATIONS_ES


def normalize_currency(text: str, lang: str = "es") -> str:
    """Normalizes currency notations ($45.20, 10€, etc.) into natural spoken words according to language."""
    if lang == "en":
        def _dollar_sub_en(m):
            amount_int = m.group(1)
            amount_dec = m.group(2) if m.lastindex and m.lastindex >= 2 else None
            word = "dollar" if amount_int == "1" else "dollars"
            if amount_dec:
                dec_int = int(amount_dec[:2].ljust(2, "0"))
                cents_word = "cent" if dec_int == 1 else "cents"
                return f"{amount_int} {word} and {dec_int} {cents_word}"
            return f"{amount_int} {word}"

        text = re.sub(r"\$(\d+)(?:[.,](\d{1,2}))?\b", _dollar_sub_en, text)
        text = re.sub(r"\b(\d+)(?:[.,](\d{1,2}))?\s*\$", _dollar_sub_en, text)

        def _euro_sub_en(m):
            amount_int = m.group(1)
            amount_dec = m.group(2) if m.lastindex and m.lastindex >= 2 else None
            word = "euro" if amount_int == "1" else "euros"
            if amount_dec:
                dec_int = int(amount_dec[:2].ljust(2, "0"))
                cents_word = "cent" if dec_int == 1 else "cents"
                return f"{amount_int} {word} and {dec_int} {cents_word}"
            return f"{amount_int} {word}"

        text = re.sub(r"€(\d+)(?:[.,](\d{1,2}))?\b", _euro_sub_en, text)
        text = re.sub(r"\b(\d+)(?:[.,](\d{1,2}))?\s*€", _euro_sub_en, text)

        text = re.sub(r"£(\d+)\b", r"\1 pounds", text)
        text = re.sub(r"\b(\d+)\s*£", r"\1 pounds", text)
        text = re.sub(r"[¥](\d+)\b", r"\1 yen", text)
        text = re.sub(r"\b(\d+)\s*[¥]", r"\1 yen", text)
        return text

    # Spanish (default)
    def _dollar_sub(m):
        amount_int = m.group(1)
        amount_dec = m.group(2) if m.lastindex and m.lastindex >= 2 else None
        word = "dólar" if amount_int == "1" else "dólares"
        if amount_dec:
            dec_int = int(amount_dec[:2].ljust(2, "0"))
            cents_word = "centavo" if dec_int == 1 else "centavos"
            return f"{amount_int} {word} con {dec_int} {cents_word}"
        return f"{amount_int} {word}"

    text = re.sub(r"\$(\d+)(?:[.,](\d{1,2}))?\b", _dollar_sub, text)
    text = re.sub(r"\b(\d+)(?:[.,](\d{1,2}))?\s*\$", _dollar_sub, text)

    def _euro_sub(m):
        amount_int = m.group(1)
        amount_dec = m.group(2) if m.lastindex and m.lastindex >= 2 else None
        word = "euro" if amount_int == "1" else "euros"
        if amount_dec:
            dec_int = int(amount_dec[:2].ljust(2, "0"))
            cents_word = "céntimo" if dec_int == 1 else "céntimos"
            return f"{amount_int} {word} con {dec_int} {cents_word}"
        return f"{amount_int} {word}"

    text = re.sub(r"€(\d+)(?:[.,](\d{1,2}))?\b", _euro_sub, text)
    text = re.sub(r"\b(\d+)(?:[.,](\d{1,2}))?\s*€", _euro_sub, text)

    def _pound_sub(m):
        amount_int = m.group(1)
        word = "libra" if amount_int == "1" else "libras"
        return f"{amount_int} {word}"

    text = re.sub(r"£(\d+)\b", _pound_sub, text)
    text = re.sub(r"\b(\d+)\s*£", _pound_sub, text)

    text = re.sub(r"[¥](\d+)\b", r"\1 yenes", text)
    text = re.sub(r"\b(\d+)\s*[¥]", r"\1 yenes", text)

    return text


def load_user_lexicon() -> Dict[str, str]:
    """Loads optional custom lexicon dictionary from user config."""
    paths = [
        os.environ.get("AGENT_TTS_LEXICON", ""),
        os.path.expanduser("~/.config/agent-tts/lexicon.json"),
        os.path.expanduser("~/.config/herdr-tts/lexicon.json"),
    ]
    for p in paths:
        if p and os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return data
            except Exception:
                pass
    return {}


def strip_ansi(text: str) -> str:
    """Removes ANSI escape codes from text."""
    return ANSI_ESCAPE.sub("", text)


def normalize_technical_terms(text: str, lang: str = "es") -> str:
    """Replaces developer acronyms, currencies, abbreviations and units with speakable expansions."""
    res = text

    # 1. Apply units and version formatting
    for pattern, replacement in UNIT_NORMALIZATIONS:
        res = re.sub(pattern, replacement, res, flags=re.IGNORECASE)

    # 2. Apply currency normalizations
    res = normalize_currency(res, lang=lang)

    # 3. Apply common abbreviations according to language
    abbr_list = COMMON_ABBREVIATIONS_EN if lang == "en" else COMMON_ABBREVIATIONS_ES
    for pattern, replacement in abbr_list:
        res = re.sub(pattern, replacement, res, flags=re.IGNORECASE)

    # 4. Apply base developer lexicon
    for pattern, replacement in BASE_TECHNICAL_LEXICON:
        res = re.sub(pattern, replacement, res, flags=re.IGNORECASE)

    # 5. Apply user custom lexicon if provided
    user_lexicon = load_user_lexicon()
    for term, expansion in user_lexicon.items():
        pattern = rf"\b{re.escape(term)}\b"
        res = re.sub(pattern, expansion, res, flags=re.IGNORECASE)

    return res


# --- Terminal chrome pre-filter -------------------------------------------
#
# OpenCode (and similar TUIs) render a footer status line, a right-hand
# status sidebar, and model/plan banners. When terminal scrollback is
# captured, all of that chrome arrives glued to (or deeply indented next
# to) the real content. The helpers below drop or trim those artifacts
# before turn extraction and deep cleaning run.

# Box-drawing, scrollbar and section glyphs that are pure decoration.
# NOTE: the light '|' and '│' are intentionally NOT listed here:
# clean_agent_text() converts table cell dividers into speech pauses
# ("col — col"), so they must survive this filter.
CHROME_BORDER_RE = re.compile("[▏▎▌▐┃┆╹▀▄■⬝█▣━─═]")

# 'cat -A' encodings of the same glyphs (e.g. M-bM-^TM-^C is U+2503) that
# survive when scrollback was captured through a `cat -A` pipe; the '$'
# suffix is cat -A's end-of-line marker glued right after the glyph.
CAT_A_JUNK_RE = re.compile(r"(?:(?:M-\^\S)|(?:M-[A-Za-z]))+\$?")

# Tokens that mark footer/sidebar material when they directly follow a run
# of 4+ spaces (TUIs glue sidebar columns to content with padding spaces).
CHROME_TOKEN_RE = re.compile(
    r"(?i)(?:"
    r"\bContext\b"
    r"|\d[\d,.]*\s*tokens"
    r"|\d+\s*%\s*used"
    r"|\$\d+(?:\.\d+)?\s*spent"
    r"|\bMCP\b"
    r"|\bLSP\b"
    r"|ctrl\+\w"
    r"|\besc\b"
    r"|OpenCode\s+v?\d"
    r"|[\w~.][\w./-]*:master"
    r"|\w[\w.-]*\s+Connected"
    r"|[•▼▲↳●]"
    r"|\[?[✓✔✕]\]?"
    r")"
)

SPACE_RUN_RE = re.compile(r"(?:\t| {4,})")
SPINNER_RE = re.compile(r"[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]{2,}")
CONTEXT_USAGE_RE = re.compile(r"\d+(?:\.\d+)?[KMk]?\s*\(\d+%\)")
CTRL_HINT_RE = re.compile(r"(?i)ctrl\+\w+\s+(?:commands|shortcuts)")
ESC_INTERRUPT_RE = re.compile(r"(?i)esc\s+(?:to\s+)?interrupt")
OPENCODE_VERSION_RE = re.compile(r"(?i)OpenCode\s+v?\d+(?:\.\d+)+")
CLICK_EXPAND_RE = re.compile(r"(?i)Click to expand")
THOUGHT_TIMING_RE = re.compile(
    r"^\s*\+?\s*Thought\s*[:·]?\s*[\d.]+\s*(?:ms|m|s)(?:\s*\d+\s*(?:ms|m|s))*(?![a-zA-Z])"
)

# Sidebar material and its wrapped continuations live at deep indentation;
# real prose and code never indent this far.
CHROME_INDENT_COLUMNS = 40

# Extended noise classifiers (used by extract_last_turn). They are
# deliberately narrow: only unambiguous chrome matches.
CHROME_BORDER_ONLY_RE = re.compile(r"^[\s▏▎▌▐┃┆╹▀▄■⬝█▣━─═—│|┌┐└┘├┤┬┴┼╭╮╯╰]+$")
CHROME_KEYWORD_RE = re.compile(r"^(?:Context|MCP|LSP)$")
CHROME_NUMBER_UNIT_RE = re.compile(
    r"^\$?\d[\d.,]*[KMk]?%?\s*(?:(?:tokens|used|spent)(?:\s|$)|$)"
)
CHROME_CONNECTED_RE = re.compile(r"^\w[\w.-]*\s+Connected$")
CHROME_MODEL_RE = re.compile(
    r"(?i)(?:Flash|Opus|Sonnet|Haiku|GPT|Gemini|GLM|Claude|Kimi|Qwen|DeepSeek|Grok|Mistral|Llama)"
)
CHROME_PLAN_RE = re.compile(r"(?i)(?:Coding Plan|Pro Plan|Max Plan|Free Plan)")
CHROME_KEYBIND_RE = re.compile(r"(?i)^\s*(?:press|type)?\s*(?:ctrl|alt|esc|shift)\s*\+")


def _leading_columns(line: str) -> int:
    """Returns the width of the leading whitespace with tabs expanded to 8."""
    columns = 0
    for ch in line:
        if ch == " ":
            columns += 1
        elif ch == "\t":
            columns += 8 - (columns % 8)
        else:
            break
    return columns


def _cut_sidebar_glue(line: str) -> str:
    """Trims right-sidebar chunks glued to content after runs of 4+ spaces.

    Repeats the cut at the LAST qualifying run until none remains, so the
    left-side content always survives while every sidebar chunk goes away.
    """
    while True:
        cut_at = None
        for run in SPACE_RUN_RE.finditer(line):
            if CHROME_TOKEN_RE.match(line, run.end()):
                cut_at = run.start()
        if cut_at is None:
            return line
        line = line[:cut_at]


def _strip_chrome_line(line: str) -> Optional[str]:
    """Applies the terminal chrome pre-filter to a single scrollback line.

    Returns the cleaned line, or None when the line is chrome-only and must
    be dropped entirely.
    """
    line = strip_ansi(line)

    # Decode 'cat -A' border artifacts first (they encode box glyphs as
    # plain ASCII), dropping the trailing end-of-line marker '$' they add.
    had_cat_a = bool(CAT_A_JUNK_RE.search(line))
    if had_cat_a:
        line = CAT_A_JUNK_RE.sub(" ", line)
        line = re.sub(r"\$\s*$", "", line)

    # Replace border/scrollbar glyphs with spaces so the indentation
    # analysis below measures real layout columns.
    line = CHROME_BORDER_RE.sub(" ", line)

    # 1. Deep-indented lines are sidebar material or its wrapped
    #    continuations: drop them entirely.
    if _leading_columns(line) >= CHROME_INDENT_COLUMNS:
        return None

    # 2. Cut same-line sidebar chunks glued after 4+ space runs.
    line = _cut_sidebar_glue(line)

    # 3. Remove unambiguous inline chrome tokens (spinners, usage meters,
    #    keybind hints, version banners, collapsible-section markers).
    line = SPINNER_RE.sub(" ", line)
    line = CONTEXT_USAGE_RE.sub(" ", line)
    line = CTRL_HINT_RE.sub(" ", line)
    line = ESC_INTERRUPT_RE.sub(" ", line)
    line = OPENCODE_VERSION_RE.sub(" ", line)
    line = CLICK_EXPAND_RE.sub(" ", line)
    line = THOUGHT_TIMING_RE.sub("", line)

    # 4. Inline removals can expose padding runs (e.g. dropping a leading
    #    '+ Thought: 2m 6s' leaves the wrapped sidebar continuation deeply
    #    indented): cut and re-check indentation once more.
    line = _cut_sidebar_glue(line)
    if _leading_columns(line) >= CHROME_INDENT_COLUMNS:
        return None

    # Keep the leading whitespace: prompt detection relies on it so that
    # indented code comments are never mistaken for prompt lines.
    if not line.strip():
        return None
    return line.rstrip()


def extract_last_turn(text: str) -> str:
    """Extracts only the last agent response from a terminal scrollback."""
    lines = []
    for raw_line in text.splitlines():
        cleaned = _strip_chrome_line(raw_line)
        if cleaned is not None:
            lines.append(cleaned)
    if not lines:
        # Everything was chrome; never fall back to the raw noisy text.
        return ""
    prefiltered = "\n".join(lines)

    # Common terminal prompts (must be at start of line, not indented code comments)
    prompt_patterns = [
        re.compile(r"^[❯>]\s+\S+"),
        re.compile(r"^(?:\$|#)\s+\S+"),
        re.compile(r"^(?:USER|Human|User):\s+\S+", re.IGNORECASE),
    ]

    tool_line_re = re.compile(r"^[●○◐◑◒◓◔◕⣾⣽⣻⢿⡿⣟⣯⣷]\s+(?:[A-Za-z]+\(|Running\b)")

    def is_terminal_noise(cl: str) -> bool:
        if not cl or cl in (">", "❯", "$", "#", "└"):
            return True
        if tool_line_re.match(cl):
            return True
        if re.search(r"(?i)(?:tokens?:|tokens\b|\bthinking\b|\brunning command|\btip: press|gemini \d|\bquotas:)", cl):
            return True
        # Unambiguous TUI chrome classifiers: border-only lines, bare
        # sidebar keywords, number+unit meters, "* Connected" status lines,
        # model/plan banners ("Name · Model · Plan") and keybind hints.
        if CHROME_BORDER_ONLY_RE.match(cl):
            return True
        if CHROME_KEYWORD_RE.match(cl):
            return True
        if CHROME_NUMBER_UNIT_RE.match(cl):
            return True
        if CHROME_CONNECTED_RE.match(cl):
            return True
        if "·" in cl and (CHROME_MODEL_RE.search(cl) or CHROME_PLAN_RE.search(cl)):
            return True
        if CHROME_KEYBIND_RE.match(cl):
            return True
        return False

    prompt_indices = []
    for idx, line in enumerate(lines):
        cl = strip_ansi(line)  # Do NOT lstrip to avoid matching indented comments
        for p in prompt_patterns:
            if p.match(cl):
                prompt_indices.append(idx)
                break

    def get_clean_slice(slice_lines):
        filtered = []
        for l in slice_lines:
            cl = strip_ansi(l).strip()
            if not is_terminal_noise(cl):
                filtered.append(l)
        return "\n".join(filtered).strip()

    if not prompt_indices:
        return get_clean_slice(lines) or prefiltered

    last_idx = prompt_indices[-1]
    candidate = get_clean_slice(lines[last_idx + 1 :])
    candidate_words = re.findall(r"[a-zA-ZáéíóúÁÉÍÓÚñÑ]{3,}", candidate)

    # If the candidate has fewer than 6 real words, the agent hasn't responded to the last prompt yet!
    # Fall back to the previous completed turn:
    if len(candidate_words) < 6:
        if len(prompt_indices) >= 2:
            prev_idx = prompt_indices[-2]
            completed = get_clean_slice(lines[prev_idx + 1 : last_idx])
            if len(re.findall(r"[a-zA-ZáéíóúÁÉÍÓÚñÑ]{3,}", completed)) >= 6:
                return completed
        else:
            before = get_clean_slice(lines[:last_idx])
            if len(re.findall(r"[a-zA-ZáéíóúÁÉÍÓÚñÑ]{3,}", before)) >= 6:
                return before

    return candidate or prefiltered


def clean_agent_text(
    text: str,
    max_chars: int = 0,
    summarize: bool = False,
    lang: str = "es",
    pre_extracted: bool = False,
) -> str:
    """Deeply cleans terminal/agent prose, removing borders, token quotas, and code blocks.
    
    If summarize is True, runs Smart Architectural Summarizer to condense text before speech.
    When pre_extracted is True, the input is already the final agent response
    (e.g. read from a structured transcript), so the scrollback extraction
    stage (extract_last_turn) is skipped and only message cleaning runs.
    """
    if not pre_extracted:
        text = extract_last_turn(text)
    text = strip_ansi(text)

    # Redact secrets once, before any other stage (including the TL;DR
    # summarizer branch below, which echoes input sentences): every
    # downstream consumer — speech, ntfy.sh notifications, podcast RSS —
    # then inherits sanitized text.
    text = redact_secrets(text)

    if summarize:
        from agent_tts.summarizer import summarize as run_summarize
        return run_summarize(text)

    # 1. Remove agent/harness metadata tags and banners (e.g. [Claude], [OpenCode], [Aider], [Herdr], [Codex])
    text = re.sub(r"^\[[A-Za-z0-9_.-]+\][^\n]*\n?", "", text, flags=re.MULTILINE)

    # 2. Remove token count lines & CLI status indicators
    text = re.sub(r"(?i)\b\d+[\d\.,]*\s*[kKmM]?\s*(?:tokens?|in|out|cached|total)\b[^\n]*", "", text)
    text = re.sub(r"(?i)tokens?:\s*[\d\.,\s/kKmM]+[^\n]*", "", text)
    text = re.sub(r"(?i)cost:\s*\$[\d\.]+[^\n]*", "", text)
    text = re.sub(r"(?i)elapsed:\s*[\d\.]+s[^\n]*", "", text)

    # 3. Handle code blocks: omit long blocks, keep short one-liners
    def code_block_sub(match):
        code = match.group(1).strip()
        lines = code.split("\n")
        if len(lines) <= 1 and len(code) < 60:
            return f" comando: {code} "
        return " [bloque de código omitido] "

    text = re.sub(r"```[a-zA-Z0-9_-]*\n?(.*?)```", code_block_sub, text, flags=re.DOTALL)
    text = re.sub(r"`([^`\n]+)`", r"\1", text)

    # 4. Remove horizontal rules and ASCII/Unicode box-drawing borders
    text = re.sub(r"^[-=_~*─━┄┅┈┉┌┐└┘├┤┬┴┼╭╮╯╰]{3,}$", "", text, flags=re.MULTILINE)
    text = re.sub(r"[┌┐└┘├┤┬┴┼╭╮╯╰]", "", text)
    text = re.sub(r"^[─━┄┅┈┉]{2,}", "", text, flags=re.MULTILINE)

    # Convert table vertical dividers to conversational pauses
    text = re.sub(r"(?<=\S)\s*[│|]\s*(?=\S)", " — ", text)
    text = re.sub(r"^[│|]\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*[│|]$", "", text, flags=re.MULTILINE)

    # 5. Clean URLs
    text = re.sub(r"https?://\S+", "enlace web", text)

    # 6. Apply technical lexicon and unit normalizations
    text = normalize_technical_terms(text, lang=lang)

    # 7. Collapse excessive whitespace and blank lines
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    # 8. Truncate if max_chars specified
    if max_chars > 0 and len(text) > max_chars:
        truncated = text[:max_chars]
        last_punct = max(truncated.rfind("."), truncated.rfind("!"), truncated.rfind("?"))
        if last_punct > max_chars * 0.6:
            text = truncated[: last_punct + 1]
        else:
            text = truncated + "..."

    return text
