"""DownClip: interfaz local para descargar segmentos con yt-dlp y FFmpeg.

Python 3.10+. Ejecutar: python app.py
El hilo de descarga comunica eventos mediante Queue; solo Tk modifica la UI.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlsplit

import customtkinter as ctk


COLORS = {
    "background": "#0F172A", "surface": "#192134", "input": "#111827",
    "text": "#F8FAFC", "muted": "#B0BCD0", "border": "#58677E",
    "accent": "#2563EB", "hover": "#1D4ED8", "error": "#FDA4AF",
    "success": "#86EFAC",
}
FILE_MARKER = "DOWNCLIP_FILE="
TIME_PATTERN = re.compile(r"[0-9]{1,6}:[0-5][0-9](?::[0-5][0-9])?")


def parse_time(value: str, label: str = "Tiempo") -> int:
    """MM:SS permite minutos >59; HH:MM:SS limita minutos y segundos a 59."""
    value = value.strip()
    if not value:
        raise ValueError(f"{label}: completa este campo (por ejemplo, 00:00).")
    if not TIME_PATTERN.fullmatch(value):
        raise ValueError(f"{label}: usa MM:SS o HH:MM:SS; segundos entre 00 y 59.")
    parts = [int(part) for part in value.split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def timestamp(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def validate_url(value: str) -> str:
    value = value.strip()
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme in {"http", "https"} and bool(parsed.hostname)
        parsed.port  # Detecta puertos mal formados.
    except ValueError:
        valid = False
    if not valid or any(char.isspace() or ord(char) < 32 for char in value):
        raise ValueError("URL: pega un enlace válido que empiece por https:// o http://.")
    return value


@dataclass(frozen=True)
class DownloadRequest:
    url: str
    start: int
    end: int
    destination: Path
    exact: bool = False
    browser: str | None = None


def build_command(request: DownloadRequest, executable: str) -> list[str]:
    """Argumentos separados, sin shell ni interpolación de comandos del usuario."""
    if request.start < 0 or request.end <= request.start:
        raise ValueError("El tiempo de fin debe ser mayor que el tiempo de inicio.")
    if request.browser not in {None, "chrome", "edge", "brave"}:
        raise ValueError("Elige chrome, edge o brave para las cookies.")
    start, end = timestamp(request.start), timestamp(request.end)
    filename = (
        f"%(title).100B [%(id)s]_{start.replace(':', '-')}-"
        f"{end.replace(':', '-')}.%(ext)s"
    )
    command = [
        executable, "--ignore-config", "--no-playlist", "--no-colors", "--newline",
        "--no-simulate", "--no-quiet", "--progress", "--no-overwrites",
        "--windows-filenames", "--trim-filenames", "180",
        "--download-sections", f"*{start}-{end}",
        "--merge-output-format", "mp4", "--recode-video", "mp4",
        "--paths", str(request.destination), "--output", filename,
        "--print", f"after_move:{FILE_MARKER}%(filepath)j",
    ]
    if request.exact:
        command.append("--force-keyframes-at-cuts")
    if request.browser:
        command.extend(["--cookies-from-browser", request.browser])
    if getattr(sys, "frozen", False):
        command.extend(["--ffmpeg-location", str(Path(sys._MEIPASS) / "tools")])
    command.extend(["--", validate_url(request.url)])
    return command


def check_dependencies() -> str:
    if getattr(sys, "frozen", False):
        tools = Path(sys._MEIPASS) / "tools"
        missing = [name for name in ("yt-dlp.exe", "ffmpeg.exe", "ffprobe.exe")
                   if not (tools / name).is_file()]
        if missing:
            raise RuntimeError("Faltan herramientas incluidas en el ejecutable: " + ", ".join(missing))
        return str(tools / "yt-dlp.exe")
    found = {name: shutil.which(name) for name in ("yt-dlp", "ffmpeg", "ffprobe")}
    missing = [name for name, path in found.items() if not path]
    if missing:
        raise RuntimeError(
            "No se encontró en el PATH: " + ", ".join(missing) + ".\n\n"
            "Instala las dependencias indicadas en README.md y vuelve a abrir la terminal.\n"
            "Si usas un entorno virtual, actívalo antes de iniciar la aplicación."
        )
    return str(found["yt-dlp"])


class DownloadJob:
    """Un trabajo por instancia; ninguna llamada de Tk desde los hilos."""

    def __init__(self, request: DownloadRequest, events: queue.Queue):
        self.request = request
        self.events = events
        self.cancelled = threading.Event()
        self.lock = threading.Lock()
        self.process: subprocess.Popen | None = None

    def cancel(self) -> None:
        self.cancelled.set()
        threading.Thread(target=self._stop_process, daemon=True).start()

    def _stop_process(self) -> None:
        # Mantiene el bloqueo durante la terminación para no reutilizar un PID terminado.
        with self.lock:
            process = self.process
            if process is None or process.poll() is not None:
                return
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW, check=False,
                )
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def run(self) -> None:
        try:
            executable = check_dependencies()
            self.request.destination.mkdir(parents=True, exist_ok=True)
            command = build_command(self.request, executable)
            if self.cancelled.is_set():
                return
            self.events.put(("log", "Preparando descarga y lectura de metadatos…"))
            options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            with self.lock:
                self.process = subprocess.Popen(
                    command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                    errors="replace", bufsize=1, env=env, **options,
                )
            if self.cancelled.is_set():
                self._stop_process()
            output_files: list[Path] = []
            with self.process.stdout as stream:
                for line in stream:
                    line = line.rstrip()
                    if line.startswith(FILE_MARKER):
                        output_files.append(Path(json.loads(line[len(FILE_MARKER):])))
                    elif line:
                        self.events.put(("log", line))
            code = self.process.wait()
            if self.cancelled.is_set():
                return
            if code != 0:
                raise RuntimeError(
                    f"yt-dlp terminó con código {code}. Revisa el registro para ver la causa.\n"
                    "Si aparece 403/Cloudflare, prueba las cookies del navegador y actualiza yt-dlp."
                )
            files = [path for path in output_files if path.is_file() and path.suffix.lower() == ".mp4" and path.stat().st_size > 0]
            if not files:
                raise RuntimeError(
                    "yt-dlp terminó sin confirmar un archivo MP4. Revisa el registro y el rango solicitado."
                )
            self.events.put(("success", files[-1]))
        except Exception as exc:
            if not self.cancelled.is_set():
                self.events.put(("error", str(exc)))
        finally:
            # También limpia procesos si falla la lectura de stdout.
            self._stop_process()
            if self.process is not None:
                self.process.wait()
            if self.cancelled.is_set():
                self.events.put(("cancelled", None))
            self.events.put(("done", None))


class TimeSelector(ctk.CTkFrame):
    """Tres listas de solo selección, con navegación por teclado y desplazamiento."""

    def __init__(self, parent, initial_seconds: int = 0):
        super().__init__(parent, fg_color="transparent")
        hours, remainder = divmod(initial_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        self.controls = []
        for column, (label, limit, initial) in enumerate([
            ("Horas", 100, hours), ("Minutos", 60, minutes), ("Segundos", 60, seconds),
        ]):
            self.grid_columnconfigure(column, weight=1, uniform="time")
            ctk.CTkLabel(self, text=label, text_color=COLORS["muted"],
                         font=("Segoe UI", 12), anchor="w").grid(
                row=0, column=column, sticky="ew", padx=(0, 6))
            control = ttk.Combobox(
                self, values=[f"{value:02d}" for value in range(limit)],
                state="readonly", height=8, width=3, justify="center",
                style="Time.TCombobox", font=("Segoe UI", 13), takefocus=True,
            )
            control.current(initial)
            control.grid(row=1, column=column, sticky="ew", padx=(0, 6), pady=(2, 0))
            self.controls.append(control)

    def get(self) -> str:
        return ":".join(control.get() for control in self.controls)

    def set_enabled(self, enabled: bool) -> None:
        for control in self.controls:
            control.configure(state="readonly" if enabled else "disabled")


class DownClipApp(ctk.CTk):
    def __init__(self):
        # Dimensiones de ventana en píxeles de pantalla; los controles conservan
        # su escalado DPI automático dentro del formulario desplazable.
        ctk.set_window_scaling(1.0)
        super().__init__()
        self.title("DownClip · Descargador de segmentos")
        self.geometry(f"850x{min(900, max(670, self.winfo_screenheight() - 100))}")
        self.minsize(710, 670)
        self.configure(fg_color=COLORS["background"])
        time_style = ttk.Style(self)
        time_style.theme_use("clam")
        time_style.configure(
            "Time.TCombobox", padding=(6, 9), fieldbackground=COLORS["input"],
            background=COLORS["surface"], foreground=COLORS["text"],
            arrowcolor=COLORS["text"], bordercolor=COLORS["border"],
        )
        time_style.map(
            "Time.TCombobox",
            fieldbackground=[("readonly", COLORS["input"])],
            foreground=[("disabled", COLORS["muted"]), ("readonly", COLORS["text"])],
            bordercolor=[("focus", COLORS["accent"])],
            selectbackground=[("readonly", COLORS["accent"])],
            selectforeground=[("readonly", COLORS["text"])],
        )
        self.events: queue.Queue = queue.Queue()
        self.job: DownloadJob | None = None
        self.closing = False
        self.destination = Path.home() / "Downloads"
        self.exact = ctk.BooleanVar(value=False)
        self.cookies = ctk.BooleanVar(value=False)
        self.inputs: list = []
        self.protocol("WM_DELETE_WINDOW", self.close_app)
        self._build_ui()
        self.after(100, self._poll_events)

    def _label(self, parent, text, **kwargs):
        kwargs.setdefault("text_color", COLORS["text"])
        return ctk.CTkLabel(parent, text=text, anchor="w", **kwargs)

    def _entry(self, parent, placeholder):
        entry = ctk.CTkEntry(
            parent, placeholder_text=placeholder, height=44, corner_radius=8,
            fg_color=COLORS["input"], border_color=COLORS["border"],
            text_color=COLORS["text"], placeholder_text_color=COLORS["muted"],
        )
        entry.bind("<FocusIn>", lambda event: entry.configure(border_color=COLORS["accent"]))
        entry.bind("<FocusOut>", lambda event: entry.configure(border_color=COLORS["border"]))
        self.inputs.append(entry)
        return entry

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        page = ctk.CTkScrollableFrame(self, fg_color="transparent")
        page.grid(row=0, column=0, sticky="nsew", padx=24, pady=20)
        page.grid_columnconfigure(0, weight=1)
        self._label(page, "DOWNCLIP", font=("Segoe UI", 14, "bold")).grid(row=0, column=0, sticky="w")
        self._label(page, "Guarda solo el momento.", font=("Segoe UI", 30, "bold")).grid(row=1, column=0, sticky="w", pady=(4, 4))
        self._label(page, "Pega un enlace, define el rango y descarga tu segmento en MP4.", text_color=COLORS["muted"]).grid(row=2, column=0, sticky="w", pady=(0, 24))

        card = ctk.CTkFrame(page, fg_color=COLORS["surface"], corner_radius=14)
        card.grid(row=3, column=0, sticky="ew")
        card.grid_columnconfigure((0, 1), weight=1, uniform="halves")
        self._label(card, "01  /  VIDEO", font=("Segoe UI", 13, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", padx=24, pady=(20, 8))
        self._label(card, "URL del video").grid(row=1, column=0, columnspan=2, sticky="w", padx=24)
        self.url = self._entry(card, "https://www.youtube.com/watch?v=…")
        self.url.grid(row=2, column=0, columnspan=2, sticky="ew", padx=24, pady=(4, 6))
        self._label(card, "YouTube · TikTok · Kick · otros sitios compatibles con yt-dlp", font=("Segoe UI", 12)).grid(row=3, column=0, columnspan=2, sticky="w", padx=24, pady=(0, 18))
        self._label(card, "02  /  SEGMENTO", font=("Segoe UI", 13, "bold")).grid(row=4, column=0, columnspan=2, sticky="w", padx=24, pady=(0, 8))
        self._label(card, "Tiempo de inicio").grid(row=5, column=0, sticky="w", padx=(24, 8))
        self._label(card, "Tiempo de fin").grid(row=5, column=1, sticky="w", padx=(8, 24))
        self.start = TimeSelector(card)
        self.end = TimeSelector(card, initial_seconds=330)
        self.start.grid(row=6, column=0, sticky="ew", padx=(24, 8), pady=4)
        self.end.grid(row=6, column=1, sticky="ew", padx=(8, 24), pady=4)
        self._label(card, "Selecciona horas, minutos y segundos. El fin debe ser posterior al inicio.", font=("Segoe UI", 12)).grid(row=7, column=0, columnspan=2, sticky="w", padx=24, pady=(0, 16))
        self.exact_check = ctk.CTkCheckBox(card, text="Corte exacto al segundo", variable=self.exact, height=32)
        self.exact_check.grid(row=8, column=0, columnspan=2, sticky="w", padx=24)
        self._label(card, "Recodifica para ajustar el corte; puede tardar más.", font=("Segoe UI", 12)).grid(row=9, column=0, columnspan=2, sticky="w", padx=24, pady=(0, 10))
        self.cookie_check = ctk.CTkCheckBox(card, text="Usar cookies del navegador (Kick/Cloudflare)", variable=self.cookies, command=self._cookie_state, height=32)
        self.cookie_check.grid(row=10, column=0, columnspan=2, sticky="w", padx=24)
        self.browser = ctk.CTkOptionMenu(card, values=["chrome", "edge", "brave"], state="disabled", height=36, fg_color=COLORS["input"], button_color=COLORS["accent"])
        self.browser.grid(row=11, column=0, columnspan=2, sticky="w", padx=24, pady=(6, 20))
        self.inputs.extend([self.exact_check, self.cookie_check])

        self._label(page, "03  /  DESTINO", font=("Segoe UI", 13, "bold")).grid(row=4, column=0, sticky="w", pady=(20, 6))
        destination_row = ctk.CTkFrame(page, fg_color="transparent")
        destination_row.grid(row=5, column=0, sticky="ew")
        destination_row.grid_columnconfigure(0, weight=1)
        self.path_var = ctk.StringVar(value=str(self.destination))
        self.path_entry = ctk.CTkEntry(destination_row, textvariable=self.path_var, state="readonly", height=42, fg_color=COLORS["input"], border_color=COLORS["border"])
        self.path_entry.grid(row=0, column=0, sticky="ew", padx=(0, 12))
        self.folder_button = ctk.CTkButton(destination_row, text="Elegir carpeta…", command=self.choose_folder, height=42, width=140)
        self.folder_button.grid(row=0, column=1)
        self.inputs.append(self.folder_button)

        # Acciones y registro fijos; solo el formulario necesita desplazarse.
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=1, column=0, sticky="ew", padx=32, pady=(0, 8))
        actions.grid_columnconfigure(0, weight=1)
        self.download_button = ctk.CTkButton(actions, text="Descargar Segmento", height=48, font=("Segoe UI", 16, "bold"), command=self.start_download)
        self.download_button.grid(row=0, column=0, sticky="ew")
        self.cancel_button = ctk.CTkButton(actions, text="Cancelar", width=100, height=48, fg_color=COLORS["surface"], state="disabled", command=self.cancel_download)
        self.cancel_button.grid(row=0, column=1, padx=(12, 0))
        self.status = self._label(self, "Listo para descargar.", wraplength=650)
        self.status.grid(row=2, column=0, sticky="ew", padx=32, pady=(4, 8))
        self._label(self, "REGISTRO EN TIEMPO REAL", font=("Segoe UI", 12, "bold")).grid(row=3, column=0, sticky="w", padx=32, pady=(8, 6))
        self.logs = ctk.CTkTextbox(self, height=140, fg_color=COLORS["input"], text_color=COLORS["muted"], font=("Consolas", 12), state="disabled", wrap="word")
        self.logs.grid(row=4, column=0, sticky="ew", padx=32, pady=(0, 24))
        self.url.focus_set()

    def choose_folder(self):
        selected = filedialog.askdirectory(parent=self, title="Carpeta de destino", initialdir=str(self.destination if self.destination.exists() else Path.home()))
        if selected:
            self.destination = Path(selected)
            self.path_var.set(str(self.destination))

    def _cookie_state(self):
        self.browser.configure(state="normal" if self.cookies.get() and self.job is None else "disabled")

    def _log(self, text):
        self.logs.configure(state="normal")
        self.logs.insert("end", text + "\n")
        # Limita el historial visible para mantener fluida la ventana en sesiones largas.
        lines = int(self.logs.index("end-1c").split(".")[0])
        if lines > 2000:
            self.logs.delete("1.0", f"{lines - 1500}.0")
        self.logs.see("end")
        self.logs.configure(state="disabled")

    def start_download(self):
        if self.job is not None:
            return
        try:
            url = validate_url(self.url.get())
            start = parse_time(self.start.get(), "Tiempo de inicio")
            end = parse_time(self.end.get(), "Tiempo de fin")
            if end <= start:
                raise ValueError("El tiempo de fin debe ser mayor que el tiempo de inicio.")
            request = DownloadRequest(url, start, end, self.destination, self.exact.get(), self.browser.get() if self.cookies.get() else None)
        except ValueError as exc:
            self.status.configure(text=str(exc), text_color=COLORS["error"])
            messagebox.showerror("Revisa los datos", str(exc), parent=self)
            return
        self.logs.configure(state="normal")
        self.logs.delete("1.0", "end")
        self.logs.configure(state="disabled")
        self.job = DownloadJob(request, self.events)
        for widget in self.inputs:
            widget.configure(state="disabled")
        self.start.set_enabled(False)
        self.end.set_enabled(False)
        self.browser.configure(state="disabled")
        self.download_button.configure(state="disabled", text="Descargando…")
        self.cancel_button.configure(state="normal")
        self.status.configure(text=f"Descargando {timestamp(start)} → {timestamp(end)} · duración solicitada {timestamp(end - start)}", text_color=COLORS["text"])
        threading.Thread(target=self.job.run, daemon=True).start()

    def cancel_download(self):
        if self.job:
            self.cancel_button.configure(state="disabled")
            self.status.configure(text="Cancelando y cerrando los procesos de descarga…")
            self.job.cancel()

    def _poll_events(self):
        # Lote acotado: una descarga con muchos logs no bloquea el bucle de Tk.
        for _ in range(100):
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self._log(payload)
            elif kind == "success":
                self._log(f"Archivo listo: {payload}")
                self.status.configure(text="Segmento MP4 listo. Consulta la ruta en el registro.", text_color=COLORS["success"])
                if not self.closing:
                    messagebox.showinfo("Descarga completada", f"Segmento guardado en:\n{payload}", parent=self)
            elif kind == "error":
                self._log("ERROR: " + payload)
                self.status.configure(text="No se completó la descarga. Consulta el registro.", text_color=COLORS["error"])
                if not self.closing:
                    messagebox.showerror("Error de descarga", payload, parent=self)
            elif kind == "cancelled":
                self.status.configure(text="Descarga cancelada. Pueden quedar archivos parciales.", text_color=COLORS["muted"])
                self._log("Cancelado por el usuario.")
            elif kind == "done":
                self.job = None
                if self.closing:
                    self.destroy()
                    return
                for widget in self.inputs:
                    widget.configure(state="normal")
                self.start.set_enabled(True)
                self.end.set_enabled(True)
                self._cookie_state()
                self.download_button.configure(state="normal", text="Descargar Segmento")
                self.cancel_button.configure(state="disabled")
        self.after(100, self._poll_events)

    def close_app(self):
        if self.job:
            self.closing = True
            self.cancel_download()
        else:
            self.destroy()


def main():
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    DownClipApp().mainloop()


if __name__ == "__main__":
    main()
