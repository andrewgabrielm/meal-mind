"""Feature (b): Bayesian shelf-life decay.

Weibull survival  S(t) = exp(-(t / (alpha * gamma_S))^beta)  with beta > 1
(increasing hazard, as real spoilage shows). gamma_S is the storage
multiplier. alpha (room-temperature scale, days) is NOT fixed: with beta held
constant, reparameterising theta = alpha^beta admits an inverse-gamma
conjugate prior:

    theta ~ InvGamma(a0, b0)
    posterior a = a0 + (# observed spoilage events)
    posterior b = b0 + sum (room-equivalent lifetime)^beta   over ALL obs
    alpha_hat   = (b / (a - 1))^(1/beta)        (posterior mean of theta)

Censored observations (consumed while still good) contribute to b but not a —
the standard Weibull right-censoring likelihood. The prior mean is calibrated
to the literature baseline, so zero data reproduces textbook shelf life and
the estimate adapts as POST /pantry/spoilage collects outcomes.

Urgency is the CONDITIONAL probability, not the plain CDF:
    P(spoils in next H | survived to t) = 1 - S(t+H) / S(t)

Pure stdlib math. No numpy, no scipy, no SQL, no HTTP.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

STORAGE_GAMMA = {"room": 1.0, "fridge": 3.0, "freezer": 12.0}

# item class -> (literature room shelf-life days = prior mean of alpha, beta)
CLASS_PARAMS: dict[str, tuple[float, float]] = {
    "leafy_green":    (2.5, 1.8),
    "vegetable":      (5.0, 2.0),
    "root_vegetable": (14.0, 2.0),
    "fruit":          (5.0, 2.0),
    "dairy_fresh":    (2.0, 2.2),
    "egg":            (21.0, 2.5),
    "meat_fresh":     (1.0, 2.2),
    "staple_dry":     (180.0, 1.5),
    "spice":          (365.0, 1.2),
    "oil_fat":        (270.0, 1.3),
}
DEFAULT_CLASS = "vegetable"

PRIOR_A0 = 3.0          # weakly informative; mean exists for a0 > 1
URGENCY_FORCE_THRESHOLD = 0.45   # forced-include cutoff for the planner


@dataclass
class Observation:
    """One spoilage outcome. lifetime_days is actual elapsed time under
    `storage`; the room-equivalent conversion happens here."""
    item_class: str
    storage: str
    lifetime_days: float
    spoiled: bool


@dataclass
class Assessment:
    commodity: str
    item_class: str
    storage: str
    age_days: float
    survival: float
    urgency: float
    forced_include: bool
    alpha_days: float
    learned_from_observations: bool
    pantry_item_id: int = 0
    quantity_g: float = 0.0
    extras: dict = field(default_factory=dict)


def class_params(item_class: str) -> tuple[float, float]:
    return CLASS_PARAMS.get(item_class, CLASS_PARAMS[DEFAULT_CLASS])


def prior_hyperparams(item_class: str) -> tuple[float, float]:
    """(a0, b0) calibrated so E[theta] = b0/(a0-1) = alpha0^beta."""
    alpha0, beta = class_params(item_class)
    b0 = (alpha0 ** beta) * (PRIOR_A0 - 1.0)
    return PRIOR_A0, b0


def room_equivalent_days(days: float, storage: str) -> float:
    """Time rescaled to room conditions: fridge days age 1/3 as fast."""
    return days / STORAGE_GAMMA.get(storage, 1.0)


def posterior_alpha(item_class: str, observations: list[Observation]) -> tuple[float, bool]:
    """Posterior point estimate of alpha (room days) for one item class.
    Returns (alpha_hat, learned) where learned=False means prior only."""
    _, beta = class_params(item_class)
    a, b = prior_hyperparams(item_class)
    learned = False
    for obs in observations:
        if obs.item_class != item_class:
            continue
        t_re = room_equivalent_days(obs.lifetime_days, obs.storage)
        b += t_re ** beta
        if obs.spoiled:
            a += 1.0
        learned = True
    alpha_hat = (b / (a - 1.0)) ** (1.0 / beta)
    return alpha_hat, learned


def survival(t_days: float, alpha: float, beta: float, storage: str) -> float:
    """S(t) = exp(-(t/(alpha*gamma_S))^beta), clamped for numeric safety."""
    if t_days <= 0:
        return 1.0
    gamma = STORAGE_GAMMA.get(storage, 1.0)
    z = (t_days / (alpha * gamma)) ** beta
    return math.exp(-min(z, 700.0))


def conditional_spoil_prob(age_days: float, horizon_days: float,
                           alpha: float, beta: float, storage: str) -> float:
    """P(spoils within horizon | survived to age) = 1 - S(t+H)/S(t)."""
    s_now = survival(age_days, alpha, beta, storage)
    if s_now <= 1e-12:
        return 1.0
    s_later = survival(age_days + horizon_days, alpha, beta, storage)
    return max(0.0, min(1.0, 1.0 - s_later / s_now))


def assess_item(*, commodity: str, item_class: str, storage: str, age_days: float,
                observations: list[Observation], horizon_days: float = 2.0,
                pantry_item_id: int = 0, quantity_g: float = 0.0) -> Assessment:
    _, beta = class_params(item_class)
    alpha_hat, learned = posterior_alpha(item_class, observations)
    s = survival(age_days, alpha_hat, beta, storage)
    u = conditional_spoil_prob(age_days, horizon_days, alpha_hat, beta, storage)
    return Assessment(
        commodity=commodity, item_class=item_class, storage=storage,
        age_days=age_days, survival=s, urgency=u,
        forced_include=u >= URGENCY_FORCE_THRESHOLD,
        alpha_days=alpha_hat, learned_from_observations=learned,
        pantry_item_id=pantry_item_id, quantity_g=quantity_g,
    )


def assess_pantry(items: list[dict], observations: list[Observation],
                  horizon_days: float = 2.0) -> list[Assessment]:
    """items: [{id, commodity, item_class, storage, age_days, quantity_g}]"""
    return [
        assess_item(
            commodity=it["commodity"], item_class=it["item_class"],
            storage=it["storage"], age_days=it["age_days"],
            observations=observations, horizon_days=horizon_days,
            pantry_item_id=it.get("id", 0), quantity_g=it.get("quantity_g", 0.0),
        )
        for it in items
    ]


def urgency_weights(assessments: list[Assessment]) -> dict[str, float]:
    """commodity -> max urgency across its pantry items. THE coupling output:
    these weights become the objective coefficients of the stage-2 ILP."""
    weights: dict[str, float] = {}
    for a in assessments:
        weights[a.commodity] = max(weights.get(a.commodity, 0.0), a.urgency)
    return weights


def forced_commodities(assessments: list[Assessment]) -> list[str]:
    seen: list[str] = []
    for a in assessments:
        if a.forced_include and a.commodity not in seen:
            seen.append(a.commodity)
    return seen
