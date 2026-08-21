"""Feature (c): menu variety enforcement — Shannon entropy over dish families.

H = -sum(p_k log2 p_k) over dish-family clusters in a rolling 14-day window.

Three deliberate departures from the system design document (all defensible):
1. Clusters are DISH FAMILIES, not recipes. Per-recipe clusters keep entropy
   near maximum and the check never fires; what feels repetitive is the base
   repeating. Cluster = dominant non-staple mass, with proteins taking
   precedence over heavier vegetables (palak paneer is a paneer dish) and
   curd/milk excluded from that precedence (a side of curd does not make aloo
   paratha a dairy dish). Rice only takes the cluster when overwhelmingly
   dominant, or every main would read as rice.
2. Entropy is normalised against ATTAINABLE clusters: K comes from the
   diet-filtered candidate pool, so a vegetarian household is not judged
   against meat clusters it can never reach.
3. Penalties are PROACTIVE: applied during retrieval ranking instead of a
   measure/penalise/re-solve loop. H is reported as the measured outcome.

Penalty per over-represented cluster is (p_k - 1/K) * strength, engaged only
when normalised entropy < HMIN. strength is capped at 4.0 — well under
retrieval's +10.0 forced-include bonus. VARIETY MUST NEVER OVERRIDE FEATURE
(b): a dying bunch of spinach gets cooked even if it is the third spinach dish
this fortnight.

History is capped by MEALS (days * 3), not plans: regenerating five times in
one sitting is not five weeks of eating.

Pure math + the data tables. No SQL, no HTTP.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from scripts.ingredient_tables import DIET_EXCLUDED_CLUSTERS, PROTEIN_PRECEDENCE, info

WINDOW_DAYS = 14
MEALS_PER_DAY = 3
HMIN = 0.8                      # normalised-entropy floor before penalties engage
PENALTY_STRENGTH = 4.0          # CAP — must stay below the +10.0 forced-include bonus
RICE_DOMINANCE = 0.6            # rice takes the cluster only above this mass share
PROTEIN_MIN_SHARE = 0.10        # protein must be at least this share of non-staple mass
PROTEIN_MIN_G = 40.0            # ...and at least this absolute mass — a teaspoon of
                                # tempering urad dal does not make curd rice a dal dish


@dataclass
class VarietyReport:
    entropy_bits: float
    max_entropy_bits: float
    normalised_entropy: float
    attainable_clusters: int
    penalties_engaged: bool
    distribution: dict[str, float]
    penalties: dict[str, float]


def assign_cluster(ingredients: list[tuple[str, float]]) -> str:
    """Dish family from (commodity, grams) pairs.

    Precedence: rice if overwhelmingly dominant -> protein (fixed precedence
    order, curd/milk excluded because they are not flagged protein) ->
    dominant non-staple mass -> dominant overall mass."""
    total = sum(g for _, g in ingredients) or 1.0

    rice_mass = sum(g for c, g in ingredients if info(c)["cluster"] == "rice_dish")
    if rice_mass / total >= RICE_DOMINANCE:
        return "rice_dish"

    nonstaple = [(c, g) for c, g in ingredients if not info(c).get("staple")]
    nonstaple_total = sum(g for _, g in nonstaple) or 1.0

    present = {c for c, g in ingredients if g > 0}
    for p in PROTEIN_PRECEDENCE:
        if p in present:
            mass = sum(g for c, g in ingredients if c == p)
            if mass >= PROTEIN_MIN_G and mass / nonstaple_total >= PROTEIN_MIN_SHARE:
                return info(p)["cluster"]

    if nonstaple:
        dominant = max(nonstaple, key=lambda cg: cg[1])[0]
        return info(dominant)["cluster"]

    if ingredients:
        dominant = max(ingredients, key=lambda cg: cg[1])[0]
        return info(dominant)["cluster"]
    return "mixed_veg"


def attainable_clusters(candidate_clusters: set[str], diet: str) -> set[str]:
    """Clusters the household can actually reach: present in the diet-filtered
    candidate pool and not excluded by the diet."""
    excluded = DIET_EXCLUDED_CLUSTERS.get(diet, set())
    return {c for c in candidate_clusters if c not in excluded}


def shannon_entropy(counts: dict[str, int]) -> float:
    total = sum(counts.values())
    if total == 0:
        return 0.0
    h = 0.0
    for n in counts.values():
        if n > 0:
            p = n / total
            h -= p * math.log2(p)
    return h


def cap_history(history: list[str], days: int = WINDOW_DAYS) -> list[str]:
    """Most recent days*3 meals — capped by meals, not plans."""
    cap = days * MEALS_PER_DAY
    return history[-cap:] if cap > 0 else []


def measure(history_clusters: list[str], candidate_clusters: set[str],
            diet: str) -> VarietyReport:
    """Entropy of the recent meal history, normalised by attainable K, plus
    the retrieval penalties for over-represented clusters."""
    history = cap_history(history_clusters)
    attain = attainable_clusters(candidate_clusters | set(history), diet)
    k = max(len(attain), 1)
    max_h = math.log2(k) if k > 1 else 1.0

    counts: dict[str, int] = {}
    for c in history:
        counts[c] = counts.get(c, 0) + 1
    h = shannon_entropy(counts)
    norm = h / max_h if max_h > 0 else 1.0
    if not history:
        norm = 1.0   # nothing eaten yet — nothing to penalise

    engaged = norm < HMIN
    penalties: dict[str, float] = {}
    if engaged and history:
        total = len(history)
        fair = 1.0 / k
        for cluster, n in counts.items():
            p = n / total
            if p > fair:
                penalties[cluster] = (p - fair) * PENALTY_STRENGTH

    return VarietyReport(
        entropy_bits=round(h, 4),
        max_entropy_bits=round(max_h, 4),
        normalised_entropy=round(norm, 4),
        attainable_clusters=k,
        penalties_engaged=engaged,
        distribution={c: round(n / len(history), 4) for c, n in counts.items()} if history else {},
        penalties=penalties,
    )


def penalty_for(report: VarietyReport, cluster: str) -> float:
    """Retrieval-ranking penalty for a candidate recipe's cluster."""
    return report.penalties.get(cluster, 0.0)
