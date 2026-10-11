"""Les sondages envoyés aux visiteurs : des vidéos à noter, ou des duels A / B.

Prisme, sur le PC, les prépare (« Sondages… ») et les écrit dans
`sondages.json`, à côté des demandes : chez lui (la page servie par le PC) et
dans le partage du NAS (la page servie par le NAS). Le serveur qui sert la
page ne fait que les lire ; les réponses, il les ajoute une par ligne dans
`sondages-reponses.jsonl`, au même endroit. Prisme lit les deux fichiers de
réponses et en fait le rapport de chaque personne.

Deux sortes :

* **notes** : une liste de vidéos, chacune notée de 1 (pas du tout) à 5
  (j'adore) ;
* **duel** : des paires de vidéos, lues ensemble ; on garde l'une des deux.

Un sondage fini compte pour `reward` vidéos regardées, dans le compteur du
bon d'achat (`bonus`). Chaque réponse s'écrit tout de suite : une personne qui
s'arrête en route reprend où elle en était, et le rapport la montre « en
cours ».

Sans Qt : le NAS s'en sert aussi.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path

FILE_NAME = "sondages.json"
ANSWERS_NAME = "sondages-reponses.jsonl"
KINDS = {"notes": "Noter des vidéos", "duel": "Duels A ou B",
         "choix": "Questions à choix", "echelle": "Échelle de 0 à 5"}
# Les sondages sans video : une question (un plat, une pratique, une idée…),
# et des reponses fermees.
TEXT_KINDS = ("choix", "echelle")
# Les reponses d'une « question a choix » : celles-ci par defaut ; le PC peut
# en proposer d'autres (de deux a six).
DEFAULT_OPTIONS = ["J'ai déjà essayé", "J'aimerais bien essayer", "Ça ne m'intéresse pas"]
OPTIONS_MIN, OPTIONS_MAX, OPTION_MAX = 2, 6, 40
# Les deux bouts de l'echelle.
DEFAULT_ENDS = ["Pas du tout intéressé·e", "Carrément intéressé·e"]
SCALE = (0, 1, 2, 3, 4, 5)
QUESTION_MAX = 200
DETAIL_MAX = 400
STATES = {"brouillon": "Brouillon", "ouvert": "Envoyé", "clos": "Clos",
          # Gardé effacé : un autre PC qui l'aurait encore ne le ramène pas.
          "supprime": "Supprimé"}
# Ce que vaut un sondage fini, en vidéos regardées (le bon d'achat).
REWARD = 5
REWARD_MAX = 50
MAX_ITEMS = 60
TITLE_MAX = 80
MESSAGE_MAX = 300
SOURCE_MAX = 80
NOTES = (1, 2, 3, 4, 5)
SIDES = ("a", "b")

_lock = threading.Lock()
_vote_lock = threading.Lock()
# Les reponses lues, par fichier : (taille lue, date, etat replie). Le
# fichier ne fait que grandir : on reprend la ou l'on s'etait arrete.
_answers: dict = {}


def paths_for(requests_path) -> tuple:
    """(sondages, réponses) : à côté du fichier des demandes ; (None, None)
    si ce serveur n'en reçoit pas."""
    if not requests_path:
        return None, None
    place = Path(requests_path)
    return place.with_name(FILE_NAME), place.with_name(ANSWERS_NAME)


def new_id(prefix: str = "s") -> str:
    return prefix + secrets.token_hex(5)


# -- les sondages : écrits par le PC, lus par les serveurs ---------------------
def _clean_video(told) -> dict | None:
    if not isinstance(told, dict):
        return None
    mark = str(told.get("mark") or "")
    if not (len(mark) == 16 and all(c in "0123456789abcdef" for c in mark)):
        return None
    return {"mark": mark, "name": str(told.get("name") or "")[:300]}


def clean(survey: dict) -> dict | None:
    """Le sondage tel qu'on le garde : ce qui ne se lit pas est écarté."""
    if not isinstance(survey, dict):
        return None
    kind = survey.get("kind") if survey.get("kind") in KINDS else None
    if kind is None:
        return None
    items = []
    seen_ids = set()
    for told in (survey.get("items") or [])[:MAX_ITEMS]:
        if not isinstance(told, dict):
            continue
        item_id = str(told.get("id") or "")[:20] or new_id("i")
        while item_id in seen_ids:
            item_id = new_id("i")
        if kind in TEXT_KINDS:
            text = " ".join(str(told.get("text") or "").split())[:QUESTION_MAX]
            if not text:
                continue
            items.append({"id": item_id, "text": text,
                          "detail": " ".join(str(told.get("detail") or "").split())
                          [:DETAIL_MAX]})
        elif kind == "notes":
            video = _clean_video(told)
            if video is None:
                continue
            items.append(dict(video, id=item_id))
        else:
            first, second = _clean_video(told.get("a")), _clean_video(told.get("b"))
            if first is None or second is None:
                continue
            items.append({"id": item_id, "a": first, "b": second})
        seen_ids.add(item_id)
    try:
        reward = max(0, min(REWARD_MAX, int(survey.get("reward", REWARD))))
    except (TypeError, ValueError):
        reward = REWARD
    state = survey.get("state") if survey.get("state") in STATES else "brouillon"
    options = [" ".join(str(one or "").split())[:OPTION_MAX]
               for one in (survey.get("options") or [])]
    options = [one for one in options if one][:OPTIONS_MAX]
    if len(options) < OPTIONS_MIN:
        options = list(DEFAULT_OPTIONS)
    ends = [" ".join(str(one or "").split())[:OPTION_MAX]
            for one in (survey.get("ends") or [])][:2]
    if len(ends) != 2:
        ends = list(DEFAULT_ENDS)
    ends = [one or default for one, default in zip(ends, DEFAULT_ENDS)]
    return {
        "id": str(survey.get("id") or new_id())[:20],
        "kind": kind,
        "title": " ".join(str(survey.get("title") or "").split())[:TITLE_MAX],
        "message": " ".join(str(survey.get("message") or "").split())[:MESSAGE_MAX],
        # Pour qui il est realise (le magazine) : la page le dit aux visiteurs.
        "source": " ".join(str(survey.get("source") or "").split())[:SOURCE_MAX],
        "reward": reward,
        # Les cles des profils a qui il s'adresse ; vide : tout le monde.
        "to": [str(key) for key in (survey.get("to") or []) if str(key)][:500],
        "state": state,
        "created": float(survey.get("created") or time.time()),
        "sent": float(survey.get("sent") or 0.0),
        "closed": float(survey.get("closed") or 0.0),
        # La derniere retouche : entre deux copies, la plus recente gagne.
        "updated": float(survey.get("updated") or survey.get("created") or time.time()),
        "items": items,
        # Questions a choix : les reponses proposees ; echelle : ses deux bouts.
        "options": options,
        "ends": ends,
    }


def _order(surveys: list) -> list:
    return sorted(surveys, key=lambda one: (-(one["sent"] or one["created"]), one["id"]))


def load(path) -> list:
    """Les sondages de ce fichier, du plus récent au plus ancien."""
    found = _read(path)
    return found if found is not None else []


def _read(path) -> list | None:
    """Comme `load`, mais None quand le fichier existe sans pouvoir être lu
    (un NAS qui hoquette) : il ne faut pas alors le récrire."""
    if path is None:
        return []
    try:
        told = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        return None
    found = []
    for one in (told.get("surveys") if isinstance(told, dict) else None) or []:
        survey = clean(one)
        if survey is not None:
            found.append(survey)
    return _order(found)


def merge(*lists) -> list:
    """Les sondages de plusieurs copies : pour chacun, la plus récente."""
    best: dict = {}
    for surveys in lists:
        for survey in surveys or []:
            known = best.get(survey["id"])
            if known is None or survey["updated"] > known["updated"]:
                best[survey["id"]] = survey
    return _order(list(best.values()))


def sync(paths: list) -> list:
    """Réunit les sondages de ces fichiers (le PC, le NAS) et récrit ceux
    qui diffèrent. Deux PC publient sur le même NAS : recopier l'un sur
    l'autre effaçait les sondages du second."""
    lists = [_read(path) for path in paths]
    merged = merge(*[found for found in lists if found])
    for path, found in zip(paths, lists):
        if path is not None and found is not None and found != merged:
            save(path, merged)
    return merged


def save(path, surveys: list) -> bool:
    """D'un bloc : le serveur ne lit jamais un fichier à moitié écrit."""
    if path is None:
        return False
    kept = [one for one in (clean(survey) for survey in surveys) if one is not None]
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        spare = target.with_name(target.name + ".tmp")
        spare.write_text(json.dumps({"version": 1, "surveys": kept}, ensure_ascii=False),
                         encoding="utf-8")
        os.replace(spare, target)
        return True
    except OSError:
        return False


_loaded: dict = {}


def _surveys_cached(path) -> list:
    """`load`, refait seulement quand le fichier a changé : la page le demande
    à chaque signe de vie."""
    if path is None:
        return []
    try:
        stat = Path(path).stat()
        stamp = (stat.st_mtime, stat.st_size)
    except OSError:
        return []
    kept = _loaded.get(str(path))
    if kept is None or kept[0] != stamp:
        kept = (stamp, load(path))
        _loaded[str(path)] = kept
    return kept[1]


def for_me(survey: dict, me: str) -> bool:
    return survey["state"] == "ouvert" and (not survey["to"] or me in survey["to"])


def playable(survey: dict, known) -> list:
    """Les questions dont ce serveur connaît les vidéos : une vidéo que le
    NAS n'a pas (pas encore publiée, masquée) ne peut pas se noter là-bas."""
    if survey["kind"] in TEXT_KINDS:
        return list(survey["items"])
    if survey["kind"] == "notes":
        return [item for item in survey["items"] if item["mark"] in known]
    return [item for item in survey["items"]
            if item["a"]["mark"] in known and item["b"]["mark"] in known]


# -- les réponses : ajoutées par le serveur, une par ligne -----------------------
def _fold_line(state: dict, entry: dict) -> None:
    """Une ligne de plus dans l'état replié : (sondage, personne) -> fiche."""
    survey, me = str(entry.get("survey") or ""), str(entry.get("me") or "")
    if not survey or not me:
        return
    one = state.setdefault((survey, me), {
        "survey": survey, "me": me, "label": "", "name": "", "votes": {},
        "first_at": 0.0, "last_at": 0.0, "done_at": 0.0, "reward": 0})
    at = float(entry.get("at") or 0.0)
    if entry.get("label"):
        one["label"] = str(entry["label"])
    if entry.get("name"):
        one["name"] = str(entry["name"])
    one["first_at"] = min(one["first_at"] or at, at)
    one["last_at"] = max(one["last_at"], at)
    if entry.get("done"):
        if not one["done_at"]:
            one["done_at"] = at
            one["reward"] = int(entry.get("reward") or 0)
        return
    item = str(entry.get("item") or "")
    if item:
        one["votes"][item] = {"value": entry.get("value"), "at": at,
                              "seconds": float(entry.get("seconds") or 0.0)}


def answers(path) -> dict:
    """Toutes les réponses d'un fichier, repliées : (sondage, personne) ->
    {votes: {question: {value, at, seconds}}, done_at, reward, name, label…}.
    Lu une fois, puis seulement ce qui s'est ajouté."""
    if path is None:
        return {}
    key = str(path)
    try:
        stat = Path(path).stat()
    except OSError:
        _answers.pop(key, None)
        return {}
    with _lock:
        kept = _answers.get(key)
        if kept is not None and kept[0] == stat.st_size and kept[1] == stat.st_mtime:
            return kept[2]
        start, state = 0, {}
        if kept is not None and kept[0] <= stat.st_size:
            # Le fichier a grandi : la suite seulement.
            start, state = kept[0], kept[2]
        try:
            with open(path, "rb") as handle:
                handle.seek(start)
                block = handle.read()
        except OSError:
            return kept[2] if kept is not None else {}
        # Une ligne pas encore finie d'ecrire attend le tour suivant.
        end = block.rfind(b"\n") + 1
        for line in block[:end].decode("utf-8", "replace").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                _fold_line(state, entry)
        _answers[key] = (start + end, stat.st_mtime if end == len(block) else 0.0, state)
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


def bonus(path, me: str) -> int:
    """Les vidéos que valent les sondages finis de cette personne."""
    if not me:
        return 0
    return sum(one["reward"] for (_survey, who), one in answers(path).items()
               if who == me and one["done_at"])


def offer(surveys_path, answers_path, me: str, known) -> list:
    """Les sondages ouverts à cette personne, et pas encore finis : ce que la
    page propose. Chaque question y garde la réponse déjà donnée."""
    if not me:
        return []
    mine = answers(answers_path)
    found = []
    for survey in _surveys_cached(surveys_path):
        if not for_me(survey, me):
            continue
        items = playable(survey, known)
        if not items:
            continue
        one = mine.get((survey["id"], me)) or {}
        if one.get("done_at"):
            continue
        votes = one.get("votes") or {}
        found.append({"survey": survey, "items": items,
                      "answers": {item["id"]: votes[item["id"]]["value"]
                                  for item in items if item["id"] in votes}})
    return found


def pending(surveys_path, answers_path, me: str, known) -> list:
    """Les identifiants des sondages qui attendent cette personne."""
    return [entry["survey"]["id"] for entry in offer(surveys_path, answers_path, me, known)]


def vote(surveys_path, answers_path, me: str, label: str, name: str, told: dict,
         known) -> dict:
    """Une réponse de la page : {survey, item, value, seconds}. Rend
    {"ok": True, "answered", "total", "done", "reward"} ou {"ok": False, "error"}."""
    if not me:
        return {"ok": False, "error": "Appareil inconnu : rechargez la page."}
    if answers_path is None:
        return {"ok": False, "error": "Les sondages ne sont pas reçus ici."}
    survey_id = str(told.get("survey") or "")
    survey = next((one for one in _surveys_cached(surveys_path) if one["id"] == survey_id),
                  None)
    if survey is None or not for_me(survey, me):
        return {"ok": False, "error": "Ce sondage est terminé."}
    items = playable(survey, known)
    item_id = str(told.get("item") or "")
    if not any(item["id"] == item_id for item in items):
        return {"ok": False, "error": "Question inconnue : rechargez la page."}
    value = told.get("value")
    if survey["kind"] in ("notes", "echelle", "choix"):
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = -1
        allowed = {"notes": NOTES, "echelle": SCALE,
                   "choix": range(len(survey["options"]))}[survey["kind"]]
        if value not in allowed:
            return {"ok": False, "error": "Cette réponse n'est pas proposée."}
    elif value not in SIDES:
        return {"ok": False, "error": "Choisissez A ou B."}
    try:
        seconds = max(0.0, min(36000.0, float(told.get("seconds") or 0.0)))
    except (TypeError, ValueError):
        seconds = 0.0
    now = round(time.time(), 3)
    # Deux reponses de la meme personne en meme temps (deux onglets) ne
    # doivent pas finir le sondage deux fois.
    with _vote_lock:
        state = answers(answers_path).get((survey_id, me)) or {}
        if state.get("done_at"):
            return {"ok": True, "answered": len(items), "total": len(items), "done": True,
                    "reward": state.get("reward", 0), "already": True}
        entry = {"at": now, "survey": survey_id, "me": me, "label": str(label or "")[:80],
                 "name": str(name or "")[:40], "item": item_id, "value": value,
                 "seconds": round(seconds, 1)}
        if not _append(answers_path, entry):
            return {"ok": False, "error": "La réponse n'a pas pu être enregistrée."}
        votes = dict(state.get("votes") or {})
        votes[item_id] = {"value": value}
        answered = sum(1 for item in items if item["id"] in votes)
        done = answered >= len(items)
        if done:
            _append(answers_path, {"at": now, "survey": survey_id, "me": me,
                                   "label": str(label or "")[:80],
                                   "name": str(name or "")[:40],
                                   "done": True, "reward": survey["reward"]})
    return {"ok": True, "answered": answered, "total": len(items), "done": done,
            "reward": survey["reward"] if done else 0}


# -- le rapport, pour Prisme ------------------------------------------------------
def reports(sources: list) -> dict:
    """[(« PC » ou « NAS », fichier des réponses)] -> {sondage: [fiches]},
    chaque fiche avec `where`, la plus récente d'abord."""
    found: dict = {}
    for where, path in sources:
        for (survey, _me), one in answers(path).items():
            found.setdefault(survey, []).append(dict(one, where=where))
    for rows in found.values():
        rows.sort(key=lambda one: -one["last_at"])
    return found


def summary(survey: dict, rows: list) -> dict:
    """La synthèse d'un sondage : pour chaque question, ce qu'en ont dit les
    participants. notes, echelle : {question: [valeurs]} ; duel : {question:
    {a: n, b: n}} ; choix : {question: [compte de chaque reponse]}."""
    by_item: dict = {}
    for row in rows:
        for item_id, vote_ in (row.get("votes") or {}).items():
            value = vote_.get("value")
            if survey["kind"] == "choix":
                if isinstance(value, int) and 0 <= value < len(survey["options"]):
                    counts = by_item.setdefault(item_id, [0] * len(survey["options"]))
                    counts[value] += 1
            elif survey["kind"] in ("notes", "echelle"):
                if value in (NOTES if survey["kind"] == "notes" else SCALE):
                    by_item.setdefault(item_id, []).append(value)
            elif value in SIDES:
                counts = by_item.setdefault(item_id, {"a": 0, "b": 0})
                counts[value] += 1
    return by_item


def bonuses(sources: list) -> dict:
    """{(« PC » ou « NAS », clé du profil): vidéos gagnées par les sondages}."""
    found: dict = {}
    for where, path in sources:
        for (_survey, me), one in answers(path).items():
            if one["done_at"]:
                found[(where, me)] = found.get((where, me), 0) + one["reward"]
    return found
