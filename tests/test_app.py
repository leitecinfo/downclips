import json
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from app import DownloadJob, DownloadRequest, build_command, check_dependencies, parse_time, validate_url


class ValidationTests(unittest.TestCase):
    def test_times_and_ranges(self):
        for value, expected in [("00:00", 0), ("05:30", 330), ("90:00", 5400), ("01:02:03", 3723)]:
            with self.subTest(value=value):
                self.assertEqual(parse_time(value), expected)
        for value in ["", "12", "-01:00", "00:60", "01:60:00", "1:2", "1:02:03:04", "nan"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_time(value)
        for start, end in [(10, 10), (20, 10), (-1, 10)]:
            with self.assertRaises(ValueError):
                build_command(DownloadRequest("https://example.com/video", start, end, Path("clips")), "yt-dlp")

    def test_urls(self):
        self.assertEqual(validate_url(" https://example.com/watch?v=abc&x=1 "), "https://example.com/watch?v=abc&x=1")
        for url in ["", "--version", "file:///etc/passwd", "https://", "https://[", "https://example.com:bad", "https://example.com/a\nb"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_url(url)

    def test_command_preserves_arguments_and_options(self):
        request = DownloadRequest("https://example.com/video?x=1&name=$hello", 15, 90, Path("folder with spaces"), True, "brave")
        command = build_command(request, "yt-dlp")
        for option, expected in [
            ("--download-sections", "*00:00:15-00:01:30"),
            ("--merge-output-format", "mp4"), ("--remux-video", "mp4"),
            ("--cookies-from-browser", "brave"), ("--paths", "folder with spaces"),
            ("--concurrent-fragments", "4"),
        ]:
            self.assertEqual(command[command.index(option) + 1], expected)
        self.assertIn("--force-keyframes-at-cuts", command)
        self.assertEqual(command[-2:], ["--", request.url])
        self.assertIn("00-00-15-00-01-30", command[command.index("--output") + 1])
        plain = build_command(DownloadRequest(request.url, 0, 1, Path("clips")), "yt-dlp")
        self.assertNotIn("--force-keyframes-at-cuts", plain)
        self.assertNotIn("--cookies-from-browser", plain)

        # Prueba modo video completo
        full = build_command(DownloadRequest(request.url, destination=Path("clips"), full_video=True), "yt-dlp")
        self.assertNotIn("--download-sections", full)
        self.assertNotIn("--force-keyframes-at-cuts", full)
        self.assertIn("-S", full)

    def test_youtube_uses_small_http_requests_without_reextract_loop(self):
        for url in ("https://www.youtube.com/watch?v=abc", "https://youtu.be/abc"):
            command = build_command(DownloadRequest(url, full_video=True), "yt-dlp")
            self.assertEqual(command[command.index("--http-chunk-size") + 1], "1M")
            self.assertNotIn("--throttled-rate", command)
            self.assertNotIn("--download-sections", command)
        command = build_command(DownloadRequest("https://example.com/youtube.com", full_video=True), "yt-dlp")
        self.assertNotIn("--http-chunk-size", command)

    def test_missing_tools_are_named(self):
        with patch("app.shutil.which", side_effect=lambda name: None if name == "ffmpeg" else name):
            with self.assertRaisesRegex(RuntimeError, "PATH: ffmpeg"):
                check_dependencies()

    def test_packaged_app_uses_bundled_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            tools = Path(directory) / "tools"
            tools.mkdir()
            with patch("app.sys.frozen", True, create=True), patch("app.sys._MEIPASS", directory, create=True):
                with self.assertRaisesRegex(RuntimeError, "yt-dlp.exe"):
                    check_dependencies()
                for name in ("yt-dlp.exe", "ffmpeg.exe", "ffprobe.exe"):
                    (tools / name).touch()
                executable = check_dependencies()
                self.assertEqual(executable, str(tools / "yt-dlp.exe"))
                command = build_command(DownloadRequest("https://example.com/video", 0, 3, Path(directory)), executable)
                self.assertEqual(command[command.index("--ffmpeg-location") + 1], str(tools))


class WorkerTests(unittest.TestCase):
    def run_fake(self, script, destination):
        events = queue.Queue()
        job = DownloadJob(DownloadRequest("https://example.com/video", 0, 3, destination), events)
        with patch("app.check_dependencies", return_value=sys.executable), patch("app.build_command", return_value=[sys.executable, "-u", "-c", script]):
            job.run()
        return list(events.queue)

    def test_reports_success_only_after_file_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            output = destination / "segmento español.mp4"
            script = f"import pathlib,json; p={str(output)!r}; pathlib.Path(p).write_bytes(b'video'); print('descargando'); print('DOWNCLIP_FILE='+json.dumps(p))"
            events = self.run_fake(script, destination)
            self.assertIn(("log", "descargando"), events)
            self.assertIn(("success", output), events)
            self.assertEqual(events[-1], ("done", None))

    def test_no_false_success_on_failure_or_missing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            for script in ["raise SystemExit(7)", "print('sin archivo')"]:
                with self.subTest(script=script):
                    events = self.run_fake(script, Path(directory))
                    self.assertTrue(any(kind == "error" for kind, _ in events))
                    self.assertFalse(any(kind == "success" for kind, _ in events))
                    self.assertEqual(events[-1], ("done", None))

    def test_cancellation_stops_process_and_emits_done(self):
        with tempfile.TemporaryDirectory() as directory:
            events = queue.Queue()
            job = DownloadJob(DownloadRequest("https://example.com/video", 0, 3, Path(directory)), events)
            with patch("app.check_dependencies", return_value=sys.executable), patch("app.build_command", return_value=[sys.executable, "-u", "-c", "import time; print('ready'); time.sleep(30)"]):
                worker = threading.Thread(target=job.run)
                worker.start()
                deadline = time.monotonic() + 5
                while job.process is None and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertIsNotNone(job.process)
                job.cancel()
                worker.join(timeout=8)
                self.assertFalse(worker.is_alive())
                self.assertIsNotNone(job.process.poll())
            self.assertIn(("cancelled", None), list(events.queue))
            self.assertEqual(list(events.queue)[-1], ("done", None))


if __name__ == "__main__":
    unittest.main()
