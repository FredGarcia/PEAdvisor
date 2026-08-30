"""Moteur d'allocation automatique (niveau L3 — Décision).

Principe :
1. Le profil de risque (1 à 7) et l'horizon déterminent une répartition
   cible entre trois poches : défensive (actifs de niveau de risque 1-3),
   cœur de portefeuille (4-5) et dynamique (6-7).
2. L'objectif (croissance / dividendes / équilibré) détermine le critère de
   sélection des valeurs à l'intérieur de chaque poche.
3. Les contraintes de diversification de config/settings.yaml sont
   appliquées : poids maximal par ligne, poids maximal par secteur,
   nombre de lignes minimal/maximal.
"""

from __future__ import annotations

import json
import math

from sqlalchemy.orm import Session

from datetime import datetime, timedelta

from peadvisor.config import charger_settings
from peadvisor.models import Actif, TypeActif
from peadvisor.schemas import (DemandeAllocation, LigneAllocation, ReponseAllocation,
                               ValeurIncomplete)

# Répartition (défensive, cœur, dynamique) en % pour chaque profil 1-7.
MIX_PROFILS: dict[int, tuple[int, int, int]] = {
    1: (70, 30, 0),
    2: (55, 40, 5),
    3: (40, 50, 10),
    4: (25, 55, 20),
    5: (15, 55, 30),
    6: (5, 50, 45),
    7: (0, 40, 60),
}

LIBELLES_POCHES = ("défensive", "cœur", "dynamique")


def _poche(actif: Actif) -> int:
    niveau = actif.niveau_risque or 4
    if niveau <= 3:
        return 0
    if niveau <= 5:
        return 1
    return 2


def _metrique(actif: Actif, objectif: str) -> float:
    """Note de sélection selon l'objectif, à partir des sous-scores.

    Critères pondérés (0-100) : au socle du score global s'ajoutent, selon
    l'objectif, le dividende / la croissance / le potentiel, plus un bonus
    transversal de qualité (ESG), et une pénalité si des données clés manquent
    (la sélection privilégie les valeurs documentées).

    Un bonus de liquidité était prévu ici, mais le moteur de scoring ne produit
    aucune sous-note « liquidité » : le terme valait toujours 0. Il est retiré
    plutôt que laissé inerte — le rétablir suppose d'abord d'ajouter la famille
    au scoring (volume / quantité échangée sont déjà collectés)."""
    sous = json.loads(actif.sous_scores) if actif.sous_scores else {}
    score = actif.score_global or 0.0
    esg = (actif.score_esg or 0.0)
    if objectif == "dividendes":
        base = 0.45 * (sous.get("dividende") or 0) + 0.15 * (sous.get("volatilite") or 0) + 0.30 * score
    elif objectif == "croissance":
        base = 0.30 * (sous.get("croissance") or 0) + 0.25 * (sous.get("potentiel") or 0) + 0.35 * score
    else:  # équilibré
        base = 0.80 * score
    note = base + 0.06 * esg
    # Pénalité de complétude : chaque donnée clé absente retire des points.
    note -= 6.0 * len(_infos_manquantes(actif))
    return max(note, 0.0)


# Données clés attendues pour sélectionner une valeur en confiance.
_CHAMPS_CLES = [
    ("cours", "cours indisponible"),
    ("score_global", "score non calculé"),
    ("niveau_risque", "niveau de risque (SRI) manquant"),
    ("secteur", "secteur non renseigné"),
    ("score_esg", "score ESG manquant"),
]


def _infos_manquantes(actif: Actif) -> list[str]:
    """Liste lisible des informations manquantes d'une valeur."""
    manquantes = [libelle for champ, libelle in _CHAMPS_CLES
                  if getattr(actif, champ, None) is None]
    # Cours périmé = information de marché à rafraîchir.
    if actif.date_cours and actif.date_cours < datetime.utcnow() - timedelta(days=7):
        manquantes.append("cours périmé (> 7 jours)")
    return manquantes


def _allouable(actif: Actif) -> bool:
    """Une valeur n'est allouable que si le cours et le score sont connus."""
    return actif.cours is not None and actif.score_global is not None


def _ajuster_horizon(mix: tuple[int, int, int], horizon: int) -> tuple[int, int, int]:
    """Horizon court → plus défensif ; horizon long → plus dynamique (±10 pts)."""
    defensif, coeur, dynamique = mix
    if horizon < 5:
        transfert = min(10, dynamique)
        return (defensif + transfert, coeur, dynamique - transfert)
    if horizon >= 15:
        transfert = min(10, defensif)
        return (defensif - transfert, coeur, dynamique + transfert)
    return mix


def proposer_allocation(session: Session, demande: DemandeAllocation) -> ReponseAllocation:
    params = charger_settings()["allocation"]
    poids_max_ligne = float(params.get("poids_max_par_ligne", 0.10))
    poids_max_secteur = float(params.get("poids_max_par_secteur", 0.30))
    lignes_min = int(params.get("lignes_min", 8))
    lignes_max = int(params.get("lignes_max", 25))
    part_min_fonds = float(params.get("part_min_etf_opcvm", 0.30))

    eligibles = session.query(Actif).filter(Actif.eligible_pea.is_(True)).all()
    # Une valeur peut exister sous plusieurs sources : on ne garde qu'une ligne
    # par ISIN (la mieux notée) pour ne pas l'allouer plusieurs fois.
    par_isin: dict[str, Actif] = {}
    for a in eligibles:
        meilleur = par_isin.get(a.isin)
        if meilleur is None or (a.score_global or 0) > (meilleur.score_global or 0):
            par_isin[a.isin] = a
    uniques = list(par_isin.values())
    # Seules les valeurs suffisamment documentées sont allouables ; les autres
    # sont signalées (informations manquantes) sans être noyées dans le portefeuille.
    actifs = [a for a in uniques if _allouable(a)]
    valeurs_incompletes = [
        ValeurIncomplete(isin=a.isin, nom=a.nom,
                         type=a.type.value if hasattr(a.type, "value") else str(a.type),
                         informations_manquantes=_infos_manquantes(a))
        for a in uniques if not _allouable(a)
    ]
    mix = _ajuster_horizon(MIX_PROFILS[demande.niveau_risque], demande.horizon_annees)

    # Poches triées par la métrique liée à l'objectif.
    poches: list[list[Actif]] = [[], [], []]
    for a in actifs:
        poches[_poche(a)].append(a)
    for p in poches:
        p.sort(key=lambda a: _metrique(a, demande.objectif), reverse=True)

    # Une poche cible vide voit son poids reversé à la poche cœur.
    poids_poches = [m / 100 for m in mix]
    for i in (0, 2):
        if poids_poches[i] > 0 and not poches[i]:
            poids_poches[1] += poids_poches[i]
            poids_poches[i] = 0.0

    # Nombre de lignes cible, proportionnel au capital.
    nb_lignes = max(lignes_min, min(lignes_max, int(demande.capital // 1500) or 1))
    nb_lignes = min(nb_lignes, sum(len(p) for p, w in zip(poches, poids_poches) if w > 0))

    lignes: list[LigneAllocation] = []
    poids_secteurs: dict[str, float] = {}

    for i, (poche, poids_poche) in enumerate(zip(poches, poids_poches)):
        if poids_poche <= 0 or not poche:
            continue
        # Assez de lignes pour que le plafond par ligne reste tenable
        # (ex. : poche à 80 % avec plafond 10 % → au moins 8 lignes).
        nb = max(1, round(nb_lignes * poids_poche), math.ceil(poids_poche / poids_max_ligne))
        nb = min(nb, len(poche))

        # Diversification par type : la poche cœur réserve une part minimale
        # aux fonds (ETF/OPCVM), classiquement le socle d'un portefeuille PEA.
        candidats = poche
        if i == 1 and part_min_fonds > 0:
            fonds = [a for a in poche if a.type != TypeActif.ACTION]
            actions_seules = [a for a in poche if a.type == TypeActif.ACTION]
            nb_fonds = min(len(fonds), math.ceil(nb * part_min_fonds))
            candidats = fonds[:nb_fonds] + actions_seules + fonds[nb_fonds:]

        selection: list[Actif] = []
        for a in candidats:
            secteur = a.secteur or "Divers"
            if poids_secteurs.get(secteur, 0.0) + poids_poche / max(nb, 1) > poids_max_secteur:
                continue  # contrainte sectorielle : on passe au candidat suivant
            selection.append(a)
            poids_secteurs[secteur] = poids_secteurs.get(secteur, 0.0) + poids_poche / max(nb, 1)
            if len(selection) >= nb:
                break

        if not selection:
            selection = poche[:nb]

        # Poids intra-poche proportionnels à la métrique, plafonnés par ligne.
        metriques = [max(_metrique(a, demande.objectif), 1.0) for a in selection]
        total_m = sum(metriques)
        poids_bruts = [poids_poche * m / total_m for m in metriques]
        exces = sum(max(0.0, p - poids_max_ligne) for p in poids_bruts)
        poids_final = [min(p, poids_max_ligne) for p in poids_bruts]
        # L'excédent au-delà du plafond est réparti uniformément sur les lignes non plafonnées.
        non_plafonnees = [j for j, p in enumerate(poids_bruts) if p < poids_max_ligne]
        for j in non_plafonnees:
            poids_final[j] += exces / len(non_plafonnees) if non_plafonnees else 0.0

        for a, p in zip(selection, poids_final):
            manquantes = _infos_manquantes(a)
            justification = (f"Poche {LIBELLES_POCHES[i]} — objectif {demande.objectif}, "
                             f"score {a.score_global or 0:.0f}/100")
            if manquantes:
                justification += f" ⚠ à compléter : {', '.join(manquantes)}"
            lignes.append(LigneAllocation(
                isin=a.isin,
                nom=a.nom,
                type=a.type.value if hasattr(a.type, "value") else str(a.type),
                secteur=a.secteur,
                poids=round(p, 4),
                montant=round(p * demande.capital, 2),
                score_global=a.score_global,
                niveau_risque=a.niveau_risque,
                justification=justification,
                informations_manquantes=manquantes,
            ))

    # Renormalisation finale (les arrondis et plafonds peuvent dévier de 100 %).
    total = sum(l.poids for l in lignes) or 1.0
    for l in lignes:
        l.poids = round(l.poids / total, 4)
        l.montant = round(l.poids * demande.capital, 2)

    repartition_types: dict[str, float] = {}
    for l in lignes:
        repartition_types[l.type] = round(repartition_types.get(l.type, 0.0) + l.poids, 4)

    criteres_objectif = {
        "dividendes": "dividende (45 %) · score global (30 %) · faible volatilité (15 %)",
        "croissance": "croissance (30 %) · potentiel (25 %) · score global (35 %)",
        "equilibre": "score global (80 %)",
    }
    criteres = [
        f"Sélection {demande.objectif} : {criteres_objectif.get(demande.objectif, 'score global')}, "
        "+ bonus ESG (6 %).",
        "Pénalité de complétude : les valeurs aux données clés manquantes sont défavorisées.",
        f"Diversification : ≤ {poids_max_ligne:.0%} par ligne, ≤ {poids_max_secteur:.0%} par secteur, "
        f"part minimale de fonds (ETF/OPCVM) dans la poche cœur.",
        f"Répartition par risque défensive/cœur/dynamique = {mix[0]}/{mix[1]}/{mix[2]} % "
        f"(profil {demande.niveau_risque}/7, horizon {demande.horizon_annees} ans).",
    ]

    commentaire = (
        f"Profil {demande.niveau_risque}/7, horizon {demande.horizon_annees} ans, objectif "
        f"« {demande.objectif} » : répartition cible défensive/cœur/dynamique = "
        f"{mix[0]}/{mix[1]}/{mix[2]} %. {len(lignes)} lignes proposées, plafond "
        f"{poids_max_ligne:.0%} par ligne et {poids_max_secteur:.0%} par secteur. "
    )
    if valeurs_incompletes:
        commentaire += (f"{len(valeurs_incompletes)} valeur(s) éligible(s) écartée(s) faute de "
                        "données suffisantes (voir « informations manquantes »). ")
    commentaire += "Proposition indicative : ceci n'est pas un conseil en investissement."

    return ReponseAllocation(
        capital=demande.capital,
        niveau_risque=demande.niveau_risque,
        horizon_annees=demande.horizon_annees,
        objectif=demande.objectif,
        repartition_types=repartition_types,
        lignes=sorted(lignes, key=lambda l: l.poids, reverse=True),
        criteres=criteres,
        valeurs_incompletes=valeurs_incompletes,
        commentaire=commentaire,
    )
