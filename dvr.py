"""Recupera un rango del historial HLS que la plataforma exponga en ese momento."""
from dataclasses import replace
import http.cookiejar
import json
import math
from pathlib import Path
import re
import tempfile
from urllib.parse import urljoin, urlsplit
from urllib.request import build_opener, HTTPCookieProcessor, Request

from app import check_dependencies, format_selector, javascript_runtime_args, validate_url
from live import LocalClipJob, MediaJob, live_inputs


def snapshot_playlist(text: str, url: str) -> tuple[str, float]:
    """Congela el índice: offsets medidos desde el último segmento disponible."""
    if not text.lstrip().startswith("#EXTM3U"):
        raise ValueError("El servidor no entregó un índice HLS válido.")
    if "#EXT-X-STREAM-INF" in text or "#EXT-X-DEFINE" in text or "#EXT-X-BYTERANGE" in text:
        raise ValueError("El historial HLS de este formato no permite recorte directo; prueba otra calidad o un VOD.")
    lines, total, count = [], 0.0, 0
    pending = False
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#EXT-X-ENDLIST", "#EXT-X-START", "#EXT-X-PRELOAD-HINT", "#EXT-X-PART:", "#EXT-X-SERVER-CONTROL", "#EXT-X-RENDITION-REPORT")):
            continue
        if line.startswith("#EXTINF:"):
            duration = float(line.partition(":")[2].split(",")[0])
            if not math.isfinite(duration) or duration <= 0 or pending:
                raise ValueError("Duraciones inválidas en el historial HLS.")
            total += duration
            pending = True
        elif not line.startswith("#"):
            if not pending:
                raise ValueError("El historial no incluye duración por segmento.")
            line = validate_url(urljoin(url, line))
            count += 1
            pending = False
        if line.startswith("#EXT-X-KEY") and 'METHOD=' in line and not any(value in line for value in ("METHOD=NONE", "METHOD=AES-128")):
            raise ValueError("El historial está protegido o usa un cifrado no compatible.")
        if line.startswith("#"):
            line = re.sub(r'URI="([^"]*)"', lambda match: f'URI="{validate_url(urljoin(url, match.group(1)))}"', line)
        lines.append(line)
    if not count or pending:
        raise ValueError("El historial está vacío o incompleto.")
    return "\n".join(lines) + "\n#EXT-X-ENDLIST\n", total


def history_range(total: float, lookback: int, duration: int) -> tuple[float, float]:
    if duration <= 0 or lookback < duration:
        raise ValueError("El retroceso debe ser al menos la duración del clip; ambos deben ser positivos.")
    if lookback > total:
        raise ValueError(f"La plataforma solo expone {int(total)} s de historial en este enlace; solicitaste retroceder {lookback} s. Usa un VOD o el búfer local.")
    start = total - lookback
    return start, start + duration


class DvrJob(MediaJob):
    def __init__(self, request, events, lookback: int, duration: int):
        super().__init__(request, events)
        self.lookback = lookback
        self.clip_duration = duration
        self.child = None

    def cancel(self):
        super().cancel()
        if self.child:
            self.child.cancel()

    def run(self):
        try:
            history_range(float("inf"), self.lookback, self.clip_duration)
            executable = check_dependencies()
            with tempfile.TemporaryDirectory(prefix="downclip_dvr_") as directory:
                root = Path(directory)
                cookie_file = root / "cookies.txt"
                command = [executable, "--ignore-config", "--no-playlist", "--no-colors",
                           "--dump-single-json", "--skip-download", "--socket-timeout", "15", "--retries", "2",
                           "-f", format_selector(self.request.quality, live=True), "-S", "res,codec:h264:m4a"]
                command += javascript_runtime_args()
                host = urlsplit(validate_url(self.request.url)).hostname.lower()
                if host == "twitch.tv" or host.endswith(".twitch.tv"):
                    # Twitch puede publicar un VOD mientras el canal sigue en directo.
                    command += ["--live-from-start"]
                if self.request.browser:
                    if self.request.browser not in {"chrome", "edge", "brave"}:
                        raise ValueError("Navegador no válido.")
                    command += ["--cookies-from-browser", self.request.browser, "--cookies", str(cookie_file)]
                command += ["--", self.request.url]
                metadata = []

                def receive(line):
                    if line.startswith("{"):
                        metadata.append(json.loads(line))
                    else:
                        self.events.put(("log", line))

                self.events.put(("log", "Consultando historial disponible; el retroceso se mide desde el último segmento publicado, que puede tener retraso respecto al reproductor."))
                self.execute(command, receive, timeout=60)
                if self.cancelled.is_set():
                    return
                if len(metadata) != 1:
                    raise ValueError("No se pudo consultar el historial del directo.")
                info = metadata[0]
                if info.get("requested_formats") or not str(info.get("protocol", "")).startswith("m3u8"):
                    raise ValueError("Este enlace no expone historial HLS combinado. Usa un VOD o conecta el búfer local antes del momento deseado.")
                url = validate_url(info["url"])
                headers = info.get("http_headers", {})
                if any("\n" in str(k) + str(v) or "\r" in str(k) + str(v) for k, v in headers.items()):
                    raise ValueError("Cabeceras no válidas en el historial.")
                jar = http.cookiejar.MozillaCookieJar(str(cookie_file))
                if cookie_file.is_file():
                    jar.load(ignore_discard=True, ignore_expires=True)
                opener = build_opener(HTTPCookieProcessor(jar))
                with opener.open(Request(url, headers=headers), timeout=15) as response:
                    data = response.read(8 * 1024 * 1024 + 1)
                    if len(data) > 8 * 1024 * 1024:
                        raise ValueError("El índice del historial es demasiado grande.")
                    playlist, total = snapshot_playlist(data.decode("utf-8-sig"), response.geturl())
                start, end = history_range(total, self.lookback, self.clip_duration)
                self.events.put(("log", f"Historial disponible: {int(total)} s. Recuperando {self.clip_duration} s desde hace {self.lookback} s."))
                source = root / "historial.m3u8"
                source.write_text(playlist, encoding="utf-8")
                input_options = live_inputs(info, cookie_file)[:-2]
                input_options += ["-protocol_whitelist", "file,http,https,tcp,tls,crypto", "-allowed_extensions", "ALL"]
                events = self.events

                class ForwardEvents:
                    def put(self, event):
                        if event[0] != "done":
                            events.put(event)

                self.child = LocalClipJob(replace(self.request, start=start, end=end), ForwardEvents(), source, total, input_options)
                if self.cancelled.is_set():
                    self.child.cancel()
                    return
                self.child.run()
        except Exception as exc:
            if not self.cancelled.is_set():
                self.events.put(("error", str(exc)))
        finally:
            self._stop_process()
            if self.process:
                self.process.wait()
            if self.cancelled.is_set():
                self.events.put(("cancelled", None))
            self.events.put(("done", None))
