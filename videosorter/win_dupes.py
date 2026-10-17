"""Les doublons : recherche, signatures, resultats gardes, guet des nouvelles videos."""
from __future__ import annotations

import os
import time
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox
from .config import photo_mode
from .dupes import PhotoDuplicateScan, SignatureGroupScan, SignatureScan
from .header import TAB_FOLDERS, TAB_SPLIT
from .scan import MODE_FILES, MODE_FLAT, Item, as_path as _as_path, human_size
from .index import INDEX


class DupesMixin:
    """Une part de la fenetre principale : MainWindow en herite."""

    def _start_dupes(self, banner: str) -> None:
        """Lance la recherche preparee dans `self.dupes`, et le dit."""
        self._dupes_stopped = False
        self._own_thread(self.dupes, "dupes")
        self.dupes.progress.connect(self.on_dupes_progress)
        self.dupes.found.connect(self.on_dupes_found)
        self.dupes.start()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self._progress_text("recherche de doublons…")
        self.progress.show()
        self.show_banner(banner, "info")

    def _dupes_kind(self) -> str:
        """La recherche en cours : « size », « image », « sigs » ou « photos »."""
        if getattr(self, "_dupes_photos", False):
            return "photos"
        if getattr(self, "_dupes_from_sigs", False):
            return "sigs"
        return "image" if getattr(self, "_dupes_by_image", False) else "size"

    def _stop_dupes(self, wanted: str = "") -> None:
        """Recliquer arrete : la recherche rendra une liste vide, qu'il ne
        faut pas annoncer comme « aucun doublon ».

        Une autre recherche demandee pendant ce temps arrete celle qui tourne,
        puis part d'elle-meme : l'autre entree du menu arretait la recherche
        en cours sans rien lancer, et sans rien dire.
        """
        self._dupes_stopped = True
        self.dupes.stop()
        running = self._dupes_kind()
        if wanted and wanted != running:
            self._dupes_next = wanted
            self.show_banner(
                f"La recherche « {self.DUPES_KINDS[running]} » s'arrête ; "
                f"celle « {self.DUPES_KINDS[wanted]} » part dès qu'elle a "
                "rendu la main.", "quiet")
            return
        self._dupes_next = None
        self.show_banner("Recherche de doublons : arrêt demandé…", "quiet")

    def _launch_dupes(self, kind: str) -> None:
        if kind == "sigs":
            self.duplicates_from_sigs()
        elif kind == "photos":
            self.find_photo_dupes()
        else:
            self.find_duplicates(by_image=kind == "image")

    def on_dupes_progress(self, seen: int) -> None:
        if self.scanning:
            return
        noun = "photo(s) recensée(s)" if photo_mode() else "vidéo(s) examinée(s)"
        self._progress_text(f"doublons : {self._thousands(seen)}",
                            f"Recherche de doublons : {self._thousands(seen)} "
                            f"{noun}…")
        self.progress.show()

    def _photo_dupes_reading(self, done: int, total: int) -> None:
        """Les empreintes des photos nouvelles : « doublons : photos 120 / 3 400 »."""
        if self.scanning:
            return
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)
        self._progress_text(
            f"doublons : photos {self._thousands(done)} / {self._thousands(total)}",
            f"Recherche de doublons : empreinte de {self._thousands(done)} photo(s) "
            f"nouvelle(s) ou modifiée(s) sur {self._thousands(total)}. Les autres ont "
            "déjà la leur.")
        self.progress.show()

    def on_dupes_found(self, groups: list) -> None:
        """Le resultat d'une recherche : propose, jamais impose.

        Il arrive des minutes apres le lancement, sur tout le NAS. Il
        remplacait d'office ce qu'on regardait -- une video, le mur, une
        autre page -- et coupait au passage la relecture de la collection.
        Il attend desormais qu'on le demande : un bandeau qu'on clique, ou
        « ⋯ › Doublons ».

        Une comparaison d'etape (`interim`, pendant les empreintes) ne dit
        rien quand elle ne trouve rien, et ne parle que si elle trouve plus
        qu'avant : ce sont des nouvelles en cours de route, pas un verdict.
        """
        scan = self.dupes
        self.dupes = None
        interim = getattr(scan, "interim", False)
        if not interim:
            if not self.scanning:
                self.progress.hide()
            self._progress_text("%v / %m analysés")
        if getattr(self, "_dupes_stopped", False):
            self._dupes_stopped = False
            following, self._dupes_next = self._dupes_next, None
            if following:
                return self._launch_dupes(following)
            if interim:
                return
            kept = self._pending_dupes
            self.show_banner(
                "Recherche de doublons arrêtée." + (
                    f" Les {len(kept[0])} groupe(s) trouvé(s) jusque-là restent "
                    "dans « ⋯ › Doublons »." if kept else ""), "quiet")
            return
        if interim:
            return self._interim_dupes_found(scan, groups)
        from_sigs = getattr(self, "_dupes_from_sigs", False)
        if from_sigs and scan is not None and not getattr(scan, "examined", 1):
            top = self.top_root()
            self.show_banner(
                f"Aucune empreinte sous {top} : lancez d'abord « Empreintes : "
                "sonder ce qui manque ».", "quiet")
            return
        if from_sigs:
            groups = self._only_in_collection(groups)
        groups = self._one_spelling(groups)
        by_image = getattr(self, "_dupes_by_image", False)
        unreadable = int(getattr(scan, "unreadable", 0) or 0)
        missed = (f" {unreadable} photo(s) illisible(s), retentée(s) à la prochaine "
                  "recherche." if unreadable and isinstance(scan, PhotoDuplicateScan)
                  else "")
        if not groups:
            # Le verdict complet remplace ce qu'une etape avait cru voir.
            self._keep_found_dupes(None, by_image)
            self.show_banner(
                "Aucun doublon trouvé." + (
                    " Par image, seules les vidéos qui ont déjà leur vignette "
                    "sont comparées : « État des vignettes » dit combien."
                    if by_image else "") + missed,
                "info")
            return
        self._keep_found_dupes(groups, by_image)
        self.show_banner(
            self._dupes_summary(groups, by_image) + ". Cliquez ici pour les "
            "afficher — ou plus tard par « ⋯ › Doublons »." + missed, "done",
            action=self.show_found_dupes, seconds=15)

    def _interim_dupes_found(self, scan, groups: list) -> None:
        """Une comparaison d'etape : ce qui est deja releve, compare.

        Le resultat attend comme les autres (« ⋯ › Doublons »), et le bandeau
        ne parle que quand il y a plus de groupes qu'a l'etape d'avant. Les
        groupes ne sont pas definitifs : une video pas encore relevee peut
        venir s'y ajouter, et la comparaison finale a le dernier mot.
        """
        spent = time.monotonic() - getattr(self, "_interim_began", time.monotonic())
        # Une comparaison de trois minutes ne se relance pas toutes les dix :
        # elle prendrait le processeur aux empreintes.
        self._interim_next = time.monotonic() + max(
            self.INTERIM_EVERY_S, 5 * spent)
        if self.sig_scan is None or not getattr(scan, "examined", 0):
            return
        groups = self._one_spelling(self._only_in_collection(groups))
        if not groups:
            return
        self._keep_found_dupes(groups, True, partial=True)
        if len(groups) <= getattr(self, "_interim_told", 0):
            return
        self._interim_told = len(groups)
        self.show_banner(
            f"Doublons, recherche en cours — sur les "
            f"{self._thousands(scan.examined)} vidéos déjà relevées : "
            + self._dupes_summary(groups, True) + ". Cliquez ici pour les "
            "voir. D'autres peuvent encore s'ajouter, y compris à ces groupes.",
            "info", action=self.show_found_dupes, seconds=15)

    @staticmethod
    def _dupes_summary(groups: list, by_image: bool) -> str:
        """« 12 groupe(s) de doublons — 15 fichier(s) en trop, jusqu'a… »."""
        extra = doubtful = gagne = 0
        for group in groups:
            _each, paths = group
            if getattr(group, "sure", True):
                extra += len(paths) - 1
                gagne += getattr(group, "gain", _each * (len(paths) - 1))
            else:
                # Trop peu pour trancher (une seule image, duree inconnue) :
                # a comparer, pas « en trop ».
                doubtful += 1
        how = "qui se ressemblent" if by_image else "de doublons"
        sure = len(groups) - doubtful
        said = []
        if sure:
            said.append(f"{sure} groupe(s) {how} — {extra} fichier(s) en trop, "
                        f"jusqu'à {human_size(gagne)} à récupérer")
        if doubtful:
            said.append(f"{doubtful} groupe(s) à comparer, trop peu sûrs pour "
                        "dire ce qui est en trop")
        return " ; ".join(said)

    def _name_dupes_action(self, count: int = 0, partial: bool = False) -> None:
        """L'entree « Afficher les doublons trouvés » n'existe que s'il y en a."""
        action = getattr(self, "dupes_result_action", None)
        if action is None:
            return
        so_far = " jusqu'ici" if partial else ""
        action.setText(f"Afficher les doublons trouvés{so_far} ({count} groupe(s))")
        action.setVisible(bool(count))

    def _keep_found_dupes(self, groups, by_image: bool, partial: bool = False) -> None:
        """Retient le resultat d'une recherche -- en memoire et sur le disque.

        Il ne vivait qu'en memoire : trois heures d'empreintes et quatre
        mille paires disparaissaient a la fermeture, sans plus aucun moyen
        de les revoir. Ecrit a chaque etape et a la fin, il revient au
        lancement suivant (`_reload_found_dupes`). Rien (None) l'efface.
        """
        from .dupes_memory import FOUND_DUPES
        self._pending_dupes = (groups, by_image) if groups else None
        self._pending_partial = bool(groups) and partial
        self._name_dupes_action(len(groups or ()), partial=self._pending_partial)
        if not groups:
            FOUND_DUPES.clear()
            return
        top = self.top_root() if self.root is not None else ""
        if not FOUND_DUPES.save(groups, by_image, partial, str(top or "")):
            self.show_banner("Les doublons trouvés n'ont pas pu être écrits sur "
                             "le disque : ils restent visibles jusqu'à la "
                             "fermeture de Prisme.", "error")

    def _reload_found_dupes(self) -> None:
        """Au lancement : le dernier resultat de doublons, s'il y en a un."""
        if self._pending_dupes is not None or self.dupes is not None:
            return
        from .dupes_memory import FOUND_DUPES
        from .tunnel import Chore
        # Lu hors du fil de l'interface : des milliers de paires a relire
        # figeaient la fenetre a son ouverture.
        Chore(FOUND_DUPES.load, self, fallback=None, then=self._found_dupes_loaded).start()

    def _found_dupes_loaded(self, found) -> None:
        if not found or self._pending_dupes is not None or self.dupes is not None:
            return
        groups, by_image, partial = found
        self._pending_dupes = (groups, by_image)
        self._pending_partial = partial
        self._name_dupes_action(len(groups), partial=partial)

    def _fresh_dupes(self, groups: list) -> list:
        """Le resultat tel qu'il vaut maintenant, peut-etre des jours apres.

        Ce qu'on a declare « pas des doublons » depuis en sort, comme ce qui
        a quitte la collection ; le resultat retenu est mis a jour d'autant.
        Ce qui est a la corbeille de la seance sort de l'affichage seulement :
        on peut encore le restaurer.
        """
        from .dupes_memory import NOT_DUPES
        before = len(groups)
        groups = NOT_DUPES.filtrer_groupes(list(groups))
        groups = self._one_spelling(self._only_in_collection(groups))
        if len(groups) != before:
            by_image = self._pending_dupes[1] if self._pending_dupes else True
            self._keep_found_dupes(groups, by_image, self._pending_partial)
        return self._still_here(groups)

    def show_found_dupes(self, groups: list | None = None, fresh=()) -> None:
        """Ouvre la revue des doublons : chaque groupe cote a cote, le dossier
        de chaque copie, et une regle pour choisir ce qu'on garde.

        `groups` : seulement ceux-la (le guetteur des nouvelles videos) ;
        sinon, le dernier resultat retenu."""
        if not isinstance(groups, list):
            groups = None          # le « checked » d'une action de menu
        if groups is None:
            pending = self._pending_dupes
            if not pending:
                self.show_banner("Aucun résultat de doublons en attente.", "quiet")
                return
            if self.root is None:
                return
            groups = self._fresh_dupes(pending[0])
        else:
            groups = self._still_here(list(groups))
        if not groups:
            self.show_banner("Plus aucun doublon à examiner : ce qui a été "
                             "trouvé a été écarté ou rangé depuis.", "quiet")
            return
        from .dupes_view import DupesReview
        old = getattr(self, "_dupes_review", None)
        if old is not None:
            try:
                old.close()
            except RuntimeError:
                pass
        review = DupesReview(self, groups, fresh=fresh)
        review.setAttribute(Qt.WA_DeleteOnClose, True)
        review.destroyed.connect(
            lambda *_a, r=review: setattr(self, "_dupes_review", None)
            if getattr(self, "_dupes_review", None) is r else None)
        self._dupes_review = review
        review.show()
        review.raise_()
        review.activateWindow()

    def show_found_dupes_board(self) -> None:
        """Montre le dernier resultat de doublons, comme une planche ordinaire.

        Une liste de passage : la relecture de la collection continue en
        dessous sans la toucher, les groupes restent groupes quel que soit
        le tri ou le filtre, et recliquer l'onglet rend la collection.

        Le resultat reste ensuite dans « ⋯ › Doublons » -- et d'une seance a
        l'autre -- jusqu'a la recherche suivante : une fois affiche, il
        disparaissait, et il fallait tout relancer pour le revoir.
        """
        pending = self._pending_dupes
        if not pending:
            self.show_banner("Aucun résultat de doublons en attente.", "quiet")
            return
        groups, by_image = pending
        if self.root is None:
            return
        groups = self._fresh_dupes(groups)
        if not groups:
            self.show_banner("Plus aucun doublon à examiner : ce qui a été "
                             "trouvé a été écarté ou rangé depuis.", "quiet")
            return
        if self.tab == TAB_SPLIT:
            if self.wall_full:
                self.toggle_wall_fullscreen(False)
            self.set_tab(TAB_FOLDERS)
        self.close_aside()
        # Les membres d'un meme groupe se suivent : c'est ce qui permet de les
        # comparer d'un coup d'oeil au lieu de les chercher dans la liste.
        # Le meilleur exemplaire vient en tete, marque « à garder » ; les
        # autres disent sur leur carte ce qui les en separe, et sont coches
        # d'office : cocher N-1 cases par groupe, a la main, etait tout le
        # travail. Un groupe trop peu sur (« à comparer ») n'est pas coche.
        items = []
        checked = set()
        extra_size = 0
        for number, group in enumerate(groups):
            _each, paths = group
            gap = getattr(group, "gap", None)
            sure = getattr(group, "sure", True)
            to_check = {os.path.normcase(str(path))
                        for path in getattr(group, "to_check", ())}
            # Jamais le meilleur, meme sous une autre casse : sous Windows et
            # sur le NAS, c'est le meme fichier.
            to_check.discard(os.path.normcase(str(paths[0])) if paths else "")
            if to_check:
                extra_size += getattr(group, "gain", 0)
            for rank, path in enumerate(paths):
                path = _as_path(path)
                item = Item(path=path, kind=MODE_FILES, videos=[path],
                            video_count=1, file_count=1)
                # Le groupe de chacun : Ctrl+A puis Supprimer emportait
                # l'original avec ses copies. Un groupe garde toujours au
                # moins son meilleur exemplaire (voir _spare_last_copies).
                item.dupe_group = number
                if gap is not None:
                    item.board_note = (("✓ à garder" if sure else "à comparer")
                                       if rank == 0 else gap(path))
                if os.path.normcase(str(path)) in to_check:
                    checked.add(item.item_id)
                items.append(item)
        # La relecture de la racine continue : elle tient la collection a
        # jour en dessous, sans rien melanger a cette liste (`on_patch`).
        # Celle d'un sous-dossier n'a plus rien a dire.
        if self.scanning and not self._scan_top:
            self.stop_scan()
            self.progress.hide()
        self._hush_players()
        self.browsing = True
        self._transient = "dupes"
        # Chaque groupe tel qu'il a ete montre, le meilleur en tete : c'est lui
        # que _spare_last_copies consulte, et non la liste du moment, qu'un
        # filtre, un tri ou un autre onglet peuvent avoir remplacee.
        self._dupe_members = {}
        for item in items:
            self._dupe_members.setdefault(item.dupe_group, []).append(item)
        self.all_items = items
        self.mode = MODE_FLAT
        self.items = list(items)
        self.index = 0
        self._list_leaf = "Doublons"
        self.board.picked_ids = set(checked)
        self._apply_selectors()
        self.refresh_board()
        self.on_picked_changed(len(self.board.picked_ids))
        said = self._dupes_summary(groups, by_image) + ". "
        if checked:
            said += (f"Le meilleur de chaque groupe est marqué « ✓ à garder » ; "
                     f"les {len(checked)} autres sont cochés d'office "
                     f"({human_size(extra_size)}) : vérifiez, puis "
                     "« Supprimer ». ")
        else:
            said += "Cochez ce dont vous ne voulez plus, puis « Supprimer ». "
        said += ("Un groupe garde toujours un exemplaire ; « Pas des doublons » "
                 "en retire un pour de bon.")
        self.show_banner(said, "info", seconds=12)

    def _dupe_group_of(self, items) -> list:
        """Les numeros des groupes de doublons de ces elements, dans l'ordre."""
        found = []
        for item in items:
            number = getattr(item, "dupe_group", None)
            if number is not None and number not in found:
                found.append(number)
        return found

    def not_dupes_picked(self) -> None:
        """« Pas des doublons » : les groupes des elements coches sortent de la
        liste, et la recherche ne les remontrera plus."""
        numbers = self._dupe_group_of(self.board.picked_items())
        if not numbers:
            self.show_banner("Cochez au moins un exemplaire du groupe qui n'en "
                             "est pas un.", "quiet")
            return
        if len(numbers) > 1:
            # Les exemplaires en trop sont coches d'office : tout accepter
            # d'un clic retirerait tous les groupes. On le demande.
            answer = QMessageBox.question(
                self, "Pas des doublons ?",
                f"Les éléments cochés touchent {len(numbers)} groupes. Les "
                "retirer tous, comme n'étant pas des doublons ?\n\n"
                "Ils quitteront la liste et ne reviendront plus dans les "
                "recherches. Pour un seul groupe : décochez les autres, ou "
                "ouvrez l'une de ses vidéos — « Pas des doublons » est aussi "
                "sur sa fiche.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
        self._dismiss_dupe_groups(numbers)

    def not_dupes_current(self) -> None:
        """Depuis la fiche d'un doublon : son groupe n'en est pas un."""
        item = self.current
        numbers = self._dupe_group_of([item] if item is not None else [])
        if not numbers:
            return
        self._dismiss_dupe_groups(numbers)
        self.show_board_at(min(self.index, max(0, len(self.items) - 1)))

    def _dismiss_dupe_groups(self, numbers: list) -> None:
        """Retient ces groupes « pas des doublons », et les retire de la liste."""
        from .dupes_memory import NOT_DUPES
        wanted = set(numbers)
        members = [item for item in self.all_items
                   if getattr(item, "dupe_group", None) in wanted]
        by_group: dict = {}
        for item in members:
            by_group.setdefault(item.dupe_group, []).append(str(item.path))
        added = 0
        for paths in by_group.values():
            for at, left in enumerate(paths):
                for right in paths[at + 1:]:
                    added += NOT_DUPES.ignorer(left, right, save=False)
        # Une seule ecriture pour tout le geste, et non une par paire.
        if added and not NOT_DUPES.save():
            self.show_banner("La mémoire des « pas des doublons » n'a pas pu être "
                             "écrite : elle le sera à la prochaine occasion.",
                             "error")
        gone = {item.item_id for item in members}
        for number in wanted:
            self._dupe_members.pop(number, None)
        page = self.board.page
        self.all_items = [item for item in self.all_items if item.item_id not in gone]
        self.items = [item for item in self.items if item.item_id not in gone]
        self.board.picked_ids -= gone
        self.index = max(0, min(self.index, len(self.items) - 1))
        if not self.items:
            self.board.empty.setText("Plus aucun groupe de doublons à examiner.")
        if self.browsing:
            self.refresh_board()
            self.board.set_page(page)
        self.on_picked_changed(len(self.board.picked_ids))
        self.show_banner(f"{len(wanted)} groupe(s) retiré(s) : ils ne reviendront "
                         "plus parmi les doublons.", "done")

    def scan_signatures(self) -> None:
        """Remplit la base d'empreintes — seulement ce qui n'y est pas encore.

        Quatre images par video, retenues avec sa taille et sa date. Le
        premier passage coute ; ensuite, ajouter mille videos ne demande que
        de sonder ces mille-la, et la recherche de doublons devient immediate.
        """
        if self.sig_scan is not None:
            self._sigs_cut = True
            self.sig_scan.stop()
            self.show_banner("Empreintes : arrêt demandé…", "quiet")
            return
        if self.root is None or not self._sigs_can_be_kept():
            return
        top = self.top_root()
        self.sig_scan = SignatureScan(top, self.cfg["thumb_width"],
                                      self.cfg["skip_hidden"], self)
        self._own_thread(self.sig_scan, "sig_scan")
        self._sigs_unreadable = 0
        self._sigs_started = None
        self._sigs_cut = False
        self._interim_next = None
        self._interim_told = 0
        self.sig_scan.progress.connect(self._sigs_progress)
        # Le recensement dure : il dit ce qu'il trouve en chemin, au lieu de
        # laisser croire que rien ne se passe.
        self.sig_scan.walking.connect(
            lambda found: self._task_said(
                "sig_scan",
                f"empreintes : recensement {self._thousands(found)} vidéos"))
        self.sig_scan.unreadable.connect(self._sigs_unreadable_count)
        self.sig_scan.done.connect(self._told_sigs)
        self.sig_scan.start()
        self.show_banner(
            f"Empreintes sous {top} : seules les vidéos nouvelles ou modifiées "
            "sont sondées. Recliquer arrête.", "info")

    def _sigs_progress(self, done: int, total: int) -> None:
        """« empreintes 1 200 / 58 000 — encore ~3 h 20 » : le temps restant,
        d'apres le rythme tenu jusque-la. Sans lui, une longue premiere fois
        ressemblait a une recherche bloquee."""
        now = time.monotonic()
        self._sig_counts = (done, total)
        if self._sigs_started is None:
            self._sigs_started = (now, done)
        line = f"empreintes {self._thousands(done)} / {self._thousands(total)}"
        began, first = self._sigs_started
        spent, made = now - began, done - first
        if spent >= 20 and made >= 5 and total > done:
            left = (total - done) * spent / made
            hours, minutes = divmod(int(left // 60), 60)
            line += (f" — encore ~{hours} h {minutes:02d}" if hours
                     else f" — encore ~{max(1, minutes)} min")
        self._task_said("sig_scan", line)
        self._interim_dupes(done)

    def _interim_dupes(self, done: int) -> None:
        """Lance une comparaison d'etape, si c'est le moment."""
        if (not getattr(self, "_dupes_after_sigs", False) or self.dupes is not None
                or self.root is None or getattr(self, "_sigs_cut", False)):
            return
        now = time.monotonic()
        due = getattr(self, "_interim_next", None)
        if due is None:
            self._interim_next = now + self.INTERIM_FIRST_S
            return
        if now < due:
            return
        self._interim_began = now
        self._interim_next = now + self.INTERIM_EVERY_S
        self._dupes_photos = False
        self._dupes_by_image = True
        self._dupes_from_sigs = True
        self._dupes_stopped = False
        self.dupes = SignatureGroupScan(self.top_root(), self)
        # Discrete : ni barre de progression, ni bandeau au depart.
        self.dupes.interim = True
        self._own_thread(self.dupes, "dupes")
        self.dupes.found.connect(self.on_dupes_found)
        self.dupes.start()

    def _sigs_unreadable_count(self, count: int) -> None:
        self._sigs_unreadable = int(count)

    def _told_sigs(self, seen: int, total: int) -> None:
        self.sig_scan = None
        if self._closing:
            return
        self._refresh_state()
        unreadable = getattr(self, "_sigs_unreadable", 0)
        cut = getattr(self, "_sigs_cut", False)
        self.show_banner(
            (f"Empreintes arrêtées : {total} dans la base, la suite reprendra "
             "où elle s'est arrêtée." if cut else
             f"Empreintes : {seen} vidéo(s) sondée(s) cette fois, "
             f"{total} dans la base.")
            + (f" {unreadable} illisible(s), retentée(s) au prochain passage."
               if unreadable else "")
            + " « Doublons d'après les empreintes » peut maintenant les "
            "comparer.", "done" if not unreadable else "info")
        if getattr(self, "_dupes_after_sigs", False):
            # « Chercher les doublons » : la comparaison suit, sans rien
            # demander -- arretees ou non, les empreintes deja relevees se
            # comparent : ce qui a ete trouve n'est pas jete.
            self._dupes_after_sigs = False
            if getattr(self.dupes, "interim", False):
                # Une etape tourne encore : elle cede la place a la finale.
                self._dupes_stopped = True
                self._dupes_next = "sigs"
                self.dupes.stop()
            else:
                self.duplicates_from_sigs()
            if cut:
                self.show_banner(
                    f"Empreintes arrêtées ({total} dans la base, la suite "
                    "reprendra où elle s'est arrêtée) : comparaison de celles "
                    "déjà relevées… Recliquer « Chercher les doublons » "
                    "l'arrête aussi.", "info")

    def find_dupes(self) -> None:
        """La seule recherche de doublons : d'apres les empreintes.

        Sonde d'abord les videos qui n'en ont pas encore (seulement celles-la :
        la seconde fois, c'est presque immediat), puis compare, tout seul.
        Recliquer arrete ce qui tourne. Les photos ont leur propre recherche
        (`find_photo_dupes`).
        """
        if photo_mode():
            return self.find_photo_dupes()
        if self.sig_scan is not None:
            # L'arrete -- et compare ce qui est deja releve.
            self._dupes_after_sigs = True
            return self.scan_signatures()
        if self.dupes is not None:
            return self.duplicates_from_sigs()   # l'arrete aussi
        if self.root is None or not self._sigs_can_be_kept():
            return
        missing = self._sigs_missing()
        if missing >= self.SIGS_WARN and not self._confirm_first_dupes(missing):
            return self.setFocus()
        self._dupes_after_sigs = True
        self.scan_signatures()

    def find_photo_dupes(self) -> None:
        """Les doublons de photos, d'un trait : copies exactes et meme image
        sous un autre poids (redimensionnee, recompressee, convertie).

        Seules les photos nouvelles ou modifiees sont lues -- leur vignette
        quand elle est faite --, les autres ont leur empreinte dans l'index
        des photos. Recliquer arrete.
        """
        if self.dupes is not None:
            self._stop_dupes("photos")
            return
        if self.root is None or not self._sigs_can_be_kept():
            return
        top = self.top_root()
        self._dupes_photos = True
        self._dupes_by_image = False
        self._dupes_from_sigs = False
        self.dupes = PhotoDuplicateScan(top, self.cfg["thumb_width"],
                                        self.cfg["skip_hidden"], self)
        self.dupes.reading.connect(self._photo_dupes_reading)
        self._start_dupes(
            f"Doublons de photos sous {top} : copies exactes et mêmes images "
            "sous un autre poids. Seules les photos nouvelles ou modifiées sont "
            "lues. Recliquer arrête.")

    def _sigs_can_be_kept(self) -> bool:
        """Faux, et le dit, quand l'index ne peut rien retenir.

        Sans index (un autre Prisme le tient, disque refuse), les empreintes
        ne vivaient qu'en memoire : des heures de sondage perdues a la
        fermeture, et tout etait a refaire au lancement suivant.
        """
        if INDEX.db is not None:
            return True
        why = f" ({INDEX.problem})" if INDEX.problem else ""
        self.show_banner(
            "Empreintes impossibles à retenir : l'index de Prisme n'est pas "
            f"disponible{why}. Fermez les autres fenêtres de Prisme, puis "
            "relancez-le avant de chercher les doublons.", "error")
        return False

    def _sigs_missing(self) -> int:
        """Combien de videos de la collection n'ont pas encore d'empreinte
        (une estimation : la collection telle qu'on la connait)."""
        try:
            if self._collection_known():
                total = sum(len(item.videos) for item in self._collection())
            else:
                total = int((self.cfg["collection"] or {}).get("videos") or 0)
            from . import roots as _roots
            known = sum(INDEX.sig_count_under(member)
                        for member in _roots.members(self.top_root()))
            return max(0, total - known)
        except Exception:                               # noqa: BLE001
            return 0

    def _confirm_first_dupes(self, missing: int) -> bool:
        """Dit, avant de lancer, que la premiere fois est longue -- et pourquoi
        ca vaut le coup."""
        box = QMessageBox(self)
        box.setWindowTitle("Chercher les doublons")
        box.setIcon(QMessageBox.Information)
        box.setText(f"<b>Une première fois un peu longue, et c'est fini.</b>")
        box.setInformativeText(
            f"Prisme doit d'abord relever l'empreinte de "
            f"{self._thousands(missing)} vidéo(s) : quelques images de chacune, "
            "qui permettent de reconnaître une copie même réencodée, recadrée ou "
            "renommée. Sur une grande collection, cela prend plusieurs heures.\n\n"
            "Pendant ce temps, Prisme reste utilisable, et le temps restant "
            "s'affiche en haut. Vous pouvez arrêter quand vous voulez : rien "
            "n'est perdu, la suite reprendra où elle s'est arrêtée.\n\n"
            "Ensuite, seules les nouvelles vidéos sont relevées : les recherches "
            "suivantes prennent quelques secondes.")
        go = box.addButton("C'est parti", QMessageBox.AcceptRole)
        box.addButton("Plus tard", QMessageBox.RejectRole)
        box.setDefaultButton(go)
        box.exec()
        return box.clickedButton() is go

    def duplicates_from_sigs(self) -> None:
        """Compare les empreintes deja en base. Aucun acces au disque.

        Dans un fil : le calcul prenait de trente secondes a plusieurs minutes
        sur le fil de l'interface, qui ne repondait plus. La lecture de la
        base s'y fait aussi, avec le tri par racine — « X:\\Films2 » n'est plus
        pris pour un morceau de « X:\\Films ». Recliquer arrete.
        """
        if self.dupes is not None:
            self._stop_dupes("sigs")
            return
        if self.root is None:
            return
        top = self.top_root()
        self._dupes_photos = False
        self._dupes_by_image = True
        self._dupes_from_sigs = True
        self.dupes = SignatureGroupScan(top, self)
        self._start_dupes(f"Comparaison des empreintes sous {top}…")

    def _watch_new_dupes(self) -> None:
        """Le guetteur : des videos sont arrivees, sont-elles deja la ?

        Seules les videos sans empreinte (les nouvelles) sont sondees, puis
        comparees a toute la collection, en fond et sans bandeau. Rien ne se
        dit s'il n'y a rien ; sinon un bandeau ouvre leur comparaison. Il
        attend qu'une premiere recherche de doublons ait releve la
        collection : sans elle, tout serait « nouveau ». Pas pour les photos :
        leur recherche ne relit deja que les nouvelles."""
        if (self._closing or self.root is None or not self.cfg["dupes_watch"]
                or photo_mode()
                or self.sig_scan is not None or self.dupes is not None
                or INDEX.db is None):
            return
        try:
            from . import roots as _roots
            known = sum(INDEX.sig_count_under(m) for m in _roots.members(self.top_root()))
        except Exception:                               # noqa: BLE001
            return
        missing = self._sigs_missing()
        if not known or not missing or missing > self.WATCH_MAX:
            return
        places = list(getattr(self, "_touched_folders", None) or [])
        if not places:
            return
        scan = SignatureScan(self.top_root(), self.cfg["thumb_width"],
                             self.cfg["skip_hidden"], self, places=places)
        scan.watch = True
        self.sig_scan = scan
        self._own_thread(scan, "sig_scan")
        scan.progress.connect(lambda done, total: self._task_said(
            "sig_scan", f"doublons : nouvelles vidéos {done} / {total}"))
        scan.done.connect(lambda seen, total, s=scan: self._watch_sigs_done(s, seen, total))
        scan.start()

    def _watch_sigs_done(self, scan, seen: int, total: int) -> None:
        if self.sig_scan is scan:
            self.sig_scan = None
        if self._closing:
            return
        if getattr(self, "_dupes_after_sigs", False):
            # « Chercher les doublons » a ete demande pendant ce temps.
            return self._told_sigs(seen, total)
        fresh = list(getattr(scan, "fresh", []) or [])
        if not fresh or self.dupes is not None:
            return
        keys = {os.path.normcase(p) for p in fresh}
        compare = SignatureGroupScan(self.top_root(), self)
        compare.watch = True
        self.dupes = compare
        self._own_thread(compare, "dupes")
        compare.found.connect(lambda groups, c=compare, k=keys, f=fresh:
                              self._watch_found(c, groups, k, f))
        compare.start()
