"""Moteur de décision multicritère (niveau L3 — Décision).

Deux méthodes sont disponibles (choix de l'utilisateur, cf. cahier des
charges §9) :

- "weighted" : score pondéré classique (celui du moteur de scoring) ;
- "topsis"   : Technique for Order Preference by Similarity to Ideal
  Solution. Chaque actif est comparé à une solution idéale (meilleure note
  sur chaque critère) et anti-idéale ; le classement se fait sur le
  coefficient de proximité (0 à 1, plus il est haut mieux c'est).

Les critères utilisés sont les sous-notes 0-100 déjà normalisées, tous
orientés « bénéfice » (la volatilité est déjà inversée en amont), pondérés
par config/scoring.yaml ou par des pondérations fournies par l'appelant.
AHP/PROMETHEE/ELECTRE pourront s'ajouter ici avec la même signature.
"""

from __future__ import annotations

import json
import math

from peadvisor.config import CRITERES_SCORE, charger_scoring
from peadvisor.models import Actif

# Critères de la matrice de décision = les familles du moteur de scoring, sans
# exception : toute famille absente d'ici verrait sa pondération silencieusement
# ignorée par TOPSIS, qui classerait alors sur un score différent du score
# pondéré. La liste est donc dérivée de la source unique (config.CRITERES_SCORE)
# et suit automatiquement l'ajout d'une famille.
CRITERES = list(CRITERES_SCORE)


def _matrice(actifs: list[Actif], defaut: float) -> list[list[float]]:
    lignes = []
    for a in actifs:
        sous = json.loads(a.sous_scores) if a.sous_scores else {}
        lignes.append([sous.get(c) if sous.get(c) is not None else defaut for c in CRITERES])
    return lignes


def classement_topsis(actifs: list[Actif], ponderations: dict[str, float] | None = None) -> list[tuple[Actif, float]]:
    """Renvoie les actifs triés par coefficient de proximité TOPSIS décroissant."""
    if not actifs:
        return []
    cfg = charger_scoring()
    poids_cfg = ponderations or cfg["ponderations"]
    poids = [float(poids_cfg.get(c, 0)) for c in CRITERES]
    total = sum(poids) or 1.0
    poids = [p / total for p in poids]

    matrice = _matrice(actifs, float(cfg.get("note_donnee_manquante", 50)))

    # 1. Normalisation vectorielle puis pondération.
    normes = [math.sqrt(sum(ligne[j] ** 2 for ligne in matrice)) or 1.0 for j in range(len(CRITERES))]
    ponderee = [[poids[j] * ligne[j] / normes[j] for j in range(len(CRITERES))] for ligne in matrice]

    # 2. Solutions idéale et anti-idéale (tous les critères sont des bénéfices).
    ideal = [max(ligne[j] for ligne in ponderee) for j in range(len(CRITERES))]
    anti = [min(ligne[j] for ligne in ponderee) for j in range(len(CRITERES))]

    # 3. Distances euclidiennes et coefficient de proximité.
    resultats = []
    for actif, ligne in zip(actifs, ponderee):
        d_ideal = math.sqrt(sum((ligne[j] - ideal[j]) ** 2 for j in range(len(CRITERES))))
        d_anti = math.sqrt(sum((ligne[j] - anti[j]) ** 2 for j in range(len(CRITERES))))
        proximite = d_anti / (d_ideal + d_anti) if (d_ideal + d_anti) > 0 else 0.5
        resultats.append((actif, round(proximite, 4)))
    return sorted(resultats, key=lambda x: x[1], reverse=True)


def classement_pondere(actifs: list[Actif]) -> list[tuple[Actif, float]]:
    """Classement par score global pondéré (déjà calculé par le moteur de scoring)."""
    notes = [(a, a.score_global if a.score_global is not None else 0.0) for a in actifs]
    return sorted(notes, key=lambda x: x[1], reverse=True)


def classer(actifs: list[Actif], methode: str = "weighted",
            ponderations: dict[str, float] | None = None) -> list[tuple[Actif, float]]:
    if methode == "topsis":
        return classement_topsis(actifs, ponderations)
    return classement_pondere(actifs)
