"""Auto-amélioration (couche de second ordre, partie « améliorer »).

Boucle fermée sur le score propriétaire : la fonction objectif est le
**pouvoir prédictif** mesuré par l'auto-observation (corrélation de Spearman
entre le score et les rendements réellement observés). L'optimiseur explore
les pondérations par montée de coordonnées bornée :

- exploration par pas de `meta.optimisation.pas` points ;
- chaque critère reste à ± `ajustement_max` points de sa valeur actuelle
  (garde-fou : l'auto-amélioration ajuste, elle ne bouleverse pas) ;
- une suggestion n'est émise que si le gain de corrélation dépasse
  `gain_minimal` (anti-sur-apprentissage sur le bruit).

Par défaut la suggestion est **proposée** et attend une validation humaine
dans l'écran Système ; si `meta.optimisation.auto_appliquer` est vrai, elle
est appliquée automatiquement après chaque mise à jour planifiée. Toute
application est journalisée et réversible (l'historique des suggestions
conserve les pondérations successives).
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from peadvisor.config import charger_scoring, charger_settings, sauvegarder_scoring
from peadvisor.models import JournalMaj, SuggestionPonderations
from peadvisor.services.introspection import correlation_spearman, fenetre_observation
from peadvisor.services.scoring import calculer_score_global, scorer_tous


def _correlation(ponderations: dict[str, float], observations: list[dict]) -> float | None:
    cfg = {"ponderations": ponderations, "note_donnee_manquante": 50}
    scores = [calculer_score_global(o["sous_scores"], cfg) for o in observations]
    return correlation_spearman(scores, [o["retour"] for o in observations])


def proposer_ponderations(session: Session) -> dict[str, Any]:
    """Cherche des pondérations plus prédictives ; crée une suggestion si gain réel."""
    params = charger_settings().get("meta", {}).get("optimisation", {})
    pas = float(params.get("pas", 5))
    ajustement_max = float(params.get("ajustement_max", 15))
    gain_minimal = float(params.get("gain_minimal", 0.02))

    observations = fenetre_observation(session)
    retours = [o["retour"] for o in observations]
    if len(observations) < 5 or len({round(r, 6) for r in retours}) < 2:
        return {"suggestion": None,
                "detail": f"Historique insuffisant ({len(observations)} observation(s) "
                          "exploitables) : le pouvoir prédictif ne peut pas encore être mesuré. "
                          "Laisser plusieurs mises à jour s'accumuler."}

    actuelles = {c: float(p) for c, p in charger_scoring()["ponderations"].items()}
    correlation_avant = _correlation(actuelles, observations)
    if correlation_avant is None:
        return {"suggestion": None, "detail": "Corrélation non calculable (scores constants)."}

    # Montée de coordonnées bornée autour des pondérations actuelles.
    candidates = dict(actuelles)
    meilleure = correlation_avant
    for _ in range(4):  # quelques passes suffisent sur les 10 familles de critères
        progres = False
        for critere in candidates:
            for delta in (pas, -pas):
                essai = dict(candidates)
                nouvelle = essai[critere] + delta
                if nouvelle < 0 or abs(nouvelle - actuelles[critere]) > ajustement_max:
                    continue
                essai[critere] = nouvelle
                corr = _correlation(essai, observations)
                if corr is not None and corr > meilleure + 1e-9:
                    candidates, meilleure, progres = essai, corr, True
        if not progres:
            break

    if meilleure < correlation_avant + gain_minimal:
        return {"suggestion": None, "correlation_actuelle": correlation_avant,
                "detail": f"Les pondérations actuelles sont déjà proches de l'optimum local "
                          f"(corrélation {correlation_avant:.2f}) : aucune suggestion émise."}

    # Renormalisation sur 100 pour rester lisible.
    total = sum(candidates.values()) or 1.0
    candidates = {c: round(p * 100 / total, 1) for c, p in candidates.items()}

    suggestion = SuggestionPonderations(
        ponderations=json.dumps(candidates),
        correlation_avant=correlation_avant,
        correlation_apres=meilleure,
        statut="proposee",
        commentaire=f"Optimisation sur {len(observations)} observations : corrélation "
                    f"score→rendement {correlation_avant:.2f} → {meilleure:.2f}.",
    )
    session.add(suggestion)
    session.commit()
    session.refresh(suggestion)
    return {"suggestion": suggestion, "detail": suggestion.commentaire}


def appliquer_suggestion(session: Session, suggestion_id: int) -> dict[str, Any]:
    """Applique une suggestion : écrit scoring.yaml, rescore tout, journalise."""
    suggestion = session.get(SuggestionPonderations, suggestion_id)
    if suggestion is None or suggestion.statut != "proposee":
        raise ValueError("Suggestion inconnue ou déjà traitée.")
    cfg = charger_scoring()
    cfg["ponderations"] = {c: float(p) for c, p in json.loads(suggestion.ponderations).items()}
    sauvegarder_scoring(cfg)
    nb = scorer_tous(session)
    suggestion.statut = "appliquee"
    session.add(JournalMaj(
        traitement="optimisation_ponderations", statut="succes",
        detail=f"Suggestion #{suggestion.id} appliquée ({suggestion.commentaire}) — "
               f"{nb} actif(s) rescorés.",
        nb_crees=0, nb_maj=nb, nb_doublons=0, nb_erreurs=0))
    session.commit()
    return {"ponderations": cfg["ponderations"], "actifs_recalcules": nb}


def rejeter_suggestion(session: Session, suggestion_id: int) -> None:
    suggestion = session.get(SuggestionPonderations, suggestion_id)
    if suggestion is None or suggestion.statut != "proposee":
        raise ValueError("Suggestion inconnue ou déjà traitée.")
    suggestion.statut = "rejetee"
    session.commit()


def cycle_second_ordre(session: Session) -> dict[str, Any]:
    """Cycle complet observation → amélioration, utilisé par le planificateur."""
    from peadvisor.services.introspection import observer

    rapport = observer(session)
    resultat = proposer_ponderations(session)
    suggestion = resultat.get("suggestion")
    auto = charger_settings().get("meta", {}).get("optimisation", {}).get("auto_appliquer", False)
    if suggestion is not None and auto:
        appliquer_suggestion(session, suggestion.id)
        resultat["detail"] += " Appliquée automatiquement (meta.optimisation.auto_appliquer)."
    return {"rapport": rapport, "optimisation": resultat.get("detail")}
