"""Les sous-titres automatiques : le cache par video, ce qu'on demande au
calcul, le « CC » des bandeaux et Ctrl+S.

Lancement : python tests/test_subtitles.py
Sans le Labo IA : le calcul lui-meme n'est pas lance (voir --verifier et
l'essai a l'ecran) ; on verifie tout ce qui l'entoure.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("PRISME_SANDBOX",
                      os.path.join(tempfile.gettempdir(), "prisme-tests-subs"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from make_fixture import make_video  # noqa: E402

FAILS: list = []


def check(condition, label: str) -> None:
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        FAILS.append(label)


def main() -> int:
    app = QApplication(sys.argv)
    from videosorter import subtitles
    from videosorter.config import Config
    from videosorter.media import Tools
    from videosorter.scan import MODE_FILES
    Tools.resolve()
    base = Path(tempfile.gettempdir()) / "prisme-subs-fixture"
    clip = base / "films" / "film_0.mp4"
    make_video(clip, 12, 1)
    import shutil
    shutil.rmtree(subtitles.folder(), ignore_errors=True)

    print("\n[1] Ce qu'on demande au calcul")
    track = subtitles.Track(str(clip))
    check(track.next_job(0.0, True, 75) == ("chunk", 0.0, subtitles.FIRST_S),
          "rien de fait : un petit morceau d'abord, au debut")
    track.merge({"start": 0.0, "end": 10.0, "language": "en", "task": "transcribe",
                 "segments": [[1.0, 3.0, "Hello there.", "Bonjour."]]}, True)
    check(track.text_at(2.0, True) == "Bonjour." and track.text_at(2.0, False) == "Hello there.",
          "le texte a l'instant, traduit ou non")
    check(track.text_at(5.0, True) == "", "rien entre deux phrases")
    check(track.next_job(8.0, True, 75) == ("chunk", 10.0, subtitles.FIRST_S),
          "juste devant la lecture : encore un petit morceau")
    check(track.next_job(2.0, True, 75) == ("chunk", 10.0, 18.0),
          "un peu d'avance : un morceau moyen")
    track.merge({"start": 10.0, "end": 40.0, "language": "en", "segments": []}, True)
    check(track.next_job(2.0, True, 75) == ("chunk", 40.0, subtitles.CHUNK_S),
          "l'avance prise : un grand morceau")
    track.merge({"start": 40.0, "end": 52.0, "eof": True, "segments": []}, True)
    check(track.next_job(2.0, True, 75) is None, "la fin du son atteinte : plus rien a faire")
    track.save()
    again = subtitles.Track(str(clip))
    check(again.text_at(2.0, True) == "Bonjour." and again.eof == 40.0,
          "garde dans le cache, relu tel quel")
    orig = subtitles.Track(str(clip))
    orig.chunks = [[0.0, 10.0, "transcribe", False]]
    check(orig.next_job(1.0, True, 75)[0] == "translate",
          "fait en langue d'origine : ne reste que la traduction")

    print("\n[2] Le « CC » des bandeaux, et Ctrl+S")
    from videosorter.window import MainWindow
    window = MainWindow(Config())
    window.show()
    window.start_root(clip.parent, MODE_FILES)
    for bar in (window.single_bar, window.floating.bar):
        check(bar.subtitles_button.text() == "CC", "« CC » dans le bandeau, coupe au depart")
    real = subtitles.available, subtitles.missing
    subtitles.available = lambda: True
    subtitles.missing = lambda _mode: []
    try:
        window.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_S, Qt.ControlModifier))
        check(window.cfg["subtitles"] == "orig", "Ctrl+S : la langue parlee")
        window.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_S, Qt.ControlModifier))
        check(window.cfg["subtitles"] == "fr"
              and window.single_bar.subtitles_button.text() == "FR"
              and window.floating.bar.subtitles_button.text() == "FR",
              "Ctrl+S encore : en francais, sur les deux bandeaux")
        window.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_S, Qt.ControlModifier))
        check(window.cfg["subtitles"] == "off", "et coupes au troisieme")
    finally:
        subtitles.available, subtitles.missing = real
    window.set_subtitles("off")
    window.close()
    subtitles.close()
    app.processEvents()
    time.sleep(0.2)
    print("\ntout est vert" if not FAILS else f"\n{len(FAILS)} echec(s)")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
