"""Feature (a): package-size optimisation — two integer programs (PuLP/CBC).

Stage 1 (purchase):
    minimise    sum x_ij * c_ij                       x_ij in Z+
    subject to  pantry_i + sum_j x_ij * w_ij >= R_i   (coverage)
                sum x_ij * c_ij <= B                  (hard budget ceiling)

Surplus is  leftover_i = pantry_i + bought_i - R_i  — the number the whole
feature exists to expose. If infeasible under budget, optional commodities are
dropped in descending cost order and the model re-solved, rather than raising.

Stage 2 (48-hour utilisation):
    maximise    sum_r y_r * value_r                   y_r in {0,1}
    where       value_r = sum_i urgency_i * min(needs_ri, available_i)
    subject to  sum_r y_r <= slots
                sum_r y_r * needs_ri <= available_i

available_i is pantry PLUS what stage 1 just bought — not surplus alone
(constraining to surplus made at-risk pantry stock invisible to the solver).
urgency_i comes straight from the decay model: THIS is where features (a) and
(b) couple. min(...) is precomputed so the objective stays linear.

No SQL, no HTTP. Money math lives here and nowhere else.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pulp


@dataclass
class Pack:
    size_g: float
    price_rs: float


@dataclass
class PurchaseLine:
    commodity: str
    required_g: float
    pantry_g: float
    bought_g: float
    surplus_g: float
    cost_rs: float
    packs: list[tuple[Pack, int]] = field(default_factory=list)  # (pack, count)
    optional_dropped: bool = False


@dataclass
class PurchaseResult:
    lines: list[PurchaseLine]
    total_cost_rs: float
    budget_rs: float
    feasible: bool
    dropped: list[str] = field(default_factory=list)


@dataclass
class LeftoverRecipe:
    recipe_id: int
    needs_g: dict[str, float]      # commodity -> grams


@dataclass
class LeftoverPick:
    recipe_id: int
    value: float
    uses: dict[str, float]


def _solve(prob: pulp.LpProblem) -> bool:
    prob.solve(pulp.PULP_CBC_CMD(msg=False))
    return pulp.LpStatus[prob.status] == "Optimal"


def solve_purchase(
    requirements: dict[str, float],          # commodity -> grams needed
    pantry: dict[str, float],                # commodity -> grams on hand
    packs: dict[str, list[Pack]],            # commodity -> purchasable packs
    budget_rs: float,
    optional: set[str] = frozenset(),
) -> PurchaseResult:
    """Minimum-cost integer pack purchase under a hard budget ceiling."""
    # commodities with nothing to buy (covered by pantry or no packs known)
    active = {
        c: req for c, req in requirements.items()
        if req - pantry.get(c, 0.0) > 1e-9 and packs.get(c)
    }
    dropped: list[str] = []

    while True:
        prob = pulp.LpProblem("purchase", pulp.LpMinimize)
        x: dict[tuple[str, int], pulp.LpVariable] = {}
        for c in active:
            for j, p in enumerate(packs[c]):
                x[(c, j)] = pulp.LpVariable(f"x_{c}_{j}", lowBound=0, cat="Integer")

        cost = pulp.lpSum(x[(c, j)] * packs[c][j].price_rs for (c, j) in x)
        prob += cost
        for c, req in active.items():
            prob += (
                pulp.lpSum(x[(c, j)] * packs[c][j].size_g for j in range(len(packs[c])))
                >= req - pantry.get(c, 0.0)
            ), f"cover_{c}"
        prob += cost <= budget_rs, "budget_ceiling"

        if _solve(prob):
            feasible = True
            break

        # Infeasible under budget: drop the most expensive optional commodity
        # (by cheapest way to cover it) and re-solve.
        droppable = [c for c in active if c in optional]
        if not droppable:
            feasible = False
            break

        def cover_cost(c: str) -> float:
            need = active[c] - pantry.get(c, 0.0)
            return min(
                p.price_rs * max(1, -(-need // p.size_g))  # ceil division
                for p in packs[c]
            )

        victim = max(droppable, key=cover_cost)
        dropped.append(victim)
        del active[victim]

    lines: list[PurchaseLine] = []
    total = 0.0
    for c, req in requirements.items():
        pantry_g = pantry.get(c, 0.0)
        bought_g = 0.0
        cost_c = 0.0
        chosen: list[tuple[Pack, int]] = []
        if feasible and c in active:
            for j, p in enumerate(packs[c]):
                n = int(round(x[(c, j)].value() or 0))
                if n > 0:
                    chosen.append((p, n))
                    bought_g += n * p.size_g
                    cost_c += n * p.price_rs
        total += cost_c
        lines.append(PurchaseLine(
            commodity=c, required_g=req, pantry_g=pantry_g, bought_g=bought_g,
            surplus_g=max(0.0, pantry_g + bought_g - req), cost_rs=cost_c,
            packs=chosen, optional_dropped=c in dropped,
        ))
    return PurchaseResult(lines=lines, total_cost_rs=total, budget_rs=budget_rs,
                          feasible=feasible, dropped=dropped)


def solve_leftover(
    recipes: list[LeftoverRecipe],
    available: dict[str, float],             # pantry + bought (grams)
    urgency: dict[str, float],               # from the decay model — the coupling
    slots: int = 4,
) -> list[LeftoverPick]:
    """48-hour utilisation: pick recipes that consume urgent stock."""
    if not recipes or slots <= 0:
        return []

    # value_r precomputed so the objective stays linear
    values: dict[int, float] = {}
    uses: dict[int, dict[str, float]] = {}
    for r in recipes:
        v = 0.0
        u: dict[str, float] = {}
        for c, need in r.needs_g.items():
            usable = min(need, available.get(c, 0.0))
            if usable > 0:
                u[c] = usable
                v += urgency.get(c, 0.0) * usable
        values[r.recipe_id] = v
        uses[r.recipe_id] = u

    prob = pulp.LpProblem("leftover", pulp.LpMaximize)
    y = {r.recipe_id: pulp.LpVariable(f"y_{r.recipe_id}", cat="Binary") for r in recipes}
    prob += pulp.lpSum(y[r.recipe_id] * values[r.recipe_id] for r in recipes)
    prob += pulp.lpSum(y.values()) <= slots, "slots"

    commodities = {c for r in recipes for c in r.needs_g}
    for c in commodities:
        prob += (
            pulp.lpSum(y[r.recipe_id] * r.needs_g[c] for r in recipes if c in r.needs_g)
            <= available.get(c, 0.0)
        ), f"stock_{c}"

    if not _solve(prob):
        return []

    picks = [
        LeftoverPick(recipe_id=r.recipe_id, value=values[r.recipe_id], uses=uses[r.recipe_id])
        for r in recipes
        if (y[r.recipe_id].value() or 0) > 0.5 and values[r.recipe_id] > 0
    ]
    picks.sort(key=lambda p: -p.value)
    return picks
