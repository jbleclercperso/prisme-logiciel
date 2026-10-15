"""Le partage : serveur du PC, adresse publique, publication sur le NAS, visiteurs."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
import shiboken6
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMenu
from .config import KIND_VIDEO, media_kind
from .media import Tools
from .access import JOURNAL
from .perf import mark
from .scan import human_duration
from .icons import DIM, icon


def _phone_links(server, top):
    """La file des liens envoyes du telephone, pour le partage du PC : les
    videos arrivent dans la collection, et dans les favoris de qui les a
    envoyees. None si la racine n'est pas un vrai dossier."""
    from . import roots
    from .config import PRIVATE_DIR
    from .downloader import available
    from .liens import FOLDER_NAME, LinkJobs
    if top is None or roots.is_union(top):
        return None

    def arrived(job: dict, path: Path) -> str:
        mark = server.library.add_download(path)
        JOURNAL.set_favorite(job.get("label", ""), mark, path.name, True)
        return mark

    return LinkJobs(Path(top) / FOLDER_NAME, PRIVATE_DIR / "liens.json", arrived,
                    ffmpeg=lambda: Tools.ffmpeg, available=available)


class ShareMixin:
    """Une part de la fenetre principale : MainWindow en herite."""

    # ------------------------------------------------------------------
    # Le partage a distance
    # ------------------------------------------------------------------
    def share_ready(self) -> bool:
        """Une serrure est-elle posee -- un mot de passe, ou la cle du lien
        d'invitation ? Sans l'une ou l'autre, on ne sert rien."""
        return bool((self.cfg["share_salt"] and self.cfg["share_digest"])
                    or self.share_invite())

    def share_invite(self) -> str:
        """La cle du lien d'invitation, tiree au premier besoin."""
        if not self.cfg["share_invite"]:
            from .web import new_invite
            self.cfg["share_invite"] = new_invite()
            self.cfg.save_soon()
        return self.cfg["share_invite"]

    def _publish_nas_key(self) -> None:
        """La seule cle vers le NAS, partage du PC coupe : sans elle, le NAS
        gardait l'ancien lien et refusait le nouveau. Dans un fil : le NAS
        peut mettre des secondes a repondre."""
        from .nas_publish import publish_access
        top = self.top_root()
        if not self.cfg["nas_publish"] or top is None or media_kind() != KIND_VIDEO:
            return
        access = {"invite": self.share_invite(), "salt": self.cfg["share_salt"],
                  "digest": self.cfg["share_digest"]}
        threading.Thread(target=publish_access,
                         args=(top, access, self.cfg["nas_tailnet"] or ""),
                         name="prisme-cle-nas", daemon=True).start()

    def publish_nas(self, force: bool = False) -> bool:
        """Depose sur le NAS le catalogue, les vignettes et la cle du partage.

        Seulement la collection de videos, et seulement si elle vit sur un
        partage reseau. Tout se fait dans un fil : la premiere fois, les
        vignettes se recopient pendant de longues minutes.
        """
        from .nas_publish import PUBLISHER
        server = self.share_server
        if (not self.cfg["nas_publish"] or server is None
                or media_kind() != KIND_VIDEO or self.top_root() is None):
            return False
        # Une analyse qui finit toutes les dix secondes ne republie pas a
        # chaque fois : le catalogue pese, et le NAS relit toutes les 30 s.
        if not force and time.time() - PUBLISHER.last_ok < 60:
            return False
        top = self.top_root()
        if getattr(self, "_nas_access_mine", False):
            # « Nouveau lien », ou un mot de passe change ici : c'est ce PC
            # qui decide, et l'autre PC s'y rangera a sa prochaine publication.
            self._nas_access_mine = False
            return self._publish_nas_now(server, top)
        from .nas_publish import published_access
        from .tunnel import Chore

        def then(found) -> None:
            if not shiboken6.isValid(self) or self._closing:
                return
            self._adopt_nas_access(found)
            if self.share_server is server:
                self._publish_nas_now(server, top)

        Chore(lambda: published_access(top), self, fallback=None, then=then).start()
        return True

    def _publish_nas_now(self, server, top) -> bool:
        from .nas_publish import PUBLISHER
        access = {"invite": self.share_invite(), "salt": self.cfg["share_salt"],
                  "digest": self.cfg["share_digest"]}
        return PUBLISHER.start(server.library, top, access,
                               self.nas_tailnet(), self._share_icons())

    def _share_icons(self) -> dict:
        """L'icone de Prisme en PNG, faite une fois (ecran d'accueil du telephone)."""
        icons = getattr(self, "_icons_png", None)
        if icons is None:
            from .widgets import app_icon_pngs
            icons = self._icons_png = app_icon_pngs()
        return icons

    def start_share(self, rebuild: bool = True) -> bool:
        """Ouvre le partage si tout est reuni. Sans bruit s'il manque quelque chose.

        Appele a chaque analyse terminee : c'est la qu'on connait enfin la
        racine. Le catalogue ne se refait que s'il le faut (`rebuild`, ou une
        collection qui a change, ou une autre racine), et toujours hors du fil
        de l'interface : il relit toute la racine dans l'index, une a huit
        secondes de fenetre figee a la fin de chaque analyse, meme d'un
        sous-dossier. Vrai veut dire « ouvert, ou en cours d'ouverture ».
        """
        # Rien a importer tant que le partage n'est pas voulu : le serveur
        # tire http.server et le courriel, pour rien.
        if not self.cfg["share"] or self.root is None or not self.share_ready():
            return False
        top = self.top_root()
        stamp = (str(top), self._collection_key())
        server = self.share_server
        if server is not None:
            if rebuild or stamp != self._share_stamp:
                self._rebuild_catalogue(server, top, stamp)
            return True
        if self._share_opening:
            return True
        self._open_share_server(top, stamp)
        return True

    def _open_share_server(self, top, stamp) -> None:
        """Le serveur et son catalogue, faits dans un fil ; branches ici."""
        from .tunnel import Chore
        from .web import Server

        self._share_opening = True
        token = self._share_token
        cfg = self.cfg
        salt, digest = cfg["share_salt"], cfg["share_digest"]
        port = int(cfg["share_port"] or 0)
        # Le Wi-Fi de la maison : le serveur ecoute tout le reseau local, et
        # plus seulement ce PC. Le tunnel, lui, passe toujours par ici.
        host = "0.0.0.0" if cfg["share_lan"] else (cfg["share_host"] or "127.0.0.1")
        expand, width = cfg["expand_parents"], cfg["thumb_width"]
        invite = self.share_invite()

        def work():
            try:
                # Ce que le telephone ne voit pas, avant le premier catalogue.
                from . import telcache, web
                from .config import PRIVATE_DIR as _private
                web.set_hidden(telcache.load(_private / telcache.FILE_NAME))
                server = Server(top, salt, digest, port=port, host=host,
                                expand=expand, width=width, invite=invite)
                from .config import PRIVATE_DIR
                from .demandes import FILE_NAME
                server.requests_path = PRIVATE_DIR / FILE_NAME
                server.downloads = _phone_links(server, top)
                server.start()
                return server
            except OSError as exc:
                return exc

        def then(result) -> None:
            if not shiboken6.isValid(self):
                return
            self._share_opening = False
            stale = (token != self._share_token or self._closing
                     or not self.cfg["share"] or not self.share_ready())
            if isinstance(result, Exception) or result is None:
                if not stale:
                    self.show_banner(f"Partage impossible : {result}", "error")
                return
            if stale:
                # Ferme ou change entre-temps : ce serveur n'a plus d'objet.
                result.stop()
                if not self._closing and self.cfg["share"] and self.share_ready():
                    self.start_share()
                return
            self.share_server = result
            self._share_stamp = stamp
            result.icons = self._share_icons()
            self.cfg["share_port"] = result.port
            self.cfg.save_soon()
            self.publish_nas()
            if self._share_announce:
                self._share_announce = False
                self.show_banner("Partage à distance ouvert.", "done")
            if self.cfg["tunnel_auto"]:
                # L'adresse publique s'ouvre d'elle-meme : c'est elle qu'on
                # veut, pas une commande a retaper a chaque fois.
                QTimer.singleShot(400, self.start_tunnel)
            elif self.cfg["tunnel_kind"] == "tailscale":
                self._check_leftover_funnel(result.port)
            if self._share_again:
                self._share_again = False
                self.start_share(rebuild=False)

        Chore(work, self, fallback=None, then=then).start()

    # -- le tunnel : une adresse publique, sans ouvrir de port -------------
    def start_tunnel(self) -> bool:
        """Ouvre l'adresse publique, par le chemin choisi.

        Tailscale peut mettre une minute a repondre : sa commande tourne hors
        du fil de l'interface, qui restait sinon figee jusqu'a quatre-vingts
        secondes — des le lancement, quand l'adresse s'ouvre d'elle-meme.
        Vrai veut dire « en cours ».
        """
        from .tunnel import Chore, Tunnel, find, find_fixed, open_fixed

        if self.share_server is None:
            return False
        if self.cfg["tunnel_kind"] == "tailscale":
            if not find_fixed():
                self.tunnel_trouble = "Tailscale n'est pas installé."
                return False
            if getattr(self, "_fixed_busy", False):
                return True
            from .share_dialog import _fixed_opened
            self._fixed_busy = True
            port, window = self.share_server.port, self
            # Une fermeture encore en cours passe d'abord : l'ouverture qui la
            # doublerait serait aussitot defaite.
            closing = getattr(self, "_fixed_closing", None)

            def work():
                if closing is not None:
                    closing.wait(35)
                return open_fixed(port)

            def then(result) -> None:
                self._fixed_busy = False
                _fixed_opened(window, result)
                _address, said = result
                if not _address and said:
                    self.show_banner(said, "error")

            Chore(work, self, fallback=("", "Tailscale n'a pas répondu."),
                  then=then).start()
            return True
        if self.tunnel is not None and self.tunnel.running:
            return True
        if not find():
            return False
        self.tunnel_trouble = ""
        self.tunnel = Tunnel(self.share_server.port, self)
        self.tunnel.ready.connect(self._tunnel_ready)
        self.tunnel.failed.connect(self._tunnel_failed)
        self.tunnel.closed.connect(self._tunnel_closed)
        return self.tunnel.start()

    def _from_old_tunnel(self) -> bool:
        """Vrai pour un signal venu d'un tunnel deja remplace ou ferme."""
        sender = self.sender()
        return sender is not None and sender is not self.tunnel

    def _tunnel_ready(self, address: str) -> None:
        if self._from_old_tunnel():
            return
        self.tunnel_address = address
        self.tunnel_trouble = ""
        self.show_banner(f"Adresse publique ouverte : {address}", "done")

    def _tunnel_failed(self, why: str) -> None:
        if self._from_old_tunnel():
            return
        self.tunnel_trouble = why
        self.tunnel_address = ""
        # Une chute en route se dit : l'adresse envoyee ne mene plus nulle
        # part, et la suivante sera differente.
        self.show_banner(why, "error")

    def _tunnel_closed(self) -> None:
        if self._from_old_tunnel():
            return
        self.tunnel_address = ""

    def stop_tunnel(self, wait: bool = False) -> None:
        """Ferme l'adresse publique.

        Pour Tailscale, hors du fil de l'interface (jusqu'a trente secondes),
        sauf a la fermeture de Prisme (`wait`), ou la fenetre est deja
        masquee et ou l'adresse ne doit pas survivre au programme. L'adresse
        n'est oubliee que si Tailscale confirme l'avoir fermee : sinon elle est
        peut-etre encore publiee, et on le dit.
        """
        if self.cfg["tunnel_kind"] == "tailscale" and self.tunnel_address:
            from .share_dialog import _fixed_closed
            from .tunnel import FIXED_CLOSED, Chore, close_fixed
            window = self

            def then(said) -> None:
                _fixed_closed(window, said)
                if said != FIXED_CLOSED:
                    self.show_banner(
                        f"L'adresse fixe est peut-être encore ouverte : {said}",
                        "error")

            if wait:
                then(close_fixed())
            else:
                import threading
                closed = threading.Event()
                self._fixed_closing = closed

                def work() -> str:
                    try:
                        return close_fixed()
                    finally:
                        closed.set()

                Chore(work, self, fallback="Tailscale n'a pas répondu.",
                      then=then).start()
        if self.tunnel is not None:
            self.tunnel.stop()
            self.tunnel = None
        if self.cfg["tunnel_kind"] != "tailscale":
            self.tunnel_address = ""

    def stop_share(self, wait: bool = False) -> None:
        # Une ouverture encore en cours, dans son fil, est oubliee : son
        # serveur se ferme a l'arrivee.
        self._share_token += 1
        self._share_opening = False
        self._share_again = False
        self.stop_tunnel(wait)
        if self.share_server is not None:
            if self.share_server.downloads is not None:
                self.share_server.downloads.close()
            self.share_server.stop()
            self.share_server = None

    def set_share(self, on: bool) -> None:
        self.cfg["share"] = bool(on)
        self.cfg.save()
        if on:
            if self.share_server is not None:
                self.show_banner("Partage à distance ouvert.", "done")
            else:
                # Il s'ouvre dans un fil : il le dira une fois ouvert.
                self._share_announce = True
                self.start_share()
        else:
            self.stop_share()
            self.show_banner("Partage à distance fermé.", "quiet")

    def set_share_password(self, given: str) -> None:
        from .web import hash_password

        salt, digest = hash_password(given)
        self.cfg["share_salt"], self.cfg["share_digest"] = salt, digest
        self.cfg.save()
        # Choisi ici : il remplace celui du NAS, au lieu de lui ceder.
        self._nas_access_mine = True
        # Changer la serrure ferme les sessions ouvertes : c'est le but.
        self.stop_share()
        self.start_share()

    def share_state(self) -> str:
        if not self.cfg["share"]:
            return "Partage fermé."
        if not self.share_ready():
            return "Aucune serrure : le partage reste fermé."
        if self.share_server is None:
            if self._share_opening:
                return "Ouverture du partage…"
            return ("Prêt, en attente d'une racine analysée."
                    if self.root is None else "Partage arrêté.")
        return (f"Ouvert — {len(self.share_server.library.videos)} vidéo(s) "
                f"servies depuis {self.share_server.library.root}")

    def share_home_link(self) -> str:
        """Le lien d'invitation sur le Wi-Fi de la maison, ou ""."""
        if self.share_server is None or not self.cfg["share_lan"]:
            return ""
        from .web import INVITE_PATH, lan_address
        here = lan_address()
        if not here:
            return ""
        return f"http://{here}:{self.share_server.port}{INVITE_PATH}{self.share_invite()}"

    def share_away_link(self) -> str:
        """Le lien d'invitation par l'adresse publique, ou "" si elle est fermee."""
        if self.share_server is None or not self.tunnel_address:
            return ""
        from .web import INVITE_PATH
        return f"{self.tunnel_address.rstrip('/')}{INVITE_PATH}{self.share_invite()}"

    def share_link(self) -> str:
        """L'adresse publique si le tunnel est ouvert, la locale sinon."""
        if self.tunnel_address:
            return self.tunnel_address + "/"
        if self.share_server is None:
            return ""
        return f"http://127.0.0.1:{self.share_server.port}/"

    def set_tunnel_kind(self, kind: str) -> None:
        """Change de chemin : l'adresse d'avant se ferme avec l'ancien."""
        if kind == self.cfg["tunnel_kind"]:
            return
        self.stop_tunnel()
        self.cfg["tunnel_kind"] = kind
        self.cfg.save()

    def tunnel_state(self) -> str:
        from .tunnel import find, fixed_state

        if self.cfg["tunnel_kind"] == "tailscale":
            if self.tunnel_address:
                return f"Adresse fixe ouverte : {self.tunnel_address}"
            if getattr(self, "_fixed_busy", False):
                return "Ouverture de l'adresse fixe…"
            ready, why = fixed_state()
            return why if not ready else f"Prêt — {why}"
        if not find():
            return "cloudflared n'est pas installé."
        if self.tunnel_address:
            return "Adresse publique ouverte."
        if self.tunnel is not None and self.tunnel.running:
            # Une relance en attente apres une chute compte comme « en
            # marche » : c'est alors la chute qu'il faut dire.
            return self.tunnel_trouble or "Ouverture de l'adresse publique…"
        return self.tunnel_trouble or "Adresse publique fermée."

    def open_share(self) -> None:
        """La fenetre du partage, sans bloquer le reste : une video de « Ce
        qui a ete regarde » s'ouvre dans le lecteur flottant, qu'une fenetre
        modale aurait rendu impossible a toucher."""
        from .share_dialog import ShareDialog
        dialog = getattr(self, "_share_dialog", None)
        if dialog is not None and shiboken6.isValid(dialog):
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()
            return
        dialog = ShareDialog(self, self)
        dialog.setModal(False)
        dialog.finished.connect(lambda _code: self._show_share_badge())
        self._share_dialog = dialog
        dialog.show()

    # L'adresse publique : chaque ecriture -- la fenetre, le dialogue du
    # partage -- met le temoin a jour.
    @property
    def tunnel_address(self) -> str:
        return self.__dict__.get("_tunnel_address", "")

    @tunnel_address.setter
    def tunnel_address(self, address: str) -> None:
        self.__dict__["_tunnel_address"] = address or ""
        self._show_share_badge()

    def _show_share_badge(self) -> None:
        """Le temoin de l'adresse publique. Il ne s'affiche plus : le compteur
        de spectateurs (l'oeil) dit ce qui compte, et le partage vit sur le NAS."""
        badge = self.__dict__.get("share_badge")
        if badge is None:
            return
        if not badge.isHidden():
            badge.hide()
        return
        address = self.tunnel_address
        if not address:
            if not badge.isHidden():
                badge.hide()
            return
        sessions = 0
        server = self.share_server
        guard = getattr(server, "guard", None)
        if guard is not None:
            now = time.time()
            try:
                sessions = sum(1 for until, _label in list(guard.sessions.values())
                               if until > now)
            except (RuntimeError, ValueError, TypeError):
                sessions = 0
        tip = (f"Adresse publique ouverte : {address}\n"
               + (f"{sessions} session(s) ouverte(s)\n" if sessions else
                  "Aucune session ouverte\n")
               + "Cliquer : Partage à distance…   ·   Clic droit : fermer l'adresse")
        if badge.toolTip() != tip:
            badge.setToolTip(tip)
        if badge.isHidden():
            badge.show()

    # -- en direct : qui regarde, et quoi ---------------------------------------
    def _poll_viewers(self) -> None:
        """Toutes les cinq secondes, hors du fil de l'interface : les visiteurs
        du partage du PC, et ceux du NAS (le fichier qu'il tient a jour)."""
        if self._viewers_busy or self._closing:
            return
        server = self.share_server
        if server is None and not self.viewers:
            # Ni partage du PC ni visiteur : le fichier du NAS se relit toutes
            # les 30 s au lieu de 5 -- six lectures du partage sur sept en
            # moins, quand personne ne regarde.
            self._viewers_quiet = getattr(self, "_viewers_quiet", 0) + 1
            if self._viewers_quiet % 6:
                return
        else:
            self._viewers_quiet = 0
        from .nas_publish import FOLDER, share_root
        from .tunnel import Chore
        root = share_root(self.top_root() or "") if self.root is not None else None

        def work():
            found = []
            if server is not None and hasattr(server, "viewers"):
                found += [dict(entry, where="PC") for entry in server.viewers()]
            if root is not None:
                live = root / FOLDER / "etat" / "en-direct.json"
                try:
                    told = json.loads(live.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    told = {}
                # Un fichier qui ne change plus depuis une minute : le serveur
                # du NAS s'est tu. Jugé à l'horloge du PC, quand on voit son
                # « at » changer : celle du NAS peut avoir des minutes d'écart,
                # et tous ses visiteurs passaient pour partis.
                now = time.time()
                stamp = float(told.get("at", 0) or 0)
                last = self._live_seen
                if last is None or last[0] != stamp:
                    fresh = last is not None or abs(now - stamp) < 60
                    self._live_seen = (stamp, now if fresh else now - 60)
                seen_at = self._live_seen[1]
                if stamp and now - seen_at < 60:
                    late = now - seen_at
                    for entry in told.get("viewers") or []:
                        if entry.get("playing"):
                            entry["at"] = float(entry.get("at", 0)) + late
                        found.append(dict(entry, where="NAS"))
            if time.time() - self._people_read > 30:
                # Les profils : qui est qui, quelle que soit l'etiquette.
                try:
                    self._people = self._read_people(root)
                except Exception:                       # noqa: BLE001
                    pass
                self._people_read = time.time()
            return found

        self._viewers_busy = True
        Chore(work, self, fallback=[], then=self._viewers_ready).start()

    def _viewers_ready(self, found: list) -> None:
        self._viewers_busy = False
        self.viewers = found or []
        self._follow_step()
        count = len(self.viewers)
        button = self.viewers_button
        text = str(count)
        if button.text() != text:
            button.setText(text)
            button.setIcon(icon("eye", "#7bd88f" if count else DIM))
        if count:
            lines = []
            for entry in self.viewers:
                what = (f"regarde « {entry.get('name')} »" if entry.get("name")
                        else entry.get("view") or "parcourt la bibliothèque")
                lines.append(f"• {self.alias(entry.get('label', ''))} "
                             f"({entry.get('where')}) : {what}")
            tip = (f"{count} personne(s) connectée(s)\n" + "\n".join(lines)
                   + "\n\nCliquer : voir, ou regarder avec elle.")
        else:
            tip = "Personne ne regarde la bibliothèque partagée en ce moment."
        if button.toolTip() != tip:
            button.setToolTip(tip)

    def _viewers_menu(self) -> None:
        menu = QMenu(self)
        if not self.viewers:
            menu.addAction("Personne n'est connecté en ce moment").setEnabled(False)
        for entry in self.viewers:
            head = menu.addAction(
                f"{self.alias(entry.get('label', ''))}  —  par le {entry.get('where')}")
            head.setEnabled(False)
            if entry.get("view"):
                # Ce qu'il a accepte de partager : ou il en est dans la page.
                menu.addAction(f"    {entry['view']}").setEnabled(False)
            if entry.get("name"):
                at = human_duration(entry.get("at", 0) or 0)
                state = "" if entry.get("playing") else " (en pause)"
                menu.addAction(icon("picture-in-picture-2"),
                               f"    Regarder avec : « {entry['name']} » à {at}{state}",
                               lambda e=dict(entry): self.watch_along(e))
            elif not entry.get("view"):
                menu.addAction("    parcourt la bibliothèque").setEnabled(False)
            following = self._following_key() == (entry.get("label"), entry.get("where"))
            follow = menu.addAction(
                "    Arrêter de le suivre" if following else
                "    Suivre en direct (le lecteur flottant suit sa vidéo)",
                (lambda: self.stop_following()) if following else
                (lambda e=dict(entry): self.follow_viewer(e)))
            follow.setEnabled(True)
            menu.addAction("    Nommer cette personne…",
                           lambda label=entry.get("label", ""): self.name_device(label))
        menu.addSeparator()
        menu.addAction("Ce qui a été regardé, les connexions…", self.open_share)
        menu.exec(self.viewers_button.mapToGlobal(
            self.viewers_button.rect().bottomLeft()))
        menu.deleteLater()

    def _read_people(self, root) -> dict:
        """Dans un fil : les profils du PC et du NAS, par etiquette."""
        from . import profils
        files = []
        server = self.share_server
        if server is not None and getattr(server, "profiles_path", None):
            files.append(Path(server.profiles_path))
        if root is not None:
            from .nas_publish import FOLDER
            files.append(root / FOLDER / "etat" / "profils.json")
        people: dict = {}
        for path in files:
            for me, profile in profils._read(path).items():
                if not isinstance(profile, dict):
                    continue
                labels = set(profile.get("labels") or [])
                labels |= {label for label in labels if f"({str(me)[:6]})" in label}
                if not labels:
                    continue
                name = str(profile.get("name") or "")
                for label in labels:
                    known = people.get(label)
                    merged = (known[1] | labels) if known else labels
                    people[label] = (name or (known[0] if known else ""), frozenset(merged))
        return people

    def alias(self, label: str) -> str:
        """Le nom de la personne : celui qu'on lui a donne (sur l'une de ses
        etiquettes), sinon celui de son profil, sinon l'etiquette."""
        aliases = self.cfg["share_aliases"] or {}
        if aliases.get(label):
            return aliases[label]
        person = self._people.get(label)
        if person:
            for other in person[1]:
                if aliases.get(other):
                    return aliases[other]
            if person[0]:
                return person[0]
        return label

    def follow_viewer(self, entry: dict) -> None:
        """Le lecteur flottant suit ce visiteur : sa video, a son instant,
        en pause quand il l'est -- relu a chaque nouvelle des visiteurs."""
        self._following = {"label": entry.get("label", ""), "where": entry.get("where", ""),
                           "video": "", "missed": 0}
        self.show_banner(f"Suivi en direct de {self.alias(entry.get('label', ''))} : le "
                         "lecteur flottant suit sa vidéo. L'œil, en haut, pour arrêter.",
                         "info", seconds=8)
        self._follow_step()

    def _share_badge_menu(self, where) -> None:
        menu = QMenu(self)
        menu.addAction("Partage à distance…", self.open_share)
        menu.addAction("Fermer l'adresse publique", self.stop_tunnel)
        menu.exec(self.share_badge.mapToGlobal(where))
        menu.deleteLater()
