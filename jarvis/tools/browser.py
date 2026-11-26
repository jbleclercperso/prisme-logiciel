"""Navigateur piloté : Jarvis ouvre des pages, les lit, clique et remplit des formulaires (Edge ou Chrome).

Le navigateur utilise un profil à part (data/navigateur) : connectez-vous une fois à vos sites dans cette
fenêtre, les sessions sont conservées.
"""
from __future__ import annotations

import base64
import os
import queue
import re
import threading

from .. import config as _config
from .registry import runtime, tool

_jobs: queue.Queue = queue.Queue()
_thread: threading.Thread | None = None
_state: dict = {}

SCAN = """() => {
  const out = [];
  const sel = 'a[href], button, input, textarea, select, [role=button], [role=link], [role=tab], '
            + '[role=menuitem], [role=checkbox], [contenteditable=true], summary';
  let n = 0;
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    if (r.width < 4 || r.height < 4 || st.visibility === 'hidden' || st.display === 'none') continue;
    if (el.disabled || el.type === 'hidden') continue;
    const label = (el.innerText || el.value || el.getAttribute('aria-label') || el.placeholder
                   || el.title || el.name || el.alt || '').replace(/\\s+/g, ' ').trim().slice(0, 70);
    const tag = el.tagName.toLowerCase();
    if (!label && tag !== 'input' && tag !== 'textarea' && tag !== 'select') continue;
    n += 1;
    el.setAttribute('data-jarvis', String(n));
    let kind = tag === 'a' ? 'lien' : tag === 'button' ? 'bouton' : tag === 'select' ? 'liste'
             : (tag === 'input' || tag === 'textarea') ? 'champ' + (el.type && el.type !== 'text' ? ' ' + el.type : '')
             : 'élément';
    out.push(`[${n}] ${kind} : ${label || '(sans libellé)'}`);
    if (n >= 120) break;
  }
  return out;
}"""


def _worker() -> None:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        _state["p"] = p
        while True:
            fn, box = _jobs.get()
            if fn is None:
                break
            try:
                box.put((True, fn()))
            except Exception as e:
                box.put((False, e))


def _do(fn, timeout: float = 90):
    global _thread
    if _thread is None or not _thread.is_alive():
        _thread = threading.Thread(target=_worker, daemon=True)
        _thread.start()
    box: queue.Queue = queue.Queue()
    _jobs.put((fn, box))
    ok, value = box.get(timeout=timeout)
    if not ok:
        raise value
    return value


def _page():
    ctx = _state.get("ctx")
    if ctx is None or not _alive(ctx):
        cfg = runtime.config.get("navigateur", {})
        profile = _config.DATA / "navigateur"
        profile.mkdir(parents=True, exist_ok=True)
        kwargs = dict(headless=bool(cfg.get("invisible", False)), no_viewport=True,
                      args=["--start-maximized", "--no-first-run", "--no-default-browser-check"])
        exe = os.environ.get("JARVIS_BROWSER_EXE")
        if exe:
            kwargs.update(executable_path=exe, no_viewport=False, viewport={"width": 1280, "height": 800})
            kwargs["args"] += ["--no-sandbox"]
        else:
            kwargs["channel"] = cfg.get("canal", "msedge")
        try:
            ctx = _state["p"].chromium.launch_persistent_context(str(profile), **kwargs)
        except Exception:
            if exe or kwargs.get("channel") == "chrome":
                raise
            kwargs["channel"] = "chrome"                # Edge absent : on essaie Chrome
            ctx = _state["p"].chromium.launch_persistent_context(str(profile), **kwargs)
        ctx.set_default_timeout(15000)
        _state["ctx"] = ctx
    pages = [pg for pg in ctx.pages if not pg.is_closed()]
    page = _state.get("page")
    if page is None or page.is_closed():
        page = pages[-1] if pages else ctx.new_page()
        _state["page"] = page
    return page


def _alive(ctx) -> bool:
    try:
        _ = ctx.pages
        return True
    except Exception:
        return False


def _snapshot(page, with_text: bool = True) -> str:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=10000)
    except Exception:
        pass
    lines = [f"Page : {page.title()} — {page.url}"]
    if with_text:
        text = re.sub(r"\n{3,}", "\n\n", page.inner_text("body", timeout=8000) or "").strip()
        lines.append("Texte de la page :\n" + (text[:5000] + ("\n[... tronqué]" if len(text) > 5000 else "")))
    elements = page.evaluate(SCAN)
    lines.append("Éléments cliquables (utilise leur numéro) :\n" + ("\n".join(elements) or "(aucun)"))
    return "\n\n".join(lines)


def _target(page, numero, texte):
    if numero:
        return page.locator(f'[data-jarvis="{int(numero)}"]').first
    if texte:
        loc = page.get_by_role("button", name=texte).or_(page.get_by_role("link", name=texte)).first
        return loc if loc.count() else page.get_by_text(texte).first
    raise ValueError("indique 'numero' (de la dernière lecture) ou 'texte'")


@tool("navigateur",
      "Pilote un vrai navigateur web pour AGIR sur un site (pas seulement l'ouvrir) : se rendre sur une page, "
      "la lire, cliquer, remplir un formulaire, valider. Enchaîne : 'ouvrir' (renvoie le texte et les éléments "
      "numérotés), puis 'cliquer' / 'remplir' avec le numéro, puis 'lire' pour voir le résultat. "
      "Actions : ouvrir (url), lire, cliquer (numero ou texte), remplir (numero, valeur, entree), "
      "touche (valeur, ex. 'Enter', 'PageDown'), retour, capture, onglets, fermer. "
      "Pour simplement afficher un site à l'utilisateur, préfère ouvrir_url.",
      {"action": {"type": "string", "enum": ["ouvrir", "lire", "cliquer", "remplir", "touche", "retour",
                                              "capture", "onglets", "fermer"]},
       "url": {"type": "string"},
       "numero": {"type": "integer", "description": "Numéro de l'élément dans la dernière lecture."},
       "texte": {"type": "string", "description": "Libellé visible de l'élément, si pas de numéro."},
       "valeur": {"type": "string", "description": "Texte à saisir, ou touche à presser."},
       "entree": {"type": "boolean", "description": "Appuyer sur Entrée après la saisie."}},
      ["action"])
def navigateur(action: str, url: str = "", numero: int = 0, texte: str = "", valeur: str = "",
               entree: bool = False):
    def run():
        if action == "fermer":
            ctx = _state.pop("ctx", None)
            _state.pop("page", None)
            if ctx is not None:
                ctx.close()
            return "Navigateur fermé."
        page = _page()
        if action == "ouvrir":
            target = url if re.match(r"^[a-z]+://", url, re.I) else "https://" + url
            page.goto(target, wait_until="domcontentloaded", timeout=45000)
            return _snapshot(page)
        if action == "lire":
            return _snapshot(page)
        if action == "cliquer":
            before = len(page.context.pages)
            _target(page, numero, texte).click()
            page.wait_for_timeout(1200)
            pages = [pg for pg in page.context.pages if not pg.is_closed()]
            if len(pages) > before:                     # le clic a ouvert un nouvel onglet : on le suit
                _state["page"] = page = pages[-1]
            return "Clic effectué.\n\n" + _snapshot(page, with_text=False)
        if action == "remplir":
            loc = _target(page, numero, texte)
            try:
                loc.fill(valeur)
            except Exception:
                loc.select_option(label=valeur)         # liste déroulante
            if entree:
                loc.press("Enter")
                page.wait_for_timeout(1500)
            return "Champ rempli." + ("\n\n" + _snapshot(page, with_text=False) if entree else "")
        if action == "touche":
            page.keyboard.press(valeur or "Enter")
            page.wait_for_timeout(800)
            return f"Touche {valeur or 'Enter'} envoyée. Page : {page.title()} — {page.url}"
        if action == "retour":
            page.go_back(wait_until="domcontentloaded")
            return _snapshot(page, with_text=False)
        if action == "onglets":
            return "\n".join(f"- {pg.title()} — {pg.url}" for pg in page.context.pages if not pg.is_closed())
        if action == "capture":
            data = page.screenshot(type="jpeg", quality=70)
            return {"text": f"Capture de la page {page.title()}.",
                    "image_b64": base64.standard_b64encode(data).decode(), "media_type": "image/jpeg"}
        return f"ERREUR : action inconnue '{action}'."

    try:
        return _do(run)
    except ImportError:
        return "ERREUR : module playwright manquant (relancer install.bat)."
