import os
import shutil
import tempfile
import unittest
from agent_tts.podcast import PodcastFeed


class TestPodcast(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.feed = PodcastFeed(
            base_dir=self.temp_dir,
            feed_title="Test Agent Feed",
            base_url="http://localhost:8844",
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_add_episode_and_generate_xml(self):
        fake_mp3 = b"\xff\xfb\x90\x44" + b"\x00" * 100
        ep = self.feed.add_episode(
            mp3_data=fake_mp3,
            title="Pruebas de compilación exitosas",
            description="El agente terminó la tarea con éxito en 42s.",
            duration_sec=42.0,
        )

        self.assertEqual(len(self.feed.episodes), 1)
        self.assertEqual(ep.title, "Pruebas de compilación exitosas")
        self.assertTrue(os.path.exists(os.path.join(self.feed.audio_dir, ep.filename)))

        xml_path = os.path.join(self.temp_dir, "podcast.xml")
        self.assertTrue(os.path.exists(xml_path))

        with open(xml_path, "r", encoding="utf-8") as f:
            xml_content = f.read()

        self.assertIn("<title>Test Agent Feed</title>", xml_content)
        self.assertIn("Pruebas de compilación exitosas", xml_content)
        self.assertIn("<enclosure url=\"http://localhost:8844/audio/", xml_content)
        self.assertIn("type=\"audio/mpeg\"", xml_content)
        self.assertIn("<itunes:duration>00:42</itunes:duration>", xml_content)

    def test_prune_episodes(self):
        fake_mp3 = b"test"
        for i in range(5):
            self.feed.add_episode(
                mp3_data=fake_mp3,
                title=f"Episode {i}",
                description="desc",
                max_episodes=3,
            )

        self.assertEqual(len(self.feed.episodes), 3)
        self.assertEqual(self.feed.episodes[0].title, "Episode 4")


if __name__ == "__main__":
    unittest.main()
