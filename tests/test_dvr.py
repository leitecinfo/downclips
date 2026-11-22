import json
from functools import partial
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from app import DownloadRequest
from dvr import DvrJob, history_range, snapshot_playlist
from live import MediaJob
from test_local_download import LoopbackServer, QuietHandler


class DvrValidationTests(unittest.TestCase):
    def test_snapshot_keeps_timeline_and_resolves_relative_segments_keys(self):
        text, total = snapshot_playlist('#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:42\n#EXT-X-KEY:METHOD=AES-128,URI="../key"\n#EXTINF:6.5,\na.ts?token=123\n#EXTINF:5.5,\nb.ts\n', 'https://example.com/live/index.m3u8')
        self.assertEqual(total, 12)
        self.assertIn('URI="https://example.com/key"', text)
        self.assertIn('https://example.com/live/a.ts?token=123', text)
        self.assertIn('#EXT-X-MEDIA-SEQUENCE:42', text)
        self.assertTrue(text.endswith('#EXT-X-ENDLIST\n'))
        self.assertEqual(history_range(total, 10, 3), (2, 5))

    def test_missing_or_unsafe_history_is_rejected(self):
        for text in ['not a playlist', '#EXTM3U\n', '#EXTM3U\n#EXTINF:NaN,\na.ts',
                     '#EXTM3U\n#EXTINF:2,\nfile:///private.ts',
                     '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\na.m3u8']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                snapshot_playlist(text, 'https://example.com/index.m3u8')
        for lookback, duration in [(13, 3), (0, 0), (2, 3), (-1, 1)]:
            with self.subTest(lookback=lookback), self.assertRaises(ValueError):
                history_range(12, lookback, duration)


@unittest.skipUnless(all(shutil.which(name) for name in ('ffmpeg', 'ffprobe')), 'Requiere FFmpeg y ffprobe')
class DvrIntegrationTests(unittest.TestCase):
    def test_recovers_old_hls_range_and_rejects_expired_range(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
                            '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=25',
                            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100',
                            '-t', '12', '-c:v', 'libx264', '-g', '50', '-sc_threshold', '0',
                            '-c:a', 'aac', '-hls_time', '2', '-hls_list_size', '0',
                            '-hls_segment_filename', str(root / 'part%d.ts'), str(root / 'history.m3u8')],
                           capture_output=True, check=True, timeout=20)
            server = LoopbackServer(('127.0.0.1', 0), partial(QuietHandler, directory=directory))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f'http://127.0.0.1:{server.server_port}/history.m3u8'
                original = MediaJob.execute

                def execute(job, command, callback, **kwargs):
                    if '--dump-single-json' in command:
                        callback(json.dumps({'is_live': True, 'url': url, 'protocol': 'm3u8_native'}))
                        return 0
                    return original(job, command, callback, **kwargs)

                for lookback in (7, 30):
                    events = queue.Queue()
                    job = DvrJob(DownloadRequest('https://www.twitch.tv/channel', destination=root / 'clips', exact=True), events, lookback, 3)
                    with patch('dvr.check_dependencies', return_value='yt-dlp'), patch.object(MediaJob, 'execute', execute):
                        job.run()
                    history = list(events.queue)
                    successes = [path for kind, path in history if kind == 'success']
                    if lookback == 30:
                        self.assertFalse(successes)
                        self.assertTrue(any(kind == 'error' and 'solo expone' in str(payload) for kind, payload in history), history)
                    else:
                        self.assertEqual(len(successes), 1, history)
                        result = subprocess.run(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(successes[0])], capture_output=True, text=True, check=True, timeout=15)
                        info = json.loads(result.stdout)
                        self.assertAlmostEqual(float(info['format']['duration']), 3, delta=0.2)
                        self.assertEqual({s['codec_type'] for s in info['streams']}, {'video', 'audio'})
                    self.assertEqual(history[-1], ('done', None))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
