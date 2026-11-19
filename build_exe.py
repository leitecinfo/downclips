"""Genera dist/DownClip.exe con las herramientas de video incluidas.

Preparación: python -m pip install pyinstaller
Descargar yt-dlp.exe oficial a vendor/yt-dlp.exe antes de ejecutar.
FFmpeg y ffprobe se toman del PATH de la máquina de compilación.
"""

from pathlib import Path
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
