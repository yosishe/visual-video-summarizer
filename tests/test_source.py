import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import source  # noqa: E402
from common import VsumError  # noqa: E402


class Links(unittest.TestCase):
    def test_youtube_ids(self):
        for url in ("https://www.youtube.com/watch?v=aircAruvnKk&list=PLZ",
                    "https://youtu.be/aircAruvnKk?t=3", "https://www.youtube.com/shorts/aircAruvnKk",
                    "https://www.youtube.com/embed/aircAruvnKk", "https://m.youtube.com/watch?feature=x&v=aircAruvnKk"):
            self.assertEqual(source.youtube_id(url), "aircAruvnKk", url)
        self.assertIsNone(source.youtube_id("https://drive.google.com/file/d/abc/view"))

    def test_is_url_and_slug(self):
        self.assertTrue(source.is_url("https://x.com/v"))
        self.assertFalse(source.is_url("/tmp/talk.mp4"))
        self.assertEqual(source.slug("GMT2026 Recording (1)"), "GMT2026-Recording-1")

    def test_playlists_and_live_streams_are_refused(self):
        for info, message in (({"_type": "playlist", "title": "L"}, "playlist"), ({"is_live": True, "title": "S"}, "live")):
            reply = subprocess.CompletedProcess([], 0, stdout=json.dumps(info), stderr="")
            with mock.patch.object(source, "run", return_value=reply), \
                    mock.patch.object(source, "need", return_value="yt-dlp"):
                with self.assertRaisesRegex(VsumError, message):
                    source.probe("https://www.youtube.com/playlist?list=PL1")

    def test_missing_file(self):
        with self.assertRaisesRegex(VsumError, "no such file"):
            source.probe("/no/such/talk.mp4")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "needs ffmpeg")
class LocalFile(unittest.TestCase):
    def test_probe_local_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp, "My Talk.mp4")
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=gray:s=160x90:d=3",
                            str(video)], check=True)
            meta, info = source.probe(str(video))
            self.assertEqual((meta["kind"], meta["id"], meta["title"], info), ("file", "My-Talk", "My Talk", {}))
            self.assertAlmostEqual(meta["duration"], 3.0, delta=0.2)
            self.assertTrue(source.has_video(video))
            self.assertEqual(source.media(meta, Path(tmp)), video.resolve())

    def test_audio_with_cover_art_has_no_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            cover, song = Path(tmp, "cover.png"), Path(tmp, "song.mp3")
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1",
                            "-frames:v", "1", str(cover)], check=True)
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                            "-i", str(cover), "-t", "2", "-map", "0", "-map", "1", "-c:v", "png",
                            "-disposition:v", "attached_pic", str(song)], check=True)
            self.assertFalse(source.has_video(song))


if __name__ == "__main__":
    unittest.main()
