from peadvisor.config import CRITERES_SCORE
from peadvisor.services.decision import CRITERES, classer
from peadvisor.services.importer import importer
from peadvisor.services.scoring import calculer_sous_scores
from peadvisor.models import Actif


def test_topsis_couvre_toutes_les_familles_du_score(session):
    """Une famille de critères absente de la matrice verrait sa pondération
    ignorée par TOPSIS, qui classerait sur un score différent du score pondéré."""
    assert set(CRITERES) == set(CRITERES_SCORE)

    importer(session, "seed")
    actif = session.query(Actif).first()
    # Les sous-notes réellement produites couvrent bien les critères attendus.
    assert set(calculer_sous_scores(actif)) == set(CRITERES)


def test_topsis_classe_tous_les_actifs(session):
    importer(session, "seed")
    actifs = session.query(Actif).all()
    classement = classer(actifs, "topsis")
    assert len(classement) == len(actifs)
    coefficients = [c for _, c in classement]
    assert all(0.0 <= c <= 1.0 for c in coefficients)
    assert coefficients == sorted(coefficients, reverse=True)


def test_classement_pondere_suit_le_score(session):
    importer(session, "seed")
    actifs = session.query(Actif).all()
    classement = classer(actifs, "weighted")
    scores = [s for _, s in classement]
    assert scores == sorted(scores, reverse=True)
