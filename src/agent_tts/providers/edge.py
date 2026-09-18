"""Microsoft Edge Neural TTS provider (zero-config, high quality, 100% free)."""

import re
from typing import Callable, List, Optional
import edge_tts

from agent_tts.boundaries import BoundaryMap, Sentence, SynthesisResult, Word
from agent_tts.constants import DEFAULT_VOICE, VOICE_MAP
from agent_tts.providers.base import TTSProvider


class EdgeTTSProvider(TTSProvider):
    """Microsoft Edge Neural TTS."""

    name = "edge"

    def resolve_voice(self, voice: str) -> str:
        v = (voice or "").lower().strip()
        return VOICE_MAP.get(v, voice if voice else DEFAULT_VOICE)

    async def synthesize(
        self,
        text: str,
        voice: str,
        rate: str,
        volume: str = "+0%",
        pitch: str = "+0Hz",
        stop_checker: Optional[Callable[[], bool]] = None,
    ) -> SynthesisResult:
        resolved = self.resolve_voice(voice)
        communicate = edge_tts.Communicate(
            text=text,
            voice=resolved,
            rate=rate,
            volume=volume,
            pitch=pitch,
            boundary="WordBoundary",
        )
        mp3_chunks = []
        raw_words = []

        async for chunk in communicate.stream():
            if stop_checker and stop_checker():
                return SynthesisResult(b"", BoundaryMap())
            if chunk["type"] == "audio":
                mp3_chunks.append(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                raw_words.append(chunk)

        mp3_data = b"".join(mp3_chunks)
        if not mp3_data:
            return SynthesisResult(b"", BoundaryMap())

        paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]
        if not paragraphs:
            paragraphs = [text.strip()] if text.strip() else [""]

        sentences: List[Sentence] = []
        words: List[Word] = []
        sent_global_idx = 0
        word_global_idx = 0
        ew_idx = 0
        total_ew = len(raw_words)

        for p_idx, p_text in enumerate(paragraphs):
            raw_sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", p_text) if s.strip()]
            if not raw_sents:
                raw_sents = [p_text]

            for s_text in raw_sents:
                s_words = s_text.split()
                s_start = None
                s_end = 0.0

                for sw in s_words:
                    if ew_idx < total_ew:
                        ew = raw_words[ew_idx]
                        ew_idx += 1
                        w_start = ew["offset"] / 10_000_000.0
                        w_dur = ew["duration"] / 10_000_000.0
                    else:
                        w_start = s_end + 0.05
                        w_dur = 0.25

                    w_obj = Word(
                        index=word_global_idx,
                        sentence_index=sent_global_idx,
                        start_sec=w_start,
                        duration_sec=w_dur,
                        text=sw,
                    )
                    words.append(w_obj)
                    word_global_idx += 1

                    if s_start is None:
                        s_start = w_start
                    s_end = max(s_end, w_start + w_dur)

                if s_start is None:
                    s_start = 0.0
                    s_end = 0.5

                sent = Sentence(
                    index=sent_global_idx,
                    paragraph_index=p_idx,
                    start_sec=s_start,
                    duration_sec=max(0.1, s_end - s_start),
                    text=s_text,
                )
                sentences.append(sent)
                sent_global_idx += 1

        boundary_map = BoundaryMap(sentences=sentences, words=words)
        return SynthesisResult(mp3_data, boundary_map)
