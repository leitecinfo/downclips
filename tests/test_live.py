"""Captura y corte reales con FFmpeg; el extractor remoto se sustituye por metadatos locales."""
import json
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from app import DownloadRequest, build_command, format_selector, javascript_runtime_args
from live import BufferProbe, LiveRecorder, LocalClipJob, MediaJob, live_inputs


class LiveValidationTests(unittest.TestCase):
    def test_packaged_runtime_is_enabled_explicitly(self):
        with tempfile.TemporaryDirectory() as directory:
            tools = Path(directory) / "tools"
            tools.mkdir()
            (tools / "node.exe").touch()
            with patch("app.sys.frozen", True, create=True), patch("app.sys._MEIPASS", directory, create=True), patch("app.shutil.which", return_value=None):
                self.assertEqual(javascript_runtime_args(), ["--js-runtimes", f"node:{tools / 'node.exe'}"])

    def test_quality_caps_and_unlimited_mode(self):
        import yt_dlp
        formats = [dict(format_id=str(h), height=h, ext="mp4", url=f"https://example.com/{h}.mp4",
                        vcodec="avc1", acodec="mp4a", protocol="https") for h in (480, 720, 1080, 2160)]
        for quality, expected in [("480p", 480), ("720p", 720), ("1080p", 1080), ("max", 2160)]:
            with self.subTest(quality=quality), yt_dlp.YoutubeDL({"quiet": True, "format": format_selector(quality)}) as ydl:
                info = ydl.process_ie_result({"id": "test", "title": "test", "formats": formats}, download=False)
                self.assertEqual(info["height"], expected)
        with self.assertRaises(ValueError):
            format_selector("invalid")

    def test_vod_never_forces_stale_youtube_clients_or_recodes_full_video(self):
        command = build_command(DownloadRequest("https://example.com", full_video=True), "yt-dlp")
        self.assertNotIn("--extractor-args", command)
        self.assertNotIn("--recode-video", command)
        self.assertEqual(command[command.index("--match-filter") + 1], "!is_live")

    def test_live_headers_are_per_input_and_reject_injection(self):
        command = live_inputs({"requested_formats": [
            {"url": "https://example.com/video", "http_headers": {"Referer": "https://example.com/"}},
            {"url": "https://example.com/audio", "http_headers": {"User-Agent": "test"}},
        ]})
        self.assertEqual(command.count("-i"), 2)
        self.assertIn("Referer: https://example.com/\r\n", command)
        with self.assertRaises(ValueError):
            live_inputs({"url": "https://example.com", "http_headers": {"X": "a\r\nb"}})

    def test_non_live_link_and_metadata_failure_are_not_success(self):
        with tempfile.TemporaryDirectory() as directory:
            for info in [{"is_live": False}, {}]:
                events = queue.Queue()
                recorder = LiveRecorder(DownloadRequest("https://example.com", destination=Path(directory)), events)
                with patch("live.check_dependencies", return_value="yt-dlp"), patch.object(recorder, "execute", side_effect=lambda command, callback, **kwargs: callback(json.dumps(info))):
                    recorder.run()
                history = list(events.queue)
                self.assertTrue(any(kind == "error" for kind, _ in history))
                self.assertFalse(any(kind in {"ready", "saved"} for kind, _ in history))
                self.assertEqual(history[-1], ("done", None))

    def test_unrecorded_range_is_rejected(self):
        events = queue.Queue()
        LocalClipJob(DownloadRequest("https://example.com", 0, 10), events, Path("missing.ts"), 5).run()
        self.assertTrue(any(kind == "error" for kind, _ in events.queue))
        self.assertFalse(any(kind == "success" for kind, _ in events.queue))


@unittest.skipUnless(all(shutil.which(name) for name in ("ffmpeg", "ffprobe")), "Requiere FFmpeg y ffprobe")
class LiveIntegrationTests(unittest.TestCase):
    def test_capture_clip_while_recording_and_graceful_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                            "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=25",
                            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
                            "-t", "8", "-c:v", "libx264", "-g", "25", "-pix_fmt", "yuv420p",
                            "-c:a", "aac", str(source)], capture_output=True, check=True, timeout=20)
            events = queue.Queue()
            recorder = LiveRecorder(DownloadRequest("https://example.com/channel", destination=root), events)
            real_execute = MediaJob.execute

            def execute(job, command, callback, **kwargs):
                if "--dump-single-json" in command:
                    callback(json.dumps({"is_live": True, "url": "https://example.com/live", "acodec": "aac"}))
                    return 0
                return real_execute(job, command, callback, **kwargs)

            thread = threading.Thread(target=recorder.run)
            with patch("live.check_dependencies", return_value="yt-dlp"), patch.object(MediaJob, "execute", execute), patch("live.live_inputs", return_value=["-re", "-stream_loop", "-1", "-i", str(source)]):
                thread.start()
                try:
                    deadline = time.monotonic() + 20
                    while recorder.duration < 5 and thread.is_alive() and time.monotonic() < deadline:
                        time.sleep(0.05)
                    self.assertGreaterEqual(recorder.duration, 5, list(events.queue))
                    clip_events = queue.Queue()
                    clip = LocalClipJob(DownloadRequest("https://example.com", 1, 4, root / "clips", exact=True),
                                        clip_events, recorder.path, recorder.duration)
                    clip.run()
                    history = list(clip_events.queue)
                    successes = [path for kind, path in history if kind == "success"]
                    self.assertEqual(len(successes), 1, history)
                    self.assertTrue(thread.is_alive(), "Crear un clip no debe detener la captura")
                    result = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(successes[0])],
                                            capture_output=True, text=True, check=True, timeout=15)
                    info = json.loads(result.stdout)
                    self.assertAlmostEqual(float(info["format"]["duration"]), 3, delta=0.2)
                    self.assertEqual({s["codec_type"] for s in info["streams"]}, {"video", "audio"})
                    recorder.stop()
                    thread.join(timeout=10)
                    self.assertFalse(thread.is_alive())
                    self.assertIn(("saved", recorder.path), list(events.queue))
                    probe_events = queue.Queue()
                    BufferProbe(recorder.path, probe_events).run()
                    self.assertTrue(any(kind == "buffer" for kind, _ in probe_events.queue), list(probe_events.queue))
                finally:
                    recorder.cancel()
                    thread.join(timeout=8)


if __name__ == "__main__":
    unittest.main()
