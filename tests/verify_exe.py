"""Prueba manual del ejecutable compilado: python tests/verify_exe.py."""

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def visible_app_windows():
    user32 = ctypes.windll.user32
    windows = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def visit(hwnd, _):
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title, len(title))
        if title.value == "DownClip · Descargador de segmentos" and user32.IsWindowVisible(hwnd):
            windows.append(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    return set(windows)


def main():
    executable = ROOT / "dist" / "DownClip.exe"
    runtime = ROOT / "build" / "smoke-runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["TEMP"] = env["TMP"] = str(runtime)
    # Comprueba la apertura sin Python ni herramientas de video en PATH.
    env["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
    previous_windows = visible_app_windows()
    process = subprocess.Popen([str(executable)], env=env)
    opened_windows = set()
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"El ejecutable terminó al abrir: {process.returncode}")
            opened_windows = visible_app_windows() - previous_windows
            tool_folders = list(runtime.glob("_MEI*/tools"))
            if opened_windows and tool_folders:
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("No se encontró la ventana de DownClip")
        tools = tool_folders[-1]
        print("Ventana del ejecutable abierta sin Python ni FFmpeg en PATH.")
        print("Herramientas extraídas:", tools, [item.name for item in tools.iterdir()])
        for tool in ("yt-dlp", "ffmpeg", "ffprobe", "node"):
            args = [str(tools / f"{tool}.exe"), "--version" if tool in {"yt-dlp", "node"} else "-version"]
            if not Path(args[0]).is_file():
                raise RuntimeError(f"Herramienta ausente: {args[0]}; estado de la aplicación: {process.poll()}")
            result = subprocess.run(args, env=env, check=True, capture_output=True,
                                    text=True, errors="replace", timeout=30,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            print(tool + ": " + result.stdout.splitlines()[0])
        from test_local_download import LocalDownloadTests
        from test_live import LiveIntegrationTests
        from test_dvr import DvrIntegrationTests
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(LocalDownloadTests)
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(LiveIntegrationTests))
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(DvrIntegrationTests))
        with patch("app.sys.frozen", True, create=True), patch("app.sys._MEIPASS", str(tools.parent), create=True):
            result = unittest.TextTestRunner(verbosity=2).run(suite)
        if not result.wasSuccessful() or result.skipped:
            raise RuntimeError("No se pudo verificar el recorte con las herramientas empaquetadas")
        print("Ejecutable y recorte con herramientas incluidas: OK")
    finally:
        user32 = ctypes.windll.user32
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        for hwnd in opened_windows:
            user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE: cierre normal de la ventana.
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           capture_output=True, check=False)
            process.wait(timeout=10)


if __name__ == "__main__":
    main()
