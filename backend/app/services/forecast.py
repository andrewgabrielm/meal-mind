"""Feature (d): price volatility forecasting — ARIMA(1,1,0) + GARCH(1,1).

Trains on the monthly WFP retail price history (price_history table). The
Agmarknet snapshot supplies CURRENT prices; it is a single day and has no
history, so volatility is always estimated from the WFP series.

Mean model — ARIMA(1,1,0): first-difference log prices (= log returns), then
AR(1) by ordinary least squares (closed form):
    r_t = c + phi * r_{t-1} + eps_t

Variance model — GARCH(1,1) on the AR residuals, fitted by Gaussian MLE with
a hand-rolled Nelder-Mead simplex (pure stdlib math — no numpy/scipy, same
policy as decay.py):
    sigma2_t = omega + a * eps2_{t-1} + b * sigma2_{t-1},  a+b < 1

Output per commodity: next-month expected price change (%), conditional
volatility (%), and a buy_now / normal / wait_if_possible advisory.

ADVISORY ONLY. The forecast never enters the purchase ILP — the hard budget
ceiling and the 100% budget-adherence claim stay untouched.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

MIN_OBSERVATIONS = 24          # two years of monthly data or no forecast
TREND_BUY_NOW = 2.0            # forecast rise ≥ 2% next month -> buy_now
TREND_WAIT = -2.0              # forecast fall ≤ -2% -> wait_if_possible


@dataclass
class Forecast:
    commodity: str
    trend_pct: float           # expected next-month price change
    volatility_pct: float      # GARCH conditional std-dev of monthly return
    advice: str                # buy_now | normal | wait_if_possible
    n_obs: int
    ar_phi: float
    garch_params: tuple[float, float, float]   # (omega, a, b)


# ---------------------------------------------------------------- mean model

def log_returns(prices: list[float]) -> list[float]:
    return [
        math.log(prices[i] / prices[i - 1])
        for i in range(1, len(prices))
        if prices[i] > 0 and prices[i - 1] > 0
    ]


def fit_ar1(r: list[float]) -> tuple[float, float]:
    """OLS fit of r_t = c + phi r_{t-1}; returns (c, phi)."""
    x, y = r[:-1], r[1:]
    n = len(x)
    if n < 2:
        return (sum(r) / len(r) if r else 0.0), 0.0
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((xi - mx) ** 2 for xi in x)
    sxy = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    phi = sxy / sxx if sxx > 1e-12 else 0.0
    phi = max(-0.99, min(0.99, phi))
    c = my - phi * mx
    return c, phi


# ------------------------------------------------------------ variance model

def _nelder_mead(f, x0: list[float], step: float = 0.5, max_iter: int = 400,
                 tol: float = 1e-9) -> list[float]:
    """Minimal Nelder-Mead simplex for a handful of parameters."""
    n = len(x0)
    simplex = [list(x0)]
    for i in range(n):
        p = list(x0)
        p[i] = p[i] * (1 + step) if p[i] != 0 else step * 0.1
        simplex.append(p)
    fv = [f(tuple(p)) for p in simplex]

    for _ in range(max_iter):
        order = sorted(range(n + 1), key=lambda i: fv[i])
        simplex = [simplex[i] for i in order]
        fv = [fv[i] for i in order]
        if abs(fv[-1] - fv[0]) < tol:
            break
        centroid = [sum(p[i] for p in simplex[:-1]) / n for i in range(n)]
        worst = simplex[-1]
        refl = [centroid[i] + (centroid[i] - worst[i]) for i in range(n)]
        fr = f(tuple(refl))
        if fr < fv[0]:
            exp_ = [centroid[i] + 2 * (centroid[i] - worst[i]) for i in range(n)]
            fe = f(tuple(exp_))
            simplex[-1], fv[-1] = (exp_, fe) if fe < fr else (refl, fr)
        elif fr < fv[-2]:
            simplex[-1], fv[-1] = refl, fr
        else:
            contr = [centroid[i] + 0.5 * (worst[i] - centroid[i]) for i in range(n)]
            fc = f(tuple(contr))
            if fc < fv[-1]:
                simplex[-1], fv[-1] = contr, fc
            else:   # shrink toward best
                best = simplex[0]
                for j in range(1, n + 1):
                    simplex[j] = [(simplex[j][i] + best[i]) / 2 for i in range(n)]
                    fv[j] = f(tuple(simplex[j]))
    return simplex[0]


def garch_nll(params: tuple[float, float, float], eps: list[float]) -> float:
    omega, a, b = params
    if omega <= 0 or a < 0 or b < 0 or a + b >= 0.999:
        return 1e12
    sigma2 = sum(e * e for e in eps) / len(eps) or 1e-8   # start at sample var
    nll = 0.0
    prev_e2 = sigma2
    for e in eps:
        sigma2 = omega + a * prev_e2 + b * sigma2
        sigma2 = max(sigma2, 1e-12)
        nll += 0.5 * (math.log(2 * math.pi * sigma2) + e * e / sigma2)
        prev_e2 = e * e
    return nll


def fit_garch(eps: list[float]) -> tuple[float, float, float]:
    """Fit (omega, a, b) by MLE. Start from variance targeting: a=.1, b=.8."""
    var = sum(e * e for e in eps) / len(eps) or 1e-8
    x0 = [var * 0.1, 0.1, 0.8]
    omega, a, b = _nelder_mead(lambda p: garch_nll(p, eps), x0)
    if omega <= 0 or a < 0 or b < 0 or a + b >= 0.999:   # fell out of bounds
        omega, a, b = var * 0.1, 0.1, 0.8
    return omega, a, b


def conditional_sigma2(eps: list[float], omega: float, a: float, b: float) -> float:
    """One-step-ahead conditional variance after the whole sample."""
    sigma2 = sum(e * e for e in eps) / len(eps) or 1e-8
    prev_e2 = sigma2
    for e in eps:
        sigma2 = omega + a * prev_e2 + b * sigma2
        prev_e2 = e * e
    return omega + a * prev_e2 + b * sigma2


# ---------------------------------------------------------------- public API

def forecast_commodity(commodity: str, prices: list[float]) -> Forecast | None:
    """prices: chronological monthly price-per-kg series."""
    return _forecast_cached(commodity, tuple(prices[-180:]))   # last 15 years


@lru_cache(maxsize=256)
def _forecast_cached(commodity: str, prices: tuple[float, ...]) -> Forecast | None:
    r = log_returns(list(prices))
    if len(r) < MIN_OBSERVATIONS:
        return None

    c, phi = fit_ar1(r)
    eps = [r[i] - (c + phi * r[i - 1]) for i in range(1, len(r))]
    omega, a, b = fit_garch(eps)

    r_next = c + phi * r[-1]
    trend_pct = (math.exp(r_next) - 1.0) * 100.0
    sigma = math.sqrt(max(conditional_sigma2(eps, omega, a, b), 0.0))
    volatility_pct = sigma * 100.0

    if trend_pct >= TREND_BUY_NOW:
        advice = "buy_now"
    elif trend_pct <= TREND_WAIT:
        advice = "wait_if_possible"
    else:
        advice = "normal"

    return Forecast(
        commodity=commodity,
        trend_pct=round(trend_pct, 2),
        volatility_pct=round(volatility_pct, 2),
        advice=advice,
        n_obs=len(prices),
        ar_phi=round(phi, 4),
        garch_params=(round(omega, 8), round(a, 4), round(b, 4)),
    )
