"""Doublons de photos : copies exactes et même image sous un autre poids.

    python tests/test_photo_dupes.py

Court et ciblé : une dizaine d'images fabriquées dans le bac à sable, une
recherche, puis une seconde qui ne doit plus rien relire.
"""
from __future__ import annotations

import os
import shutil
import struct
import sys
import tempfile
from pathlib import Path

BOX = os.path.join(tempfile.gettempdir(), "prisme_sandbox_photo_dupes")
shutil.rmtree(BOX, ignore_errors=True)
os.environ["PRISME_SANDBOX"] = BOX
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.stdout.reconfigure(encoding="utf-8")

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor, QImage, QLinearGradient, QPainter, QPen, QTransform,
)
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from videosorter import config, dupes  # noqa: E402
from videosorter.config import KIND_PHOTO, kind_paths, set_media_kind  # noqa: E402
from videosorter.dupes_memory import NOT_DUPES, FoundDupes  # noqa: E402
from videosorter.index import INDEX  # noqa: E402

FAILS = []


def check(condition, label: str) -> None:
    print(("  ok   " if condition else "  ÉCHEC ") + label)
    if not condition:
        FAILS.append(label)


def scene(seed: int, width: int = 1600, height: int = 1000) -> QImage:
    """Une « photo » : un ciel en degrade, des collines, des formes placees
    selon `seed`. Deux graines donnent deux images franchement differentes."""
    image = QImage(width, height, QImage.Format_RGB32)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    sky = QLinearGradient(0, 0, 0, height)
    sky.setColorAt(0, QColor.fromHsv((seed * 47) % 360, 120, 230))
    sky.setColorAt(1, QColor.fromHsv((seed * 47 + 40) % 360, 60, 90))
    painter.fillRect(image.rect(), sky)
    for n in range(9):
        x = ((seed * 131 + n * 197) % 100) / 100 * width
        y = ((seed * 71 + n * 89) % 100) / 100 * height
        r = (40 + (seed * 13 + n * 29) % 160) / 1000 * width
        painter.setBrush(QColor.fromHsv((seed * 29 + n * 61) % 360, 200, 60 + 20 * (n % 9)))
        painter.setPen(QPen(QColor(20, 20, 20), 6))
        painter.drawEllipse(QPointF(x, y), r, r * 0.7)
    painter.fillRect(QRectF(0, height * (0.55 + (seed % 5) / 20), width, height),
                     QColor.fromHsv((seed * 83) % 360, 160, 70))
    painter.end()
    return image


def with_exif_orientation(jpeg: Path, orientation: int) -> None:
    """Glisse un bloc EXIF « Orientation » juste apres l'en-tete du JPEG."""
    tiff = (b"MM\x00\x2a\x00\x00\x00\x08" + struct.pack(">H", 1)
            + struct.pack(">HHIHH", 0x0112, 3, 1, orientation, 0)
            + struct.pack(">I", 0))
    body = b"Exif\x00\x00" + tiff
    data = jpeg.read_bytes()
    jpeg.write_bytes(data[:2] + b"\xff\xe1" + struct.pack(">H", len(body) + 2)
                     + body + data[2:])


def main() -> int:
    check(str(config.APP_DIR) == BOX, "tout se passe dans le bac à sable")
    set_media_kind(KIND_PHOTO)
    INDEX.reopen(kind_paths(KIND_PHOTO)[0], migrate=False)
    root = Path(BOX) / "photos"
    (root / "Vacances").mkdir(parents=True)
    (root / "Copies").mkdir()

    original = scene(1)
    original.save(str(root / "Vacances" / "plage.jpg"), "JPG", 92)
    shutil.copy2(root / "Vacances" / "plage.jpg", root / "Copies" / "plage (copie).jpg")
    original.scaled(800, 500, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).save(
        str(root / "Copies" / "plage-petite.jpg"), "JPG", 55)
    original.save(str(root / "Copies" / "plage.png"), "PNG")
    original.scaled(1200, 750, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).save(
        str(root / "Copies" / "plage.webp"), "WEBP", 60)
    # Rangee couchee, redressee par son EXIF (6 : tourner d'un quart a droite).
    turned = root / "Copies" / "plage-telephone.jpg"
    original.transformed(QTransform().rotate(-90)).save(str(turned), "JPG", 85)
    with_exif_orientation(turned, 6)
    # Une autre photo, et deux BMP de meme taille mais d'images differentes.
    scene(2).save(str(root / "Vacances" / "montagne.jpg"), "JPG", 90)
    scene(3, 400, 300).save(str(root / "Vacances" / "a.bmp"), "BMP")
    scene(4, 400, 300).save(str(root / "Vacances" / "b.bmp"), "BMP")
    # Une icone : trop petite pour compter.
    scene(5, 24, 24).save(str(root / "Vacances" / "icone.png"), "PNG")
    shutil.copy2(root / "Vacances" / "icone.png", root / "Copies" / "icone.png")

    print("\n[1] Une recherche trouve copies exactes et memes images")
    reads = []
    real_image = dupes._photo_image

    def counted(photo, width=0):
        reads.append(str(photo))
        return real_image(photo, width)
    dupes._photo_image = counted

    def run():
        out = {}
        scan = dupes.PhotoDuplicateScan(root, 0, True)
        scan.found.connect(lambda groups: out.setdefault("groups", groups))
        scan.run()
        return out.get("groups") or []

    groups = run()
    names = [[p.name for p in g.paths] for g in groups]
    print("      groupes :", names)
    check(len(groups) == 1, "un seul groupe")
    group = groups[0] if groups else None
    members = {p.name for p in group.paths} if group else set()
    check(members == {"plage.jpg", "plage (copie).jpg", "plage-petite.jpg", "plage.png",
                      "plage.webp", "plage-telephone.jpg"},
          "la copie, la réduite, le PNG, le WebP et la photo redressée par l'EXIF")
    check(group is not None and group.sure, "le groupe est sûr : les autres sont cochés d'office")
    pixels = [g.widths[i] * g.heights[i] for g in groups[:1] for i in range(len(g.paths))]
    check(group is not None and pixels[0] == max(pixels) and group.dimensions(0) == "1600×1000",
          "le meilleur en tête : la plus grande définition")
    full = [i for i, value in enumerate(pixels) if value == pixels[0]]
    check(group is not None and group.sizes[0] == max(group.sizes[i] for i in full),
          "puis le plus gros fichier")
    check(group is not None and group.dimensions(
        [p.name for p in group.paths].index("plage-telephone.jpg")) == "1600×1000",
        "l'orientation EXIF est respectée")
    small = group.paths[[p.name for p in group.paths].index("plage-petite.jpg")] if group else ""
    check(group is not None and group.gap(small).startswith("800×500 au lieu de 1600×1000"),
          f"ce qui sépare une copie du meilleur ({group.gap(small) if group else ''})")
    check(len(reads) == 9, f"chaque photo assez grande est lue une fois ({len(reads)})")

    print("\n[2] La seconde fois, rien n'est relu")
    reads.clear()
    again = run()
    check(not reads, f"aucune photo relue ({len(reads)})")
    check([[str(p) for p in g.paths] for g in again]
          == [[str(p) for p in g.paths] for g in groups], "le même résultat")

    print("\n[3] La revue montre des photos")
    from videosorter.dupes_view import DupesReview
    review = DupesReview(None, groups)
    check(review.photo and review.defaults[:2] == ("best", "biggest"),
          "critères : définition, puis poids")
    check(review.groups and review.groups[0].keep[0] is True
          and all(k is False for k in review.groups[0].keep[1:]),
          "« ✓ garder » sur le meilleur, les autres en corbeille d'office")
    check("×" in review._facts_text(review.groups[0], 0), "les dimensions de la photo")
    review.close()
    review.deleteLater()

    print("\n[4] Un fichier de résultat par collection")
    check(FoundDupes().path.name == "doublons-trouves-photos.json",
          "les doublons de photos ne reviennent pas parmi les vidéos")

    print("\n[5] « Pas des doublons » vaut pour de bon")
    NOT_DUPES.ignorer_groupe([str(p) for p in group.paths] if group else [])
    check(not run(), "le groupe ne revient plus")

    INDEX.close()
    print("\n" + ("Tout est bon." if not FAILS else f"{len(FAILS)} échec(s)."))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
