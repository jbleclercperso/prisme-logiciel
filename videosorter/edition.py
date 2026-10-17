"""Quelle version de Prisme tourne ici : la version perso, ou la version vendue.

Un seul code, deux programmes. La version perso a tout. La version vendue a
tout aussi -- labo IA, recherche sur Internet, Telegram, partage de SA
bibliotheque avec SON telephone ou SON NAS -- sauf ce qui regarde les
visiteurs : accès envoyés à d'autres, journaux de visites, ce qui a été
regardé, l'oeil, sondages, missions, récompenses, inscription, demandes reçues.

`construire.py --edition vendue` réécrit la ligne EDITION dans la copie qu'il
empaquette et en retire les modules de VISITORS_ONLY : ils ne voyagent pas
chez un client. Le code commun demande `perso()` avant d'ouvrir l'une de ces
portes, et n'importe ces modules qu'à ce moment-là.

Depuis les sources, c'est la version perso ; PRISME_EDITION=vendue montre
la version vendue sans rien construire.
"""
from __future__ import annotations

import os

EDITION = "perso"
SOLD = "vendue"

# Les modules qui ne servent qu'aux visiteurs : absents de la version vendue.
VISITORS_ONLY = (
    "sondages", "sondages_dialog", "missions", "missions_dialog",
    "visitors_view", "demandes_dialog",
)


def current() -> str:
    if EDITION != "perso":
        return EDITION
    return SOLD if os.environ.get("PRISME_EDITION", "").strip().lower() == SOLD else "perso"


def perso() -> bool:
    """La version perso : les visiteurs, et tout ce qui va avec."""
    return current() == "perso"
