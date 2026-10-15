"""Ce qui arrive du telephone et des visiteurs : demandes, sondages, missions, videos cachees, Telegram."""
from __future__ import annotations

import json
import time
from pathlib import Path
import shiboken6
from .header import TAB_FOLDERS, TAB_SPLIT
from .perf import mark
from .scan import MODE_FILES, MODE_FLAT, Item, as_path as _as_path
from .widgets import PhoneHideDialog


class PhoneMixin:
    """Une part de la fenetre principale : MainWindow en herite."""

    # -- les demandes du telephone ------------------------------------------------
    def _import_phone_videos(self) -> None:
        """Les videos que le NAS a telechargees pour les telephones : rangees
        dans la collection, hors du fil de l'interface (`nas_publish`)."""
        from . import roots
        top = self.top_root()
        if (top is None or roots.is_union(top) or getattr(self, "_phone_importing", False)
                or time.time() - getattr(self, "_phone_imported_at", 0.0) < 120):
            return
        from .nas_publish import import_downloads
        from .tunnel import Chore
        self._phone_importing = True
        self._phone_imported_at = time.time()

        def then(count) -> None:
            self._phone_importing = False
            if count:
                from .liens import FOLDER_NAME
                self.show_banner(
                    f"{count} vidéo(s) envoyée(s) depuis le téléphone, rangée(s) dans "
                    f"« {FOLDER_NAME} ».", "done", seconds=10)

        Chore(lambda: import_downloads(top), self, fallback=0, then=then).start()

    def _demande_paths(self) -> list:
        """Ou les demandes arrivent : chez Prisme (page servie par le PC), et
        dans le partage du NAS (page servie par le NAS)."""
        from .config import PRIVATE_DIR
        from .demandes import FILE_NAME
        from .nas_publish import FOLDER, share_root
        paths = [PRIVATE_DIR / FILE_NAME]
        top = self.top_root()
        share = share_root(top) if top is not None else None
        if share is not None:
            paths.append(Path(share) / FOLDER / "etat" / FILE_NAME)
        return paths

    def _poll_demandes(self) -> None:
        if self._closing or getattr(self, "_asks_reading", False):
            return
        self._import_phone_videos()
        self._poll_surveys()
        self._poll_missions()
        from . import demandes
        from .tunnel import Chore
        paths = self._demande_paths()
        self._asks_reading = True
        Chore(lambda: demandes.read_all(paths), self, fallback=None,
              then=self._demandes_read).start()

    def _demandes_read(self, entries) -> None:
        self._asks_reading = False
        if entries is None:
            return
        self._asks = entries
        seen = set(self.cfg["demandes_vues"] or [])
        fresh = [e for e in entries if e["id"] not in seen]
        if fresh:
            self.ask_badge.setText(f"➤ {len(fresh)} demande{'s' if len(fresh) > 1 else ''}")
            if not self.ask_badge.isVisible():
                self.show_banner(f"Nouvelle demande du téléphone : « {fresh[0]['text'][:80]} »",
                                 "info")
            self.ask_badge.show()
        else:
            self.ask_badge.hide()

    # -- les sondages aux visiteurs (sondages.py) -------------------------------
    def _survey_sources(self) -> list:
        """Ou arrivent les reponses : chez Prisme et dans le partage du NAS."""
        from . import sondages
        from .config import PRIVATE_DIR
        from .nas_publish import FOLDER, share_root
        sources = [("PC", PRIVATE_DIR / sondages.ANSWERS_NAME)]
        top = self.top_root()
        share = share_root(top) if top is not None else None
        if share is not None:
            sources.append(("NAS", Path(share) / FOLDER / "etat" / sondages.ANSWERS_NAME))
        return sources

    def _poll_surveys(self) -> None:
        """Qui vient de finir un sondage : un bandeau, qui mene a son rapport.
        Rien n'est relu tant qu'aucun sondage n'a ete envoye."""
        if getattr(self, "_surveys_reading", False):
            return
        from . import sondages
        from .config import PRIVATE_DIR
        from .tunnel import Chore
        surveys = sondages.load(PRIVATE_DIR / sondages.FILE_NAME)
        if not any(one["state"] in ("ouvert", "clos") for one in surveys):
            return
        sources = self._survey_sources()
        self._surveys_reading = True
        Chore(lambda: sondages.reports(sources), self, fallback=None,
              then=lambda found: self._surveys_read(found, surveys)).start()

    def _surveys_read(self, found, surveys) -> None:
        self._surveys_reading = False
        if not found:
            return
        titles = {one["id"]: one["title"] for one in surveys if one["state"] != "supprime"}
        seen = set(self.cfg["sondages_vus"] or [])
        fresh = []
        for survey_id, rows in found.items():
            if survey_id not in titles:
                continue
            for row in rows:
                mark = f"{survey_id}|{row['where']}|{row['me']}"
                if row["done_at"] and mark not in seen:
                    fresh.append((row["done_at"], survey_id, row, mark))
        if not fresh:
            return
        fresh.sort(key=lambda one: -one[0])
        self.cfg["sondages_vus"] = (list(self.cfg["sondages_vus"] or [])
                                    + [one[3] for one in fresh])[-3000:]
        self.cfg.save_soon()

        def who(row) -> str:
            given = self.alias(row["label"]) if row.get("label") else ""
            return given if given and given != row["label"] else (row["name"] or "Un visiteur")

        _at, survey_id, row, _mark = fresh[0]
        if len(fresh) == 1:
            text = (f"✓ {who(row)} a fini le sondage « {titles[survey_id]} » — "
                    "cliquer pour voir son rapport.")
        else:
            names = ", ".join(dict.fromkeys(who(one[2]) for one in fresh[:3]))
            text = (f"✓ {len(fresh)} sondages finis ({names}"
                    + ("…" if len(fresh) > 3 else "") + ") — cliquer pour voir les rapports.")
        self.show_banner(text, "done", seconds=10, action=lambda: self.open_surveys(
            survey_id, (row["where"], row["me"])))

    # -- ce que le telephone ne voit pas (telcache.py) ---------------------------
    def phone_hidden(self) -> dict:
        """Les dossiers et videos caches sur le telephone : {cle: element}."""
        from . import telcache
        from .config import PRIVATE_DIR
        return telcache.active(telcache.load(PRIVATE_DIR / telcache.FILE_NAME))

    def set_phone_hidden(self, targets: list, hide: bool) -> int:
        """Cache (ou remontre) ces [(chemin, « dossier » ou « video »)] sur la
        page des visiteurs. Rend le nombre d'elements changes."""
        from . import telcache
        from .config import PRIVATE_DIR
        from .nas_publish import share_root
        place = PRIVATE_DIR / telcache.FILE_NAME
        entries = telcache.load(place)
        share = share_root(self.top_root() or "")
        changed = 0
        for path, kind in targets:
            key = telcache.key_of(path)
            known = entries.get(key)
            if hide:
                if known is not None and not known["removed"]:
                    continue
                entries[key] = telcache.entry(path, kind, share)
            else:
                if known is None or known["removed"]:
                    continue
                known.update(removed=True, updated=time.time())
            changed += 1
        if changed:
            telcache.save(place, entries)
            self._apply_phone_hidden(entries)
        return changed

    def _apply_phone_hidden(self, entries: dict) -> None:
        """La page du PC suit tout de suite ; le NAS recoit la liste et se
        refait seul (`nas/serveur.py`)."""
        from . import web
        from .nas_publish import share_root, sync_hidden
        from .tunnel import Chore
        web.set_hidden(entries)
        server, share = self.share_server, share_root(self.top_root() or "")

        def work():
            if server is not None:
                server.library.refresh()
            if share is not None:
                sync_hidden(share)
            return True
        Chore(work, self, fallback=False).start()

    def edit_phone_hidden(self) -> None:
        """⋯ › Visiteurs › Cachés sur le téléphone : la liste a cocher, comme
        « Dossiers masqués », avec l'arborescence pour en ajouter."""
        hidden = self.phone_hidden()
        current = [(one["path"], one["kind"]) for one in
                   sorted(hidden.values(), key=lambda one: one["path"].lower())]
        top = self.top_root()
        dialog = PhoneHideDialog(current, self, start=str(top) if top is not None else "")
        if dialog.exec() != dialog.DialogCode.Accepted:
            return self.setFocus()
        from . import telcache
        kept = {telcache.key_of(path): (Path(path), kind) for path, kind in dialog.chosen()}
        shown = [(Path(one["path"]), one["kind"]) for key, one in hidden.items()
                 if key not in kept]
        added = [target for key, target in kept.items() if key not in hidden]
        changed = 0
        if shown:
            changed += self.set_phone_hidden(shown, False)
        if added:
            changed += self.set_phone_hidden(added, True)
        if changed:
            self.show_banner(f"Cachés sur le téléphone : {len(kept)} élément(s).", "done")
        self.setFocus()

    # -- les missions video (missions.py) -----------------------------------------
    def _mission_sources(self) -> list:
        """Ou arrivent les videos des missions : chez Prisme et sur le NAS."""
        from . import missions
        from .config import PRIVATE_DIR
        from .nas_publish import FOLDER, share_root
        found = [("PC", PRIVATE_DIR / missions.SENDS_NAME, PRIVATE_DIR / missions.FOLDER_NAME)]
        top = self.top_root()
        share = share_root(top) if top is not None else None
        if share is not None:
            state = Path(share) / FOLDER / "etat"
            found.append(("NAS", state / missions.SENDS_NAME, state / missions.FOLDER_NAME))
        return found

    def _poll_missions(self) -> None:
        """Une video de mission vient d'arriver : un bandeau, qui mene a elle.
        Rien n'est relu tant qu'aucune mission n'a ete publiee."""
        if getattr(self, "_missions_reading", False):
            return
        from . import missions
        from .config import PRIVATE_DIR
        from .tunnel import Chore
        book = missions.load(PRIVATE_DIR / missions.FILE_NAME)
        if not any(one["state"] in ("ouverte", "close") for one in book["missions"].values()):
            return
        sources = self._mission_sources()
        self._missions_reading = True
        Chore(lambda: missions.received(sources), self, fallback=None,
              then=lambda found: self._missions_read(found, book)).start()

    def _missions_read(self, found, book) -> None:
        self._missions_reading = False
        if not found:
            return
        seen = set(self.cfg["missions_vues"] or [])
        fresh = [one for one in found if one["done_at"] and one["id"] not in seen
                 and one["mission"] in book["missions"]]
        if not fresh:
            return
        self.cfg["missions_vues"] = (list(self.cfg["missions_vues"] or [])
                                     + [one["id"] for one in fresh])[-3000:]
        self.cfg.save_soon()
        first = fresh[0]
        title = book["missions"][first["mission"]]["title"]
        who = first["name"] or "Un visiteur"
        text = (f"📹 {who} a envoyé sa vidéo pour la mission « {title} » — cliquer pour la "
                "regarder." if len(fresh) == 1 else
                f"📹 {len(fresh)} vidéos de mission reçues — cliquer pour les voir.")
        self.show_banner(text, "done", seconds=12, action=lambda: self.open_missions(
            first["mission"], first["id"]))

    def open_missions(self, mission_id: str = "", send_id: str = "") -> None:
        """La fenetre des missions video (pas modale)."""
        from .missions_dialog import MissionsWindow
        window = getattr(self, "_missions_window", None)
        if window is None or not shiboken6.isValid(window):
            window = MissionsWindow(self)
            self._missions_window = window
        window.show()
        window.raise_()
        window.activateWindow()
        if mission_id:
            window.select(mission_id, send_id)

    def open_surveys(self, survey_id: str = "", person=None) -> None:
        """La fenetre des sondages (pas modale), sur ce sondage et cette
        personne s'ils sont donnes."""
        from .sondages_dialog import SurveysWindow
        window = getattr(self, "_surveys_window", None)
        if window is None or not shiboken6.isValid(window):
            window = SurveysWindow(self)
            self._surveys_window = window
        window.show()
        window.raise_()
        window.activateWindow()
        if survey_id:
            window.select(survey_id, person)

    def show_survey_board(self, entries: list, title: str) -> None:
        """Les videos d'un rapport de sondage, en planche, chaque carte portant
        sa note (`sondages_dialog`). Une liste de passage, comme les doublons :
        recliquer l'onglet rend la collection."""
        if self.root is None or not entries:
            return
        if self.tab == TAB_SPLIT:
            if self.wall_full:
                self.toggle_wall_fullscreen(False)
            self.set_tab(TAB_FOLDERS)
        self.close_aside()
        self._leave_transient()
        items = []
        for path, note in entries:
            path = _as_path(path)
            item = Item(path=path, kind=MODE_FILES, videos=[path],
                        video_count=1, file_count=1)
            item.board_note = note
            items.append(item)
        if self.scanning and not self._scan_top:
            self.stop_scan()
            self.progress.hide()
        self._hush_players()
        self.browsing = True
        self._transient = "sondage"
        self.all_items = items
        self.mode = MODE_FLAT
        self.items = list(items)
        self.index = 0
        self._list_leaf = title
        self.board.clear_picked()
        self._apply_selectors()
        self.refresh_board()
        self.on_picked_changed(0)
        self.show_banner(f"{title} : {len(items)} vidéo(s), chacune avec sa note. "
                         "Recliquer l'onglet rend la collection.", "info", seconds=8)

    def _telegram_follow(self) -> None:
        """Groupes suivis : verifies au lancement puis a intervalle regulier,
        fenetre Telegram ouverte ou non ; un bandeau dit ce qui est arrive.

        Sans groupe suivi, rien ne demarre : ni le module (trois mille
        lignes), ni sa minuterie. Ouvrir la fenetre Telegram la demarre."""
        if getattr(self, "_telegram_watch", None) is not None:
            return
        from .config import APP_DIR
        try:
            state = json.loads((APP_DIR / "telegram.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        followed = any(c.get("alert") or c.get("auto") for c in state.get("channels") or []
                       if isinstance(c, dict))
        if not (state.get("api_id") and followed) and not getattr(self, "_telegram_dialog", None):
            return
        try:
            from .telegram_hub import start_watch
            start_watch(self)
        except Exception:
            pass

    def open_telegram(self, journal: bool = False) -> None:
        """Ses groupes, leurs alertes, la recherche ; `journal` : ouvrir sur
        le journal (clic sur le bandeau d'une alerte)."""
        from .telegram_hub import TelegramDialog
        dialog = getattr(self, "_telegram_dialog", None)
        if dialog is None or not shiboken6.isValid(dialog):
            dialog = TelegramDialog(self)
            self._telegram_dialog = dialog
        # Les alertes qu'on va cocher doivent tourner, fenetre fermee ensuite.
        self._telegram_follow()
        if journal:
            dialog.show_journal()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def open_demandes(self) -> None:
        """Les demandes reçues ; les ouvrir, c'est les avoir vues."""
        from . import demandes
        from .demandes_dialog import DemandesDialog
        paths = self._demande_paths()
        entries = demandes.read_all(paths)
        self.cfg["demandes_vues"] = sorted({e["id"] for e in entries})
        self.cfg.save_soon()
        self.ask_badge.hide()
        dialog = DemandesDialog(self, entries, paths)
        dialog.exec()
        self.setFocus()
