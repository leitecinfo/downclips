"""Captura live a disco y recorta el medio recibido sin detener la emisión remota."""
from __future__ import annotations

import http.cookiejar
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from urllib.request import Request

from app import DownloadJob, DownloadRequest, check_dependencies, format_selector, javascript_runtime_args, validate_url


def media_tool(name: str) -> str:
    if getattr(sys, "frozen", False):
        return str(Path(sys._MEIPASS) / "tools" / f"{name}.exe")
    import shutil
    found = shutil.which(name)
    if not found:
        raise RuntimeError(f"No se encuentra {name} en PATH.")
    return found


class MediaJob(DownloadJob):
    def execute(self, command, on_line, *, interactive=False, timeout=None):
        if self.cancelled.is_set():
            raise RuntimeError("Operación cancelada.")
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        with self.lock:
            self.process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE if interactive else subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace", bufsize=1, **options,
            )
        if self.cancelled.is_set():
            self._stop_process()
        expired = threading.Event()

        def expire():
            expired.set()
            self._stop_process()

        timer = threading.Timer(timeout, expire) if timeout else None
        if timer:
            timer.start()
        try:
            with self.process.stdout as stream:
                for line in stream:
                    if line.strip():
                        on_line(line.strip())
            code = self.process.wait()
            if expired.is_set():
                raise RuntimeError("El servidor no respondió dentro del tiempo de espera.")
            if code and not self.cancelled.is_set():
                raise RuntimeError(f"La herramienta terminó con código {code}. Consulta el registro.")
            return code
        finally:
            if timer:
                timer.cancel()
            if self.process.stdin:
                self.process.stdin.close()


def live_inputs(info: dict, cookies: Path | None = None) -> list[str]:
    """HTTP headers/cookies se aplican por entrada, incluidos streams separados."""
    jar = http.cookiejar.MozillaCookieJar(str(cookies)) if cookies else None
    if jar and cookies.is_file():
        jar.load(ignore_discard=True, ignore_expires=True)
    args = []
    for fmt in info.get("requested_formats") or [info]:
        url = validate_url(fmt.get("url", ""))
        if fmt.get("protocol") == "http_dash_segments":
            raise ValueError("Este directo no expone un stream HTTP/HLS utilizable. Prueba otra calidad o su VOD.")
        headers = {**info.get("http_headers", {}), **fmt.get("http_headers", {})}
        # FFmpeg necesita las cookies del dominio de cada URL multimedia.
        if jar:
            request = Request(url)
            jar.add_cookie_header(request)
            if request.has_header("Cookie"):
                headers["Cookie"] = request.get_header("Cookie")
        if any("\r" in str(k) + str(v) or "\n" in str(k) + str(v) for k, v in headers.items()):
            raise ValueError("Cabeceras HTTP no válidas en el directo.")
        args += ["-rw_timeout", "20000000"]
        if headers:
            args += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
        args += ["-i", url]
    return args


class LiveRecorder(MediaJob):
    """Búfer conservado en disco. Detener finaliza FFmpeg; cancelar aborta."""
    def __init__(self, request: DownloadRequest, events: queue.Queue):
        super().__init__(request, events)
        self.path = request.destination / "Directos" / f"directo_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.ts"
        self.duration = 0.0
        self.ready = False
        self.stopping = threading.Event()

    def stop(self):
        self.stopping.set()
        if not self.ready:
            self.cancel()
            return
        with self.lock:
            if self.ready and self.process and self.process.poll() is None:
                try:
                    self.process.stdin.write("q\n")
                    self.process.stdin.flush()
                except (OSError, ValueError):
                    pass

    def _progress(self, line):
        if line.startswith("out_time_us="):
            try:
                duration = max(0.0, int(line.partition("=")[2]) / 1_000_000)
            except ValueError:
                return
            if math.isfinite(duration):
                self.duration = max(self.duration, duration)
                self.events.put(("duration", self.duration))
            if self.stopping.is_set():
                self.stop()
        elif "=" not in line or line.startswith(("[", "Error")):
            self.events.put(("log", line))

    def run(self):
        try:
            executable = check_dependencies()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.events.put(("log", "Conectando al directo; 00:00 será el primer fotograma recibido, no el inicio de la emisión."))
            with tempfile.TemporaryDirectory(prefix="downclip_auth_") as directory:
                cookie_file = Path(directory) / "cookies.txt"
                command = [executable, "--ignore-config", "--no-playlist", "--no-colors",
                           "--dump-single-json", "--skip-download", "--socket-timeout", "20",
                           "--retries", "2", "-f", format_selector(self.request.quality, live=True),
                           "-S", "res,codec:h264:m4a"]
                command += javascript_runtime_args()
                if self.request.browser:
                    if self.request.browser not in {"chrome", "edge", "brave"}:
                        raise ValueError("Navegador de cookies no válido.")
                    command += ["--cookies-from-browser", self.request.browser, "--cookies", str(cookie_file)]
                command += ["--", validate_url(self.request.url)]
                metadata = []

                def read_metadata(line):
                    if line.startswith("{"):
                        metadata.append(json.loads(line))
                    else:
                        self.events.put(("log", line))

                self.execute(command, read_metadata, timeout=60)
                if self.cancelled.is_set() or self.stopping.is_set():
                    return
                if len(metadata) != 1 or not metadata[0].get("is_live"):
                    raise ValueError("El enlace no está emitiendo en vivo. Para un video guardado usa Video / VOD.")
                info = metadata[0]
                command = [media_tool("ffmpeg"), "-hide_banner", "-loglevel", "warning", "-y"]
                command += live_inputs(info, cookie_file if self.request.browser else None)
                formats = info.get("requested_formats") or [info]
                command += ["-map", "0:v:0"]
                audio_index = next((i for i, fmt in enumerate(formats) if fmt.get("acodec") != "none"), None)
                if audio_index is not None:
                    command += ["-map", f"{audio_index}:a:0?"]
                command += ["-c", "copy", "-t", "21600", "-flush_packets", "1",
                            "-progress", "pipe:1", "-stats_period", "1", "-nostats",
                            "-f", "mpegts", str(self.path)]
                # ready se publica al recibir datos, no al resolver la URL.
                def progress(line):
                    if not self.ready and line.startswith("out_time_us=") and line != "out_time_us=N/A":
                        self.ready = True
                        self.events.put(("ready", self.path))
                    self._progress(line)

                self.execute(command, progress, interactive=True)
                if not self.cancelled.is_set():
                    if self.duration <= 0 or not self.path.is_file() or self.path.stat().st_size == 0:
                        raise RuntimeError("El directo terminó sin recibir video.")
                    self.events.put(("saved", self.path))
        except Exception as exc:
            if not self.cancelled.is_set():
                self.events.put(("error", str(exc)))
        finally:
            self._stop_process()
            if self.process:
                self.process.wait()
            self.events.put(("done", None))


class BufferProbe(MediaJob):
    def __init__(self, source: Path, events: queue.Queue):
        super().__init__(DownloadRequest("https://example.invalid"), events)
        self.source = source

    def run(self):
        try:
            lines = []
            self.execute([media_tool("ffprobe"), "-v", "error", "-show_entries", "format=duration:stream=codec_type",
                          "-of", "json", str(self.source)], lines.append, timeout=20)
            info = json.loads("\n".join(lines))
            duration = float(info.get("format", {}).get("duration", 0))
            if not math.isfinite(duration) or duration <= 0 or not any(s.get("codec_type") == "video" for s in info.get("streams", [])):
                raise ValueError("El archivo no contiene una grabación de video válida.")
            self.events.put(("buffer", (self.source, duration)))
        except Exception as exc:
            if not self.cancelled.is_set():
                self.events.put(("error", str(exc)))
        finally:
            self._stop_process()
            if self.process:
                self.process.wait()
            self.events.put(("done", None))


class LocalClipJob(MediaJob):
    def __init__(self, request, events, source: Path, available: float, input_options=()):
        super().__init__(request, events)
        self.source = source
        self.available = available
        self.input_options = list(input_options)

    def run(self):
        temporary = None
        try:
            if not 0 <= self.request.start < self.request.end <= self.available:
                raise ValueError("El rango debe estar dentro del tiempo ya grabado del directo.")
            self.request.destination.mkdir(parents=True, exist_ok=True)
            output = self.request.destination / f"clip_{self.source.stem}_{self.request.start}-{self.request.end}_{uuid.uuid4().hex[:8]}.mp4"
            temporary = output.with_suffix(".part.mp4")
            command = [media_tool("ffmpeg"), "-hide_banner", "-loglevel", "warning", "-nostdin", "-y",
                       "-ss", str(self.request.start), *self.input_options, "-i", str(self.source),
                       "-t", str(self.request.end - self.request.start), "-map", "0:v:0", "-map", "0:a:0?"]
            if self.request.exact:
                command += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac"]
            else:
                command += ["-c", "copy"]
            command += ["-movflags", "+faststart", str(temporary)]
            self.events.put(("log", "Guardando clip local" + (" con corte exacto (solo se recodifica el clip)…" if self.request.exact else " por copia de streams (corte aproximado)…")))
            self.execute(command, lambda line: self.events.put(("log", line)))
            if self.cancelled.is_set():
                return
            # Nunca dar éxito a un archivo vacío o a un clip truncado por un corte de red.
            probe = []
            self.execute([media_tool("ffprobe"), "-v", "error", "-show_entries", "format=duration:stream=codec_type",
                          "-of", "json", str(temporary)], probe.append, timeout=20)
            info = json.loads("\n".join(probe))
            duration = float(info.get("format", {}).get("duration", 0))
            if not any(s.get("codec_type") == "video" for s in info.get("streams", [])) or duration <= 0:
                raise RuntimeError("El clip no contiene video válido.")
            if self.request.exact and abs(duration - (self.request.end - self.request.start)) > 0.5:
                raise RuntimeError("El clip está incompleto: el búfer no contiene todo el rango solicitado.")
            if not self.cancelled.is_set():
                temporary.replace(output)
                self.events.put(("success", output))
        except Exception as exc:
            if not self.cancelled.is_set():
                self.events.put(("error", str(exc)))
        finally:
            self._stop_process()
            if self.process:
                self.process.wait()
            if temporary and temporary.exists():
                temporary.unlink()
            if self.cancelled.is_set():
                self.events.put(("cancelled", None))
            self.events.put(("done", None))
