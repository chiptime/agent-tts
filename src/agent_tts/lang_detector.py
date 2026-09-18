"""Automatic language detection and dynamic voice switching for multilingual speech."""

import re
from typing import List, Tuple

# Most distinctive frequency words for Spanish and English
ES_WORDS = {
    "el", "la", "de", "que", "en", "los", "del", "las", "por", "un", "para", "con",
    "no", "una", "su", "al", "lo", "como", "más", "pero", "sus", "este", "ya", "o",
    "porque", "cuando", "muy", "sin", "sobre", "también", "me", "hasta", "hay",
    "donde", "quien", "desde", "todo", "nos", "durante", "todos", "uno", "les",
    "ni", "contra", "otros", "fueron", "ese", "eso", "había", "ante", "ellos",
    "esto", "mí", "antes", "algunos", "qué", "unos", "yo", "otro", "otras", "otra",
    "él", "tanto", "esa", "estos", "mucho", "quienes", "nada", "muchos", "cual",
    "sea", "poco", "ella", "estar", "haber", "estas", "estaba", "estamos", "algunas",
    "algo", "nosotros", "he", "has", "ha", "hemos", "han", "solucionar", "ejecutar",
    "archivo", "archivos", "código", "función", "repositorio", "pruebas", "cambios",
}

EN_WORDS = {
    "the", "be", "to", "of", "and", "a", "in", "that", "have", "i", "it", "for",
    "not", "on", "with", "he", "as", "you", "do", "at", "this", "but", "his",
    "by", "from", "they", "we", "say", "her", "she", "or", "an", "will", "my",
    "one", "all", "would", "there", "their", "what", "so", "up", "out", "if",
    "about", "who", "get", "which", "go", "me", "when", "make", "can", "like",
    "time", "no", "just", "him", "know", "take", "people", "into", "year", "your",
    "good", "some", "could", "them", "see", "other", "than", "then", "now", "look",
    "only", "come", "its", "over", "think", "also", "back", "after", "use", "two",
    "how", "our", "work", "first", "well", "way", "even", "new", "want", "because",
    "any", "these", "give", "day", "most", "us", "error", "failed", "found", "fixed",
}

SPANISH_CHARS = re.compile(r"[áéíóúüñ¿¡]", re.IGNORECASE)


def detect_language(text: str, default: str = "es") -> str:
    """Detects dominant language ('es' or 'en') for a text segment."""
    if not text or not text.strip():
        return default

    # Check for unmistakable Spanish characters
    if SPANISH_CHARS.search(text):
        return "es"

    tokens = re.findall(r"\b[a-zA-ZáéíóúüñÁÉÍÓÚÜÑ]+\b", text.lower())
    if not tokens:
        return default

    es_score = sum(1 for t in tokens if t in ES_WORDS)
    en_score = sum(1 for t in tokens if t in EN_WORDS)

    if es_score > en_score:
        return "es"
    if en_score > es_score:
        return "en"

    return default


def segment_by_language(text: str, default_lang: str = "es") -> List[Tuple[str, str]]:
    """Splits text into contiguous sentences grouped by detected language."""
    raw_sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    if not raw_sentences:
        return [(default_lang, text)]

    classified = []
    current_lang = default_lang
    for s in raw_sentences:
        detected = detect_language(s, default=current_lang)
        classified.append((detected, s))

    # Group adjacent sentences sharing the same language
    segments: List[Tuple[str, str]] = []
    current_chunk: List[str] = []
    active_lang = classified[0][0]

    for lang, s_text in classified:
        if lang == active_lang:
            current_chunk.append(s_text)
        else:
            if current_chunk:
                segments.append((active_lang, " ".join(current_chunk)))
            active_lang = lang
            current_chunk = [s_text]

    if current_chunk:
        segments.append((active_lang, " ".join(current_chunk)))

    return segments


def resolve_voice_for_language(base_voice: str, target_lang: str, provider: str = "edge") -> str:
    """Selects an appropriate voice variant for the target language preserving gender."""
    is_male = any(m in base_voice.lower() for m in ("alvaro", "jorge", "guy", "onyx", "echo", "adam", "arnold"))

    if target_lang == "en":
        if provider == "openai":
            return "onyx" if is_male else "nova"
        if provider.startswith("eleven"):
            return "adam" if is_male else "rachel"
        return "en-US-GuyNeural" if is_male else "en-US-JennyNeural"

    # Default to Spanish
    if provider == "openai":
        return "onyx" if is_male else "nova"
    if provider.startswith("eleven"):
        return "adam" if is_male else "rachel"
    return "es-ES-AlvaroNeural" if is_male else "es-ES-ElviraNeural"
