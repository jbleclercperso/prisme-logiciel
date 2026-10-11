"""Les missions vidéo : « Vidéo à la demande », de petits travaux payés en bons
d'achat.

Prisme, sur le PC, publie des missions : un titre, une consigne, un prix (en
euros de bon d'achat Amazon), et combien de vidéos il en attend au plus. La
page des visiteurs les montre (Récompenses › « Voir les offres ») ; le
visiteur filme, envoie sa vidéo, et suit la réponse.

L'envoi se fait par morceaux (`CHUNK`) : un tunnel Cloudflare refuse les
corps de plus de cent méga-octets, et un téléphone en 4G qui décroche
reprend là où il en était, sans tout renvoyer. Chaque vidéo reçue est rangée
par le serveur sous un nom qu'il choisit lui-même (rien du nom envoyé
n'entre dans le chemin), à côté des demandes : chez Prisme, ou dans le
partage du NAS. Elle n'est jamais servie à personne : seul le PC la regarde.

Deux fichiers, à côté des demandes :

* `missions.json` -- les missions et les verdicts (validée, refusée, bon
  envoyé), écrits par le PC et réunis entre le PC et le NAS (le plus récent
  gagne) ;
* `missions-envois.jsonl` -- les envois, une ligne quand ils commencent, une
  quand ils sont complets, ajoutées par le serveur.

Sans Qt : le NAS s'en sert aussi.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path

FILE_NAME = "missions.json"
SENDS_NAME = "missions-envois.jsonl"
FOLDER_NAME = "missions-recues"
STATES = {"brouillon": "Brouillon", "ouverte": "Publiée", "close": "Close",
          "supprime": "Supprimée"}
VERDICTS = {"attente": "À examiner", "validee": "Validée — bon à envoyer",
            "payee": "Bon envoyé", "refusee": "Refusée"}
TITLE_MAX = 80
BRIEF_MAX = 3000
SITE_MAX = 80
NOTE_MAX = 300
PRICE_MAX = 1000
# Une video d'un telephone : quelques centaines de mega-octets, rarement plus.
SIZE_MAX = 4 * 1024 ** 3
CHUNK = 8 * 1024 * 1024
CHUNK_MAX = 16 * 1024 * 1024
VIDEO_EXT = {"mp4", "mov", "m4v", "webm", "3gp", "mkv", "avi"}
MIME_EXT = {"video/mp4": "mp4", "video/quicktime": "mov", "video/webm": "webm",
            "video/3gpp": "3gp", "video/x-matroska": "mkv", "video/x-m4v": "m4v",
            "video/x-msvideo": "avi"}
# Au plus tant d'envois en attente de reponse, par personne et par mission ;
# un envoi laisse en route plus d'un jour ne compte plus.
PENDING_MAX = 3
STALE = 24 * 3600

_lock = threading.Lock()
_sends: dict = {}


def paths_for(requests_path) -> tuple:
    """(missions, envois, dossier des videos recues), a cote des demandes."""
    if not requests_path:
        return None, None, None
    place = Path(requests_path)
    return (place.with_name(FILE_NAME), place.with_name(SENDS_NAME),
            place.with_name(FOLDER_NAME))


def new_id(prefix: str = "m") -> str:
    return prefix + secrets.token_hex(5)


# -- les missions : ecrites par le PC ------------------------------------------
def _text(value, limit: int, lines: bool = False) -> str:
    text = str(value or "")
    if lines:
        text = "\n".join(" ".join(line.split()) for line in text.splitlines())
        while "\n\n\n" in text:
            text = text.replace("\n\n\n", "\n\n")
        return text.strip()[:limit]
    return " ".join(text.split())[:limit]


def clean_mission(told: dict) -> dict | None:
    if not isinstance(told, dict):
        return None
    try:
        price = max(0, min(PRICE_MAX, int(told.get("price") or 0)))
    except (TypeError, ValueError):
        price = 0
    try:
        places = max(0, min(10_000, int(told.get("places") or 0)))
    except (TypeError, ValueError):
        places = 0
    now = time.time()
    return {
        "id": str(told.get("id") or new_id())[:20],
        "title": _text(told.get("title"), TITLE_MAX),
        "brief": _text(told.get("brief"), BRIEF_MAX, lines=True),
        # Pour quel site la video est realisee : la page le dit.
        "site": _text(told.get("site"), SITE_MAX),
        "price": price,
        # Combien de videos validees au plus (0 : sans limite).
        "places": places,
        "to": [str(key) for key in (told.get("to") or []) if str(key)][:500],
        "state": told.get("state") if told.get("state") in STATES else "brouillon",
        "created": float(told.get("created") or now),
        "sent": float(told.get("sent") or 0.0),
        "updated": float(told.get("updated") or told.get("created") or now),
    }


def clean_verdict(told: dict) -> dict | None:
    if not isinstance(told, dict) or told.get("state") not in VERDICTS:
        return None
    return {"state": told["state"], "note": _text(told.get("note"), NOTE_MAX),
            "at": float(told.get("at") or 0.0),
            "updated": float(told.get("updated") or told.get("at") or 0.0)}


def _read(path) -> dict | None:
    """{"missions": {id: mission}, "verdicts": {envoi: verdict}} ; None quand
    le fichier existe sans pouvoir etre lu (ne pas le recrire)."""
    if path is None:
        return {"missions": {}, "verdicts": {}}
    try:
        told = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"missions": {}, "verdicts": {}}
    except (OSError, ValueError):
        return None
    found = {"missions": {}, "verdicts": {}}
    if not isinstance(told, dict):
        return found
    for one in told.get("missions") or []:
        mission = clean_mission(one)
        if mission is not None:
            found["missions"][mission["id"]] = mission
    for key, one in (told.get("verdicts") or {}).items():
        verdict = clean_verdict(one)
        if verdict is not None:
            found["verdicts"][str(key)[:20]] = verdict
    return found


def load(path) -> dict:
    found = _read(path)
    return found if found is not None else {"missions": {}, "verdicts": {}}


def save(path, book: dict) -> bool:
    if path is None:
        return False
    body = {"version": 1,
            "missions": sorted(book["missions"].values(), key=lambda m: -m["created"]),
            "verdicts": book["verdicts"]}
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        spare = target.with_name(target.name + ".tmp")
        spare.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        os.replace(spare, target)
        return True
    except OSError:
        return False


def merge(*books) -> dict:
    found = {"missions": {}, "verdicts": {}}
    for book in books:
        for part in ("missions", "verdicts"):
            for key, one in (book or {}).get(part, {}).items():
                known = found[part].get(key)
                if known is None or one["updated"] > known["updated"]:
                    found[part][key] = one
    return found


def sync(paths: list) -> dict:
    """Reunit les copies (PC, NAS) et recrit celles qui different."""
    books = [_read(path) for path in paths]
    merged = merge(*[book for book in books if book])
    for path, book in zip(paths, books):
        if path is not None and book is not None and book != merged:
            save(path, merged)
    return merged


_loaded: dict = {}


def _book_cached(path) -> dict:
    """`load`, refait seulement quand le fichier change."""
    if path is None:
        return {"missions": {}, "verdicts": {}}
    try:
        stat = Path(path).stat()
        stamp = (stat.st_mtime, stat.st_size)
    except OSError:
        return {"missions": {}, "verdicts": {}}
    kept = _loaded.get(str(path))
    if kept is None or kept[0] != stamp:
        kept = (stamp, load(path))
        _loaded[str(path)] = kept
    return kept[1]


def for_me(mission: dict, me: str) -> bool:
    return mission["state"] == "ouverte" and (not mission["to"] or me in mission["to"])


# -- les envois : ajoutes par le serveur -----------------------------------------
def _fold(state: dict, entry: dict) -> None:
    send = str(entry.get("id") or "")
    if not send:
        return
    if entry.get("event") == "debut":
        state[send] = {"id": send, "mission": str(entry.get("mission") or ""),
                       "me": str(entry.get("me") or ""), "label": str(entry.get("label") or ""),
                       "name": str(entry.get("name") or ""),
                       "email": str(entry.get("email") or ""),
                       "file": str(entry.get("file") or ""), "size": int(entry.get("size") or 0),
                       "original": str(entry.get("original") or ""),
                       "at": float(entry.get("at") or 0.0), "done_at": 0.0}
    elif entry.get("event") == "fin" and send in state:
        state[send]["done_at"] = float(entry.get("at") or 0.0)


def sends(path) -> dict:
    """Tous les envois d'un fichier : {envoi: fiche}. Relu seulement quand il
    a grandi, et seulement la suite."""
    if path is None:
        return {}
    key = str(path)
    try:
        stat = Path(path).stat()
    except OSError:
        _sends.pop(key, None)
        return {}
    with _lock:
        kept = _sends.get(key)
        if kept is not None and kept[0] == stat.st_size and kept[1] == stat.st_mtime:
            return kept[2]
        start, state = 0, {}
        if kept is not None and kept[0] <= stat.st_size:
            start, state = kept[0], kept[2]
        try:
            with open(path, "rb") as handle:
                handle.seek(start)
                block = handle.read()
        except OSError:
            return kept[2] if kept is not None else {}
        end = block.rfind(b"\n") + 1
        for line in block[:end].decode("utf-8", "replace").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                _fold(state, entry)
        _sends[key] = (start + end, stat.st_mtime if end == len(block) else 0.0, state)
        return state


def _append(path, entry: dict) -> bool:
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return True
    except OSError:
        return False


def _validated(book: dict, all_sends: dict, mission_id: str) -> int:
    return sum(1 for send_id, send in all_sends.items()
               if send["mission"] == mission_id
               and book["verdicts"].get(send_id, {}).get("state") in ("validee", "payee"))


def offers(book_path, sends_path, me: str) -> list:
    """Les missions ouvertes a cette personne, avec ses propres envois."""
    if not me:
        return []
    book = _book_cached(book_path)
    all_sends = sends(sends_path)
    found = []
    for mission in sorted(book["missions"].values(), key=lambda m: -(m["sent"] or m["created"])):
        if not for_me(mission, me):
            continue
        mine = []
        for send_id, send in sorted(all_sends.items(), key=lambda kv: -kv[1]["at"]):
            if send["mission"] != mission["id"] or send["me"] != me:
                continue
            if not send["done_at"]:
                continue
            verdict = book["verdicts"].get(send_id) or {"state": "attente", "note": ""}
            mine.append({"id": send_id, "at": send["at"], "size": send["size"],
                         "state": verdict["state"], "note": verdict["note"]})
        full = bool(mission["places"]) and \
            _validated(book, all_sends, mission["id"]) >= mission["places"]
        found.append({"id": mission["id"], "title": mission["title"],
                      "brief": mission["brief"], "price": mission["price"],
                      "site": mission["site"], "full": full, "mine": mine})
    return found


def _ext(told: dict) -> str:
    name = str(told.get("name") or "")
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in VIDEO_EXT:
        return ext
    return MIME_EXT.get(str(told.get("type") or "").lower().split(";")[0], "")


def start(book_path, sends_path, folder, me: str, label: str, name: str, email: str,
          told: dict) -> dict:
    """Un envoi commence : {mission, size, name, type}. Rend {"ok", "id",
    "chunk"} ou {"ok": False, "error"}."""
    if not me:
        return {"ok": False, "error": "Appareil inconnu : rechargez la page."}
    if folder is None:
        return {"ok": False, "error": "Les vidéos ne sont pas reçues ici."}
    if not email:
        return {"ok": False, "error": "Ajoutez d'abord votre email (Récompenses › Modifier "
                                      "mon profil) : c'est là qu'arrivera votre bon."}
    book = _book_cached(book_path)
    mission = book["missions"].get(str(told.get("mission") or ""))
    if mission is None or not for_me(mission, me):
        return {"ok": False, "error": "Cette mission n'est plus proposée."}
    try:
        size = int(told.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    if size <= 0:
        return {"ok": False, "error": "Ce fichier est vide."}
    if size > SIZE_MAX:
        return {"ok": False, "error": "Cette vidéo dépasse 4 Go : raccourcissez-la, ou "
                                      "filmez en 1080p plutôt qu'en 4K."}
    ext = _ext(told)
    if not ext:
        return {"ok": False, "error": "Ce fichier n'est pas une vidéo (mp4, mov, webm…)."}
    now = time.time()
    all_sends = sends(sends_path)
    if mission["places"] and _validated(book, all_sends, mission["id"]) >= mission["places"]:
        return {"ok": False, "error": "Cette mission est complète : merci !"}
    waiting = 0
    for send_id, send in all_sends.items():
        if send["mission"] != mission["id"] or send["me"] != me:
            continue
        verdict = book["verdicts"].get(send_id, {}).get("state", "attente")
        if verdict == "attente" and (send["done_at"] or now - send["at"] < STALE):
            waiting += 1
    if waiting >= PENDING_MAX:
        return {"ok": False, "error": f"Déjà {waiting} vidéos en attente de réponse pour "
                                      "cette mission : attendez notre retour."}
    send_id = new_id("e")
    rel = f"{mission['id']}/{time.strftime('%Y-%m-%d_%H%M%S')}_{send_id}.{ext}"
    target = Path(folder) / rel
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"")
    except OSError as trouble:
        return {"ok": False, "error": f"Impossible de recevoir la vidéo : {trouble}"}
    if not _append(sends_path, {"event": "debut", "id": send_id, "mission": mission["id"],
                                "me": me, "label": str(label or "")[:80],
                                "name": str(name or "")[:40], "email": str(email or "")[:120],
                                "file": rel, "size": size,
                                "original": str(told.get("name") or "")[:120],
                                "at": round(now, 3)}):
        return {"ok": False, "error": "L'envoi n'a pas pu être noté."}
    return {"ok": True, "id": send_id, "chunk": CHUNK, "have": 0}


def put(sends_path, folder, me: str, send_id: str, offset: int, data: bytes) -> dict:
    """Un morceau de l'envoi `send_id`, a la position `offset`. Rend
    {"ok", "have", "done"} ; si la position n'est pas celle attendue,
    {"ok": False, "have": …} : la page reprend de la."""
    send = sends(sends_path).get(str(send_id or ""))
    if send is None or send["me"] != me or folder is None:
        return {"ok": False, "error": "Envoi inconnu : recommencez."}
    if send["done_at"]:
        return {"ok": True, "have": send["size"], "done": True, "already": True}
    target = Path(folder) / send["file"]
    with _lock:
        try:
            have = target.stat().st_size
        except OSError:
            return {"ok": False, "error": "Envoi perdu : recommencez."}
        if offset != have:
            return {"ok": False, "have": have, "retry": True}
        if have + len(data) > send["size"]:
            return {"ok": False, "error": "Le fichier ne fait pas la taille annoncée."}
        try:
            with open(target, "ab") as handle:
                handle.write(data)
        except OSError as trouble:
            return {"ok": False, "error": f"Écriture impossible : {trouble}"}
        have += len(data)
        done = have >= send["size"]
        if done:
            _append(sends_path, {"event": "fin", "id": send["id"], "at": round(time.time(), 3)})
    return {"ok": True, "have": have, "done": done}


# -- pour Prisme -----------------------------------------------------------------
def received(sources: list) -> list:
    """[(« PC » ou « NAS », envois, dossier)] -> les envois, le plus recent
    d'abord, chacun avec `where` et le chemin complet de sa video."""
    found = []
    for where, sends_path, folder in sources:
        for send in sends(sends_path).values():
            found.append(dict(send, where=where,
                              path=str(Path(folder) / send["file"]) if folder else ""))
    found.sort(key=lambda one: -one["at"])
    return found
