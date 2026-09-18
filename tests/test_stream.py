import array
import unittest

from agent_tts.audio import AudioSession
from agent_tts.boundaries import BoundaryMap, Paragraph, Sentence, Word
from agent_tts.cli import shift_boundary_map, split_sentence_groups


def make_decoded(frames, sample_rate=24000, nchannels=1, sample_width=2):
    """Builds a minimal stand-in for a miniaudio decoded sound with the given shape."""

    class FakeDecoded:
        pass

    decoded = FakeDecoded()
    decoded.sample_rate = sample_rate
    decoded.nchannels = nchannels
    decoded.sample_width = sample_width
    decoded.samples = array.array("h", [0] * (frames * nchannels))
    return decoded


class TestSplitSentenceGroups(unittest.TestCase):
    def test_empty_text_returns_empty_list(self):
        self.assertEqual(split_sentence_groups(""), [])
        self.assertEqual(split_sentence_groups("   \n \t "), [])

    def test_short_text_is_single_group(self):
        text = "Hello world. This is short."
        self.assertEqual(split_sentence_groups(text), [text])

    def test_greedy_packing_respects_max_chars(self):
        text = "One two. Three four. Five six. Seven eight."
        groups = split_sentence_groups(text, max_chars=16)
        self.assertGreater(len(groups), 1)
        for group in groups:
            self.assertLessEqual(len(group), 16)
        # No text lost or reordered.
        self.assertEqual(" ".join(groups), text)

    def test_long_sentence_split_on_commas(self):
        sentence = "A" * 300 + ", " + "B" * 300
        groups = split_sentence_groups(sentence, max_chars=250)
        self.assertEqual(groups, ["A" * 300, "B" * 300])

    def test_long_sentence_without_commas_stays_whole(self):
        text = "x" * 1000
        self.assertEqual(split_sentence_groups(text, max_chars=250), [text])

    def test_newlines_act_as_sentence_separators(self):
        groups = split_sentence_groups("First line here\nSecond line here", max_chars=250)
        self.assertEqual(groups, ["First line here Second line here"])

    def test_never_returns_empty_for_non_empty_text(self):
        for text in (".", "a", "...", "No punctuation at all here"):
            self.assertTrue(split_sentence_groups(text))


class TestShiftBoundaryMap(unittest.TestCase):
    def _make_bmap(self):
        sentences = [
            Sentence(index=0, start_sec=0.0, duration_sec=1.0, text="One.", paragraph_index=0),
            Sentence(index=1, start_sec=1.0, duration_sec=1.5, text="Two.", paragraph_index=1),
        ]
        words = [
            Word(index=0, sentence_index=0, start_sec=0.0, duration_sec=0.5, text="One."),
            Word(index=1, sentence_index=1, start_sec=1.0, duration_sec=0.5, text="Two."),
        ]
        paragraphs = [
            Paragraph(index=0, start_sec=0.0, duration_sec=1.0, text="One.", sentence_indices=[0]),
            Paragraph(index=1, start_sec=1.0, duration_sec=1.5, text="Two.", sentence_indices=[1]),
        ]
        return BoundaryMap(sentences=sentences, words=words, paragraphs=paragraphs)

    def test_offsets_and_index_rebasing(self):
        shifted = shift_boundary_map(
            self._make_bmap(),
            offset_sec=7.5,
            sent_index_base=10,
            word_index_base=20,
            paragraph_index_base=3,
        )
        self.assertEqual([s.index for s in shifted.sentences], [10, 11])
        self.assertEqual([s.start_sec for s in shifted.sentences], [7.5, 8.5])
        self.assertEqual([s.duration_sec for s in shifted.sentences], [1.0, 1.5])
        self.assertEqual([s.paragraph_index for s in shifted.sentences], [3, 4])
        self.assertEqual([w.index for w in shifted.words], [20, 21])
        self.assertEqual([w.sentence_index for w in shifted.words], [10, 11])
        self.assertEqual([w.start_sec for w in shifted.words], [7.5, 8.5])
        self.assertEqual([p.index for p in shifted.paragraphs], [3, 4])
        self.assertEqual([p.start_sec for p in shifted.paragraphs], [7.5, 8.5])
        self.assertEqual(shifted.paragraphs[1].sentence_indices, [11])

    def test_original_map_is_not_mutated(self):
        bmap = self._make_bmap()
        shift_boundary_map(bmap, offset_sec=5.0, sent_index_base=2, word_index_base=4, paragraph_index_base=1)
        self.assertEqual(bmap.sentences[0].start_sec, 0.0)
        self.assertEqual(bmap.sentences[0].index, 0)
        self.assertEqual(bmap.words[1].start_sec, 1.0)
        self.assertEqual(bmap.paragraphs[1].index, 1)


class TestAudioSessionStreaming(unittest.TestCase):
    def _make_session(self, frames):
        session = AudioSession()
        session.sample_rate = 24000
        session.nchannels = 1
        session.bytes_per_sample = 2
        session.frame_size = 2
        session.raw_bytes = bytes(frames * 2)
        session.total_frames = frames
        session.current_frame = 0
        session.state["status"] = "playing"
        return session

    def test_prepare_pcm_then_append_grows_buffer(self):
        session = AudioSession()
        session.prepare_pcm(make_decoded(100))
        self.assertTrue(session._buffer_loaded)
        self.assertEqual(session.total_frames, 100)
        self.assertEqual(session.current_frame, 0)
        self.assertTrue(session.append_pcm(make_decoded(50)))
        self.assertEqual(session.total_frames, 150)

    def test_append_pcm_grows_buffer_and_total(self):
        session = AudioSession()
        self.assertTrue(session.append_pcm(make_decoded(100)))
        self.assertEqual(session.total_frames, 100)
        self.assertEqual(len(session.raw_bytes), 200)
        self.assertAlmostEqual(session.state["total"], 100 / 24000)
        self.assertTrue(session.append_pcm(make_decoded(50)))
        self.assertEqual(session.total_frames, 150)
        self.assertAlmostEqual(session.state["total"], 150 / 24000)

    def test_append_pcm_skips_mismatched_format(self):
        session = AudioSession()
        session.append_pcm(make_decoded(100))
        self.assertFalse(session.append_pcm(make_decoded(50, sample_rate=48000)))
        self.assertFalse(session.append_pcm(make_decoded(50, nchannels=2)))
        self.assertEqual(session.total_frames, 100)

    def test_stream_generator_holds_silence_while_producing(self):
        session = self._make_session(100)
        session.state["producing"] = True

        gen = session._stream_generator()
        self.assertEqual(next(gen), b"")  # Prime

        chunk = gen.send(40)
        self.assertEqual(len(chunk), 80)
        self.assertEqual(session.current_frame, 40)

        chunk = gen.send(40)
        self.assertEqual(session.current_frame, 80)

        # Only 20 frames remain: partial read drains the buffer exactly.
        chunk = gen.send(40)
        self.assertEqual(len(chunk), 40)
        self.assertEqual(session.current_frame, 100)

        # Drained while producing: silence instead of ending the stream.
        chunk = gen.send(40)
        self.assertEqual(chunk, bytes(80))
        self.assertEqual(session.current_frame, 100)

        # Appended PCM becomes available mid-stream and playback resumes.
        self.assertTrue(session.append_pcm(make_decoded(10)))
        chunk = gen.send(40)
        self.assertEqual(len(chunk), 20)
        self.assertEqual(session.current_frame, 110)

        # Producer finishes: next request terminates the stream.
        session.state["producing"] = False
        self.assertEqual(gen.send(40), b"")
        with self.assertRaises(StopIteration):
            gen.send(40)

    def test_stream_generator_ends_when_drained_and_not_producing(self):
        session = self._make_session(100)
        session.state["producing"] = False

        gen = session._stream_generator()
        self.assertEqual(next(gen), b"")  # Prime

        chunk = gen.send(100)
        self.assertEqual(len(chunk), 200)
        self.assertEqual(session.current_frame, 100)

        # Drained and nothing else is coming: end of stream.
        self.assertEqual(gen.send(100), b"")
        with self.assertRaises(StopIteration):
            gen.send(100)


if __name__ == "__main__":
    unittest.main()
