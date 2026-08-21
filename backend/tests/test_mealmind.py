"""MealMind test suite.

Covers the three algorithms (decay, optimizer, variety), the forecast model,
price resolution, the planner pipeline with LLM grounding, the API surface
including the UX-revamp endpoints, and the ingest parser. Everything runs
offline — no API keys, no network.
"""
from __future__ import annotations

import math
import os
import tempfile

# isolated DB for the whole suite — must precede app imports
_db_fd, _db_path = tempfile.mkstemp(prefix="mealmind_test_", suffix=".db")
os.close(_db_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path}"
# tests are OFFLINE by contract: a real key in .env must never reach them
# (env vars outrank .env in pydantic-settings)
os.environ["LLM_API_KEY"] = ""
os.environ["QUICKCOMMERCE_API_KEY"] = ""

import pytest                                              # noqa: E402
from fastapi.testclient import TestClient                  # noqa: E402

from app.main import app                                   # noqa: E402
from app.services import decay, forecast, variety          # noqa: E402
from app.services.optimizer import (                       # noqa: E402
    LeftoverRecipe, Pack, solve_leftover, solve_purchase,
)
from app.services.planner import (                         # noqa: E402
    CandidateRecipe, HeuristicPlanner, LLMPlanner, PlanInputs,
    build_plan, filter_candidates, rank_candidates, requirements_for, Meal,
)
from app.services.pricing import resolve_prices            # noqa: E402
from app.services.quickcommerce import match_score, parse_quantity_g  # noqa: E402
from scripts import ingest_recipes, seed as seed_script    # noqa: E402

client = TestClient(app)


@pytest.fixture(scope="session", autouse=True)
def seeded():
    seed_script.seed()
    # authenticate as the demo user (owns the seeded pantry) for all API tests
    login = client.post("/api/v1/auth/login", json={
        "email": seed_script.DEMO_EMAIL, "password": seed_script.DEMO_PASSWORD})
    assert login.status_code == 200, login.text
    client.headers["Authorization"] = f"Bearer {login.json()['token']}"
    # synthetic monthly history so forecast advisories exist without the WFP CSV
    from datetime import date
    from app import models
    from app.db import SessionLocal
    db = SessionLocal()
    try:
        base = 30.0
        for i in range(72):
            base *= 1.006 + (0.03 if i % 7 == 0 else -0.004)
            db.add(models.PriceHistory(
                commodity="onion", month=date(2020 + i // 12, i % 12 + 1, 1),
                price_per_kg=round(base, 2)))
        db.commit()
    finally:
        db.close()
    yield


# ======================================================================
# (b) Bayesian decay
# ======================================================================

class TestDecay:
    def test_survival_starts_at_one(self):
        assert decay.survival(0, alpha=5, beta=2, storage="room") == 1.0

    def test_survival_monotone_decreasing(self):
        s = [decay.survival(t, 5, 2, "room") for t in range(0, 15)]
        assert all(a >= b for a, b in zip(s, s[1:]))

    def test_fridge_slows_decay(self):
        room = decay.survival(4, 5, 2, "room")
        fridge = decay.survival(4, 5, 2, "fridge")
        freezer = decay.survival(4, 5, 2, "freezer")
        assert room < fridge < freezer

    def test_prior_reproduces_literature_baseline(self):
        """Zero data -> alpha_hat equals the textbook shelf life exactly."""
        for cls, (alpha0, _) in decay.CLASS_PARAMS.items():
            alpha_hat, learned = decay.posterior_alpha(cls, [])
            assert not learned
            assert alpha_hat == pytest.approx(alpha0, rel=1e-9)

    def test_early_spoilage_shrinks_alpha(self):
        obs = [decay.Observation("vegetable", "room", 1.0, spoiled=True)] * 3
        alpha_hat, learned = decay.posterior_alpha("vegetable", obs)
        assert learned
        assert alpha_hat < decay.CLASS_PARAMS["vegetable"][0]

    def test_censored_longevity_grows_alpha_without_event(self):
        """Consumed-while-good contributes to b only (right-censoring)."""
        obs = [decay.Observation("vegetable", "room", 10.0, spoiled=False)] * 3
        alpha_hat, _ = decay.posterior_alpha("vegetable", obs)
        assert alpha_hat > decay.CLASS_PARAMS["vegetable"][0]

    def test_censored_room_equivalence(self):
        """10 fridge days = 10/3 room days, so a fridge-censored observation
        moves alpha less than the same days at room temperature."""
        fridge = decay.posterior_alpha(
            "vegetable", [decay.Observation("vegetable", "fridge", 10.0, False)])[0]
        room = decay.posterior_alpha(
            "vegetable", [decay.Observation("vegetable", "room", 10.0, False)])[0]
        assert fridge < room

    def test_urgency_is_conditional_not_cdf(self):
        """An item that already survived is riskier over the next horizon than
        a fresh one (beta > 1 => increasing hazard)."""
        aged = decay.conditional_spoil_prob(4, 2, alpha=5, beta=2, storage="room")
        fresh = decay.conditional_spoil_prob(0, 2, alpha=5, beta=2, storage="room")
        assert aged > fresh

    def test_urgency_bounds(self):
        for t in (0, 1, 5, 50):
            u = decay.conditional_spoil_prob(t, 2, alpha=5, beta=2, storage="room")
            assert 0.0 <= u <= 1.0

    def test_forced_include_threshold(self):
        items = [
            {"id": 1, "commodity": "spinach", "item_class": "leafy_green",
             "storage": "room", "age_days": 3.0, "quantity_g": 250},
            {"id": 2, "commodity": "rice", "item_class": "staple_dry",
             "storage": "room", "age_days": 3.0, "quantity_g": 1000},
        ]
        out = decay.assess_pantry(items, [])
        forced = decay.forced_commodities(out)
        assert "spinach" in forced and "rice" not in forced


# ======================================================================
# (a) package-size optimisation
# ======================================================================

PACKS = {
    "toor_dal": [Pack(500, 78), Pack(1000, 148)],
    "spinach": [Pack(250, 25)],
    "ghee": [Pack(180, 125)],
}


class TestOptimizer:
    def test_coverage_met(self):
        res = solve_purchase({"toor_dal": 700}, {}, PACKS, budget_rs=1000)
        line = res.lines[0]
        assert res.feasible and line.bought_g + line.pantry_g >= 700

    def test_budget_ceiling_is_hard(self):
        res = solve_purchase({"toor_dal": 700}, {}, PACKS, budget_rs=1000)
        assert res.total_cost_rs <= 1000

    def test_surplus_exposed_exactly(self):
        """700 g needed, packs of 500 -> 1000 g bought, 300 g surplus."""
        res = solve_purchase({"toor_dal": 700}, {}, PACKS, budget_rs=1000)
        line = res.lines[0]
        assert line.surplus_g == pytest.approx(line.pantry_g + line.bought_g - 700)
        assert line.surplus_g > 0

    def test_pantry_reduces_purchase(self):
        without = solve_purchase({"toor_dal": 700}, {}, PACKS, 1000).total_cost_rs
        with_pantry = solve_purchase({"toor_dal": 700}, {"toor_dal": 400}, PACKS, 1000).total_cost_rs
        assert with_pantry < without

    def test_infeasible_drops_optional_first(self):
        res = solve_purchase(
            {"toor_dal": 500, "ghee": 180}, {}, PACKS, budget_rs=100,
            optional={"ghee"},
        )
        assert res.feasible
        assert "ghee" in res.dropped
        dal = next(l for l in res.lines if l.commodity == "toor_dal")
        assert dal.bought_g >= 500

    def test_infeasible_without_optional_reports(self):
        res = solve_purchase({"toor_dal": 500}, {}, PACKS, budget_rs=10)
        assert not res.feasible

    def test_leftover_respects_slots(self):
        recipes = [LeftoverRecipe(i, {"spinach": 100}) for i in range(6)]
        picks = solve_leftover(recipes, {"spinach": 10_000}, {"spinach": 0.9}, slots=2)
        assert len(picks) <= 2

    def test_leftover_respects_stock(self):
        recipes = [LeftoverRecipe(1, {"spinach": 300}), LeftoverRecipe(2, {"spinach": 300})]
        picks = solve_leftover(recipes, {"spinach": 400}, {"spinach": 0.9}, slots=4)
        assert sum(300 for _ in picks) <= 400 or len(picks) <= 1

    def test_leftover_sees_pantry_plus_bought(self):
        """available = pantry + bought; at-risk pantry stock must be visible."""
        recipes = [LeftoverRecipe(1, {"spinach": 200, "toor_dal": 100})]
        pantry_plus_bought = {"spinach": 250, "toor_dal": 500}   # dal was bought
        picks = solve_leftover(recipes, pantry_plus_bought, {"spinach": 0.8}, slots=2)
        assert [p.recipe_id for p in picks] == [1]

    def test_the_two_features_couple(self):
        """THE central claim: urgency from the decay model steers stage 2.
        Same stock, same recipes — flipping which commodity is urgent flips
        which recipe the leftover ILP picks."""
        recipes = [
            LeftoverRecipe(1, {"spinach": 250}),
            LeftoverRecipe(2, {"curd": 250}),
        ]
        available = {"spinach": 250, "curd": 250}
        spinach_urgent = solve_leftover(recipes, available, {"spinach": 0.9, "curd": 0.05}, slots=1)
        curd_urgent = solve_leftover(recipes, available, {"spinach": 0.05, "curd": 0.9}, slots=1)
        assert spinach_urgent[0].recipe_id == 1
        assert curd_urgent[0].recipe_id == 2


# ======================================================================
# (c) variety enforcement
# ======================================================================

class TestVariety:
    def test_protein_precedence_palak_paneer(self):
        """500 g spinach vs 250 g paneer: still a paneer dish."""
        cluster = variety.assign_cluster([("spinach", 500), ("paneer", 250), ("onion", 100)])
        assert cluster == "paneer"

    def test_curd_side_is_not_a_dairy_dish(self):
        cluster = variety.assign_cluster(
            [("wheat_flour", 300), ("potato", 400), ("curd", 100), ("ghee", 40)])
        assert cluster == "potato"

    def test_rice_needs_overwhelming_dominance(self):
        assert variety.assign_cluster([("rice", 700), ("onion", 60)]) == "rice_dish"
        pulao = variety.assign_cluster(
            [("rice", 350), ("carrot", 100), ("beans", 100), ("green_peas", 100), ("onion", 120)])
        assert pulao != "rice_dish"

    def test_tempering_dal_does_not_claim_dish(self):
        """8 g of urad tempering does not make curd rice a legume dish."""
        cluster = variety.assign_cluster(
            [("rice", 300), ("curd", 400), ("urad_dal", 8), ("ginger", 10)])
        assert cluster != "legume"

    def test_uniform_history_max_entropy(self):
        h = variety.shannon_entropy({"a": 5, "b": 5, "c": 5, "d": 5})
        assert h == pytest.approx(2.0)

    def test_attainable_k_excludes_diet_impossible(self):
        pool = {"legume", "paneer", "mixed_veg", "chicken", "egg"}
        assert variety.attainable_clusters(pool, "vegetarian") == {"legume", "paneer", "mixed_veg"}

    def test_penalties_only_below_threshold(self):
        varied = ["legume", "paneer", "mixed_veg", "leafy_green", "potato", "rice_dish"] * 2
        report = variety.measure(varied, set(varied), "vegetarian")
        assert not report.penalties_engaged

        monotonous = ["legume"] * 12
        report2 = variety.measure(monotonous, {"legume", "paneer", "mixed_veg", "potato"}, "vegetarian")
        assert report2.penalties_engaged and report2.penalties.get("legume", 0) > 0

    def test_penalty_never_beats_forced_include(self):
        """Hard guardrail: max possible penalty < +10.0 forced bonus."""
        monotonous = ["legume"] * 42
        report = variety.measure(monotonous, {"legume", "mixed_veg"}, "vegetarian")
        assert max(report.penalties.values()) <= variety.PENALTY_STRENGTH < 10.0

    def test_history_capped_by_meals_not_plans(self):
        history = ["legume"] * 200
        assert len(variety.cap_history(history)) == variety.WINDOW_DAYS * variety.MEALS_PER_DAY

    def test_empty_history_no_penalties(self):
        report = variety.measure([], {"legume", "mixed_veg"}, "vegetarian")
        assert report.normalised_entropy == 1.0 and not report.penalties_engaged


# ======================================================================
# (d) forecast — ARIMA(1,1,0) + GARCH(1,1)
# ======================================================================

class TestForecast:
    def test_log_returns(self):
        r = forecast.log_returns([100, 110, 99])
        assert r[0] == pytest.approx(math.log(1.1))
        assert r[1] == pytest.approx(math.log(0.9))

    def test_ar1_recovers_persistence(self):
        import random
        rng = random.Random(7)
        r, prev = [], 0.0
        for _ in range(400):
            prev = 0.001 + 0.6 * prev + rng.gauss(0, 0.01)
            r.append(prev)
        _, phi = forecast.fit_ar1(r)
        assert 0.4 < phi < 0.8

    def test_garch_stays_stationary(self):
        import random
        rng = random.Random(11)
        eps = [rng.gauss(0, 0.05) for _ in range(300)]
        omega, a, b = forecast.fit_garch(eps)
        assert omega > 0 and a >= 0 and b >= 0 and a + b < 1

    def test_short_series_returns_none(self):
        assert forecast.forecast_commodity("onion", [10.0] * 10) is None

    def test_rising_series_advises_buy_now(self):
        prices = [100 * (1.04 ** i) for i in range(60)]   # steady 4%/mo climb
        f = forecast.forecast_commodity("test_rising", prices)
        assert f is not None and f.trend_pct >= 2.0 and f.advice == "buy_now"


# ======================================================================
# quickcommerce: quantity parser + matcher
# ======================================================================

class TestQuickCommerce:
    def test_grams(self):
        assert parse_quantity_g("500 g") == 500

    def test_kg_and_multipack(self):
        assert parse_quantity_g("1 kg") == 1000
        assert parse_quantity_g("2 x 250g") == 500

    def test_litres_and_ml(self):
        assert parse_quantity_g("1 L") == 1000
        assert parse_quantity_g("500ml") == 500

    def test_pieces_and_dozen(self):
        assert parse_quantity_g("6 pcs", piece_weight_g=55) == 330
        assert parse_quantity_g("1 dozen", piece_weight_g=55) == 660

    def test_unparseable(self):
        assert parse_quantity_g("family pack") is None

    def test_ketchup_limitation_is_real(self):
        """Documented limitation: token overlap scores ketchup as tomato."""
        assert match_score("tomato", "Tomato Ketchup 500g") == 1.0
        assert match_score("toor dal", "Toor Dal Premium") == 1.0
        assert match_score("paneer", "Amul Butter") == 0.0


# ======================================================================
# pricing resolution
# ======================================================================

class TestPricing:
    def test_cache_beats_seed(self):
        res = resolve_prices(
            ["onion"],
            cached_packs={"onion": [Pack(1000, 38)]},
            seeded_packs={"onion": [Pack(1000, 40)]},
        )
        assert res.source_of("onion") == "cache"

    def test_seed_is_the_fallback(self):
        res = resolve_prices(["onion"], {}, {"onion": [Pack(1000, 40)]})
        assert res.source_of("onion") == "seed"
        assert res.by_commodity["onion"].packs[0].price_rs == 40

    def test_live_wins_and_is_cached(self):
        class FakeClient:
            def fetch_packs(self, commodity, words, piece_weight):
                from app.services.quickcommerce import LivePack
                return [LivePack(commodity, 1000, 35, "Fresh Onion 1kg", 1.0)]
        res = resolve_prices(["onion"], {"onion": [Pack(1000, 38)]},
                             {"onion": [Pack(1000, 40)]}, live_client=FakeClient())
        assert res.source_of("onion") == "live"
        assert len(res.to_cache) == 1


# ======================================================================
# planner: retrieval, arrangement, grounding
# ======================================================================

def _mini_corpus() -> list[CandidateRecipe]:
    mk = CandidateRecipe
    return [
        mk(1, "Kanda Poha", "breakfast", "vegetarian", "west", 20, 4,
           [("poha", 250), ("onion", 150), ("sunflower_oil", 30)]),
        mk(2, "Besan Chilla", "breakfast", "vegetarian", "north", 20, 4,
           [("besan", 200), ("onion", 100), ("tomato", 100)]),
        mk(3, "Dal Tadka", "lunch", "vegetarian", "north", 35, 4,
           [("toor_dal", 250), ("onion", 100), ("tomato", 150)]),
        mk(4, "Palak Paneer", "dinner", "vegetarian", "north", 40, 4,
           [("spinach", 500), ("paneer", 250), ("onion", 120)]),
        mk(5, "Chicken Curry", "dinner", "non_vegetarian", "", 50, 4,
           [("chicken", 800), ("onion", 250), ("tomato", 200)]),
        mk(6, "Aloo Gobi", "dinner", "vegetarian", "north", 35, 4,
           [("potato", 300), ("cauliflower", 400), ("onion", 100)]),
        mk(7, "Curd Rice", "lunch", "vegetarian", "south", 20, 4,
           [("rice", 300), ("curd", 400), ("ginger", 10)]),
        mk(8, "Egg Bhurji", "breakfast", "eggetarian", "west", 15, 4,
           [("egg", 220), ("onion", 120), ("tomato", 100)]),
    ]


def _inputs(**over) -> PlanInputs:
    base = dict(
        budget_rs=1500, days=1, family_size=4, diet="vegetarian", region="",
        max_cook_mins=60, dislikes=[], pantry_items=[], observations=[],
        candidates=_mini_corpus(), history_clusters=[], cached_packs={},
        seeded_packs={c: [Pack(500, 40), Pack(1000, 70)] for c in
                      ("poha", "onion", "tomato", "besan", "toor_dal", "spinach",
                       "paneer", "potato", "cauliflower", "rice", "curd",
                       "sunflower_oil", "ginger", "chicken", "egg")},
        live_client=None, price_history={},
    )
    base.update(over)
    return PlanInputs(**base)


class TestPlanner:
    def test_diet_filter_excludes_meat_and_egg(self):
        pool = filter_candidates(_inputs(diet="vegetarian"))
        names = {r.name for r in pool}
        assert "Chicken Curry" not in names and "Egg Bhurji" not in names

    def test_dislikes_filter(self):
        pool = filter_candidates(_inputs(dislikes=["bitter_gourd", "cauliflower"]))
        assert all("cauliflower" not in dict(r.ingredients) for r in pool)

    def test_cook_time_filter(self):
        pool = filter_candidates(_inputs(max_cook_mins=25))
        assert all(r.time_mins <= 25 for r in pool)

    def test_forced_include_outranks_variety_penalty(self):
        """A dying bunch of spinach gets cooked even in a spinach-heavy week."""
        report = variety.measure(["paneer"] * 12, {"paneer", "mixed_veg", "legume"},
                                 "vegetarian")
        pool = filter_candidates(_inputs())
        ranked = rank_candidates(pool, {}, {"spinach": 0.9}, ["spinach"], report)
        assert ranked[0].name == "Palak Paneer"

    def test_heuristic_fills_every_slot(self):
        meals = HeuristicPlanner().arrange(
            rank_candidates(filter_candidates(_inputs()), {}, {}, [],
                            variety.measure([], set(), "vegetarian")), days=1)
        assert meals is not None
        assert {(m.day, m.course) for m in meals} == {(1, "breakfast"), (1, "lunch"), (1, "dinner")}
        breakfast = next(m for m in meals if m.course == "breakfast")
        assert breakfast.recipe.course == "breakfast"

    def test_requirements_scale_with_family(self):
        r = _mini_corpus()[0]
        m = [Meal(1, "breakfast", r)]
        req4 = requirements_for(m, 4)
        req8 = requirements_for(m, 8)
        assert req8["poha"] == pytest.approx(2 * req4["poha"])

    def test_llm_never_sees_prices(self):
        llm = LLMPlanner("http://x", "k", "m")
        prompt = llm._prompt(_mini_corpus(), days=1)
        for token in ("₹", "price", "cost", "budget", "rs."):
            assert token not in prompt.lower()

    def test_llm_hallucinated_id_rejected(self):
        llm = LLMPlanner("http://x", "k", "m")
        bad = '{"meals": [{"day": 1, "course": "breakfast", "recipe_id": 999}]}'
        assert llm._validate(bad, _mini_corpus(), days=1) is None

    def test_llm_incomplete_plan_rejected(self):
        llm = LLMPlanner("http://x", "k", "m")
        partial = '{"meals": [{"day": 1, "course": "breakfast", "recipe_id": 1}]}'
        assert llm._validate(partial, _mini_corpus(), days=1) is None

    def test_llm_reprompted_once_then_heuristic(self):
        calls = []

        class BadLLM(LLMPlanner):
            def _chat(self, messages):
                calls.append(1)
                return '{"meals": [{"day": 1, "course": "breakfast", "recipe_id": 999}]}'

        outcome = build_plan(_inputs(), BadLLM("http://x", "k", "m"))
        assert len(calls) == 2                      # exactly one re-prompt
        assert outcome.planner_used == "heuristic"  # then deterministic fallback

    def test_llm_retries_transient_failures(self, monkeypatch):
        """A free-tier 503 must not silently demote the plan to the heuristic.
        The retry lives inside _chat, so this patches the HTTP call itself."""
        import app.services.planner as planner_mod

        calls = []
        good = ('{"meals": ['
                '{"day": 1, "course": "breakfast", "recipe_id": 1},'
                '{"day": 1, "course": "lunch", "recipe_id": 3},'
                '{"day": 1, "course": "dinner", "recipe_id": 4}]}')

        class Resp:
            def __init__(self, code, payload=None):
                self.status_code, self._p, self.text = code, payload, str(payload)

            def json(self):
                return self._p

        def fake_post(url, **kw):
            calls.append(1)
            if len(calls) < 3:                    # two 503s, then success
                return Resp(503, {"error": "overloaded"})
            return Resp(200, {"choices": [{"message": {"content": good}}]})

        monkeypatch.setattr(planner_mod.httpx, "post", fake_post)
        monkeypatch.setattr(planner_mod.time, "sleep", lambda *_: None)  # no real waiting

        outcome = build_plan(_inputs(), LLMPlanner("http://x", "k", "m"))
        assert len(calls) == 3                    # retried twice, then succeeded
        assert outcome.planner_used == "llm"      # NOT demoted to the heuristic

    def test_quota_exhaustion_switches_model(self, monkeypatch):
        """Free tiers cap requests PER MODEL PER DAY, so a 429 on the primary
        must move to the next model rather than demote to the heuristic."""
        import app.services.planner as planner_mod
        seen = []
        good = ('{"meals": ['
                '{"day": 1, "course": "breakfast", "recipe_id": 1},'
                '{"day": 1, "course": "lunch", "recipe_id": 3},'
                '{"day": 1, "course": "dinner", "recipe_id": 4}]}')

        class Resp:
            def __init__(self, code, payload=None):
                self.status_code, self._p, self.text = code, payload, str(payload)

            def json(self):
                return self._p

        def fake_post(url, **kw):
            model = kw["json"]["model"]
            seen.append(model)
            if model == "primary":                      # out of quota today
                return Resp(429, {"error": "quota"})
            return Resp(200, {"choices": [{"message": {"content": good}}]})

        monkeypatch.setattr(planner_mod.httpx, "post", fake_post)
        monkeypatch.setattr(planner_mod.time, "sleep", lambda *_: None)

        llm = LLMPlanner("http://x", "k", "primary", fallback_models=["backup"])
        outcome = build_plan(_inputs(), llm)
        assert seen == ["primary", "backup"]            # switched, did not retry
        assert outcome.planner_used == "llm"
        assert llm.model_used == "backup"

    def test_planner_note_explains_fallback(self):
        """When the heuristic takes over, the response says why."""
        class DeadLLM(LLMPlanner):
            def _chat(self, messages):
                raise RuntimeError("HTTP 503 from the model provider")

        outcome = build_plan(_inputs(), DeadLLM("http://x", "k", "m"))
        assert outcome.planner_used == "heuristic"
        assert "503" in outcome.planner_note

    def test_no_key_notes_the_reason(self):
        outcome = build_plan(_inputs(), llm=None)
        assert outcome.planner_note == "no LLM_API_KEY configured"

    def test_llm_valid_plan_accepted(self):
        class GoodLLM(LLMPlanner):
            def _chat(self, messages):
                return ('{"meals": ['
                        '{"day": 1, "course": "breakfast", "recipe_id": 1},'
                        '{"day": 1, "course": "lunch", "recipe_id": 3},'
                        '{"day": 1, "course": "dinner", "recipe_id": 4}]}')

        outcome = build_plan(_inputs(), GoodLLM("http://x", "k", "m"))
        assert outcome.planner_used == "llm"
        assert [m.recipe.id for m in outcome.meals] == [1, 3, 4]

    def test_nonveg_cap_zero_makes_veg_plan(self):
        """diet=non_vegetarian but cap 0 -> pool may contain meat, plan may not."""
        outcome = build_plan(_inputs(diet="non_vegetarian", max_nonveg_meals=0), llm=None)
        assert all(m.recipe.diet != "non_vegetarian" for m in outcome.meals)

    def test_nonveg_cap_is_also_a_target(self):
        """'Non-veg once' should PRODUCE one meat meal, not just permit it."""
        outcome = build_plan(_inputs(diet="non_vegetarian", days=1, max_nonveg_meals=1), llm=None)
        assert sum(m.recipe.diet == "non_vegetarian" for m in outcome.meals) == 1

    def test_nonveg_meals_spread_across_days(self):
        """Cap 2 over 2 days -> one meat meal per day, not a meat Monday."""
        outcome = build_plan(_inputs(diet="non_vegetarian", days=2, max_nonveg_meals=2), llm=None)
        per_day: dict[int, int] = {}
        for m in outcome.meals:
            if m.recipe.diet == "non_vegetarian":
                per_day[m.day] = per_day.get(m.day, 0) + 1
        assert all(n <= 1 for n in per_day.values())

    def test_nonveg_never_at_breakfast(self):
        outcome = build_plan(_inputs(diet="non_vegetarian", days=1, max_nonveg_meals=3), llm=None)
        b = next(m for m in outcome.meals if m.course == "breakfast")
        assert b.recipe.diet != "non_vegetarian"

    def test_llm_over_cap_plan_rejected(self):
        """An LLM answer with too many non-veg meals fails validation."""
        llm = LLMPlanner("http://x", "k", "m")
        over = ('{"meals": ['
                '{"day": 1, "course": "breakfast", "recipe_id": 8},'
                '{"day": 1, "course": "lunch", "recipe_id": 3},'
                '{"day": 1, "course": "dinner", "recipe_id": 5}]}')
        pool = _mini_corpus()
        assert llm._validate(over, pool, days=1, max_nonveg=0) is None
        assert llm._validate(over, pool, days=1, max_nonveg=1) is not None

    def test_pipeline_offline_heuristic(self):
        outcome = build_plan(_inputs(), llm=None)
        assert outcome.planner_used == "heuristic"
        assert outcome.purchase.total_cost_rs <= 1500


# ======================================================================
# auth
# ======================================================================

class TestAuth:
    def _fresh(self):
        """Client with no Authorization header."""
        from fastapi.testclient import TestClient as TC
        from app.main import app as _app
        return TC(_app)

    def test_protected_endpoints_require_token(self):
        anon = self._fresh()
        assert anon.get("/api/v1/pantry").status_code in (401, 403)
        assert anon.post("/api/v1/plans/generate", json={}).status_code in (401, 403)

    def test_register_login_me_roundtrip(self):
        anon = self._fresh()
        r = anon.post("/api/v1/auth/register", json={
            "email": "asha@example.com", "password": "secret123", "name": "Asha"})
        assert r.status_code == 201
        token = r.json()["token"]
        me = anon.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.json()["email"] == "asha@example.com"
        # duplicate email rejected
        assert anon.post("/api/v1/auth/register", json={
            "email": "asha@example.com", "password": "secret123"}).status_code == 409

    def test_wrong_password_rejected(self):
        anon = self._fresh()
        r = anon.post("/api/v1/auth/login", json={
            "email": seed_script.DEMO_EMAIL, "password": "not-the-password"})
        assert r.status_code == 401

    def test_tampered_token_rejected(self):
        anon = self._fresh()
        good = anon.post("/api/v1/auth/login", json={
            "email": seed_script.DEMO_EMAIL,
            "password": seed_script.DEMO_PASSWORD}).json()["token"]
        forged = good[:-4] + ("AAAA" if not good.endswith("AAAA") else "BBBB")
        r = anon.get("/api/v1/pantry", headers={"Authorization": f"Bearer {forged}"})
        assert r.status_code == 401

    def test_users_see_only_their_own_data(self):
        anon = self._fresh()
        token = anon.post("/api/v1/auth/register", json={
            "email": "ravi@example.com", "password": "secret123"}).json()["token"]
        h = {"Authorization": f"Bearer {token}"}
        # new user: empty pantry, while the demo user's has items
        assert anon.get("/api/v1/pantry", headers=h).json() == []
        demo_pantry = client.get("/api/v1/pantry").json()
        assert len(demo_pantry) > 0
        # and cannot read the demo user's plan
        plan = client.post("/api/v1/plans/generate", json={"budget_rs": 1500, "days": 1}).json()
        assert anon.get(f"/api/v1/plans/{plan['plan_id']}", headers=h).status_code == 404

    def test_password_hashing_is_salted(self):
        from app.services.auth import hash_password, verify_password
        a, b = hash_password("same-password"), hash_password("same-password")
        assert a != b                      # unique salts
        assert verify_password("same-password", a)
        assert not verify_password("other", a)


# ======================================================================
# API — full pipeline through HTTP, offline
# ======================================================================

class TestAPI:
    def test_health(self):
        assert client.get("/health").json() == {"status": "ok"}

    def test_generate_plan_offline(self):
        resp = client.post("/api/v1/plans/generate", json={"budget_rs": 1500, "days": 2})
        assert resp.status_code == 200, resp.text
        plan = resp.json()
        assert plan["planner"] == "heuristic"           # no LLM key configured
        assert len(plan["meals"]) == 6                  # 2 days x 3 courses
        assert plan["totals"]["within_budget"] is True
        assert plan["totals"]["spent_rs"] <= 1500
        assert plan["variety"]["attainable_clusters"] >= 2
        TestAPI._plan = plan

    def test_meals_carry_the_recipe(self):
        """Every planned meal ships its scaled ingredients and a method."""
        plan = TestAPI._plan
        for m in plan["meals"]:
            assert m["ingredients"], m["recipe_name"]
            assert all(i["grams"] > 0 for i in m["ingredients"])
            assert m["instructions"].strip(), f"{m['recipe_name']} has no method"

    def test_recipe_ingredients_scale_with_family(self):
        p3 = client.post("/api/v1/plans/generate",
                         json={"budget_rs": 1500, "days": 1, "family_size": 3}).json()
        p6 = client.post("/api/v1/plans/generate",
                         json={"budget_rs": 1500, "days": 1, "family_size": 6}).json()
        # same recipe appearing in both plans must scale 2x
        by_id3 = {m["recipe_id"]: m for m in p3["meals"]}
        shared = [m for m in p6["meals"] if m["recipe_id"] in by_id3]
        assert shared, "expected overlapping recipes between runs"
        for m6 in shared:
            g3 = {i["commodity"]: i["grams"] for i in by_id3[m6["recipe_id"]]["ingredients"]}
            for i in m6["ingredients"]:
                assert i["grams"] == pytest.approx(2 * g3[i["commodity"]], rel=0.02)

    def test_shopping_list_exposes_surplus(self):
        plan = TestAPI._plan
        bought = [i for i in plan["shopping_list"] if i["bought_g"] > 0]
        assert bought, "expected at least one purchase"
        for item in bought:
            assert item["surplus_g"] == pytest.approx(
                item["pantry_g"] + item["bought_g"] - item["required_g"], abs=0.51)

    def test_forecast_advisories_attached(self):
        """WFP history is loaded by seed, so staples carry trend/volatility."""
        plan = TestAPI._plan
        advised = [i for i in plan["shopping_list"] if i["trend_pct"] is not None]
        assert advised, "expected at least one commodity with a forecast"
        for item in advised:
            assert item["advice"] in ("buy_now", "normal", "wait_if_possible")

    def test_pantry_tick_freezes_recipes(self):
        """UX revamp: ticking pantry re-solves stages 4-7 but NEVER changes
        which meals are suggested."""
        plan = TestAPI._plan
        before = [(m["day"], m["course"], m["recipe_id"]) for m in plan["meals"]]
        # tick a big bag of the most expensive purchased commodity
        target = max(plan["shopping_list"], key=lambda i: i["cost_rs"])
        resp = client.post(f"/api/v1/plans/{plan['plan_id']}/pantry", json={
            "ticks": [{"commodity": target["commodity"],
                       "quantity_g": target["required_g"], "storage": "room"}]})
        assert resp.status_code == 200, resp.text
        after = resp.json()
        assert [(m["day"], m["course"], m["recipe_id"]) for m in after["meals"]] == before
        assert after["totals"]["spent_rs"] <= plan["totals"]["spent_rs"]

    def test_swap_changes_only_unlocked(self):
        plan = TestAPI._plan
        locked_slot = {"day": plan["meals"][0]["day"], "course": plan["meals"][0]["course"]}
        locked_id = plan["meals"][0]["recipe_id"]
        resp = client.post(f"/api/v1/plans/{plan['plan_id']}/swap",
                           json={"locked": [locked_slot]})
        assert resp.status_code == 200, resp.text
        after = resp.json()
        kept = next(m for m in after["meals"]
                    if m["day"] == locked_slot["day"] and m["course"] == locked_slot["course"])
        assert kept["recipe_id"] == locked_id and kept["locked"] is True
        assert len(after["meals"]) == len(plan["meals"])

    def test_get_plan_roundtrip(self):
        plan = TestAPI._plan
        resp = client.get(f"/api/v1/plans/{plan['plan_id']}")
        assert resp.status_code == 200
        assert resp.json()["plan_id"] == plan["plan_id"]

    def test_tiny_budget_still_hard_ceiling(self):
        resp = client.post("/api/v1/plans/generate", json={"budget_rs": 50, "days": 1})
        assert resp.status_code == 200
        assert resp.json()["totals"]["spent_rs"] <= 50

    def test_pantry_crud(self):
        resp = client.post("/api/v1/pantry", json={
            "commodity": "carrot", "quantity_g": 300, "storage": "fridge"})
        assert resp.status_code == 201
        item = resp.json()
        assert item["item_class"] == "root_vegetable"   # inferred
        listing = client.get("/api/v1/pantry").json()
        assert any(i["id"] == item["id"] for i in listing)
        assert client.delete(f"/api/v1/pantry/{item['id']}").status_code == 204
        assert client.delete(f"/api/v1/pantry/{item['id']}").status_code == 404

    def test_decay_endpoint_reports_urgency(self):
        out = client.get("/api/v1/pantry/decay").json()
        assert out["items"], "demo pantry should be assessed"
        spinach = [i for i in out["items"] if i["commodity"] == "spinach"]
        rice = [i for i in out["items"] if i["commodity"] == "rice"]
        assert spinach and rice
        # 3-day fridge spinach is a real risk; 30-day rice is not
        assert spinach[0]["urgency"] > 0.2 > rice[0]["urgency"]

    def test_spoilage_feeds_the_posterior(self):
        before = client.get("/api/v1/pantry/decay").json()
        b_item = next(i for i in before["items"] if i["item_class"] == "leafy_green")
        assert b_item["learned_from_observations"] is False
        client.post("/api/v1/pantry/spoilage", json={
            "item_class": "leafy_green", "storage": "fridge",
            "lifetime_days": 2.0, "spoiled": True})
        after = client.get("/api/v1/pantry/decay").json()
        a_item = next(i for i in after["items"] if i["item_class"] == "leafy_green")
        assert a_item["learned_from_observations"] is True
        assert a_item["alpha_days"] < b_item["alpha_days"]   # early spoilage shrinks alpha

    def test_preferences_roundtrip(self):
        put = client.put("/api/v1/preferences", json={
            "diet": "vegetarian", "region": "south", "max_cook_mins": 45,
            "dislikes": ["bitter_gourd"], "family_size": 3})
        assert put.status_code == 200
        got = client.get("/api/v1/preferences").json()
        assert got["region"] == "south" and got["dislikes"] == ["bitter_gourd"]
        # restore defaults for other tests
        client.put("/api/v1/preferences", json={
            "diet": "vegetarian", "region": "", "max_cook_mins": 60,
            "dislikes": [], "family_size": 4})

    def test_preferences_shape_the_plan(self):
        client.put("/api/v1/preferences", json={
            "diet": "vegetarian", "region": "", "max_cook_mins": 25,
            "dislikes": [], "family_size": 4})
        plan = client.post("/api/v1/plans/generate", json={"budget_rs": 1500, "days": 1}).json()
        assert all(m["time_mins"] <= 25 for m in plan["meals"])
        client.put("/api/v1/preferences", json={
            "diet": "vegetarian", "region": "", "max_cook_mins": 60,
            "dislikes": [], "family_size": 4})


# ======================================================================
# pantry ticks persist + cooking draws stock down
# ======================================================================

class TestPantryLifecycle:
    def test_ticking_creates_pantry_items(self):
        """'I already have this' is a statement about the real kitchen."""
        plan = client.post("/api/v1/plans/generate",
                           json={"budget_rs": 1500, "days": 1}).json()
        target = next(i for i in plan["shopping_list"] if i["bought_g"] > 0)
        before = {p["commodity"] for p in client.get("/api/v1/pantry").json()}
        client.post(f"/api/v1/plans/{plan['plan_id']}/pantry", json={"ticks": [
            {"commodity": target["commodity"], "quantity_g": 400, "storage": "room"}]})
        after = client.get("/api/v1/pantry").json()
        row = next(p for p in after if p["commodity"] == target["commodity"])
        assert row["quantity_g"] == 400
        assert row["item_class"]            # inferred, not blank
        _ = before

    def test_re_ticking_does_not_duplicate(self):
        plan = client.post("/api/v1/plans/generate",
                           json={"budget_rs": 1500, "days": 1}).json()
        tick = {"ticks": [{"commodity": "besan", "quantity_g": 300, "storage": "room"}]}
        client.post(f"/api/v1/plans/{plan['plan_id']}/pantry", json=tick)
        n1 = len(client.get("/api/v1/pantry").json())
        client.post(f"/api/v1/plans/{plan['plan_id']}/pantry", json=tick)
        rows = client.get("/api/v1/pantry").json()
        assert len(rows) == n1
        assert sum(1 for r in rows if r["commodity"] == "besan") == 1

    def test_cooking_draws_stock_down(self):
        plan = client.post("/api/v1/plans/generate",
                           json={"budget_rs": 1500, "days": 1}).json()
        meal = plan["meals"][0]
        need = {i["commodity"]: i["grams"] for i in meal["ingredients"]}
        big = max(need, key=need.get)
        client.post(f"/api/v1/plans/{plan['plan_id']}/pantry", json={"ticks": [
            {"commodity": big, "quantity_g": need[big] * 2, "storage": "room"}]})
        r = client.post(f"/api/v1/plans/{plan['plan_id']}/cooked",
                        json={"day": meal["day"], "course": meal["course"]})
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["used_from_pantry"].get(big) == pytest.approx(need[big], rel=0.02)
        left = next(p for p in client.get("/api/v1/pantry").json() if p["commodity"] == big)
        assert left["quantity_g"] == pytest.approx(need[big], rel=0.02)   # half remains
        assert any(m["cooked"] for m in out["plan"]["meals"]
                   if m["day"] == meal["day"] and m["course"] == meal["course"])

    def test_unaffordable_plan_is_not_called_within_budget(self):
        """Regression: an infeasible solve buys nothing and costs nothing. That
        must never be reported as a free, within-budget shopping trip — it is
        the one claim the whole project rests on."""
        r = client.post("/api/v1/plans/generate", json={"budget_rs": 60, "days": 3})
        assert r.status_code == 200, r.text
        t = r.json()["totals"]
        if t["affordable"]:
            pytest.skip("seeded prices happened to fit this budget")
        assert t["spent_rs"] == 0
        assert t["within_budget"] is False          # was True — the lie
        assert t["min_budget_rs"] and t["min_budget_rs"] > 60

    def test_affordable_plan_reports_real_numbers(self):
        t = client.post("/api/v1/plans/generate",
                        json={"budget_rs": 3000, "days": 2}).json()["totals"]
        assert t["affordable"] is True
        assert t["spent_rs"] > 0 and t["within_budget"] is True

    def test_min_budget_solve_does_not_crash_on_big_baskets(self):
        """The relaxed re-solve must use a finite ceiling — PuLP rejects inf."""
        from app.services.optimizer import solve_purchase, Pack
        packs = {c: [Pack(500, 200.0)] for c in ("a", "b", "c")}
        req = {"a": 100, "b": 100, "c": 100}
        assert not solve_purchase(req, {}, packs, 100).feasible
        big = 2.0 * sum(max(p.price_rs for p in o) * (1 + req[c] / min(p.size_g for p in o))
                        for c, o in packs.items()) + 1000.0
        relaxed = solve_purchase(req, {}, packs, big)
        assert relaxed.feasible and relaxed.total_cost_rs == 600.0

    def test_tick_list_never_empties(self):
        """Regression: the dialog lists everything the plan REQUIRES, not just
        what is still being bought — otherwise it empties out as you tick and
        renders as a bare yes/no prompt."""
        plan = client.post("/api/v1/plans/generate",
                           json={"budget_rs": 1500, "days": 1}).json()
        rows = lambda p: [i for i in p["shopping_list"] if i["required_g"] > 0]
        before = rows(plan)
        assert len(before) > 3
        after = client.post(f"/api/v1/plans/{plan['plan_id']}/pantry", json={"ticks": [
            {"commodity": i["commodity"], "quantity_g": i["required_g"], "storage": "room"}
            for i in before]}).json()
        assert len(rows(after)) == len(before)
        assert sum(1 for i in after["shopping_list"] if i["pantry_g"] > 0) >= len(before) - 1

    def test_unticking_removes_from_pantry(self):
        """0 g means 'I don't have this after all'."""
        plan = client.post("/api/v1/plans/generate",
                           json={"budget_rs": 1500, "days": 1}).json()
        url = f"/api/v1/plans/{plan['plan_id']}/pantry"
        client.post(url, json={"ticks": [
            {"commodity": "jaggery", "quantity_g": 250, "storage": "room"}]})
        assert any(p["commodity"] == "jaggery" for p in client.get("/api/v1/pantry").json())
        client.post(url, json={"ticks": [
            {"commodity": "jaggery", "quantity_g": 0, "storage": "room"}]})
        assert not any(p["commodity"] == "jaggery" for p in client.get("/api/v1/pantry").json())

    def test_cooking_is_not_repeatable(self):
        plan = client.post("/api/v1/plans/generate",
                           json={"budget_rs": 1500, "days": 1}).json()
        meal = plan["meals"][1]
        slot = {"day": meal["day"], "course": meal["course"]}
        assert client.post(f"/api/v1/plans/{plan['plan_id']}/cooked", json=slot).status_code == 200
        assert client.post(f"/api/v1/plans/{plan['plan_id']}/cooked", json=slot).status_code == 409

    def test_oldest_stock_is_eaten_first(self):
        """The at-risk item goes first — the whole point of feature (b)."""
        from app.services.consumption import plan_consumption
        pantry = [
            {"id": 1, "commodity": "spinach", "item_class": "leafy_green",
             "storage": "fridge", "quantity_g": 200, "age_days": 5},
            {"id": 2, "commodity": "spinach", "item_class": "leafy_green",
             "storage": "fridge", "quantity_g": 200, "age_days": 1},
        ]
        c = plan_consumption(pantry, {"spinach": 250})
        assert c.draws[0].pantry_item_id == 1 and c.draws[0].grams_taken == 200
        assert c.draws[1].pantry_item_id == 2 and c.draws[1].grams_taken == 50
        assert [d.pantry_item_id for d in c.exhausted_draws] == [1]

    def test_cooking_teaches_the_decay_model(self):
        """An item eaten before spoiling is a right-censored observation."""
        from app.services.consumption import Draw
        from app.services import decay
        aged = Draw(pantry_item_id=1, commodity="spinach", item_class="leafy_green",
                    storage="fridge", grams_taken=200, grams_left=0, age_days=6)
        assert aged.exhausted
        obs = [decay.Observation(aged.item_class, aged.storage, aged.age_days, spoiled=False)]
        alpha_after, learned = decay.posterior_alpha("leafy_green", obs)
        alpha_prior, _ = decay.posterior_alpha("leafy_green", [])
        assert learned and alpha_after > alpha_prior   # survived longer than expected


# ======================================================================
# ingest parser
# ======================================================================

class TestIngest:
    def test_parse_line_piece_count(self):
        c, g = ingest_recipes.parse_line("6 Karela (Bitter Gourd/ Pavakkai) - deseeded")
        assert c == "bitter_gourd" and g == pytest.approx(6 * 80)

    def test_parse_line_volume_measure(self):
        c, g = ingest_recipes.parse_line("3 tablespoon Gram flour (besan)")
        assert c == "besan" and g == pytest.approx(3 * 15 * 0.5)

    def test_parse_line_to_taste_is_not_a_failure(self):
        assert ingest_recipes.parse_line("Salt - to taste") == ("salt", 8.0)
        assert ingest_recipes.parse_line("A few drops of rosewater") in (None, "skip")

    def test_parse_line_mass(self):
        c, g = ingest_recipes.parse_line("250 grams Paneer cut in cubes")
        assert c == "paneer" and g == 250

    def test_synthetic_row_converts(self):
        row = {
            "TranslatedRecipeName": "Test Dal Fry",
            "TranslatedIngredients": "1 cup Toor dal,2 Onion - sliced,"
                                     "3 Tomato,1 teaspoon Turmeric powder,"
                                     "Salt - to taste,2 tablespoon Oil",
            "Course": "Lunch", "Diet": "Vegetarian", "Cuisine": "Punjabi",
            "Servings": "4", "TotalTimeInMins": "40", "URL": "http://example.com",
            "TranslatedInstructions": "Cook the dal.Add the tadka and serve hot.",
        }
        recipe, reason = ingest_recipes.parse_recipe(row)
        assert reason == "ok"
        assert recipe["diet"] == "vegetarian" and recipe["region"] == "north"
        assert "toor_dal" in recipe["ingredients"]
        # the method must ride along from TranslatedInstructions
        assert recipe["instructions"].startswith("Cook the dal.")

    def test_side_dish_rejected_by_design(self):
        recipe, reason = ingest_recipes.parse_recipe(
            {"Course": "Side Dish", "TranslatedIngredients": "1 cup Toor dal"})
        assert recipe is None and reason == "course"

    def test_non_indian_cuisine_rejected_by_default(self):
        row = {"Course": "Dinner", "Cuisine": "Thai",
               "TranslatedIngredients": "1 cup Rice,2 Onion,3 Tomato,2 tablespoon Oil"}
        recipe, reason = ingest_recipes.parse_recipe(row)
        assert recipe is None and reason == "cuisine"
        recipe, reason = ingest_recipes.parse_recipe(row, indian_only=False)
        assert reason == "ok"

    def test_gujarati_bom_variant_is_indian(self):
        """The CSV's 'Gujarati Recipes' carries a zero-width BOM — must match."""
        from scripts.ingredient_tables import is_indian_cuisine
        assert is_indian_cuisine("Gujarati Recipes﻿")
        assert is_indian_cuisine("Tamil Nadu")
        assert not is_indian_cuisine("Continental")
