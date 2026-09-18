"""Text sanitization, normalization, and terminal cleaner for agent speech."""

import re

ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

# Common developer technical replacements for clearer speech
TECHNICAL_LEXICON = [
    (r"\bPR\b", "pull request"),
    (r"\bPRs\b", "pull requests"),
    (r"\brepo\b", "repositorio"),
    (r"\brepos\b", "repositorios"),
    (r"\bCLI\b", "C L I"),
    (r"\bAPI\b", "A P I"),
    (r"\bAPIs\b", "A P Is"),
    (r"\bURL\b", "U R L"),
    (r"\bURLs\b", "U R Ls"),
    (r"\bDB\b", "base de datos"),
    (r"\bconfig\b", "configuración"),
    (r"\benv\b", "entorno"),
]


def strip_ansi(text: str) -> str:
    """Removes ANSI escape codes from text."""
    return ANSI_ESCAPE.sub("", text)


def normalize_technical_terms(text: str) -> str:
    """Replaces common developer acronyms with speakable expansions."""
    res = text
    for pattern, replacement in TECHNICAL_LEXICON:
        res = re.sub(pattern, replacement, res, flags=re.IGNORECASE)
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
        # Check for multi-line user prompt continuation
        response_start = last_prompt_idx + 1
        while response_start < len(lines):
            raw_line = lines[response_start]
            clean_line = strip_ansi(raw_line).strip()
            # If line is indented or looks like prompt continuation without empty line
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


def clean_agent_text(text: str, max_chars: int = 0) -> str:
    """Deeply cleans terminal/agent prose, removing borders, token quotas, and code blocks."""
    text = extract_last_turn(text)
    text = strip_ansi(text)

    # 1. Remove Herdr / Collie metadata tags and banners
    text = re.sub(r"\[Herdr\][^\n]*\n?", "", text)
    text = re.sub(r"\[Claude\][^\n]*\n?", "", text)
    text = re.sub(r"\[OpenCode\][^\n]*\n?", "", text)

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

    # 6. Apply technical lexicon substitutions
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
