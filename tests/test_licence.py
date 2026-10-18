"""La licence : Ed25519 (vecteurs de la RFC 8032), cles, essai, prolongation.

    python tests/test_licence.py
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import tempfile

os.environ.setdefault("PRISME_SANDBOX", os.path.join(tempfile.gettempdir(), "prisme-test-licence"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.stdout.reconfigure(encoding="utf-8")

from videosorter import licence as L  # noqa: E402

FAILS = []


def check(condition, label: str) -> None:
    print(("  ok   " if condition else "  ÉCHEC ") + label)
    if not condition:
        FAILS.append(label)


# -- la signature, pour fabriquer des cles d'essai (le site fait de meme) -----------

def _expand(secret: bytes):
    h = hashlib.sha512(secret).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_of(secret: bytes) -> bytes:
    return L._compress(L._mul(_expand(secret)[0], L._G))


def sign(secret: bytes, message: bytes) -> bytes:
    a, prefix = _expand(secret)
    public = L._compress(L._mul(a, L._G))
    r = L._hash_mod_q(prefix + message)
    big_r = L._compress(L._mul(r, L._G))
    h = L._hash_mod_q(big_r + public + message)
    return big_r + int.to_bytes((r + h * a) % L._Q, 32, "little")


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def make_key(secret: bytes, prefix: str | None = None, **content) -> str:
    payload = json.dumps(content, separators=(",", ":")).encode()
    return (prefix or L.PREFIX) + b64(payload) + "." + b64(sign(secret, payload))


def site_activation(secret: bytes, limit: int = 2):
    """Le site, pour /api/activation : deux ordinateurs par licence, puis non."""
    known: dict[str, list[str]] = {}

    def post(url, body, timeout):
        data = L.read_key(body["key"], public_of(secret).hex())
        if data is None:
            return {"error": "key"}
        lid = L.licence_id(data)
        machines = known.setdefault(lid, [])
        if body.get("action") == "liberer":
            if body["machine"] in machines:
                machines.remove(body["machine"])
            return {"ok": True}
        if body["machine"] not in machines:
            if len(machines) >= limit:
                return {"error": "limite", "machines": [f"PC-{i + 1}" for i in range(len(machines))]}
            machines.append(body["machine"])
        return {"activation": make_key(secret, prefix=L.ACTIVATION_PREFIX, lid=lid,
                                       machine=body["machine"], issued=0)}
    return post


class Cfg(dict):
    def __init__(self, **values):
        super().__init__(pin_salt="", pin_digest="", licence_key="", trial_started=0,
                         licence_seen=0, licence_checked=0, licence_activation="")
        self.update(values)

    def save(self):
        pass

    save_soon = save


def main() -> int:
    print("\n[1] Ed25519, vecteurs de la RFC 8032")
    secret = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    public = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    signature = bytes.fromhex(
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e3970"
        "1cf9b46bd25bf5f0595bbe24655141438e7a100b")
    check(public_of(secret) == public, "la clé publique du vecteur 1")
    check(sign(secret, b"") == signature, "la signature du vecteur 1")
    check(L.verify(public, b"", signature), "la signature du vecteur 1 est reconnue")
    check(not L.verify(public, b"x", signature), "un message changé ne passe pas")
    forged = bytearray(signature); forged[5] ^= 1
    check(not L.verify(public, b"", bytes(forged)), "une signature altérée ne passe pas")
    secret2 = bytes.fromhex("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb")
    check(L.verify(public_of(secret2), b"\x72", sign(secret2, b"\x72")), "le vecteur 2 aussi")

    print("\n[2] Les clés")
    site_secret = bytes(range(32))
    site_public = public_of(site_secret).hex()
    now = 1_800_000_000.0
    year = make_key(site_secret, plan="an", expires=int(now + 200 * L.DAY), email="a@b.c")
    life = make_key(site_secret, plan="vie", expires=0)
    check(L.read_key(year, site_public)["plan"] == "an", "une clé signée par le site se lit")
    check(L.read_key(year.lower(), site_public) is None, "une clé abîmée ne se lit pas")
    check(L.read_key(make_key(bytes(32), plan="vie", expires=0), site_public) is None,
          "une clé signée par quelqu'un d'autre est refusée")
    check(L.read_key(year[:20] + "\n " + year[20:], site_public) is not None,
          "les espaces et retours à la ligne d'un copier-coller ne gênent pas")

    print("\n[3] L'essai")
    check(L.status(Cfg(), now, public="").mode == "dev",
          "sans clé publique : version de développement, sans essai")
    cfg = Cfg()
    first = L.status(cfg, now, site_public, registry=False)
    check(first.mode == "essai" and first.days_left == 14, "au premier lancement : 14 jours d'essai")
    later = L.status(cfg, now + 13.5 * L.DAY, site_public, registry=False)
    check(later.mode == "essai" and later.days_left == 1, "au 14e jour : encore 1 jour")
    check(L.status(cfg, now + 15 * L.DAY, site_public, registry=False).mode == "fini",
          "après 14 jours : essai fini")
    check(L.status(cfg, now + 1 * L.DAY, site_public, registry=False).mode == "fini",
          "reculer l'horloge ne rend pas de jours")

    print("\n[4] La licence")
    pc1, pc2, pc3 = "1" * 32, "2" * 32, "3" * 32
    site = site_activation(site_secret)
    cfg = Cfg(trial_started=now - 30 * L.DAY)
    check(L.accept_key(cfg, "PRISME1-nimportequoi", now, site_public, post=site, machine=pc1) != "",
          "une fausse clé est refusée, avec une explication")
    check(L.accept_key(cfg, year, now, site_public, post=site, machine=pc1, name="PC-1") == "",
          "une bonne clé est acceptée, et cet ordinateur activé")
    got = L.status(cfg, now, site_public, registry=False, machine=pc1)
    check(got.mode == "licence" and got.plan == "an" and got.days_left == 200,
          "essai fini, mais licence : Prisme s'ouvre")
    check(L.status(cfg, now + 201 * L.DAY, site_public, registry=False, machine=pc1).mode == "fini",
          "une licence expirée ne suffit plus")
    life_cfg = Cfg(trial_started=now - 30 * L.DAY)
    check(L.accept_key(life_cfg, life, now, site_public, post=site, machine=pc1) == ""
          and L.status(life_cfg, now + 5000 * L.DAY, site_public, registry=False,
                       machine=pc1).mode == "licence",
          "une licence à vie ne s'arrête pas")

    print("\n[5] Deux ordinateurs au plus")
    check(L.status(Cfg(**cfg), now, site_public, registry=False, machine=pc3).mode == "fini",
          "réglages recopiés sur un autre PC : la licence n'y vaut rien")
    check(L.status(Cfg(trial_started=now - 30 * L.DAY, licence_key=year), now, site_public,
                   registry=False, machine=pc1).mode == "fini",
          "une clé sans activation ne débloque rien")
    second = Cfg(trial_started=now - 30 * L.DAY)
    check(L.accept_key(second, year, now, site_public, post=site, machine=pc2) == ""
          and L.status(second, now, site_public, registry=False, machine=pc2).mode == "licence",
          "le deuxième ordinateur s'active")
    third = Cfg(trial_started=now - 30 * L.DAY)
    said = L.accept_key(third, year, now, site_public, post=site, machine=pc3)
    check("2 ordinateurs" in said and "PC-1" in said and third["licence_key"] == "",
          f"le troisième est refusé, et la clé n'est pas gardée ({said[:60]}…)")
    check(L.accept_key(Cfg(), year, now, site_public, post=site, machine=pc1) == "",
          "réactiver un ordinateur déjà compté ne coûte rien")
    check(L.release(second, site_public, post=site, machine=pc2) == ""
          and second["licence_key"] == "" and second["licence_activation"] == "",
          "« Libérer cet ordinateur » retire la licence de ce PC")
    check(L.accept_key(third, year, now, site_public, post=site, machine=pc3) == "",
          "une place libérée, le troisième s'active")
    offline = Cfg(trial_started=now - 30 * L.DAY)
    said = L.accept_key(offline, year, now, site_public, machine=pc2,
                        post=lambda *a: (_ for _ in ()).throw(OSError()))
    check("connexion" in said and offline["licence_key"] == "",
          "sans internet, la première activation explique pourquoi elle attend")
    other = make_key(site_secret, prefix=L.ACTIVATION_PREFIX, lid="autre", machine=pc1, issued=0)
    check(L.status(Cfg(licence_key=year, licence_activation=other), now, site_public,
                   registry=False, machine=pc1).mode != "licence",
          "le jeton d'une autre licence ne débloque pas celle-ci")

    print("\n[6] La prolongation d'un abonnement")
    month = make_key(site_secret, plan="mois", expires=int(now + 5 * L.DAY))
    longer = make_key(site_secret, plan="mois", expires=int(now + 35 * L.DAY))
    asked = []

    def site(url, body, timeout):
        asked.append((url, body))
        return {"key": longer}

    cfg = Cfg(licence_key=month)
    said = L.renew(cfg, now, site_public, post=site)
    check(said.startswith("Licence prolongée") and cfg["licence_key"] == longer
          and asked and asked[0][0].endswith("/api/licence") and asked[0][1]["key"] == month,
          f"à 5 jours de la fin : clé prolongée demandée au site ({said})")
    check(L.renew(cfg, now + 3600, site_public, post=site) == "" and len(asked) == 1,
          "pas plus d'une demande par jour")
    far = Cfg(licence_key=year)
    check(L.renew(far, now, site_public, post=site) == "" and len(asked) == 1,
          "loin de l'échéance : on ne demande rien")
    silent = Cfg(licence_key=month)
    check(L.renew(silent, now, site_public, post=lambda *a: (_ for _ in ()).throw(OSError())) == ""
          and silent["licence_key"] == month, "site injoignable : rien ne change")

    print("\n" + ("tout est vert" if not FAILS else f"{len(FAILS)} échec(s)"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
