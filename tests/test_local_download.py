"""Integración real, sin Internet: video sintético servido en loopback."""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import queue
import shutil
from socketserver import TCPServer
import subprocess
import tempfile
import threading
import unittest

from app import DownloadJob, DownloadRequest


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


class LoopbackServer(ThreadingHTTPServer):
    def server_bind(self):
        # No requiere DNS inverso; funciona también con nombres de equipo acentuados.
        TCPServer.server_bind(self)
        self.server_name = "localhost"
        self.server_port = self.server_address[1]


@unittest.skipUnless(all(shutil.which(tool) for tool in ["yt-dlp", "ffmpeg", "ffprobe"]), "Requiere yt-dlp, FFmpeg y ffprobe en PATH")
class LocalDownloadTests(unittest.TestCase):
    def test_exact_segment_is_mp4_with_audio_and_expected_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=25",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
                "-t", "6", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-movflags", "+faststart", str(source),
            ], check=True, capture_output=True, timeout=30)
            server = LoopbackServer(("127.0.0.1", 0), partial(QuietHandler, directory=directory))
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            try:
                events = queue.Queue()
                job = DownloadJob(DownloadRequest(
                    f"http://127.0.0.1:{server.server_port}/source.mp4",
                    1, 4, root / "salida con espacios", exact=True,
                ), events)
                worker = threading.Thread(target=job.run, daemon=True)
                worker.start()
                worker.join(timeout=45)
                if worker.is_alive():
                    job.cancel()
                    worker.join(timeout=8)
                    self.fail("La descarga local excedió el tiempo de espera")
                history = list(events.queue)
                successes = [payload for kind, payload in history if kind == "success"]
                self.assertEqual(len(successes), 1, history)
                output = successes[0]
                self.assertEqual(output.suffix, ".mp4")
                self.assertIn("00-00-01-00-00-04", output.name)
                result = subprocess.run([
                    "ffprobe", "-v", "error", "-show_format", "-show_streams",
                    "-of", "json", str(output),
                ], check=True, capture_output=True, text=True, timeout=15)
                metadata = json.loads(result.stdout)
                self.assertAlmostEqual(float(metadata["format"]["duration"]), 3, delta=0.2)
                self.assertEqual({stream["codec_type"] for stream in metadata["streams"]}, {"audio", "video"})
            finally:
                server.shutdown()
                server.server_close()
                server_thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
