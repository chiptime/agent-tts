"""Text sanitization, normalization, and terminal cleaner for agent speech."""

import json
import os
import re
from typing import Dict, List, Optional, Tuple

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
COMMON_ABBREVIATIONS: List[Tuple[str, str]] = [
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


def normalize_currency(text: str) -> str:
    """Normalizes currency notations ($45.20, 10€, etc.) into natural spoken words."""
    def _dollar_sub(m):
        amount_int = m.group(1)
        amount_dec = m.group(2) if m.lastindex and m.lastindex >= 2 else None
        word = "dólar" if amount_int == "1" else "dólares"
        if amount_dec:
            dec_int = int(amount_dec[:2].ljust(2, "0"))
            cents_word = "centavo" if dec_int == 1 else "centavos"
            return f"{amount_int} {word} con {dec_int} {cents_word}"
        return f"{amount_int} {word}"

    # $45.20 or $45
    text = re.sub(r"\$(\d+)(?:[.,](\d{1,2}))?\b", _dollar_sub, text)
    # 45$ or 45.20$
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

    # €45.20 or €45
    text = re.sub(r"€(\d+)(?:[.,](\d{1,2}))?\b", _euro_sub, text)
    # 45€ or 45.20€
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


def normalize_technical_terms(text: str) -> str:
    """Replaces developer acronyms, currencies, abbreviations and units with speakable expansions."""
    res = text

    # 1. Apply units and version formatting
    for pattern, replacement in UNIT_NORMALIZATIONS:
        res = re.sub(pattern, replacement, res, flags=re.IGNORECASE)

    # 2. Apply currency normalizations ($45.20, €10, etc.)
    res = normalize_currency(res)

    # 3. Apply common abbreviations (ej., etc., aprox., Dr.)
    for pattern, replacement in COMMON_ABBREVIATIONS:
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


def extract_last_turn(text: str) -> str:
    """Extracts only the last agent response from a terminal scrollback."""
    lines = text.splitlines()
    if not lines:
        return text

    # Common terminal prompts
    prompt_patterns = [
        re.compile(r"^❯\s+"),
        re.compile(r"^>\s+"),
        re.compile(r"^\$\s+"),
        re.compile(r"^#\s+"),
        re.compile(r"^USER:\s+", re.IGNORECASE),
        re.compile(r"^Human:\s+", re.IGNORECASE),
        re.compile(r"^User:\s+", re.IGNORECASE),
    ]

    last_prompt_idx = -1
    for idx, line in enumerate(lines):
        clean_l = strip_ansi(line).strip()
        for p in prompt_patterns:
            if p.match(clean_l):
                last_prompt_idx = idx
                break

    if last_prompt_idx != -1:
        response_start = last_prompt_idx + 1
        while response_start < len(lines):
            raw_line = lines[response_start]
            clean_line = strip_ansi(raw_line).strip()
            if not clean_line:
                response_start += 1
                break
            if raw_line.startswith("   ") or raw_line.startswith("\t"):
                response_start += 1
            else:
                break

        turn_lines = lines[response_start:]
        if turn_lines:
            return "\n".join(turn_lines)

    return text


def clean_agent_text(text: str, max_chars: int = 0, summarize: bool = False) -> str:
    """Deeply cleans terminal/agent prose, removing borders, token quotas, and code blocks.
    
    If summarize is True, runs Smart Architectural Summarizer to condense text before speech.
    """
    text = extract_last_turn(text)
    text = strip_ansi(text)

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
    text = normalize_technical_terms(text)

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
