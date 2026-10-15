"""Les videos seules dans leur dossier, proposees au regroupement."""
from __future__ import annotations

import os
from pathlib import Path
import shiboken6
from PySide6.QtCore import QTimer


class SolosMixin:
    """Une part de la fenetre principale : MainWindow en herite."""

    # -- les videos seules dans leur dossier (solos.py) -------------------------
    def _solo_places(self):
        """(racine principale, racines joignables), ou None si la principale
        ne repond pas : c'est elle qui recoit."""
        from . import roots, solos
        top = self.top_root()
        listed = roots.roots() or ([top] if top is not None and not roots.is_union(top)
                                   else [])
        reach = self._reachable_roots
        live = [r for r in listed if reach is None or str(r) in reach]
        main = solos.main_root(listed)
        if main is None or not any(os.path.normcase(str(main)) == os.path.normcase(str(r))
                                   for r in live):
            return None
        return main, live

    def _propose_solos(self) -> None:
        """Apres une analyse : les dossiers a une seule video, et les « 1 » des
        autres racines, proposes au regroupement. Une fois par seance pour les
        memes ; rien n'est fait sans accord."""
        if (self._closing or getattr(self, "_solo_busy", False)
                or getattr(self, "_solo_dialog", None) is not None
                or not self._plain_whole):
            return
        from . import solos
        from .tunnel import Chore
        places = self._solo_places()
        if places is None:
            return
        main, live = places
        home, pics = main / solos.HOME_NAME, main / solos.PICS_NAME
        snoozed = self.__dict__.setdefault("_solo_snoozed", set())
        found = solos.candidates(self._plain_items, home, pics, live,
                                 list(self.cfg["solo_ignored"] or []) + list(snoozed))
        merging = "merge" not in snoozed

        def work():
            # Sur le disque, pas d'apres l'index : il peut avoir du retard.
            plans = [plan for plan in (solos.inspect(f) for f in found) if plan]
            merges = []
            if merging:
                for source in solos.other_homes(live, home):
                    try:
                        merges.append((source, sum(1 for _e in os.scandir(source))))
                    except OSError:
                        continue
            return plans, merges

        def then(result) -> None:
            if not result or self._closing or not shiboken6.isValid(self):
                return
            plans, merges = result
            if plans or merges:
                self._show_solos(plans, merges, home, pics)

        Chore(work, self, fallback=None, then=then).start()

    def _show_solos(self, plans: list, merges: list, home: Path, pics: Path) -> None:
        from .solos_dialog import SolosDialog
        if getattr(self, "_solo_dialog", None) is not None:
            return
        dialog = SolosDialog(self, plans, merges, home)
        self._solo_dialog = dialog
        snoozed = self._solo_snoozed

        def closed(_code) -> None:
            self._solo_dialog = None
            snoozed.update(str(plan["folder"]) for plan in plans)
            if merges:
                snoozed.add("merge")
            dialog.deleteLater()

        dialog.finished.connect(closed)
        dialog.chosen.connect(
            lambda folders, merge, left: self._gather_solos(folders, merge, left, home, pics))
        # Sans bloquer la fenetre : on peut finir ce qu'on regardait.
        dialog.setModal(False)
        dialog.show()
        dialog.raise_()

    def _gather_solos(self, folders: list, merge: list, left: list,
                      home: Path, pics: Path) -> None:
        """Fait ce qui a ete coche, hors du fil de l'interface."""
        from . import solos
        from .tunnel import Chore
        if left:
            self.cfg["solo_ignored"] = sorted(set(self.cfg["solo_ignored"] or []) | set(left))
            self.cfg.save_soon()
        if not folders and not merge:
            return
        for plan in folders:
            self._release_media(plan["video"])
        for source in merge:
            self._release_media(source)
        self._solo_busy = True
        total = len(folders) + len(merge)
        state = {"done": 0, "videos": 0, "merged": 0, "name": "", "total": total}
        self._solo_state = state

        def work():
            entries, failures = [], []
            for plan in folders:
                state["name"] = plan["folder"].name
                try:
                    solos.gather(plan, home, pics, entries)
                    state["videos"] += 1
                except Exception as exc:                    # noqa: BLE001
                    failures.append(f"{plan['folder'].name} : {exc}")
                state["done"] += 1
            for source in merge:
                before = len(entries)

                def seen(name: str) -> None:
                    state["name"] = name
                try:
                    solos.merge_home(source, home, entries, seen)
                except Exception as exc:                    # noqa: BLE001
                    failures.append(f"{source} : {exc}")
                state["merged"] += len(entries) - before
                state["done"] += 1
            return entries, failures

        ticker = QTimer(self)
        ticker.setInterval(1500)
        ticker.timeout.connect(lambda: self.show_banner(
            f"Regroupement dans « {solos.HOME_NAME} »… {state['done']}/{total} — "
            f"{state['name']}", "quiet", seconds=3))
        ticker.start()

        def then(result) -> None:
            ticker.stop()
            ticker.deleteLater()
            self._solo_busy = False
            entries, failures = result or ([], ["interrompu"])
            self.history.extend(entries)
            del self.history[:-self.HISTORY_MAX]
            parts = []
            if state["videos"]:
                parts.append(f"{state['videos']} vidéo(s) regroupée(s) dans « {home} »")
            if state["merged"]:
                parts.append(f"{state['merged']} élément(s) fusionné(s) depuis les autres « 1 »")
            text = ("✓ " + " ; ".join(parts) + ". Ctrl+Z annule.") if parts else ""
            if failures:
                text = (text + " " if text else "") + (
                    f"{len(failures)} échec(s) : " + " ; ".join(failures[:3]))
            tone = "error" if failures else "done"
            if entries and not self.scanning:
                # Le bilan attend la fin de la relecture : « Analyse terminée »
                # l'effacait aussitot.
                self._solo_summary = (text, tone, bool(failures)) if text else None
                self._launch_scan()
            elif text:
                self.show_banner(text, tone, seconds=10, keep=bool(failures))

        Chore(work, self, fallback=None, then=then).start()
