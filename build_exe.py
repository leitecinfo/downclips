"""Genera dist/DownClip.exe con las herramientas de video incluidas.

Preparación: python -m pip install pyinstaller
Descargar yt-dlp.exe oficial a vendor/yt-dlp.exe antes de ejecutar.
FFmpeg, ffprobe y Node.js >=22 se toman del PATH de la máquina de compilación.
"""

from pathlib import Path
import subprocess
import shutil

import PyInstaller.__main__


def main():
    root = Path(__file__).resolve().parent
    tools = [root / "vendor" / "yt-dlp.exe"]
    for name in ("ffmpeg", "ffprobe"):
        executable = shutil.which(name)
        if not executable:
            raise SystemExit(f"No se encuentra {name} en el PATH para incluirlo.")
        tools.append(Path(executable))
    if not tools[0].is_file():
        raise SystemExit("Descarga el ejecutable oficial de yt-dlp en vendor/yt-dlp.exe.")
    # El portable debe resolver los retos JavaScript de YouTube sin depender del PATH.
    runtime = shutil.which("node")
    if not runtime:
        raise SystemExit("Se requiere Node.js >=22 en PATH para incluirlo en el portable.")
    version = subprocess.run([runtime, "--version"], check=True, capture_output=True, text=True).stdout.strip()
    if int(version.lstrip("v").split(".")[0]) < 22:
        raise SystemExit("Se requiere Node.js >=22 para yt-dlp.")
    tools.append(Path(runtime))
    options = [
        str(root / "app.py"), "--name", "DownClip", "--onefile", "--windowed",
        "--noconfirm", "--noupx", "--collect-all", "customtkinter",
        "--distpath", str(root / "dist"), "--workpath", str(root / "build"),
        "--specpath", str(root),
    ]
    for executable in tools:
        options.extend(["--add-data", f"{executable}:tools"])
    PyInstaller.__main__.run(options)


if __name__ == "__main__":
    main()
