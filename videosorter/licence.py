"""L'essai de quatorze jours, et les cles de licence.

Une cle « PRISME1-… » est signee par le site (Ed25519) et se verifie ici,
hors ligne, avec la seule cle publique ci-dessous. Sa forme :

    PRISME1-<contenu>.<signature>

ou <contenu> et <signature> sont en base64 « url » sans remplissage ; le
contenu est un petit JSON : {"plan": "mois" | "an" | "vie", "expires": <date
d'expiration, secondes depuis 1970, 0 pour une licence a vie>, "email": …,
"issued": …}, et la signature porte sur ces octets-la, tels quels.

- **La version perso (edition.py) n'a ni essai ni licence** : elle est a son
  proprietaire. Seule la version vendue compte ses quatorze jours et demande
  une cle. PUBLIC_KEY vide fait de meme (version de developpement).
- PUBLIC_KEY est la moitie publique de la paire dont le site garde l'autre
  (variable PRISME_LICENCE_PRIVATE de Netlify). Changer de paire rendrait
  muettes toutes les cles deja vendues.
- Le debut de l'essai est note dans la configuration **et** dans le registre
  (HKCU\\Software\\Prisme) : effacer l'un ne remet pas l'essai a zero.
  Reculer l'horloge ne sert a rien non plus : on retient le plus tard deja vu.
- Un abonnement se prolonge seul : dans ses dix derniers jours, Prisme
  demande une cle plus longue au site (POST /api/licence), une fois par jour
  au plus.
- **Deux ordinateurs au plus par licence** (decision du 18/10/2026). En
  collant sa cle, Prisme fait activer cet ordinateur par le site
  (POST /api/activation : la cle, une empreinte anonyme de la machine et son
  nom). Le site refuse le troisieme. Il rend un jeton « PRISMEA1-… », signe
  avec la meme cle que les licences, qui designe cette licence ET cet
  ordinateur : Prisme le verifie ensuite sans connexion, et recopier ses
  reglages sur un autre PC ne suffit pas. « Liberer cet ordinateur » (fenetre
  de licence) le retire du compte, pour en activer un autre.

La verification Ed25519 suit la RFC 8032, sans dependance.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass

PUBLIC_KEY = "aXOVtybORQp9PC68beQLUNEZVBswCUBR9gT1bip34ss="
SITE = "https://prisme-desktop.com"
# Ou l'on achete : les formules du site.
BUY = SITE + "/#tarifs"
PREFIX = "PRISME1-"
ACTIVATION_PREFIX = "PRISMEA1-"
MAX_COMPUTERS = 2
TRIAL_DAYS = 14
RENEW_DAYS = 10
DAY = 86400.0
_REG_PATH = r"Software\Prisme"
_REG_NAME = "Debut"

# -- Ed25519 (RFC 8032), verification ---------------------------------------------

_P = 2 ** 255 - 19
_Q = 2 ** 252 + 27742317777372353535851937790883648493


def _inv(x: int) -> int:
    return pow(x, _P - 2, _P)


_D = -121665 * _inv(121666) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _add(a: tuple, b: tuple) -> tuple:
    """Addition de deux points, en coordonnees etendues (X, Y, Z, T)."""
    x1, y1, z1, t1 = a
    x2, y2, z2, t2 = b
    k1 = (y1 - x1) * (y2 - x2) % _P
    k2 = (y1 + x1) * (y2 + x2) % _P
    k3 = 2 * t1 * t2 * _D % _P
    k4 = 2 * z1 * z2 % _P
    e, f, g, h = k2 - k1, k4 - k3, k4 + k3, k2 + k1
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(scalar: int, point: tuple) -> tuple:
    result = (0, 1, 1, 0)                   # le point neutre
    while scalar > 0:
        if scalar & 1:
            result = _add(result, point)
        point = _add(point, point)
        scalar >>= 1
    return result


def _same(a: tuple, b: tuple) -> bool:
    return ((a[0] * b[2] - b[0] * a[2]) % _P == 0
            and (a[1] * b[2] - b[1] * a[2]) % _P == 0)


def _recover_x(y: int, sign: int):
    if y >= _P:
        return None
    x2 = (y * y - 1) * _inv(_D * y * y + 1) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_GY = 4 * _inv(5) % _P
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)


def _decompress(raw: bytes):
    if len(raw) != 32:
        return None
    y = int.from_bytes(raw, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def _compress(point: tuple) -> bytes:
    z = _inv(point[2])
    x, y = point[0] * z % _P, point[1] * z % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _hash_mod_q(data: bytes) -> int:
    return int.from_bytes(hashlib.sha512(data).digest(), "little") % _Q


def verify(public: bytes, message: bytes, signature: bytes) -> bool:
    """La signature Ed25519 de `message` par la cle `public` est-elle bonne ?"""
    if len(public) != 32 or len(signature) != 64:
        return False
    a = _decompress(public)
    r = _decompress(signature[:32])
    if a is None or r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _Q:
        return False
    h = _hash_mod_q(signature[:32] + public + message)
    return _same(_mul(s, _G), _add(r, _mul(h, a)))


# -- les cles ---------------------------------------------------------------------

def _public_bytes(public: str) -> bytes:
    text = (public or "").strip()
    if len(text) == 64:
        try:
            return bytes.fromhex(text)
        except ValueError:
            pass
    try:
        return base64.b64decode(text + "=" * (-len(text) % 4))
    except ValueError:
        return b""


def _b64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def read_key(key: str, public: str | None = None, prefix: str = PREFIX) -> dict | None:
    """Le contenu d'une cle (ou, avec ACTIVATION_PREFIX, d'un jeton
    d'activation) dont la signature est bonne, ou None."""
    text = "".join((key or "").split())
    if not text.upper().startswith(prefix):
        return None
    body = text[len(prefix):]
    head, _, tail = body.partition(".")
    try:
        payload, signature = _b64url(head), _b64url(tail)
    except (ValueError, TypeError):
        return None
    public_raw = _public_bytes(PUBLIC_KEY if public is None else public)
    if not public_raw or not verify(public_raw, payload, signature):
        return None
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        data["expires"] = int(data.get("expires") or 0)
    except (TypeError, ValueError):
        return None
    return data


# -- deux ordinateurs par licence ---------------------------------------------------

def machine_id() -> str:
    """L'empreinte anonyme de cet ordinateur : un condense de l'identifiant que
    Windows donne a son installation (MachineGuid), jamais l'identifiant lui-meme."""
    raw = ""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                            winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)) as key:
            raw = str(winreg.QueryValueEx(key, "MachineGuid")[0])
    except (OSError, ImportError):
        raw = ""
    if not raw:
        import uuid
        raw = "node-%012x" % uuid.getnode()
    return hashlib.sha256(("prisme:" + raw.strip().lower()).encode("utf-8")).hexdigest()[:32]


def machine_name() -> str:
    """Le nom de l'ordinateur, pour que le client reconnaisse ses machines."""
    import os
    import platform
    return (os.environ.get("COMPUTERNAME") or platform.node() or "")[:60]


def licence_id(data: dict) -> str:
    """L'identifiant stable d'une licence : l'abonnement (il ne change pas quand
    la cle se prolonge), ou le client et la date d'achat pour une licence a vie.
    Meme calcul que licenceId() dans netlify/lib/licence.mjs du site."""
    sub = str(data.get("subscription") or "")
    if sub:
        return sub
    try:
        issued = int(data.get("issued") or 0)
    except (TypeError, ValueError):
        issued = 0
    return f"{data.get('customer') or ''}:{issued}"


def _activated(cfg, data: dict, public: str, machine: str | None = None) -> bool:
    """La cle est-elle activee sur CET ordinateur ?"""
    try:
        saved = cfg["licence_activation"]
    except KeyError:
        saved = ""
    token = read_key(saved, public, ACTIVATION_PREFIX)
    if token is None:
        return False
    return (token.get("lid") == licence_id(data)
            and token.get("machine") == (machine or machine_id()))


# -- l'essai ----------------------------------------------------------------------

def _registry_start() -> float:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_PATH) as key:
            value, _kind = winreg.QueryValueEx(key, _REG_NAME)
        return float(value)
    except (OSError, ImportError, ValueError):
        return 0.0


def _write_registry_start(moment: float) -> None:
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _REG_PATH) as key:
            winreg.SetValueEx(key, _REG_NAME, 0, winreg.REG_SZ, str(int(moment)))
    except (OSError, ImportError):
        pass


def trial_start(cfg, now: float, registry: bool = True) -> float:
    """Le debut de l'essai : le plus ancien des deux endroits ou il est note,
    ou maintenant (et on le note aux deux)."""
    known = [v for v in (float(cfg["trial_started"] or 0),
                         _registry_start() if registry else 0.0) if v > 0]
    start = min(known) if known else now
    if float(cfg["trial_started"] or 0) != start:
        cfg["trial_started"] = start
        cfg.save_soon()
    if registry and _registry_start() != int(start):
        _write_registry_start(start)
    return start


@dataclass
class Status:
    mode: str                  # « dev », « licence », « essai », « fini »
    days_left: int = 0
    expires: int = 0           # 0 : a vie (ou sans objet)
    plan: str = ""
    email: str = ""

    @property
    def usable(self) -> bool:
        return self.mode != "fini"


def status(cfg, now: float | None = None, public: str | None = None,
           registry: bool = True, machine: str | None = None) -> Status:
    """Ou en est cette installation : licence, essai, ou essai fini."""
    if public is None:
        from . import edition
        if edition.perso():
            return Status("dev")
        public = PUBLIC_KEY
    if not public:
        return Status("dev")
    now = time.time() if now is None else now
    # L'horloge reculee ne rend pas de jours : on garde le plus tard deja vu.
    seen = float(cfg["licence_seen"] or 0)
    if now > seen:
        cfg["licence_seen"] = now
        cfg.save_soon()
    now = max(now, seen)
    data = read_key(cfg["licence_key"], public)
    if (data is not None and (not data["expires"] or data["expires"] > now)
            and _activated(cfg, data, public, machine)):
        left = int((data["expires"] - now) // DAY) if data["expires"] else 0
        return Status("licence", left, data["expires"], str(data.get("plan") or ""),
                      str(data.get("email") or ""))
    start = trial_start(cfg, now, registry)
    left = TRIAL_DAYS - int((now - start) // DAY)
    if left > 0:
        return Status("essai", left)
    return Status("fini")


def accept_key(cfg, key: str, now: float | None = None, public: str | None = None,
               post=None, machine: str | None = None, name: str | None = None) -> str:
    """Enregistre une cle si elle est bonne, encore valable, et si le site
    accepte d'activer cet ordinateur (deux au plus par licence). Rend "" si
    c'est fait, sinon ce qui ne va pas. Appel reseau (quelques secondes)."""
    now = time.time() if now is None else now
    key = "".join((key or "").split())
    data = read_key(key, public)
    if data is None:
        return "Cette clé n'est pas valable (vérifiez qu'elle est copiée en entier)."
    if data["expires"] and data["expires"] <= now:
        return "Cette clé a expiré."
    machine = machine or machine_id()
    try:
        answer = (post or _post)(SITE.rstrip("/") + "/api/activation",
                                 {"key": key, "machine": machine,
                                  "nom": machine_name() if name is None else name}, 15.0)
    except Exception:                                   # noqa: BLE001
        return ("Impossible de joindre le site pour activer la licence. Vérifiez la "
                "connexion internet, puis réessayez : la première activation en a besoin, "
                "ensuite Prisme s'en passe.")
    answer = answer or {}
    if answer.get("error") == "limite":
        names = [str(n) for n in (answer.get("machines") or []) if str(n).strip()]
        where = f" ({', '.join(names)})" if names else ""
        return (f"Cette licence est déjà activée sur {MAX_COMPUTERS} ordinateurs{where}. "
                "Ouvrez Prisme sur l'un d'eux, puis Licence › « Libérer cet ordinateur », "
                "et recommencez ici. Ordinateur perdu ou réinstallé ? Écrivez-nous à "
                "contact@prisme-desktop.com.")
    if answer.get("error") == "expiree":
        return "Cette clé a expiré."
    token = str(answer.get("activation") or "")
    got = read_key(token, public, ACTIVATION_PREFIX)
    if got is None or got.get("lid") != licence_id(data) or got.get("machine") != machine:
        return "L'activation n'a pas abouti. Réessayez dans un instant, ou écrivez-nous."
    cfg["licence_key"] = key
    cfg["licence_activation"] = token
    cfg.save()
    return ""


def release(cfg, public: str | None = None, post=None, machine: str | None = None) -> str:
    """« Liberer cet ordinateur » : le site ne le compte plus, et la licence
    quitte cet ordinateur (on pourra l'activer sur un autre). Rend "" si c'est
    fait, sinon ce qui ne va pas. Appel reseau."""
    key = cfg["licence_key"]
    if read_key(key, public) is None:
        return "Aucune licence sur cet ordinateur."
    try:
        answer = (post or _post)(SITE.rstrip("/") + "/api/activation",
                                 {"action": "liberer", "key": key,
                                  "machine": machine or machine_id()}, 15.0)
    except Exception:                                   # noqa: BLE001
        return "Impossible de joindre le site. Vérifiez la connexion internet, puis réessayez."
    if not (answer or {}).get("ok"):
        return "Le site n'a pas pu libérer cet ordinateur. Réessayez dans un instant, ou écrivez-nous."
    cfg["licence_key"] = ""
    cfg["licence_activation"] = ""
    cfg.save()
    return ""


# -- la prolongation d'un abonnement ------------------------------------------------

def renewal_due(cfg, now: float | None = None, public: str | None = None) -> bool:
    """Faut-il demander au site une cle prolongee ? (Dix derniers jours d'un
    abonnement, et pas deja demande dans la journee.)"""
    now = time.time() if now is None else now
    data = read_key(cfg["licence_key"], public)
    if data is None or not data["expires"]:
        return False
    if data["expires"] - now > RENEW_DAYS * DAY:
        return False
    return now - float(cfg["licence_checked"] or 0) >= DAY


def renew(cfg, now: float | None = None, public: str | None = None,
          timeout: float = 10.0, post=None) -> str:
    """Demande au site une cle prolongee. Rend un message si la licence a ete
    prolongee, "" sinon (pas le moment, site muet, pas encore renouvelee).
    Hors du fil de l'interface : c'est un appel reseau."""
    now = time.time() if now is None else now
    if not renewal_due(cfg, now, public):
        return ""
    cfg["licence_checked"] = now
    cfg.save_soon()
    old = read_key(cfg["licence_key"], public) or {}
    try:
        answer = (post or _post)(SITE.rstrip("/") + "/api/licence",
                                 {"key": cfg["licence_key"]}, timeout)
    except Exception:                                   # noqa: BLE001
        return ""
    new_key = (answer or {}).get("key") or ""
    data = read_key(new_key, public)
    if data is None or (data["expires"] and data["expires"] <= old.get("expires", 0)):
        return ""
    cfg["licence_key"] = "".join(new_key.split())
    cfg.save()
    if not data["expires"]:
        return "Licence renouvelée."
    return "Licence prolongée jusqu'au " + time.strftime("%d/%m/%Y", time.localtime(data["expires"])) + "."


def _post(url: str, body: dict, timeout: float) -> dict:
    import urllib.error
    import urllib.request
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            return json.loads(answer.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        # 400 « cle illisible » : la reponse dit pourquoi ; le reste (5xx) remonte.
        if error.code < 500:
            try:
                return json.loads(error.read().decode("utf-8"))
            except ValueError:
                return {}
        raise
