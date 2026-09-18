"""Microsoft Edge Neural TTS provider (zero-config, high quality, 100% free)."""

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
            boundary="SentenceBoundary",
        )
        mp3_chunks = []
        raw_sentences = []

        async for chunk in communicate.stream():
            if stop_checker and stop_checker():
                return SynthesisResult(b"", BoundaryMap())
            if chunk["type"] == "audio":
                mp3_chunks.append(chunk["data"])
            elif chunk["type"] == "SentenceBoundary":
                raw_sentences.append(chunk)

        mp3_data = b"".join(mp3_chunks)
        if not mp3_data:
            return SynthesisResult(b"", BoundaryMap())

        sentences: List[Sentence] = []
        words: List[Word] = []
        word_global_idx = 0

        for idx, s in enumerate(raw_sentences):
            start_sec = s["offset"] / 10_000_000.0
            duration_sec = s["duration"] / 10_000_000.0
            s_text = s["text"]
            sentences.append(
                Sentence(
                    index=idx,
                    start_sec=start_sec,
                    duration_sec=duration_sec,
                    text=s_text,
                )
            )
            raw_words = s_text.split()
            if raw_words:
                w_dur = duration_sec / len(raw_words)
                w_time = start_sec
                for w in raw_words:
                    words.append(
                        Word(
                            index=word_global_idx,
                            sentence_index=idx,
                            start_sec=w_time,
                            duration_sec=w_dur,
                            text=w,
                        )
                    )
                    w_time += w_dur
                    word_global_idx += 1

        boundary_map = BoundaryMap(sentences=sentences, words=words)
        return SynthesisResult(mp3_data, boundary_map)
