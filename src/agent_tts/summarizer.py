"""Smart Architectural Summarizer (TL;DR Pre-Flight).

Condenses massive terminal outputs (git diffs, test logs, stack traces, compiler errors,
and verbose agent responses) into crisp, punchy voice recaps before synthesis.
Zero external API requirement, zero latency, 100% offline heuristics.
"""

import re
from typing import List, Optional, Tuple


def _extract_git_diff(text: str) -> Optional[str]:
    """Detects and summarizes git diff patches."""
    diff_lines = re.findall(r"^diff --git a/(.*?) b/(.*?)$", text, flags=re.MULTILINE)
    if not diff_lines:
        return None

    files = [b for _, b in diff_lines]
    additions = len(re.findall(r"^\+[^+]", text, flags=re.MULTILINE))
    deletions = len(re.findall(r"^-[^-]", text, flags=re.MULTILINE))

    num_files = len(files)
    if num_files == 1:
        file_desc = files[0]
    elif num_files == 2:
        file_desc = f"{files[0]} y {files[1]}"
    else:
        file_desc = f"{files[0]}, {files[1]} y {num_files - 2} más"

    return f"Diff de Git: {num_files} archivo{'s' if num_files != 1 else ''} con cambios ({file_desc}). {additions} líneas añadidas y {deletions} eliminadas."


def _extract_git_status(text: str) -> Optional[str]:
    """Detects and summarizes git status output."""
    if "On branch " not in text and "Changes not staged for commit:" not in text and "nothing to commit" not in text:
        return None

    branch_match = re.search(r"On branch\s+(\S+)", text)
    branch = branch_match.group(1) if branch_match else "actual"

    if "nothing to commit, working tree clean" in text:
        return f"Estado de Git en rama {branch}: directorio de trabajo limpio, nada que confirmar."

    modified = len(re.findall(r"modified:\s+(\S+)", text))
    untracked = 0
    if "Untracked files:" in text:
        untracked_block = text.split("Untracked files:")[1].split("\n\n")[0]
        untracked = len([
            line.strip() for line in untracked_block.splitlines()
            if line.strip() and not line.strip().startswith("(") and not line.strip().startswith("# (")
        ])

    parts = []
    if modified > 0:
        parts.append(f"{modified} modificado{'s' if modified != 1 else ''}")
    if untracked > 0:
        parts.append(f"{untracked} sin seguimiento")

    detail = ", ".join(parts) if parts else "cambios pendientes"
    return f"Estado de Git en rama {branch}: {detail}."


def _extract_test_results(text: str) -> Optional[str]:
    """Detects and summarizes output from various test runners (pytest, jest, cargo, unittest)."""
    # Python unittest: Ran 16 tests in 0.404s\n\nOK or FAILED (failures=1)
    unittest_match = re.search(r"Ran\s+(\d+)\s+tests?\s+in\s+([\d\.]+)s.*?\n\n(OK|FAILED[^\n]*)", text, flags=re.DOTALL)
    if unittest_match:
        count, dur, status = unittest_match.groups()
        if "OK" in status:
            return f"Pruebas finalizadas con éxito: {count} pruebas pasadas en {dur} segundos."
        return f"Pruebas con fallos: {count} pruebas ejecutadas en {dur} segundos con errores."

    # Pytest: === 15 passed, 1 failed in 2.34s ===
    pytest_match = re.search(r"=+ (.*?) in ([\d\.]+)s =+", text)
    if pytest_match:
        summary_str, dur = pytest_match.groups()
        return f"Resultado de Pytest en {dur} segundos: {summary_str}."

    # Jest / Vitest: Tests: 1 failed, 15 passed, 16 total
    jest_match = re.search(r"Tests:\s+([^\n]+)", text)
    if jest_match:
        return f"Resultado de pruebas: {jest_match.group(1).strip()}."

    # Cargo test: test result: ok. 12 passed; 0 failed; 0 ignored
    cargo_match = re.search(r"test result:\s+(\w+)\.\s+([\d]+ passed[^\n]*)", text)
    if cargo_match:
        status, details = cargo_match.groups()
        clean_details = details.replace(";", ",")
        return f"Resultado de Cargo test ({status}): {clean_details}."

    return None


def _extract_traceback(text: str) -> Optional[str]:
    """Detects and summarizes Python tracebacks or JS unhandled exceptions."""
    if "Traceback (most recent call last):" in text:
        lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
        last_line = lines[-1] if lines else "Error desconocido"

        # Find location of last frame
        file_matches = re.findall(r'File "([^"]+)", line (\d+)', text)
        loc = ""
        if file_matches:
            last_file, last_line_no = file_matches[-1]
            short_file = last_file.split("/")[-1]
            loc = f" en {short_file} línea {last_line_no}"

        return f"Excepción detectada{loc}: {last_line}."

    # JavaScript / Node error
    js_match = re.search(r"^([A-Z][a-zA-Z]+Error):\s*([^\n]+)", text, flags=re.MULTILINE)
    if js_match:
        err_type, err_msg = js_match.groups()
        return f"Error de ejecución {err_type}: {err_msg}."

    return None


def _extract_compiler_error(text: str) -> Optional[str]:
    """Detects and summarizes compiler or type checker errors."""
    # Rustc: error[E0308]: mismatched types ... --> src/foo.rs:10:5
    rust_match = re.search(r"error\[E\d+\]:\s*([^\n]+).*?-->\s*([^:\n]+):(\d+)", text, flags=re.DOTALL)
    if rust_match:
        msg, file_path, line_no = rust_match.groups()
        short_file = file_path.split("/")[-1]
        return f"Error de compilación en {short_file} línea {line_no}: {msg}."

    # TypeScript: error TS2322: Type 'string' is not assignable to type 'number'.
    ts_match = re.search(r"(?:error\s+TS\d+:\s*)([^\n]+)", text)
    if ts_match:
        return f"Error de TypeScript: {ts_match.group(1)}."

    return None


def _split_sentences(text: str) -> List[str]:
    """Splits text into cleaned sentences preserving semantic units."""
    raw = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    cleaned = []
    for s in raw:
        # Ignore isolated bullet symbols or markdown artifacts
        s_clean = re.sub(r"^[-*•#\d\.\)\s]+", "", s).strip()
        if len(s_clean) > 8:
            cleaned.append(s_clean)
    return cleaned


def summarize_prose(text: str, max_sentences: int = 2) -> str:
    """Condenses long agent prose or markdown reports to key takeaway sentences."""
    # Check for markdown headers (sections)
    headers = re.findall(r"^#{1,3}\s+(.+)$", text, flags=re.MULTILINE)

    sentences = _split_sentences(text)
    if not sentences:
        return text[:200]

    if len(sentences) <= max_sentences:
        return " ".join(sentences)

    lead_sentence = sentences[0]
    # Ensure lead sentence ends with punctuation
    if not lead_sentence.endswith((".", "!", "?")):
        lead_sentence += "."

    # Pick the most informative closing sentence (conclusion, summary, or next action)
    closing_sentence = sentences[-1]
    for s in reversed(sentences[1:]):
        lower = s.lower()
        if any(w in lower for w in ("conclusión", "resumen", "listo", "completado", "éxito", "siguiente", "paso", "resultado", "final")):
            closing_sentence = s
            break

    if not closing_sentence.endswith((".", "!", "?")):
        closing_sentence += "."

    if lead_sentence == closing_sentence:
        return lead_sentence

    result = f"{lead_sentence} {closing_sentence}"
    if headers and len(headers) >= 3 and len(result) < 180:
        clean_headers = ", ".join(headers[:3])
        result += f" Cubre: {clean_headers}."

    return result


def summarize(text: str, max_sentences: int = 2) -> str:
    """Main entry point: detects content type and generates a punchy TL;DR speech recap."""
    text_clean = text.strip()
    if not text_clean:
        return ""

    # 1. Specialized technical detections
    git_diff_summary = _extract_git_diff(text_clean)
    if git_diff_summary:
        return git_diff_summary

    git_status_summary = _extract_git_status(text_clean)
    if git_status_summary:
        return git_status_summary

    test_summary = _extract_test_results(text_clean)
    if test_summary:
        return test_summary

    traceback_summary = _extract_traceback(text_clean)
    if traceback_summary:
        return traceback_summary

    compiler_summary = _extract_compiler_error(text_clean)
    if compiler_summary:
        return compiler_summary

    # 2. General prose condensation
    return summarize_prose(text_clean, max_sentences=max_sentences)
