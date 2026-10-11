"""Les sondages : envoyés par Prisme, proposés par la page, les réponses, le bonus.

    python tests/test_sondages.py
"""
from __future__ import annotations

import http.client
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

BOX = os.path.join(tempfile.gettempdir(), "prisme-test-sondages")
shutil.rmtree(BOX, ignore_errors=True)
os.environ["PRISME_SANDBOX"] = BOX
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.stdout.reconfigure(encoding="utf-8")

from videosorter import profils, sondages  # noqa: E402
from videosorter.web import Library, Server, new_invite  # noqa: E402

FAILS = []


def check(condition, label: str) -> None:
    print(("  ok   " if condition else "  ÉCHEC ") + label)
    if not condition:
        FAILS.append(label)


def main() -> int:
    root = Path(BOX) / "coll"
    root.mkdir(parents=True)
    files = []
    for n in range(6):
        path = root / f"video {n}.mp4"
        path.write_bytes(b"\0" * 64)
        files.append(path)
    marks = [Library.mark(path) for path in files]
    key = new_invite()
    box = Path(BOX) / "etat" / "demandes.jsonl"
    server = Server(root, "", "", port=0, invite=key)
    server.requests_path = box
    server.library.videos = dict(zip(marks, files))
    surveys_path, answers_path = server.survey_paths
    check(surveys_path.name == "sondages.json" and answers_path.parent == box.parent,
          "les fichiers des sondages vivent à côté des demandes")
    me, other = "a" * 32, "b" * 32
    notes = sondages.clean({"kind": "notes", "title": "Vos préférées", "reward": 5,
                            "state": "ouvert", "sent": 1.0,
                            "source": "  Le   Magazine ",
                            "items": [{"mark": m, "name": f"video {i}.mp4"}
                                      for i, m in enumerate(marks[:3])]
                            + [{"mark": "f" * 16, "name": "absente du serveur"}]})
    duel = sondages.clean({"kind": "duel", "title": "Laquelle ?", "reward": 3,
                           "state": "ouvert", "to": [other], "sent": 2.0,
                           "items": [{"a": {"mark": marks[3]}, "b": {"mark": marks[4]}}]})
    draft = sondages.clean({"kind": "notes", "title": "Pas encore", "state": "brouillon",
                            "items": [{"mark": marks[5]}]})
    check(sondages.save(surveys_path, [notes, duel, draft]), "Prisme écrit les sondages")
    port = server.start()

    def call(method, route, payload=None, who=me):
        link = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        link.request(method, route, body=body,
                     headers={"Content-Type": "application/json", "X-Prisme-Cle": key,
                              "X-Prisme-Moi": who})
        answer = link.getresponse()
        return answer.status, json.loads(answer.read() or b"{}")

    try:
        print("\n[1] Ce que la page propose")
        link = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        link.request("GET", "/")
        page = link.getresponse().read().decode("utf-8")
        check("organisateur" not in page.split("function showPollIntro")[1].split("function firstOpen")[0]
              and "Sondage réalisé pour" in page,
              "le sondage annonce son magazine, plus « l'organisateur »")
        check('id="poll"' in page and 'id="toast"' in page and 'id="vol"' in page,
              "la page porte le sondage, la notification et le curseur de volume")
        code, said = call("GET", "/api/sondages")
        found = said.get("surveys") or []
        check(code == 200 and [s["id"] for s in found] == [notes["id"]],
              "seul le sondage envoyé à tout le monde est proposé (ni brouillon, ni celui d'un autre)")
        check(len(found[0]["items"]) == 3 and found[0]["items"][0]["v"]["name"] == "video 0.mp4",
              "la vidéo que ce serveur n'a pas est écartée, les autres viennent avec leur nom")
        check(found[0].get("source") == "Le Magazine",
              "la page apprend pour quel magazine le sondage est réalisé")
        code, said = call("GET", "/api/sondages", who=other)
        check(sorted(s["kind"] for s in said.get("surveys") or []) == ["duel", "notes"],
              "la personne visée reçoit aussi le duel")
        code, said = call("POST", "/api/ici", {"view": "parcourt"})
        check(said.get("sondages") == [notes["id"]], "le signe de vie annonce le sondage en attente")

        print("\n[2] Répondre")
        items = found[0]["items"]
        code, said = call("POST", "/api/sondage", {"survey": notes["id"], "item": items[0]["id"],
                                                   "value": 9})
        check(code == 400 and not said.get("ok"), "une note hors de 1 à 5 est refusée")
        code, said = call("POST", "/api/sondage", {"survey": notes["id"], "item": items[0]["id"],
                                                   "value": 2, "seconds": 7.5})
        check(said.get("ok") and said.get("answered") == 1 and not said.get("done"),
              "une note est gardée (1 / 3)")
        code, said = call("POST", "/api/sondage", {"survey": notes["id"], "item": items[0]["id"],
                                                   "value": 4})
        check(said.get("answered") == 1, "changer d'avis ne compte pas deux fois")
        code, said = call("GET", "/api/sondages")
        check(said["surveys"][0]["answers"] == {items[0]["id"]: 4},
              "en revenant, la page retrouve la réponse déjà donnée")
        code, before = call("GET", "/api/profil")
        call("POST", "/api/sondage", {"survey": notes["id"], "item": items[1]["id"], "value": 5})
        code, said = call("POST", "/api/sondage", {"survey": notes["id"],
                                                   "item": items[2]["id"], "value": 1})
        check(said.get("done") and said.get("reward") == 5, "la dernière note finit le sondage")
        check(said.get("seen") == before.get("seen", 0) + 5,
              f"un sondage fini compte pour 5 vidéos ({before.get('seen')} → {said.get('seen')})")
        code, said = call("GET", "/api/sondages")
        check(not said.get("surveys"), "un sondage fini n'est plus proposé")
        code, said = call("POST", "/api/sondage", {"survey": notes["id"], "item": items[2]["id"],
                                                   "value": 3})
        check(said.get("already"), "y répondre encore ne redonne pas le bonus")
        check(sondages.bonus(answers_path, me) == 5, "le bonus est compté une seule fois")
        code, said = call("POST", "/api/sondage", {"survey": duel["id"], "item":
                                                   duel["items"][0]["id"], "value": "a"})
        check(not said.get("ok"), "le duel d'un autre ne se répond pas")
        code, said = call("POST", "/api/sondage", {"survey": duel["id"], "item":
                                                   duel["items"][0]["id"], "value": "b"},
                          who=other)
        check(said.get("done") and said.get("reward") == 3, "le duel se joue en un choix")

        print("\n[3] Le rapport, chez Prisme")
        rows = sondages.reports([("PC", answers_path)])
        mine = rows[notes["id"]][0]
        check(mine["me"] == me and mine["done_at"] and mine["where"] == "PC",
              "le rapport dit qui a fini, et par quel serveur")
        check(mine["votes"][items[0]["id"]]["value"] == 4
              and mine["votes"][items[0]["id"]]["seconds"] == 0.0,
              "la dernière réponse compte")
        check(sondages.summary(duel, rows[duel["id"]]) == {duel["items"][0]["id"]: {"a": 0, "b": 1}},
              "la synthèse du duel compte les choix")
        check(sondages.bonuses([("PC", answers_path)]) == {("PC", me): 5, ("PC", other): 3},
              "les bonus de chacun, pour les fiches des visiteurs")
        closed = dict(notes, state="clos")
        sondages.save(surveys_path, [closed, duel])
        code, said = call("GET", "/api/sondages", who=other)
        check(not said.get("surveys"), "un sondage clos n'est plus proposé")
        check(profils.GOAL > 0, "les règles du bon d'achat sont là")

        print("\n[4] Les sondages en texte")
        choix = sondages.clean({"kind": "choix", "title": "Cuisine", "state": "ouvert",
                                "sent": 3.0, "items": [
                                    {"id": "q1", "text": "Tajine de poulet",
                                     "detail": "plat marocain"}, {"id": "q2", "text": "Paella"}]})
        echelle = sondages.clean({"kind": "echelle", "title": "Envies", "state": "ouvert",
                                  "sent": 4.0, "ends": ["Bof", "Carrément"],
                                  "items": [{"id": "e1", "text": "Massage"}]})
        check(choix["options"] == sondages.DEFAULT_OPTIONS,
              "les réponses par défaut : déjà essayé / aimerais essayer / pas intéressé")
        sondages.save(surveys_path, [choix, echelle])
        code, said = call("GET", "/api/sondages")
        texts = {s["kind"]: s for s in said.get("surveys") or []}
        check(texts.get("choix", {}).get("items", [{}])[0].get("detail") == "plat marocain"
              and texts["echelle"]["ends"] == ["Bof", "Carrément"],
              "la page reçoit les questions, les réponses et les bouts de l'échelle")
        code, said = call("POST", "/api/sondage", {"survey": choix["id"], "item": "q1",
                                                   "value": 3})
        check(code == 400, "une réponse qui n'est pas proposée est refusée")
        call("POST", "/api/sondage", {"survey": choix["id"], "item": "q1", "value": 0})
        code, said = call("POST", "/api/sondage", {"survey": choix["id"], "item": "q2",
                                                   "value": 2})
        check(said.get("done"), "deux réponses finissent le sondage à choix")
        code, said = call("POST", "/api/sondage", {"survey": echelle["id"], "item": "e1",
                                                   "value": 0})
        check(said.get("done"), "0 est une réponse de l'échelle")
        rows = sondages.reports([("PC", answers_path)])
        check(sondages.summary(choix, rows[choix["id"]]) == {"q1": [1, 0, 0], "q2": [0, 0, 1]},
              "la synthèse compte chaque réponse")
        check(sondages.summary(echelle, rows[echelle["id"]]) == {"e1": [0]},
              "la synthèse de l'échelle garde les valeurs")
    finally:
        server.stop()
    print("\n" + ("ÉCHECS : " + ", ".join(FAILS) if FAILS else "Tout est vert."))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
