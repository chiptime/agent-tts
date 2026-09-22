"""Private Podcast / Audio RSS Feed engine.

Generates and serves valid RSS 2.0 / iTunes podcast feeds from synthesized agent audio sessions,
allowing developers to subscribe on mobile podcast apps (Pocket Casts, Overcast, Apple Podcasts).
100% standard library (zero external dependencies).
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import format_datetime
import http.server
import json
import os
import re
import socketserver
from typing import List, Optional
import xml.sax.saxutils as saxutils

DEFAULT_PODCAST_DIR = os.environ.get(
    "AGENT_TTS_PODCAST_DIR",
    os.path.expanduser("~/.local/share/agent-tts/podcast"),
)

# Base URL used in the RSS XML and enclosure links. Hosts that serve the
# feed on a non-default port override it via this env var (read live).
DEFAULT_PODCAST_BASE_URL = os.environ.get(
    "AGENT_TTS_PODCAST_BASE_URL",
    "http://localhost:8844",
)


@dataclass
class PodcastEpisode:
    """Represents a single audio episode in the podcast feed."""

    id: str
    title: str
    description: str
    filename: str
    pub_date: str
    duration_sec: float
    file_size_bytes: int


class PodcastFeed:
    """Manages episode persistence and RSS XML generation for private feeds."""

    def __init__(
        self,
        base_dir: str = DEFAULT_PODCAST_DIR,
        feed_title: str = "Agent Audio Feed",
        feed_description: str = "Private audio updates and voice syntheses from AI coding agents",
        base_url: str = DEFAULT_PODCAST_BASE_URL,
    ):
        self.base_dir = os.path.abspath(base_dir)
        self.audio_dir = os.path.join(self.base_dir, "audio")
        self.meta_file = os.path.join(self.base_dir, "episodes.json")
        self.xml_file = os.path.join(self.base_dir, "podcast.xml")
        self.feed_title = feed_title
        self.feed_description = feed_description
        self.base_url = base_url.rstrip("/")

        os.makedirs(self.audio_dir, exist_ok=True)
        self.episodes: List[PodcastEpisode] = self._load_episodes()

    def _load_episodes(self) -> List[PodcastEpisode]:
        if not os.path.exists(self.meta_file):
            return []
        try:
            with open(self.meta_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return [PodcastEpisode(**item) for item in data]
        except Exception:
            return []

    def _save_episodes(self) -> None:
        try:
            with open(self.meta_file, "w", encoding="utf-8") as f:
                json.dump([e.__dict__ for e in self.episodes], f, indent=2)
        except OSError:
            pass

    def add_episode(
        self,
        mp3_data: bytes,
        title: str,
        description: str,
        duration_sec: float = 0.0,
        max_episodes: int = 50,
    ) -> PodcastEpisode:
        """Saves an MP3 episode, updates metadata, and regenerates podcast.xml."""
        timestamp = int(datetime.now(timezone.utc).timestamp())
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", title[:30].strip()).strip("_") or "episode"
        filename = f"{timestamp}_{slug}.mp3"
        filepath = os.path.join(self.audio_dir, filename)

        with open(filepath, "wb") as f:
            f.write(mp3_data)

        pub_date_rfc = format_datetime(datetime.now(timezone.utc))

        episode = PodcastEpisode(
            id=str(timestamp),
            title=title.strip() or f"Agent Audio ({timestamp})",
            description=description.strip() or "Audio session synthesized by agent-tts.",
            filename=filename,
            pub_date=pub_date_rfc,
            duration_sec=float(duration_sec),
            file_size_bytes=len(mp3_data),
        )

        self.episodes.insert(0, episode)

        # Prune old episodes if exceeding max_episodes
        if len(self.episodes) > max_episodes:
            pruned = self.episodes[max_episodes:]
            self.episodes = self.episodes[:max_episodes]
            for p in pruned:
                old_path = os.path.join(self.audio_dir, p.filename)
                if os.path.exists(old_path):
                    try:
                        os.remove(old_path)
                    except OSError:
                        pass

        self._save_episodes()
        self.generate_feed_xml()
        return episode

    def generate_feed_xml(self) -> str:
        """Generates standard RSS 2.0 with iTunes podcast extensions."""
        title_esc = saxutils.escape(self.feed_title)
        desc_esc = saxutils.escape(self.feed_description)
        pub_date_rfc = format_datetime(datetime.now(timezone.utc))

        items_xml = []
        for ep in self.episodes:
            ep_title = saxutils.escape(ep.title)
            ep_desc = saxutils.escape(ep.description)
            enclosure_url = f"{self.base_url}/audio/{ep.filename}"
            dur_mins = int(ep.duration_sec // 60)
            dur_secs = int(ep.duration_sec % 60)
            itunes_dur = f"{dur_mins:02d}:{dur_secs:02d}"

            item = f"""    <item>
      <title>{ep_title}</title>
      <description>{ep_desc}</description>
      <pubDate>{ep.pub_date}</pubDate>
      <guid isPermaLink="false">{ep.id}</guid>
      <enclosure url="{enclosure_url}" length="{ep.file_size_bytes}" type="audio/mpeg" />
      <itunes:duration>{itunes_dur}</itunes:duration>
      <itunes:summary>{ep_desc}</itunes:summary>
    </item>"""
            items_xml.append(item)

        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>{title_esc}</title>
    <description>{desc_esc}</description>
    <link>{self.base_url}/podcast.xml</link>
    <language>es</language>
    <pubDate>{pub_date_rfc}</pubDate>
    <lastBuildDate>{pub_date_rfc}</lastBuildDate>
    <itunes:author>agent-tts</itunes:author>
    <itunes:summary>{desc_esc}</itunes:summary>
    <itunes:category text="Technology" />
{chr(10).join(items_xml)}
  </channel>
</rss>"""

        with open(self.xml_file, "w", encoding="utf-8") as f:
            f.write(xml)

        return xml


def run_podcast_server(
    port: int = 8844,
    host: str = "0.0.0.0",
    podcast_dir: str = DEFAULT_PODCAST_DIR,
) -> None:
    """Runs a zero-dependency HTTP server delivering the podcast feed and audio enclosures."""
    os.makedirs(podcast_dir, exist_ok=True)

    class CustomHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=podcast_dir, **kwargs)

        def log_message(self, format, *args):
            # Keep stderr clean
            pass

    class ReusableServer(socketserver.ThreadingTCPServer):
        allow_reuse_address = True

    with ReusableServer((host, port), CustomHandler) as httpd:
        print(f"🎙️ Podcast RSS Feed active at: http://{host}:{port}/podcast.xml")
        httpd.serve_forever()
