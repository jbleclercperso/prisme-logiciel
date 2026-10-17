"""Le moteur de reparation PixMedic, cote Python du labo (programme vendu).

Le programme empaquete n'emporte pas les bibliotheques lourdes du moteur
(numba, OpenCV, Pillow, PyAV : environ 150 Mo) : elles s'installent au premier
besoin dans le Python du labo (iapython), et le moteur y tourne. Ce module y
est importe : ni Qt ni rien de Prisme, seulement le moteur et ses reponses,
mises en dictionnaires simples pour traverser la connexion.
"""
from __future__ import annotations

import json
import os
import queue
import sys


def _plain(value):
    """Un rapport du moteur sans types numpy (ils ne traversent pas d'un
    Python a l'autre sans risque) : un aller-retour JSON."""
    return json.loads(json.dumps(value, default=str))


def selftest_photo() -> dict:
    """Le bilan de sante (selftest) : une photo fabriquee, abimee comme le
    fait un lecteur de carte (256 octets perdus, bloc repete), reparee par
    le moteur. Tourne la ou vit le moteur : numba, OpenCV et Pillow y sont."""
    import cv2
    import numpy as np
    from reparateur import api, zones
    rng = np.random.default_rng(1)
    img = cv2.GaussianBlur(rng.integers(0, 255, (1200, 1600, 3), np.uint8), (0, 0), 3)
    data = bytearray(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 95])[1].tobytes())
    e = (len(data) // 2 // 4096) * 4096 - 260
    s = zones.start_candidates(e)[3]
    o = bytes(data)
    data[s:e - 256] = o[s + 256:e]
    data[e - 256:e] = o[e:e + 256]
    res = api.repair_photo_bytes(bytes(data))
    return _plain({"outcome": res.outcome, "error_code": res.error_code, "report": res.report or {}})


def serve(engine_src: str, requests, answers) -> None:
    """Attend (« diagnose », chemin) ou (« repair », (chemin, cible)), repond
    (« ok », valeur) ou (« error », phrase), jusqu'a « quit »."""
    if engine_src and engine_src not in sys.path:
        sys.path.insert(0, engine_src)
    try:
        from reparateur import api
    except Exception as exc:                                # noqa: BLE001
        answers.put(("error", f"{type(exc).__name__} : {exc}"))
        return
    answers.put(("ok", {"ready": True, "python": sys.version.split()[0], "pid": os.getpid()}))
    while True:
        try:
            kind, payload = requests.get(timeout=2)
        except queue.Empty:
            continue
        except (EOFError, OSError):
            return
        if kind == "quit":
            return
        try:
            if kind == "selftest":
                answers.put(("ok", selftest_photo()))
            elif kind == "diagnose":
                answers.put(("ok", _plain(api.diagnose(payload))))
            elif kind == "repair":
                path, target = payload
                res = api.repair_file(path, target)
                answers.put(("ok", _plain({
                    "outcome": res.outcome, "output_path": res.output_path,
                    "public_message": res.public_message, "report": res.report or {},
                    "error_code": res.error_code})))
            else:
                answers.put(("error", f"demande inconnue : {kind}"))
        except Exception as exc:                            # noqa: BLE001
            answers.put(("error", f"{type(exc).__name__} : {exc}"))
