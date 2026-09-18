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

    def test_paragraph_boundaries_and_navigation(self):
        text = "Párrafo uno con dos frases. Segunda frase del uno.\n\nPárrafo dos que es independiente. Final del dos."
        bmap = estimate_boundaries_from_text(text, total_duration_sec=10.0)
        self.assertEqual(len(bmap.paragraphs), 2)
        self.assertEqual(len(bmap.sentences), 4)

        p0 = bmap.get_paragraph_at(1.0)
        self.assertIsNotNone(p0)
        self.assertEqual(p0.index, 0)
        self.assertIn("Párrafo uno", p0.text)

        # Next paragraph from middle of paragraph 0
        p_next = bmap.get_next_paragraph(1.0)
        self.assertIsNotNone(p_next)
        self.assertEqual(p_next.index, 1)
        self.assertIn("Párrafo dos", p_next.text)

        # Prev paragraph from start of paragraph 1 (< 2.0s in) -> should go back to paragraph 0
        p_prev = bmap.get_prev_paragraph(p_next.start_sec + 0.5, replay_threshold=2.0)
        self.assertIsNotNone(p_prev)
        self.assertEqual(p_prev.index, 0)

        # AudioSession paragraph IPC commands
        session = AudioSession(label="ParaTest", boundaries=bmap)
        session.sample_rate = 24000
        session.nchannels = 1
        session.bytes_per_sample = 2
        session.frame_size = 2
        session.total_frames = 24000 * 10

        # Jump to next paragraph
        res = session.handle_ipc_command("next-paragraph")
        self.assertIn("para_idx=1", res)
        self.assertEqual(session.current_frame, round(p_next.start_sec * 24000))

        # Check current paragraph command
        cur_para = session.handle_ipc_command("paragraph")
        self.assertIn("para_idx=1", cur_para)
        self.assertIn("Párrafo dos", cur_para)

    def test_bionic_reading_functions(self):
        from agent_tts.boundaries import bionic_word, apply_bionic_reading

        # Short word <= 3 chars: bold 1 char
        self.assertEqual(bionic_word("el"), "\x1b[1me\x1b[22ml")
        self.assertEqual(bionic_word("sol"), "\x1b[1ms\x1b[22mol")

        # Medium word 4-6 chars: bold 2 chars
        self.assertEqual(bionic_word("hola"), "\x1b[1mho\x1b[22mla")
        self.assertEqual(bionic_word("mundo"), "\x1b[1mmu\x1b[22mndo")

        # Punctuation handling
        self.assertEqual(bionic_word("¡hola!"), "¡\x1b[1mho\x1b[22mla!")

        # Full sentence formatting
        bionic_text = apply_bionic_reading("El sol brilla en la ciudad.")
        self.assertIn("\x1b[1m", bionic_text)
        self.assertIn("\x1b[22m", bionic_text)

        # Highlighting with bionic reading enabled
        hl_bionic = self.bmap.format_highlighted_sentence(0.8, ansi=True, bionic=True)
        self.assertIn("\x1b[1;33;4msentence\x1b[0m", hl_bionic)
        # Upcoming word "here." should have bionic fixation
        self.assertIn("\x1b[1mhe\x1b[22mre.", hl_bionic)

    def test_autoscroll_and_scroll_info_ipc(self):
        session = AudioSession(label="ScrollTest", boundaries=self.bmap, autoscroll=True, bionic=True, zen=True)
        session.sample_rate = 24000
        session.nchannels = 1
        session.bytes_per_sample = 2
        session.frame_size = 2
        session.total_frames = 24000 * 10
        session.current_frame = 24000 * 2  # 2.0s -> 20%

        scroll_info = session.handle_ipc_command("scroll-info")
        self.assertIn("pos=2.00", scroll_info)
        self.assertIn("total=10.00", scroll_info)
        self.assertIn("pct=20.0", scroll_info)
        self.assertIn("sent_idx=1", scroll_info)
        self.assertIn("total_sents=3", scroll_info)

        # Highlight with bionic enabled on session
        hl_res = session.handle_ipc_command("highlight")
        self.assertIn("\x1b[1m", hl_res)


if __name__ == "__main__":
    unittest.main()
