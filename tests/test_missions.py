"""Les missions vidéo (envoi par morceaux, verdicts) et ce que le téléphone ne voit pas.

    python tests/test_missions.py
"""
from __future__ import annotations

import http.client
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

BOX = os.path.join(tempfile.gettempdir(), "prisme-test-missions")
shutil.rmtree(BOX, ignore_errors=True)
os.environ["PRISME_SANDBOX"] = BOX
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.stdout.reconfigure(encoding="utf-8")

from videosorter import demandes, missions, profils, telcache  # noqa: E402
from videosorter.web import Server, new_invite  # noqa: E402

FAILS = []


def check(condition, label: str) -> None:
    print(("  ok   " if condition else "  ÉCHEC ") + label)
    if not condition:
        FAILS.append(label)


def main() -> int:
    root = Path(BOX) / "coll"
    root.mkdir(parents=True)
    key = new_invite()
    etat = Path(BOX) / "etat"
    etat.mkdir(parents=True)
    me, nomail = "a" * 32, "b" * 32
    (etat / profils.FILE_NAME).write_text(json.dumps({
        me: {"name": "Camille", "email": "camille@exemple.fr", "labels": [], "at": 1.0},
        nomail: {"name": "Léo", "email": "", "labels": [], "at": 1.0}}), encoding="utf-8")
    server = Server(root, "", "", port=0, invite=key)
    server.requests_path = etat / "demandes.jsonl"
    book_path, sends_path, folder = server.mission_paths
    now = time.time()
    ice = missions.clean_mission({"title": "Ice challenge", "brief": "Filmez-vous de face.\n"
                                  "Au moins une minute.", "price": 20, "state": "ouverte",
                                  "sent": now, "places": 1, "site": " Le  Site "})
    draft = missions.clean_mission({"title": "Pas encore", "price": 5})
    missions.save(book_path, {"missions": {ice["id"]: ice, draft["id"]: draft},
                              "verdicts": {}})
    port = server.start()

    def call(method, route, payload=None, who=me, raw=None):
        link = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        body = raw if raw is not None else (json.dumps(payload).encode("utf-8")
                                            if payload is not None else None)
        link.request(method, route, body=body, headers={
            "Content-Type": "application/octet-stream" if raw is not None
            else "application/json", "X-Prisme-Cle": key, "X-Prisme-Moi": who})
        answer = link.getresponse()
        return answer.status, json.loads(answer.read() or b"{}")

    try:
        print("\n[1] Les offres")
        code, said = call("GET", "/api/missions")
        found = said.get("missions") or []
        check(code == 200 and [m["title"] for m in found] == ["Ice challenge"],
              "seule la mission publiée est proposée")
        check(found and "\n" in found[0]["brief"] and found[0]["price"] == 20,
              "avec sa consigne (retours à la ligne gardés) et son prix")
        check(said.get("email") == "camille@exemple.fr", "la page sait où ira le bon")
        check(found and found[0].get("site") == "Le Site",
              "et pour quel site la vidéo est réalisée")

        print("\n[2] Envoyer une vidéo, par morceaux")
        video = os.urandom(3 * 1024 * 1024 + 123)
        code, said = call("POST", "/api/mission", {"mission": ice["id"], "size": len(video),
                                                   "name": "défi.mp4", "type": "video/mp4"},
                          who=nomail)
        check(code == 400 and "email" in said.get("error", ""), "sans email : refusé, et dit")
        code, said = call("POST", "/api/mission", {"mission": ice["id"], "size": 10,
                                                   "name": "photo.jpg", "type": "image/jpeg"})
        check(code == 400, "une photo n'est pas une vidéo")
        code, said = call("POST", "/api/mission", {"mission": ice["id"], "size": len(video),
                                                   "name": "../../défi.mp4",
                                                   "type": "video/mp4"})
        check(code == 200 and said.get("ok"), "l'envoi commence")
        send = said["id"]
        piece = 1024 * 1024
        code, said = call("POST", f"/api/mission/morceau?id={send}&debut=5", raw=video[:piece])
        check(said.get("retry") and said.get("have") == 0,
              "un morceau mal placé : le serveur dit où reprendre")
        have = 0
        while have < len(video):
            code, said = call("POST", f"/api/mission/morceau?id={send}&debut={have}",
                              raw=video[have:have + piece])
            if not said.get("ok"):
                break
            have = said["have"]
        check(said.get("done") and have == len(video), "tous les morceaux arrivent")
        stored = missions.sends(sends_path)[send]
        target = folder / stored["file"]
        check(target.read_bytes() == video, "la vidéo est intacte")
        check(".." not in stored["file"] and target.parent.parent == folder,
              "rangée sous un nom choisi par le serveur, dans son dossier")
        asks = demandes.read_all([server.requests_path])
        check(any(a["kind"] == "mission" and "Ice challenge" in a["text"] for a in asks),
              "Prisme en est prévenu (« Demandes reçues »)")
        code, said = call("POST", f"/api/mission/morceau?id={send}&debut={have}", raw=b"x")
        check(said.get("already"), "un morceau de trop ne la prévient pas deux fois")
        code, said = call("POST", f"/api/mission/morceau?id={send}&debut=0", raw=b"x",
                          who=nomail)
        check(not said.get("ok"), "l'envoi d'un autre ne se complète pas")

        print("\n[3] Le verdict")
        code, said = call("GET", "/api/missions")
        mine = said["missions"][0]["mine"]
        check(len(mine) == 1 and mine[0]["state"] == "attente", "la personne voit « en examen »")
        book = missions.load(book_path)
        book["verdicts"][send] = {"state": "validee", "note": "", "at": time.time(),
                                  "updated": time.time()}
        missions.save(book_path, book)
        code, said = call("GET", "/api/missions")
        check(said["missions"][0]["mine"][0]["state"] == "validee"
              and said["missions"][0]["full"], "validée — et la mission (une place) complète")
        code, said = call("POST", "/api/mission", {"mission": ice["id"], "size": 10,
                                                   "name": "b.mp4"})
        check(code == 400 and "complète" in said.get("error", ""),
              "une mission complète n'accepte plus d'envoi")
        merged = missions.merge(book, {"missions": {}, "verdicts": {
            send: {"state": "payee", "note": "", "at": 1.0, "updated": time.time() + 5}}})
        check(merged["verdicts"][send]["state"] == "payee",
              "entre deux copies, le verdict le plus récent gagne")

        print("\n[4] Caché sur le téléphone")
        share = Path(BOX) / "partage"
        rel_hidden = telcache.entry(share / "Privé" / "Vacances", "dossier", share)
        check(rel_hidden["rel"] == "Privé/Vacances", "le chemin relatif au partage (NAS)")
        match = telcache.Matcher({telcache.key_of(share / "Privé"): telcache.entry(
            share / "Privé", "dossier", share)})
        check(match.path(share / "Privé" / "a.mp4") and not match.path(share / "Privée" / "a.mp4"),
              "un dossier caché cache ce qu'il contient, et rien d'autre")
        check(match.rel("privé/sous/b.mp4") and not match.rel("public/b.mp4"),
              "le NAS reconnaît les mêmes, par chemin relatif")
        gone = dict(telcache.entry(share / "Privé", "dossier", share), removed=True,
                    updated=time.time() + 10)
        joined = telcache.merge({"k": telcache.entry(share / "Privé", "dossier", share)},
                                {"k": gone})
        check(not telcache.active(joined), "remontré sur un PC : remontré partout")
    finally:
        server.stop()
    print("\n" + ("ÉCHECS : " + ", ".join(FAILS) if FAILS else "Tout est vert."))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
