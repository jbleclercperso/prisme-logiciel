"""Les racines : plusieurs collections, leur menu, leur guet, les nouveaux essais."""
from __future__ import annotations

from .winkit import PAGE_WELCOME
import os
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFileDialog
from .perf import mark
from .scan import MODE_FOLDERS, canon_root


# Les delais des nouveaux essais quand la racine ne repond pas (NAS endormi,
# coupure) : vite d'abord, puis une fois par minute tant qu'il se tait.
RETRY_DELAYS_S = (5, 15, 60)


class RootsMixin:
    """Une part de la fenetre principale : MainWindow en herite."""

    # ------------------------------------------------------------------
    # Racine et analyse
    # ------------------------------------------------------------------
    # -- plusieurs racines -------------------------------------------------------
    def _init_roots(self) -> None:
        """Les racines de la configuration ; la premiere fois, celle qu'on
        avait deja. Puis on les sonde regulierement : un disque qu'on branche
        ou debranche se voit dans les vingt secondes."""
        from . import roots
        chosen = [str(p) for p in (self.cfg["roots"] or []) if p]
        current = self.cfg["root"]
        if not chosen and current and not roots.is_union(current):
            chosen = [str(canon_root(current))]
            self.cfg["roots"] = chosen
        roots.set_roots(chosen)
        roots.set_off(self.cfg["roots_off"] or [])
        self._reachable_roots = None
        self._roots_timer = QTimer(self)
        self._roots_timer.setInterval(20000)
        self._roots_timer.timeout.connect(self._watch_roots)
        self._roots_timer.start()
        QTimer.singleShot(1500, self._watch_roots)

    def _watch_roots(self) -> None:
        """Sonde les racines hors du fil de l'interface."""
        if getattr(self, "_roots_probing", False) or self._closing:
            return
        from . import roots
        from .tunnel import Chore
        self._roots_probing = True
        Chore(lambda: frozenset(roots.check_all()), self, fallback=None,
              then=self._roots_seen).start()

    def _roots_seen(self, found) -> None:
        from . import roots
        self._roots_probing = False
        if found is None:
            return
        before, self._reachable_roots = self._reachable_roots, found
        self._show_root_button()
        if before is None or before == found:
            return
        gone = [roots.label(p) for p in before - found]
        back = [roots.label(p) for p in found - before]
        if gone:
            self.show_banner(f"{', '.join(gone)} : injoignable — ses vidéos sont masquées "
                             "jusqu'à son retour.", "info")
        if back:
            self.show_banner(f"{', '.join(back)} : de retour — ses vidéos réapparaissent.",
                             "done")
        if roots.is_union(self.root) and not self.scanning:
            # La reunion se recompose : les dossiers de la racine partie
            # quittent l'ecran, ceux de la racine revenue y reviennent.
            self._launch_scan()

    def _show_root_button(self) -> None:
        """« ⌂ 2/2 » : combien de racines on regarde, sur combien. Le nom de
        la racine est deja dans le fil d'Ariane, juste a cote : le bouton le
        repetait."""
        from . import roots
        top = self.top_root()
        total = len(roots.roots())
        if top is None or total < 2:
            self.root_button.setText("⌂  Racines")
            self.root_button.setToolTip("Les racines : en ajouter, choisir celles qu'on regarde")
            return
        shown = len(roots.members(top)) if roots.is_union(top) else 1
        self.root_button.setText(f"⌂  {shown}/{total}")
        self.root_button.setToolTip(
            f"{shown} racine{'s' if shown > 1 else ''} affichée{'s' if shown > 1 else ''} "
            f"sur {total} : cliquer pour cocher celles qu'on regarde")

    def _root_ticks(self) -> set:
        """Les racines cochees : celles qu'on regarde en ce moment."""
        from . import roots
        top = self.top_root()
        if top is None:
            return set()
        if roots.is_union(top):
            return {os.path.normcase(str(p)) for p in roots.chosen_on()}
        return {os.path.normcase(str(p)) for p in roots.roots()
                if os.path.normcase(str(p)) == os.path.normcase(str(top))}

    def _fill_root_menu(self) -> None:
        """Une case par racine : cochee, on la regarde. Le menu reste ouvert
        le temps de cocher ; la vue suit a sa fermeture."""
        from PySide6.QtWidgets import QCheckBox, QWidgetAction
        from . import roots
        menu = self.root_menu
        menu.clear()
        reach = self._reachable_roots
        chosen = roots.roots()
        ticks = self._root_ticks()
        self._root_boxes = []
        if chosen:
            head = menu.addAction("Racines affichées")
            head.setEnabled(False)
        for root in chosen:
            ok = reach is None or str(root) in reach
            box = QCheckBox(roots.label(root) + ("" if ok else "  — injoignable"), menu)
            box.setObjectName("rootTick")
            box.setChecked(os.path.normcase(str(root)) in ticks)
            box.setEnabled(ok)
            box.setToolTip(str(root) if ok else
                           f"{root}\nDébranchée ou éteinte : elle revient d'elle-même.")
            box.toggled.connect(lambda _on, b=box: self._keep_one_tick(b))
            holder = QWidgetAction(menu)
            holder.setDefaultWidget(box)
            menu.addAction(holder)
            self._root_boxes.append((root, box))
        if chosen:
            menu.addSeparator()
        menu.addAction("Ajouter des racines…", self.add_root).setToolTip(
            "Un ou plusieurs dossiers d'un coup (Ctrl ou Maj pour en choisir plusieurs)")
        if chosen:
            drop = menu.addMenu("Retirer une racine")
            for root in chosen:
                drop.addAction(roots.label(root),
                               lambda r=root: self.remove_root(r)).setToolTip(str(root))

    def _root_menu_closed(self) -> None:
        """Le menu se ferme : on regarde les racines cochees."""
        from . import roots
        pairs = getattr(self, "_root_boxes", [])
        if not pairs:
            return
        wanted = [root for root, box in pairs if box.isChecked()]
        self._root_boxes = []
        if not wanted:
            return
        if {os.path.normcase(str(p)) for p in wanted} == self._root_ticks():
            return
        off = [str(root) for root, _box in pairs if root not in wanted]
        self.cfg["roots_off"] = off
        self.cfg.save_soon()
        roots.set_off(off)
        # Une seule : on l'ouvre elle-meme ; plusieurs : leur reunion.
        self.open_root_async(wanted[0] if len(wanted) == 1 else Path(roots.UNION),
                             MODE_FOLDERS)

    def add_root(self) -> None:
        """Une racine de plus : un disque, un dossier du PC, un partage."""
        from . import roots
        from .widgets import pick_folders
        start = str(self.top_root()) if self.top_root() is not None and not roots.is_union(
            self.top_root()) else ""
        chosen = pick_folders(self, "Ajouter une ou plusieurs racines", start)
        if not chosen:
            return
        have = {os.path.normcase(str(p)) for p in roots.roots()}
        fresh = [str(canon_root(p)) for p in chosen
                 if os.path.normcase(str(canon_root(p))) not in have]
        if not fresh:
            return self.show_banner("Ces racines y étaient déjà.", "quiet")
        self.cfg["roots"] = [str(p) for p in roots.roots()] + fresh
        self.cfg.save_soon()
        roots.set_roots(self.cfg["roots"])
        self.show_banner(f"{len(fresh)} racine(s) ajoutée(s) : le bouton ⌂ coche celles "
                         "qu'on regarde.", "done")
        self._reachable_roots = None
        self._watch_roots()
        if len(roots.roots()) > 1 and not roots.is_union(self.top_root()):
            self.open_root_async(Path(roots.UNION), MODE_FOLDERS)
        elif roots.is_union(self.root) and not self.scanning:
            self._launch_scan()

    def remove_root(self, root) -> None:
        """Retire une racine de la liste ; rien ne bouge sur le disque."""
        from . import roots
        keep = [str(p) for p in roots.roots()
                if os.path.normcase(str(p)) != os.path.normcase(str(root))]
        self.cfg["roots"] = keep
        self.cfg["roots_off"] = [p for p in (self.cfg["roots_off"] or [])
                                 if os.path.normcase(str(p)) != os.path.normcase(str(root))]
        self.cfg.save_soon()
        roots.set_roots(keep)
        roots.set_off(self.cfg["roots_off"])
        self.show_banner(f"« {roots.label(root)} » retirée de la liste des racines (rien "
                         "n'a bougé sur le disque).", "quiet")
        if roots.is_union(self.root) and not self.scanning:
            self._launch_scan()
        self._show_root_button()

    def choose_root(self) -> None:
        start = self.cfg["root"] or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Choisir le dossier racine", start)
        if chosen:
            # « Réseau › AS1104T › partage » ou « X: » : la meme racine, donc
            # les memes cles d'index, de favoris et de vignettes.
            self.open_root_async(canon_root(chosen))

    def on_root_unreachable(self, why: str) -> None:
        """La racine n'a pas repondu : la liste reste celle du dernier passage.

        Rien n'a ete efface. On le dit, puis on reessaie tout seul — 5 s, 15 s,
        puis chaque minute — tant que le NAS se tait.
        """
        if self.sender() is not None and self.sender() is not self.scan_thread:
            return
        delay = RETRY_DELAYS_S[min(self._retry_step, len(RETRY_DELAYS_S) - 1)]
        self._retry_step += 1
        self.show_banner(
            f"NAS injoignable — liste du dernier passage. Nouvel essai dans "
            f"{delay} s.\n{why}", "error")
        self.retry_timer.start(delay * 1000)

    def _retry_root(self) -> None:
        """Nouvel essai de lecture, sur la racine qu'on regarde encore."""
        if self.root is None or self.scanning:
            return
        if self._page() == PAGE_WELCOME:
            return
        mark(f"nouvel essai {self.root}")
        self._launch_scan()

    def on_tree_root_changed(self, path: str) -> None:
        # La meme ecriture partout (lettre plutot que \\serveur\partage).
        self.cfg["tree_root"] = str(canon_root(path)) if path else ""
        self.cfg.save_soon()
