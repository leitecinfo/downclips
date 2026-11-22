"""Comprobación manual de controles Tk: python tests/verify_ui.py (abre ventanas)."""
from pathlib import Path
import sys
from types import SimpleNamespace
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import DownClipApp


def main():
    app = DownClipApp()
    try:
        app.update()
        app.mode.set("Directo en vivo")
        app._change_mode()
        app.update()
        assert app.live_panel.winfo_manager() == "grid"
        assert app.full_video_check.cget("state") == "disabled"
        assert app.exact.get()
        assert app.download_button.cget("text") == "Guardar clip del directo"
        assert app.history_back.get() == "00:01:00"
        assert app.history_length.get() == "00:00:30"
        app.live_duration = 65.8
        app._live_controls()
        app.mark_live("last")
        assert app.start.get() == "00:00:35"
        assert app.end.get() == "00:01:05"
        app.recorder = SimpleNamespace(stopping=threading.Event())
        app._live_controls()
        assert app.mode.cget("state") == "disabled"
        assert app.url.cget("state") == "disabled"
        assert app.mark_end_button.cget("state") == "normal"
        app.job = object()
        app._live_controls()
        assert app.mark_end_button.cget("state") == "disabled"
        assert app.history_button.cget("state") == "disabled"
        app.recorder = None
        app.job = None
        app._live_controls()
        app.mode.set("Video / VOD")
        app._change_mode()
        app.full_video.set(True)
        app._on_full_video_toggle()
        assert str(app.start.controls[0].cget("state")) == "disabled"
        app.full_video.set(False)
        app._on_full_video_toggle()
        assert str(app.start.controls[0].cget("state")) == "readonly"
        for size in ("710x670", "850x900"):
            app.geometry(size)
            app.update()
            assert app.cancel_button.winfo_rooty() + app.cancel_button.winfo_height() < app.winfo_rooty() + app.winfo_height()
        print("UI: modos, marcas, estados y ventanas mínima/amplia: OK")
    finally:
        app.destroy()


if __name__ == "__main__":
    main()
