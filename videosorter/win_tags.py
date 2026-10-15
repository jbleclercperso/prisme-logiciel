"""L'onglet Mots-cles : familles, liste, mots a soi."""
from __future__ import annotations

from pathlib import Path
import shiboken6
from .config import photo_mode
from .header import TAB_TAGS
from .tagging import TagsThread, iter_tag_items
from .perf import mark
from .tagging import fold
from .scan import MODE_FILES, MODE_FLAT, MODE_FOLDERS, Item
from .index import INDEX
from .widgets import TagsDialog


# Les dossiers virtuels epingles en tete de la collection : ce qu'aucun
# nom ne range, et ce qui n'est dans aucun sous-dossier. Leur nom dit ce
# qu'ils contiennent : « À trier » et « Orphelins » ne s'expliquaient pas,
# on ne se rappelait jamais lequel etait lequel.
PINNED_UNREAD = "Noms sans aucun mot — partout"


PINNED_LOOSE = "Noms sans aucun mot — en vrac"


class TagsMixin:
    """Une part de la fenetre principale : MainWindow en herite."""

    def open_tag(self, item) -> None:
        """Montre les vidéos réunies par un mot-clé, comme une liste ordinaire."""
        self.levels.append({
            "root": self.root, "mode": self.mode,
            "item_id": item.item_id,
        })
        # Sans `stat()` : sur un partage, ouvrir un mot-cle de cinq cents videos
        # coutait cinq cents allers-retours pour une taille que rien n'affiche.
        self.all_items = [Item(path=Path(video), kind=MODE_FILES, videos=[video],
                               video_count=1, file_count=1)
                          for video in item.videos]
        self.items = self._filtered()
        self.apply_sort()
        self.mode = MODE_FLAT
        self.index = 0
        # Les videos d'un autre mot-cle, pas celle d'avant : le lecteur de
        # cote lisait encore une video de la liste quittee.
        if not self.aside_playlist and not self.aside.isHidden():
            self.close_aside()
        self.start_harvest()
        self._apply_selectors()
        # Un mot-cle n'est pas un dossier : sans ce rappel, le fil d'Ariane
        # restait sur la racine et l'on ne savait plus ce qu'on regardait.
        self._list_leaf = item.name
        self.crumbs.set_path(self.top_root(), self.root)
        self.crumbs.append_leaf(item.name)
        noun = "photo(s)" if photo_mode() else "vidéo(s)"
        if item.path.name == PINNED_UNREAD:
            told = (f"{len(self.items)} {noun} dont le nom ne dit rien (8f7e6d5c4b, "
                    "IMG_2041…), où qu'elles soient : à regarder pour les ranger")
        elif item.path.name == PINNED_LOOSE:
            told = (f"{len(self.items)} {noun} dont le nom ne dit rien, posées en "
                    "vrac à la racine ou dans un dossier « + », hors de tout "
                    "sous-dossier : à regarder pour les ranger")
        else:
            told = f"{len(self.items)} {noun} portant « {item.path.name} »"
        self.show_banner(told, "info")
        if self.browsing:
            self.refresh_board()
        elif self.items:
            self.show_item(0)

    def _add_tag_items(self) -> None:
        """Place les dossiers virtuels en tete, une fois l'analyse terminee.

        Ils se calculent sur les videos deja trouvees : inutile de relire le
        disque, l'analyse vient de le faire. Les deux familles se calculent
        hors du fil de l'interface et se gardent tant que la collection ne
        bouge pas : « Mes mots » se refaisait a chaque visite, deux a quatre
        secondes de fenetre figee sur cent mille videos.
        """
        if self.mode != MODE_FOLDERS:
            return
        mark(f"_add_tag_items {self.tag_family}")
        # Les dossiers virtuels ne debordent plus sur la liste des dossiers
        # reels : ils ont leur onglet, c'est la qu'on les cherche.
        if self.tab != TAB_TAGS:
            if any(i.is_tag for i in self.all_items):
                self.all_items = [i for i in self.all_items if not i.is_tag]
                if not self.all_items:
                    self.all_items = self._collection()
                self.items = self._filtered()
                self.apply_sort()
            return
        # Les mots se tirent de la collection entiere, jamais de la liste
        # affichee : celle-ci peut etre celle des mots eux-memes.
        plain = self._collection()
        key = self._collection_key()
        # Deux familles : les mots qu'on a saisis, et ceux que les noms de
        # fichiers repetent d'eux-memes. Les seconds ne demandent aucune saisie
        # et decrivent souvent mieux la collection que ce qu'on aurait pense.
        if self.tag_family != "mine":
            cached = self._top_tags
            if cached is None or cached[0] != key:
                if self._tags_thread is None or self._tags_key != key:
                    videos = [video for item in plain for video in item.videos]
                    self._tags_key = key
                    self._tags_thread = TagsThread(videos, INDEX.titles, self)
                    self._tags_thread.ready.connect(self._on_top_tags)
                    self._tags_thread.start()
                    self.show_banner("Mots fréquents : calcul en cours…", "info")
                if cached is None:
                    return self._tags_computing("Calcul des mots fréquents…")
                # L'ancienne liste reste a l'ecran le temps du calcul : une
                # planche videe puis refaite pour presque les memes mots.
            found = cached[1]
            if not found:
                return self._no_tags(
                    "Aucun mot ne revient assez souvent dans ces noms de fichiers.")
            return self._show_tag_list(found)
        words = self.tags
        if not words:
            # Aucun mot-cle : la liste se vide **et l'affichage suit**. Il
            # restait auparavant sur les categories precedentes, si bien que
            # « Mes mots-cles » semblait rendre les mots frequents.
            return self._no_tags(
                "Ajoutez les vôtres par « ⋯ › Mots-clés automatiques… », ou "
                "choisissez « Fréquents » pour les laisser deviner.")
        # Mes propres mots valent pour une seule video ; ceux tires des noms
        # de fichiers (`TagsThread`) doivent en reunir plusieurs.
        minimum = 1
        members = self.cfg["tag_members"] or {}
        wanted = (key, tuple(words), minimum,
                  tuple(sorted((k, len(v)) for k, v in members.items())))
        cached = self._mine_tags
        if cached is None or cached[0] != wanted:
            if self._mine_key != wanted:
                self._mine_key = wanted
                videos = [video for item in plain for video in item.videos]
                # Par tranches sur le fil de l'interface, et non dans un fil :
                # un calcul Python y garde le verrou global, et la fenetre
                # restait figee jusqu'a la fin (`iter_tag_items`).
                old = self._mine_slicer
                if old is not None and shiboken6.isValid(old):
                    old.stop()
                    old.deleteLater()
                self._mine_slicer = self._in_slices(
                    iter_tag_items(list(words), videos, minimum, dict(members)),
                    lambda found, k=wanted: self._on_mine_tags(k, found))
            if cached is None or cached[0][1:3] != wanted[1:3]:
                return self._tags_computing("Calcul de vos mots-clés…")
            # Memes mots, collection un peu changee : l'ancienne liste reste
            # a l'ecran le temps du calcul.
        found = cached[1]
        if not found:
            return self._no_tags(
                "Aucun de vos mots-clés ne figure dans les noms de fichiers.")
        self._show_tag_list(found)

    def _tags_up_to_date(self) -> bool:
        """Les mots-cles a l'ecran sont-ils ceux de la collection actuelle ?

        Une relecture qui n'a rien change ne les refait pas : la planche des
        mots-cles se rebatissait a chaque fin d'analyse, pour les memes.
        """
        key = self._collection_key()
        if self.tag_family != "mine":
            cached = self._top_tags
            return (cached is not None and cached[0] == key
                    and self.all_items is cached[1])
        cached = self._mine_tags
        return (cached is not None and cached[0][0] == key
                and cached[0][1] == tuple(self.tags)
                and self.all_items is cached[1])

    def _on_mine_tags(self, key, found: list) -> None:
        """Vos mots-cles sont faits : ils s'affichent, s'ils sont encore voulus."""
        if key != self._mine_key:
            # Un calcul remplace entre-temps (autres mots, autre collection).
            return
        self._mine_key = None
        self._mine_tags = (key, found)
        if self._at_tag_list() and self.tag_family == "mine":
            self._add_tag_items()

    def _tags_computing(self, text: str) -> None:
        """Rien a montrer encore : on dit que ca se calcule."""
        self.all_items = []
        self.items = []
        self.board.empty.setText(text)
        if self.browsing:
            self.refresh_board()
        self.item_title.setText(text)
        self.item_subtitle.setText("")
        self._show_counts()

    def _no_tags(self, why: str) -> None:
        """Aucun mot-cle : la liste se vide, et le dit.

        « Mes mots » sans aucune correspondance laissait la liste des
        dossiers a l'ecran, sous l'onglet des mots-cles.
        """
        self.all_items = []
        self.items = []
        self.board.empty.setText(why)
        if self.browsing:
            self.refresh_board()
        self.item_title.setText("Aucun mot-clé")
        self.item_subtitle.setText(why)
        self._show_counts()
        self.update_counter()
        self._tell_tags([])

    def _show_tag_list(self, found: list) -> None:
        # Dans son onglet, un mot-cle n'est pas un en-tete pose sur la liste des
        # dossiers : c'est toute la liste (`_add_tag_items` ne vient ici que
        # sous l'onglet des mots-cles).
        self.all_items = found
        self.items = self._filtered()
        self.apply_sort()
        self.board.empty.setText("Rien à afficher ici.")
        self.start_harvest()
        if self.browsing:
            self.refresh_board()
        elif self.items:
            self.show_item(min(self.index, len(self.items) - 1))
        self._show_counts()
        self.update_counter()
        self._tell_tags(found)

    def _tell_tags(self, found: list) -> None:
        """Apres « Mots-clés automatiques… » : combien ont trouve des videos."""
        if not self._tags_banner:
            return
        self._tags_banner = False
        self.show_banner(
            f"{len(found)} mot(s)-clé(s) sur {len(self.tags)} ont trouvé "
            f"des vidéos", "info")

    def _on_top_tags(self, found: list) -> None:
        sender = self.sender()
        if sender is not None and sender is not self._tags_thread:
            # Un calcul remplace entre-temps (autre racine, autre collection) :
            # son resultat se rangeait sous la cle du nouveau.
            return
        self.board.empty.setText("Rien à afficher ici.")
        self._top_tags = (self._tags_key, found)
        self._tags_thread = None
        if self._at_tag_list() and self.tag_family != "mine":
            self._add_tag_items()

    def set_tag_family(self, family: str) -> None:
        """Mes propres mots-clés, ou ceux que les noms de fichiers répètent."""
        if family == self.tag_family:
            # Recliquer la famille ou l'on est : on remonte a sa liste. C'est
            # le geste qu'on fait quand on s'est perdu dans un mot-cle.
            return self.go_home()
        self.tag_family = family
        self.cfg["tag_family"] = family
        self.cfg.save_soon()
        self.tag_chips.set_value(family)
        # Le chemin de tous les onglets vers leur liste. Depuis l'interieur
        # d'un mot-cle, la remise a zero faite ici laissait le mode a plat :
        # les puces changeaient, la planche montrait toujours l'ancien mot, et
        # Echap quittait la racine.
        self.close_aside()
        if self.root is not None and not self._show_tab_list():
            self.start_root(self.top_root(), MODE_FOLDERS, reset_levels=True)

    def _at_tag_list(self) -> bool:
        """Vrai a la racine de l'onglet « Mots-clés », la ou vivent ses mots."""
        if self.tab != TAB_TAGS or self.levels or self.root is None:
            return False
        top = self.top_root()
        return top is not None and Path(self.root) == Path(top)

    def edit_tags(self) -> None:
        """Saisit les mots-clés qui deviendront des dossiers virtuels.

        Un mot par ligne ; les dossiers se refont aussitôt, sans relire le
        disque — ce qu'il faut pour savoir en changeant un mot s'il attrape ce
        qu'on visait.
        """
        dialog = TagsDialog(self.tags, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return self.setFocus()
        self.tags = dialog.result_tags()
        self.cfg["tags"] = self.tags
        # Un mot retire emporte les videos qu'on y avait rangees a la main.
        kept = {fold(t) for t in self.tags}
        members = self.cfg["tag_members"] or {}
        if any(fold(k) not in kept for k in members):
            self.cfg["tag_members"] = {k: v for k, v in members.items() if fold(k) in kept}
            self._mine_tags = None
        self.tag_family = "mine"
        self.cfg["tag_family"] = "mine"
        self.cfg.save()
        self.tag_chips.set_value("mine")
        if self.root is None:
            return self.setFocus()
        # Le compte se dit une fois les mots faits : ils se calculent hors
        # du fil de l'interface (`_tell_tags`).
        self._tags_banner = True
        if self.tab == TAB_TAGS and self.mode == MODE_FOLDERS:
            self._add_tag_items()
        else:
            self.set_tab(TAB_TAGS)
        self.setFocus()
