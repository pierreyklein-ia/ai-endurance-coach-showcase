"""Agent Expert = RETRIEVAL, pas génération.

Le nœud RAG ne fait aucun appel LLM : il récupère des passages sourcés et les
injecte dans le contexte du Coach. Économie : 1 appel LLM par échange, et la
traçabilité (evidence_ids) permet d'auditer chaque décision devant le jury.

Prod : pgvector (même Postgres que le checkpointer LangGraph) + embeddings
multilingues ; corpus limité à des sources en accès ouvert / sous licence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Evidence:
    id: str
    source: str          # référence bibliographique (DOI)
    text: str            # extrait verbatim, ou synthèse de l'équipe marquée « Synthèse : » (coach/knowledge.py)
    supports_rules: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()


class RetrieverPort(Protocol):
    def search(self, query: str, tags: set[str], k: int = 3) -> list[Evidence]: ...


class TagRetriever:
    """Retriever minimal par tags, pour le MVP et les tests.

    Pour ~50-200 fiches bien taguées, un filtrage par tags + BM25 bat souvent
    une recherche vectorielle en précision ET en coût. Passer au vectoriel
    quand le corpus le justifie, pas avant.
    """

    def __init__(self, corpus: list[Evidence]):
        self.corpus = corpus

    def search(self, query: str, tags: set[str], k: int = 4) -> list[Evidence]:
        return rank(self.corpus, tags, k)


def rank(corpus: list[Evidence], tags: set[str], k: int) -> list[Evidence]:
    """Fiches qui partagent le plus de thèmes avec la question ; à égalité, un thème rare (contraste, sommeil)
    compte plus qu'un thème présent partout (charge, renfo), pour que la fiche la plus précise remonte (v0.22)."""
    df: dict[str, int] = {}
    for e in corpus:
        for t in set(e.tags):
            df[t] = df.get(t, 0) + 1
    scored = [(len(m), sum(1 / df[t] for t in m), i, e) for i, e in enumerate(corpus) for m in [tags & set(e.tags)]]
    return [e for n, w, i, e in sorted(scored, key=lambda x: (-x[0], -x[1], x[2])) if n > 0][:k]


# PLACEHOLDERS : à remplacer par des extraits verbatim des articles.
# Les références existent ; le champ `text` n'est PAS une citation.
SEED_CORPUS = [
    Evidence("ev_detraining_01", "Mujika & Padilla, Sports Med 2000 (détraining court terme)",
             "[PLACEHOLDER — insérer l'extrait sur la cinétique de perte de VO2max]",
             ("R05_catch_up",), ("missed_sessions", "detraining")),
    Evidence("ev_acwr_critique_01", "Impellizzeri et al., IJSPP 2020 (limites de l'ACWR)",
             "[PLACEHOLDER — insérer l'extrait sur les limites conceptuelles de l'ACWR]",
             ("R03_acwr",), ("load", "acwr")),
    Evidence("ev_illness_01", "[À SOURCER : retour à l'entraînement après infection]",
             "[PLACEHOLDER]", ("R02_illness",), ("illness",)),
]
