"""Le tri ultra rapide et les videos similaires."""
from __future__ import annotations

import os
import time
from pathlib import Path
from PySide6.QtWidgets import QMessageBox
from .header import TAB_SPLIT
from .perf import mark
from .scan import under_veiled, MODE_FILES, MODE_FLAT, MODE_FOLDERS, Item


class UltraMixin:
    """Une part de la fenetre principale : MainWindow en herite."""

    # -- « Similaires » : que des videos qui ressemblent a celle-ci ----------------
    @staticmethod
    def _tick_similar(bar, on: bool) -> None:
        box = getattr(bar, "similar", None)
        if box is not None and box.isChecked() != on:
            box.blockSignals(True)
            box.setChecked(on)
            box.blockSignals(False)

    def _similar_scope(self, seed: str = ""):
        """Ou chercher : toutes les racines qui repondent, pas le seul dossier.

        Celle de la video regardee en est toujours : un sondage du NAS qui
        avait tarde (trois secondes) le disait injoignable, et toutes ses
        voisines etaient ecartees -- « aucune vidéo ressemblante »."""
        from . import roots
        top = self.top_root()
        listed = roots.roots() or ([top] if top is not None and not roots.is_union(top)
                                   else [])
        reach = self._reachable_roots
        home = roots.owner(seed) if seed else None
        if home is None and top is not None and not roots.is_union(top):
            home = top
        keep = [r for r in listed if reach is None or str(r) in reach]
        if home is not None and not any(os.path.normcase(str(r)) == os.path.normcase(str(home))
                                        for r in keep):
            keep.append(home)
        return [os.path.normcase(str(r)).rstrip("\\/") for r in keep]

    def _find_similar(self, seed: str, then) -> None:
        """Les voisines de `seed`, hors du fil de l'interface : `then(liste)`,
        ou `then(None)` avec la raison deja dite."""
        from . import similar
        from .tunnel import Chore
        scope = self._similar_scope(seed)
        name = Path(seed).name
        self.show_banner(f"✦ Recherche des vidéos qui ressemblent à « {name} »…",
                         "quiet", seconds=20)

        def keep(path: str) -> bool:
            low = os.path.normcase(path)
            if scope and not any(low == top or low.startswith(top + os.sep)
                                 for top in scope):
                return False
            return not under_veiled(path)

        def work():
            space = similar.space()
            if space is None:
                return "index"
            if not space.knows(seed):
                return "inconnue"
            raw = space.nearest(seed)
            kept = [path for path, _score in raw if keep(path)]
            found, missing = [], 0
            for path in kept:
                # L'index peut garder une video deplacee entre-temps.
                if os.path.exists(path):
                    found.append(path)
                    if len(found) >= self.SIMILAR_COUNT:
                        break
                else:
                    missing += 1
                    if missing >= 400 and not found:
                        break
            if not found:
                # Rien ne reste : on dit a quelle etape, plutot qu'un « aucune ».
                return ("vide", len(raw), len(kept), missing)
            return found

        def done(result) -> None:
            if result == "index" or result is None:
                self.show_banner("Les ressemblances viennent de l'index du Labo IA, encore "
                                 "vide : lancez l'indexation (bouton ✦ en haut à droite).",
                                 "error", seconds=8)
                return then(None)
            if result == "inconnue":
                self.show_banner(f"« {name} » n'est pas encore dans l'index du Labo IA : "
                                 "indexez-la (Labo IA) pour chercher ses voisines.",
                                 "error", seconds=8)
                return then(None)
            if isinstance(result, tuple):
                _tag, raw, kept, missing = result
                if not raw:
                    why = "l'index du Labo ne lui connaît aucune voisine"
                elif not kept:
                    why = (f"ses {raw} voisines sont hors des racines joignables "
                           f"ou dans des dossiers masqués")
                else:
                    why = (f"ses voisines ne sont plus à leur place sur le disque "
                           f"({missing} introuvables) : relancez l'indexation du Labo")
                self.show_banner(f"Aucune vidéo ressemblante trouvée : {why}.", "error",
                                 seconds=10)
                return then(None)
            if not result:
                self.show_banner("Aucune vidéo ressemblante trouvée.", "quiet")
                return then(None)
            then(result)

        Chore(work, self, fallback=None, then=done).start()

    def set_similar(self, on: bool) -> None:
        """L'interrupteur ✦ de la fiche : la liste devient les voisines de la
        video regardee, de la plus ressemblante a la moins ; decoche, on
        retrouve la liste d'avant."""
        if not on:
            return self._stop_similar()
        item = self.current
        if (item is None or self.browsing or item.kind == MODE_FOLDERS
                or item.is_tag or self._transient == "dupes"):
            self._tick_similar(self.single_bar, False)
            self.show_banner("✦ Similaires : ouvrez d'abord la fiche d'une vidéo.", "quiet")
            return
        seed = str(item.path)

        def arrived(found) -> None:
            current = self.current
            box = getattr(self.single_bar, "similar", None)
            if (found is None or box is None or not box.isChecked()
                    or current is None or str(current.path) != seed):
                self._tick_similar(self.single_bar, False)
                return
            self._enter_similar(seed, found)

        self._find_similar(seed, arrived)

    def _enter_similar(self, seed: str, videos: list) -> None:
        back = {"root": self.root, "mode": self.mode, "levels": list(self.levels),
                "all_items": self.all_items, "items": self.items, "index": self.index,
                "list_leaf": self._list_leaf, "origin": self.origin}
        position = max(0, self.single.player.position())
        made = [Item(path=Path(path), kind=MODE_FILES, videos=[Path(path)],
                     video_count=1, file_count=1) for path in [seed] + videos]
        self._similar = {"seed": seed, "back": back}
        self._transient = "similar"
        self.all_items = made
        self.items = list(made)
        self.mode = MODE_FLAT
        self.index = 0
        self.plans = {}
        self.pages = {}
        name = Path(seed).name
        self._list_leaf = f"✦ Comme « {Path(seed).stem} »"
        self.crumbs.set_path(self.top_root(), self.root)
        self.crumbs.append_leaf(self._list_leaf)
        self._pending_start = ((os.path.normcase(seed), position, time.monotonic())
                               if position > 0 else None)
        self.show_item(0)
        self._apply_selectors()
        self.show_banner(f"✦ {len(videos)} vidéos qui ressemblent à « {name} », de la plus "
                         "proche à la moins : ▸ ou Espace pour la suivante. Décochez ✦ "
                         "pour revenir.", "info", seconds=8)

    def _stop_similar(self) -> None:
        state, self._similar = self._similar, None
        if state is None or self._transient != "similar":
            return
        back = state["back"]
        item = self.current
        path = str(item.path) if item is not None else ""
        position = max(0, self.single.player.position())
        self._transient = ""
        self.root, self.mode, self.origin = back["root"], back["mode"], back["origin"]
        self.levels = back["levels"]
        self.all_items, self.items = back["all_items"], back["items"]
        self._list_leaf = back["list_leaf"]
        self.plans = {}
        self.pages = {}
        self.crumbs.set_path(self._origin_for(self.root), self.root)
        if self._list_leaf:
            self.crumbs.append_leaf(self._list_leaf)
        self._apply_selectors()
        # La video regardee continue ; ▸ reprend la suite habituelle.
        wanted = os.path.normcase(path)
        for at, other in enumerate(self.items):
            if os.path.normcase(str(other.path)) == wanted:
                self._pending_start = ((wanted, position, time.monotonic())
                                       if position > 0 else None)
                return self.show_item(at)
        if path:
            # Une voisine venue d'ailleurs : la suite, c'est son dossier.
            return self.play_in_app(path, position / 1000.0)
        self.show_item(min(back["index"], len(self.items) - 1))

    def set_aside_similar(self, on: bool) -> None:
        """Le meme interrupteur, pour le lecteur de cote : ◂ ▸ y parcourent
        les voisines de la video qu'il lit."""
        if not on:
            state, self._aside_similar = self._aside_similar, None
            if state is not None:
                self.aside_playlist = state["playlist"]
                self.aside_playlist_at = state["at"]
            return
        seed = self.aside_current
        if not seed:
            self._tick_similar(self.aside_bar, False)
            return

        def arrived(found) -> None:
            box = getattr(self.aside_bar, "similar", None)
            if (found is None or box is None or not box.isChecked()
                    or self.aside_current != seed):
                self._tick_similar(self.aside_bar, False)
                return
            self._aside_similar = {"playlist": list(self.aside_playlist),
                                   "at": self.aside_playlist_at}
            self.aside_playlist = [seed] + found
            self.aside_playlist_at = 0
            self.show_banner(f"✦ {len(found)} vidéos qui ressemblent à « {Path(seed).name} » "
                             "dans le lecteur de côté : ▸ pour la suivante.", "info")

        self._find_similar(seed, arrived)

    # -- l'ultra tri : tout a la souris ------------------------------------------
    def toggle_ultra(self, on=None) -> None:
        """Ctrl+U, le ⚡ du bandeau : clic gauche garde, clic droit supprime."""
        on = (not self.ultra) if on is None else bool(on)
        if on and not self._ultra_warned:
            # Depuis le lecteur flottant, la question vient sur lui : Prisme
            # est peut-etre reduit, et elle attendait derriere.
            asker = self.floating if self.floating.active else self
            answer = QMessageBox.question(
                asker, "Ultra tri",
                "Sur la fiche d'une vidéo, tout se fait à la souris :\n\n"
                "   • clic gauche sur l'image : on la garde, et c'est la suivante ;\n"
                "   • clic droit sur l'image : on la supprime.\n\n"
                "Une vidéo triée ne revient qu'au tour suivant ; passée sans "
                "choix (Espace, →), elle reste à trier et reviendra. La "
                "pastille ⚡ en haut à gauche de l'image dit combien sont "
                "déjà triées, sur combien.\n\n"
                "Ce qui est supprimé passe par la corbeille de séance : Ctrl+Z le "
                "reprend. Sur le NAS, c'est détruit pour de bon à la fermeture de "
                "Prisme.\n\nActiver l'ultra tri ?")
            if answer != QMessageBox.Yes:
                on = False
            else:
                self._ultra_warned = True
        self.ultra = on
        hook = self._ultra_click if on else None
        self.single.ultra = hook
        self.floating.player.ultra = hook
        box = getattr(self.single_bar, "ultra", None)
        if box is not None and box.isChecked() != on:
            box.blockSignals(True)
            box.setChecked(on)
            box.blockSignals(False)
        self._show_task_pills()
        self._show_ultra_tally()
        self.show_banner("⚡ Ultra tri : clic gauche, on garde ; clic droit, on supprime. "
                         "Ctrl+U pour arrêter." if on else "Ultra tri arrêté.",
                         "info" if on else "quiet")

    def _ultra_click(self, button: str) -> None:
        item = self.current
        if (not self.ultra or self.browsing or item is None or self.tab == TAB_SPLIT
                or self._quiet):
            return
        # Deux clics trop rapprochés ne valent qu'un : le second tomberait sur
        # la video suivante avant qu'on l'ait vue.
        now = time.monotonic()
        if now - self._ultra_at < 0.4:
            return
        self._ultra_at = now
        if button == "left":
            # Garder est un choix : la video sort du tour (Espace, lui, passe
            # sans choisir).
            self._ultra_chose(item)
            return self.act_skip()
        if item.kind == MODE_FOLDERS or item.is_tag:
            return self.show_banner("Ultra tri : le clic droit ne supprime que des vidéos, "
                                    "jamais un dossier entier.", "quiet")
        self.act_delete()

    # Ce que l'ultra tri parcourt n'a pas toujours de fin : le hasard tire sans
    # cesse, et la voisine dans le dossier (« rester dans ce dossier ») fait le
    # tour. Un petit dossier ramenait donc tres vite les memes videos -- et
    # celles qu'on venait de supprimer, sur une fiche noire, « Déjà traité ».
    # Chaque tour retient ce qui est passe (garde, supprime, range) : rien n'y
    # revient avant que tout le reste soit passe.
    def _ultra_video(self):
        """La video de la fiche, si l'ultra tri vaut pour elle ; None sinon."""
        item = self.current
        if (not self.ultra or self.browsing or item is None or self.tab == TAB_SPLIT
                or item.kind == MODE_FOLDERS or item.is_tag):
            return None
        return item

    def _ultra_tour(self, item) -> dict:
        """Le tour en cours pour ce que l'on parcourt depuis cette video."""
        if self._in_random_run():
            key, kind = ("hasard", str(self.top_root())), "hasard"
        elif self._stays_in_folder():
            key = ("dossier", os.path.normcase(str(Path(item.path).parent)))
            kind = "dossier"
        else:
            key, kind = ("liste", str(self.root), self.mode, self.tab), "liste"
        tour = self._ultra_tours.get(key)
        if tour is None:
            tour = self._ultra_tours[key] = {"kind": kind, "passed": set(),
                                             "total": 0, "mark": None}
        return tour

    def _ultra_count(self, item) -> tuple:
        """(passees, total) dans le tour en cours."""
        tour = self._ultra_tour(item)
        passed = tour["passed"]
        if tour["kind"] == "hasard":
            # Toute la collection, qu'on ne recompte que si elle change.
            folders, weights = self._random_pool()
            mark = ("hasard", weights[-1] if weights else 0)
            if tour["mark"] == mark:
                return len(passed), tour["total"]
            pool = (str(video) for folder in folders for video in folder.videos)
        elif tour["kind"] == "liste":
            # La liste peut compter toute la collection : on ne la recompte
            # que si elle change. Passer une video la retire du reste et
            # l'ajoute aux passees -- le total ne bouge pas.
            mark = (id(self.items), len(self.items))
            if tour["mark"] == mark:
                return len(passed), tour["total"]
            pool = (str(other.path) for other in self.items
                    if other.kind != MODE_FOLDERS and not other.is_tag
                    and not other.locked)
        else:
            mark = None
            pool = (str(video) for video in self._folder_videos(str(item.path)))
        gone = self._departed()
        rest = 0
        for path in pool:
            key = os.path.normcase(path)
            if key not in passed and key not in gone and not self._in_trash(key):
                rest += 1
        tour["mark"] = mark
        tour["total"] = len(passed) + rest
        return len(passed), tour["total"]

    def _show_ultra_tally(self) -> None:
        """« ⚡ 12 / 40 triées », en haut a gauche de l'image."""
        single = getattr(self, "single", None)
        if single is None:
            return
        text = ""
        item = self._ultra_video() if getattr(self, "ultra", False) else None
        if item is not None:
            done, total = self._ultra_count(item)
            if total:
                text = f"⚡ {done} / {total} triées"
        # La ou la video joue : la fiche, ou le lecteur flottant. Le guet des
        # bandeaux la repose ensuite a chaque battement.
        floating = getattr(self, "floating", None)
        afloat = floating is not None and floating.active
        if afloat:
            single.tally_text = text
            single.hide_tally()
            floating.player.set_tally(text)
        else:
            single.set_tally(text)
            if floating is not None:
                floating.player.tally_text = text

    def _ultra_next(self) -> bool:
        """La suivante de l'ultra tri, jamais deja passee dans ce tour.

        False quand ce n'est pas a lui de choisir (un dossier, un mot-cle, une
        analyse qui apporte encore des videos) : `advance` fait comme avant.
        """
        item = self._ultra_video()
        if item is None:
            return False
        tour = self._ultra_tour(item)
        passed = tour["passed"]
        here = os.path.normcase(str(item.path))
        # Seul un choix la sort du tour. Passee sans choisir (Espace, →, la
        # fin de la video), elle n'est pas triee : elle reviendra.
        chosen, self._ultra_choice = self._ultra_choice == here, ""
        if chosen:
            passed.add(here)
        if tour["kind"] == "hasard":
            moved = self.pick_random(skip=passed)
        elif tour["kind"] == "dossier":
            moved = self._ultra_folder_step(item, passed)
        else:
            moved = self._ultra_list_step(passed)
            if not moved and self.scanning:
                return False
        if moved:
            return True
        if here not in passed and here not in self._departed():
            # Plus qu'elle a trier : on ne boucle pas sur elle, on le dit.
            self.show_banner("⚡ C'est la dernière vidéo pas encore triée de ce tour : "
                             "clic gauche pour la garder, clic droit pour la supprimer.",
                             "info")
            return True
        return self._ultra_round_done(tour, item)

    def _ultra_chose(self, item) -> None:
        """Une decision sur cette video : elle compte comme triee."""
        if self.ultra and item is not None:
            self._ultra_choice = os.path.normcase(str(item.path))

    def _ultra_list_step(self, passed) -> bool:
        """La prochaine de la liste, hors ce tour et ce qui est parti."""
        gone = self._departed()
        count = len(self.items)
        for offset in range(1, count):
            # Jusqu'au bout de la liste, puis depuis le debut : un tour
            # commence la ou l'on a ouvert la fiche, pas forcement en tete.
            position = (self.index + offset) % count
            other = self.items[position]
            if other.kind == MODE_FOLDERS or other.is_tag or other.locked:
                continue
            key = os.path.normcase(str(other.path))
            if key in passed or key in gone:
                continue
            self.show_item(position)
            return True
        return False

    def _ultra_folder_step(self, item, passed) -> bool:
        """La prochaine du dossier, hors ce tour et ce qui est parti."""
        listing = [str(video) for video in self._folder_videos(str(item.path))]
        keys = [os.path.normcase(path) for path in listing]
        here = os.path.normcase(str(item.path))
        at = keys.index(here) if here in keys else -1
        gone = self._departed()
        locked = {os.path.normcase(str(other.path)) for other in self.items
                  if other.locked}
        for offset in range(1, len(listing) + 1):
            position = (at + offset) % len(listing)
            key = keys[position]
            if key == here or key in passed or key in gone or key in locked:
                continue
            target = listing[position]
            for index, other in enumerate(self.items):
                if str(other.path) == target:
                    self.show_item(index)
                    return True
            self.play_in_app(target)
            return True
        return False

    def _ultra_round_done(self, tour, item) -> bool:
        """Tout est passe : on le dit, et un nouveau tour commence."""
        count = len(tour["passed"])
        tour["passed"] = set()
        tour["total"], tour["mark"] = 0, None
        # Pas de page « Tri terminé » : elle coupait la video, jusque dans le
        # lecteur flottant, noir. On repart, et le bandeau le dit.
        # Le nouveau tour part de zero : rien n'y est trie sans un choix.
        if tour["kind"] == "hasard":
            moved = self.pick_random()
        elif tour["kind"] == "liste":
            moved = self._ultra_list_step(tour["passed"])
        else:
            moved = self._ultra_folder_step(item, tour["passed"])
        if not moved:
            self.show_banner("C'est la seule vidéo qui reste ici.", "quiet")
            self._show_ultra_tally()
            return True
        # Apres le tirage, dont le bandeau aurait recouvert celui-ci.
        self.show_banner(f"⚡ Tour complet : les {count} vidéos sont passées — "
                         "un nouveau tour commence.", "done")
        return True
