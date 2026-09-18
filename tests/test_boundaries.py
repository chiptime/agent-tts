import unittest
from agent_tts.boundaries import (
    BoundaryMap,
    Sentence,
    Word,
    SynthesisResult,
    estimate_boundaries_from_text,
)
from agent_tts.audio import AudioSession


class TestBoundaries(unittest.TestCase):
    def setUp(self):
        self.sentences = [
            Sentence(index=0, start_sec=0.0, duration_sec=2.0, text="First sentence here."),
            Sentence(index=1, start_sec=2.0, duration_sec=3.0, text="Second sentence is longer."),
            Sentence(index=2, start_sec=5.0, duration_sec=1.5, text="Third sentence ends."),
        ]
        self.words = [
            Word(index=0, sentence_index=0, start_sec=0.0, duration_sec=0.6, text="First"),
            Word(index=1, sentence_index=0, start_sec=0.6, duration_sec=0.7, text="sentence"),
            Word(index=2, sentence_index=0, start_sec=1.3, duration_sec=0.7, text="here."),
            Word(index=3, sentence_index=1, start_sec=2.0, duration_sec=0.7, text="Second"),
            Word(index=4, sentence_index=1, start_sec=2.7, duration_sec=0.8, text="sentence"),
            Word(index=5, sentence_index=1, start_sec=3.5, duration_sec=0.5, text="is"),
            Word(index=6, sentence_index=1, start_sec=4.0, duration_sec=1.0, text="longer."),
            Word(index=7, sentence_index=2, start_sec=5.0, duration_sec=0.5, text="Third"),
            Word(index=8, sentence_index=2, start_sec=5.5, duration_sec=0.5, text="sentence"),
            Word(index=9, sentence_index=2, start_sec=6.0, duration_sec=0.5, text="ends."),
        ]
        self.bmap = BoundaryMap(sentences=self.sentences, words=self.words)

    def test_get_sentence_at(self):
        s0 = self.bmap.get_sentence_at(0.5)
        self.assertIsNotNone(s0)
        self.assertEqual(s0.index, 0)
        self.assertEqual(s0.text, "First sentence here.")

        s1 = self.bmap.get_sentence_at(2.5)
        self.assertIsNotNone(s1)
        self.assertEqual(s1.index, 1)

        s2 = self.bmap.get_sentence_at(5.5)
        self.assertIsNotNone(s2)
        self.assertEqual(s2.index, 2)

    def test_navigation_next_and_prev(self):
        # Next sentence from mid-sentence 0
        nxt = self.bmap.get_next_sentence(0.5)
        self.assertIsNotNone(nxt)
        self.assertEqual(nxt.index, 1)

        # Prev sentence when < 1.2s into sentence 1 -> should go back to sentence 0
        prev = self.bmap.get_prev_sentence(2.3, replay_threshold=1.2)
        self.assertIsNotNone(prev)
        self.assertEqual(prev.index, 0)

        # Prev sentence when > 1.2s into sentence 1 -> should replay sentence 1
        replay = self.bmap.get_prev_sentence(3.8, replay_threshold=1.2)
        self.assertIsNotNone(replay)
        self.assertEqual(replay.index, 1)

    def test_format_highlighted_sentence(self):
        # Plain text
        plain = self.bmap.format_highlighted_sentence(0.8, ansi=False)
        self.assertEqual(plain, "First sentence here.")

        # ANSI highlighted word
        ansi_hl = self.bmap.format_highlighted_sentence(0.8, ansi=True)
        # Word index 1 ("sentence") is active between 0.6 and 1.3
        self.assertIn("\x1b[1;33;4msentence\x1b[0m", ansi_hl)
        # Word index 0 ("First") is past -> dimmed
        self.assertIn("\x1b[2mFirst\x1b[0m", ansi_hl)

    def test_estimate_boundaries(self):
        text = "Hello world! This is a simple test. And final part."
        est_bmap = estimate_boundaries_from_text(text, total_duration_sec=6.0)
        self.assertEqual(len(est_bmap.sentences), 3)
        self.assertTrue(len(est_bmap.words) > 5)
        self.assertAlmostEqual(est_bmap.sentences[0].start_sec, 0.0)
        self.assertAlmostEqual(est_bmap.sentences[-1].end_sec, 6.0, delta=0.01)

    def test_synthesis_result_subclass(self):
        raw_bytes = b"\x00\x01\x02\x03"
        res = SynthesisResult(raw_bytes, self.bmap)
        self.assertEqual(bytes(res), raw_bytes)
        self.assertEqual(len(res), 4)
        self.assertEqual(res[:2], b"\x00\x01")
        self.assertTrue(hasattr(res, "boundaries"))
        self.assertEqual(len(res.boundaries.sentences), 3)

    def test_audio_session_ipc_boundary_commands(self):
        session = AudioSession(label="Test", boundaries=self.bmap)
        session.sample_rate = 24000
        session.nchannels = 1
        session.bytes_per_sample = 2
        session.frame_size = 2
        session.total_frames = 24000 * 7  # 7 seconds

        # Set position at 0.5s -> frame = 12000
        session.current_frame = 12000

        # Status check contains sentence
        status = session.handle_ipc_command("status")
        self.assertIn("sent_idx=0", status)
        self.assertIn("sentence=First sentence here.", status)

        # Next sentence
        next_res = session.handle_ipc_command("next-sentence")
        self.assertIn("sent_idx=1", next_res)
        self.assertEqual(session.current_frame, int(2.0 * 24000))

        # Current sentence
        cur_res = session.handle_ipc_command("sentence")
        self.assertIn("sent_idx=1", cur_res)
        self.assertIn("text=Second sentence is longer.", cur_res)

        # Highlight command
        hl_res = session.handle_ipc_command("highlight")
        self.assertIn("Second", hl_res)


if __name__ == "__main__":
    unittest.main()
