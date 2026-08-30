# PEAdvisor

Application d'aide à la décision pour l'investissement via un **Plan d'Épargne en
Actions (PEA)**. Elle centralise les valeurs éligibles (actions, ETF, OPCVM),
calcule un **score propriétaire paramétrable**, classe les actifs par **analyse
multicritère (TOPSIS ou score pondéré)** et propose des **allocations de
portefeuille** adaptées au profil de l'investisseur (capital, risque 1-7,
horizon, objectif).

> ⚠️ PEAdvisor est un outil d'aide à la décision : ses propositions sont
> indicatives et ne constituent pas un conseil en investissement. Le jeu de
> données fourni (`seed`) est **illustratif**, pas du temps réel.

## Démarrage rapide

```bash
python -m venv .venv && source .venv/bin/activate   # optionnel mais recommandé
pip install -r requirements.txt
python run.py
```

Puis ouvrir :

- **Tableau de bord** : http://localhost:8000
- **API REST (Swagger)** : http://localhost:8000/docs

Au premier lancement, la base SQLite (`peadvisor.db`) est créée et alimentée
automatiquement depuis la source active. Les tests s'exécutent avec
`python -m pytest tests/`.

## Fonctionnalités

| Domaine | Contenu |
|---|---|
| Référentiel | Actions, ETF, OPCVM éligibles PEA — 20+ champs par actif (ISIN, secteur, PER, rendement, ESG, volatilité, consensus…) |
| Import | 9 sources branchables — API (EODHD, Marketstack, AlphaVantage, TwelveData, FMP, `stooq`/`yahoo` sans clé) **et scraping Boursorama** ; gestion des clés API (fichier gitignoré ou variables d'env.), bouton « Tester » diagnostique par source, colonne **Source** par actif, normalisation, **dédoublonnage par ISIN**, journal |
| Scoring | Score 0-100, 10 familles de critères, **pondérations modifiables** depuis l'interface (recalcul immédiat), historique des scores |
| Quantitatif | Historiques de cours (~3 ans) et indicateurs calculés : volatilité réalisée (réinjectée dans le scoring), perf 1 an, drawdown max, Sharpe, Sortino, VaR 95 %, corrélations |
| Décision | Matrice multicritère : score pondéré ou **TOPSIS** |
| Allocation | Capital + risque (1-7) + horizon + objectif (croissance / dividendes / équilibré) → portefeuille avec contraintes de diversification |
| Simulateur | Projection PEA : versement initial + versements programmés, réinvestissement des dividendes, 3 scénarios (prudent/médian/optimiste), **fiscalité PEA estimée**, trajectoire graphique |
| Tableau de bord | KPI, répartitions type/secteur/pays, tops (opportunités, dividendes, croissance), classement TOPSIS |
| Watchlist | Suivi de valeurs, ajout/retrait en un clic |
| Automatisation | Mise à jour quotidienne ou hebdomadaire planifiée (APScheduler), activable dans `config/settings.yaml` |
| Second ordre | **Auto-observation** (complétude, fraîcheur, anomalies, dérive des scores, pouvoir prédictif) et **auto-amélioration** (recommandations + optimisation des pondérations validée sur les rendements réalisés, supervision humaine par défaut) — écran Système |
| Agent (MCP) | Serveur MCP (`mcp_server.py`) : 18 outils pour piloter PEAdvisor depuis Claude Desktop — analyse, explications, allocation, auto-diagnostic (voir docs/08) |

## Paramétrage (sans toucher au code)

- `config/settings.yaml` — source de données, PEA-PME, planification des mises à
  jour, contraintes d'allocation (plafond par ligne/secteur, part minimale de fonds).
- `config/scoring.yaml` — pondérations et bornes de normalisation du score
  (modifiables aussi depuis l'écran **Paramètres** de l'interface).

## Documentation

| Document | Contenu |
|---|---|
| [docs/01-choix-technologiques.md](docs/01-choix-technologiques.md) | Pourquoi Python / FastAPI / SQLite, alternatives écartées |
| [docs/02-architecture.md](docs/02-architecture.md) | Architecture en 4 niveaux, arborescence, flux de données |
| [docs/03-modele-donnees.md](docs/03-modele-donnees.md) | Tables, champs, règles de gestion |
| [docs/04-scoring-et-decision.md](docs/04-scoring-et-decision.md) | Normalisation, score propriétaire, TOPSIS |
| [docs/05-allocation.md](docs/05-allocation.md) | Profils de risque, poches, contraintes de diversification |
| [docs/06-roadmap.md](docs/06-roadmap.md) | État d'avancement vs cahier des charges, prochaines étapes |
| [docs/07-auto-observation.md](docs/07-auto-observation.md) | Couche de second ordre : auto-observation et auto-amélioration |
| [docs/08-agent-mcp.md](docs/08-agent-mcp.md) | Agent : serveur MCP pour Claude Desktop (installation, outils, exemples) |
| [docs/09-sources-donnees.md](docs/09-sources-donnees.md) | Sources de données : intégrées, clés API, étude comparative (~30 fournisseurs) |

## Structure du projet

```
PEAdvisor/
├── config/            # Paramétrage YAML (settings + scoring)
├── docs/              # Documentation explicative
├── peadvisor/         # Code applicatif
│   ├── sources/       # L1 — connecteurs de données (seed, yahoo, …)
│   ├── services/      # L2/L3 — import, scoring, décision, allocation, planificateur
│   ├── routers/       # API REST (FastAPI)
│   ├── data/          # Jeu de données de démonstration
│   ├── models.py      # Modèle de données (SQLAlchemy)
│   └── main.py        # Point d'entrée FastAPI
├── static/            # L4 — tableau de bord web (HTML/CSS/JS, sans dépendance)
├── tests/             # Tests unitaires (pytest)
└── run.py             # Lancement du serveur
```
