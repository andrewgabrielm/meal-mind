# CLAUDE.md

Persistent context for Claude Code on the MealMind project. Read fully before
running anything.

## What this project is

Budget-aware meal planner for Indian households (MDS482-4, Christ University).
Three algorithms carry the academic weight:

- **(a) Package-size optimisation** — two integer programs (PuLP/CBC): a
  minimum-cost purchase under a hard budget ceiling, then a 48-hour plan that
  spends the surplus those fixed pack sizes forced on the user.
- **(b) Bayesian shelf-life decay** — a Weibull survival model with a conjugate
  prior on the scale parameter, estimating spoilage risk from purchase date,
  item class and storage.
- **(c) Menu variety enforcement** — Shannon entropy over dish families in a
  rolling window, penalising over-represented families during retrieval.
- **(d) Price volatility advisories** — ARIMA(1,1,0) mean + GARCH(1,1)
  variance, hand-rolled in pure Python (`services/forecast.py`), trained on
  the WFP monthly price history. ADVISORY ONLY: it labels shopping-list items
  buy_now / normal / wait_if_possible and must never enter the purchase ILP.

(a) and (b) are coupled: the urgency probability from (b) is the objective
coefficient of the second integer program in (a). That coupling is the
project's central claim and is guarded by `test_the_two_features_couple`.

## Stack — do not change

- **Backend: FastAPI** on Python 3.11+. Keep it. No Flask, no Django.
- SQLAlchemy 2.x, SQLite by default (PostgreSQL via one env var).
- PuLP + CBC for the integer programs. `math` stdlib for the decay AND
  forecast models — no numpy/scipy/statsmodels anywhere.
- Pydantic v2 for the wire contract. httpx for the two outbound clients
  (LLM, QuickCommerce).
- LLM planner targets an **OpenAI-compatible** chat-completions endpoint
  (Grok via `https://api.x.ai/v1` or OpenAI), switched by `LLM_BASE_URL`.
- **Frontend: a single static PWA** (`frontend/index.html`), no build step,
  no npm, no CDN, no external fonts.

No new frameworks or heavy dependencies. If something seems to need one, stop
and ask.

## Architecture contract — enforce strictly

```
routers/          HTTP only. NO SQL, NO business logic. Validate, delegate, return.
services/         business logic + algorithms. NO SQL, NO Session, HTTP-agnostic.
repositories.py   ALL database access. NO business logic.
```

If a change would put SQL in a service or business logic in a router, it is
wrong — route it through the correct layer instead.

## Layout

```
backend/
├─ app/
│  ├─ main.py            app factory, CORS, /health
│  ├─ config.py          settings from .env
│  ├─ db.py              engine, session, get_db
│  ├─ models.py          10 SQLAlchemy tables
│  ├─ schemas.py         Pydantic request/response
│  ├─ repositories.py    all SQL
│  ├─ presenter.py       PlanInputs assembly + PlanResponse serialisation
│  ├─ routers/           auth.py, pantry.py, plans.py, preferences.py
│  └─ services/
│     ├─ consumption.py    oldest-first stock draw when a meal is cooked
│     ├─ decay.py          feature (b) — Weibull + conjugate posterior
│     ├─ optimizer.py      feature (a) — both integer programs
│     ├─ variety.py        feature (c) — clustering + Shannon entropy
│     ├─ forecast.py       feature (d) — ARIMA + GARCH advisories, pure math
│     ├─ planner.py        retrieval, LLM arrangement, pipeline assembly
│     ├─ pricing.py        live -> cache -> seed resolution
│     └─ quickcommerce.py  live price client + quantity parser
├─ scripts/
│  ├─ seed.py                28 hand-structured recipes, prices, demo pantry
│  ├─ ingest_recipes.py      recipe CSV (dataset/) -> recipes
│  ├─ ingest_prices.py       WFP monthly history -> price_history (GARCH data)
│  ├─ mock_quickcommerce.py  stand-in provider for testing the live-price path
│  ├─ prices.py              switch live/seed price source, inspect tiers
│  └─ ingredient_tables.py   measure/density/alias tables (edit THIS to tune)
├─ tests/test_mealmind.py    105 tests
├─ requirements.txt
└─ .env.example

dataset/                  (project root, gitignore-worthy: ~50 MB)
├─ IndianFoodDatasetCSV (1).csv           Archana's Kitchen recipes, ~6,900 rows
├─ wfp_food_prices_ind.csv                WFP monthly retail prices 1994-2026
└─ 9ef84268-d588-465a-a308-a864a43d0070.csv   Agmarknet one-day mandi snapshot

frontend/
├─ index.html            the whole app
├─ serve.py              dev server: all interfaces, correct MIME, LAN URLs
├─ manifest.webmanifest, sw.js, icons/
└─ DEBUG_PHONE.md        brief for "won't load on my phone"
```

## Pipeline order

`POST /api/v1/plans/generate` runs these in sequence. The order is deliberate.

```
1  decay assessment    pantry ages -> urgency weights + forced-include list
2  recipe retrieval    diet filter, pantry-overlap ranking, variety penalty
3  LLM arrangement     picks recipe_ids into day/course slots — SEES NO PRICES
4  requirements        chosen recipes -> grams per commodity x family_size
5  price resolution    QuickCommerce -> cache -> seed
6  purchase ILP        min-cost packs under hard budget -> shopping list + surplus
7  leftover ILP        surplus + stock -> 48h meals, weighted by step-1 urgency
8  log + respond       meal_plan_log, then PlanResponse
```

Two things people get wrong about this:

- **The LLM is in the middle, not at the end.** If it were last it would see
  prices and be the thing deciding affordability, which no LLM can be trusted
  to do. Putting it at step 3 means everything after it is deterministic math.
- **Prices are fetched at step 5, not up front.** You cannot price ingredients
  until you know which recipes were chosen. Fetching earlier also wastes
  QuickCommerce credits on commodities you may not use.

## Data sources

- **Recipes** come from the local `recipes` / `recipe_ingredients` tables,
  loaded by `scripts/seed.py` (28 hand-structured) or `scripts/ingest_recipes.py`
  (the Archana's Kitchen CSV in `dataset/`). The LLM never invents a recipe —
  it selects only `recipe_id`s it was handed, and every returned id is validated.
- **Prices** come from the QuickCommerce API when `QUICKCOMMERCE_API_KEY` is
  set, falling back to the `price_packs` cache rows and then to seeded prices.
  Seeded prices are derived from the Agmarknet mandi snapshot (median modal
  price x 1.4 retail markup) where it covers a commodity, hand-set otherwise.
  The seeded path is the backup and the testing path; it must keep working
  with no key, no network and no CSV.
- **Price history** (WFP, monthly, 1994-2026) exists ONLY to train feature
  (d). Current prices never come from it; volatility never comes from the
  one-day Agmarknet snapshot.

## The algorithms

### (b) Bayesian decay — `services/decay.py`

Weibull survival `S(t) = exp(-(t/(alpha*gamma_S))^beta)`. `beta > 1` gives the
increasing hazard real spoilage shows; `gamma_S` is the storage multiplier
(room 1.0, fridge 3.0, freezer 12.0).

Genuinely Bayesian because `alpha` is not fixed. With `beta` held constant,
reparameterising `theta = alpha^beta` gives an inverse-gamma conjugate prior:

```
posterior a = a0 + (observed spoilage events)
posterior b = b0 + sum (room-equivalent lifetime)^beta   over ALL observations
alpha_hat   = (b / (a - 1))^(1/beta)
```

Censored observations (consumed while still good) contribute to `b` but not
`a` — the standard Weibull right-censoring likelihood. The prior is calibrated
so its mean equals the literature baseline, so zero data gives textbook shelf
life and the estimate adapts as `POST /pantry/spoilage` collects outcomes.

Urgency uses the **conditional** probability, not the plain CDF from the
original system design: `P(spoils in next H | survived to t) = 1 - S(t+H)/S(t)`.
An item that already survived four days is a different risk to a fresh one.

### (a) Package-size optimisation — `services/optimizer.py`

Stage 1, purchase:

```
minimise    sum x_ij * c_ij                    x_ij in Z+
subject to  pantry_i + sum_j x_ij * w_ij >= R_i     (coverage)
            sum x_ij * c_ij <= B                    (hard budget ceiling)
```

Surplus is `leftover_i = pantry_i + bought_i - R_i` — the number the whole
feature exists to expose. Infeasible under budget drops optional commodities in
descending cost order and re-solves rather than raising.

Stage 2, 48-hour utilisation:

```
maximise    sum_r y_r * value_r                y_r in {0,1}
where       value_r = sum_i urgency_i * min(needs_ri, available_i)
subject to  sum_r y_r <= slots
            sum_r y_r * needs_ri <= available_i
```

`available` is pantry **plus** what stage 1 just bought — not surplus alone.
Constraining to surplus made at-risk pantry stock invisible to the solver and
blocked recipes on ingredients the shopping list was buying anyway.

`urgency_i` comes straight from the decay model. **This is where the features
couple.** `min(...)` is precomputed, so the objective stays linear.

### (c) Variety enforcement — `services/variety.py`

Shannon entropy `H = -sum(p_k log2 p_k)` over dish families in a 14-day window.

Three deliberate departures from the system design document, all defensible and
all worth keeping:

1. **Clusters are dish families, not recipes.** Per-recipe clusters would keep
   entropy near maximum (each dish appears once or twice a fortnight) and the
   check would never fire. What feels repetitive is the base repeating — dal
   and rice seven nights running. Clusters are assigned by dominant non-staple
   mass, with **proteins taking precedence over heavier vegetables** (palak
   paneer is a paneer dish), and **curd/milk excluded from that precedence**
   (a side of curd does not make aloo paratha a dairy dish). Rice only takes a
   cluster when overwhelmingly dominant, or every main would read as rice.
2. **Normalised entropy against ATTAINABLE clusters.** `H` is bounded by
   `log2(K)`, so a raw threshold needs retuning whenever the corpus changes.
   Worse, `K` must be the clusters the diet can actually reach — a vegetarian
   household can never cook egg or meat, so dividing by `log2(8)` understates
   their variety and puts the threshold out of reach by construction. `K` comes
   from the diet-filtered candidate pool.
3. **Penalties are proactive, not a re-solve loop.** The doc describes
   measuring entropy then adding penalties then re-solving. Same effect with
   less machinery: the penalty applies during retrieval ranking so entropy
   stays high, and `H` is reported as the measured outcome.

Penalty per cluster is `(p_k - 1/K) * strength` for over-represented families
only, engaged solely when normalised entropy drops below `Hmin = 0.8`. Capped
at `strength = 4.0`, well under retrieval's `+10.0` forced-include bonus —
**variety must never override feature (b)**. A dying bunch of spinach gets
cooked even if it is the third spinach dish this fortnight.

History is capped by **meals** (`days * 3`), not plans. Regenerating five times
in one sitting is not five weeks of eating.

`PlanResponse.variety` reports `entropy_bits`, `max_entropy_bits`,
`normalised_entropy`, `attainable_clusters`, `penalties_engaged` and the cluster
`distribution`.

## The LLM's role

It arranges retrieved recipes into day and course slots. That is all.

It may emit only `recipe_id`s it was given; every id is validated against the
candidate set and a plan containing an unknown one is rejected and re-prompted
once. It never sees prices, never sums a basket, never decides affordability.

With `LLM_API_KEY` blank, the deterministic `HeuristicPlanner` runs and the
whole pipeline works offline. `PlanResponse.planner` reports which ran.

## Commands

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env
python -m scripts.seed                          # 28 recipes, prices, demo pantry
                                                # (also loads WFP history if present)
uvicorn app.main:app --reload                   # http://localhost:8000/docs
pytest                                          # 105 tests, all offline

python -m scripts.ingest_recipes --dry-run      # parse the recipe CSV, report only
python -m scripts.ingest_recipes                # replace recipe tables
python -m scripts.ingest_recipes --keep-seed    # append to the seeded 28
python -m scripts.ingest_prices                 # WFP history -> price_history

cd ../frontend && python3 serve.py              # http://localhost:8080
```

`MOCK = true` at the top of `frontend/index.html` runs the app with no backend.
That is the presentation mode. Set `false` to use the live API.

If `pytest` gives `sqlite disk I/O error`, the project is on a filesystem SQLite
cannot journal on (some network/container mounts). Move it to a local path or
set `DATABASE_URL=sqlite:////tmp/mealmind.db`. Environment issue, not a bug.

## Guardrails — do not modify without being asked

These are verified and carry the project's academic weight:

- `services/decay.py` — the Weibull math and conjugate posterior update.
- `services/optimizer.py` — both integer programs and their constraints.
- `services/variety.py` — clustering rules, entropy normalisation, penalty cap.
- LLM grounding and validation in `services/planner.py` (`LLMPlanner`). Never
  loosen it; it is what stops hallucinated recipes.
- The coupling in `planner.build_plan` — urgency weights feeding the leftover
  ILP. `test_the_two_features_couple` must keep passing.

Also:

- **Forecasts are advisory only.** `services/forecast.py` output goes into
  shopping-list badges, never into the purchase ILP's objective or
  constraints. If a change makes a forecast move money, it is wrong.

- **The LLM never computes cost or budget.** All money math stays in the
  purchase ILP. This is what makes "100% budget adherence" defensible.
- **Variety never outranks urgency.** If a change lets the entropy penalty
  exceed the forced-include bonus, it is wrong.
- **Tune `scripts/ingredient_tables.py`, never `ingest_recipes.py`**, when
  ingest coverage needs improving.
- Auth EXISTS (added on request): stdlib-only PBKDF2 + hand-rolled HS256 JWT
  in `services/auth.py`, endpoints in `routers/auth.py`, every router keyed to
  the token's user. Demo login `demo@mealmind.app` / `demo1234` owns the
  seeded pantry. Set `JWT_SECRET` in .env before exposing beyond localhost.
  Do not swap in passlib/PyJWT or add OAuth/roles unless asked.
- Do not swap the ORM, add Alembic, or change the DB unless asked.

## UX revamp — implemented

`TASK_ux_revamp.md` describes the shipped plan-first flow: inverted pantry
entry (plan first, tick what you have second), household preferences (region,
cooking time, dislikes), and meal swapping with slot locks. Read it before
touching `planner.py`, `schemas.py`, the routers, or the frontend plan screen.

Its endpoints:
- `POST /plans/{plan_id}/pantry` — apply ticked pantry, re-solve stages 4-7,
  **recipes frozen** (a pantry tick must never change which meals are suggested)
- `POST /plans/{plan_id}/swap` — refill unlocked slots (excluding the recipes
  being swapped away), re-solve stages 4-7
- `POST /plans/{plan_id}/cooked` — mark a meal made: draw its ingredients from
  the pantry OLDEST STOCK FIRST (`services/consumption.py`), record a censored
  observation for anything emptied, re-solve stages 4-7. Idempotent (409 on
  repeat). Ticks persist as real pantry rows, upserted by commodity.
- `GET/PUT /preferences` — request fields override, preferences fill gaps

User-initiated swaps change recipes; ticking a pantry box never does. Keep those
two code paths separate (`apply_pantry` vs `planner.refill_slots`).

## Known limitations — state these, do not hide them

- **Corpus is 28 recipes** unless the CSV is ingested. For the vegetarian
  default only **K=4 dish families are attainable** (legume, mixed_veg,
  paneer, potato) — the corpus is too small to reach the full family set.
  Measured normalised entropy runs 0.94–0.98 because the heuristic avoids
  same-day cluster repeats proactively; expect it to sag toward the 0.80
  threshold only under sustained monotonous choices.
- **No real quick-commerce API has been called.** No Indian platform publishes
  a public product API, so the client speaks a generic contract. It IS verified
  end to end against `scripts/mock_quickcommerce.py` (28/28 items resolved
  "live", cached, budget respected). Adapting to a real provider means editing
  `QuickCommerceClient._search` only. Commodity matching is token
  overlap, so "Tomato Ketchup" scores as a tomato match — a negative-keyword
  list is needed before this is trusted with real money
  (`test_ketchup_limitation_is_real` documents it).
- **The LLM planner is verified against Gemini** (`gemini-flash-latest` via
  Google's OpenAI-compatible endpoint, free tier): grounding validation passed
  and a full plan was arranged live on 06/08/2026. Grok and OpenAI remain
  config-swap options, untested for lack of credits. With no key the heuristic
  runs; tests stay fully offline.
- **Recipe ingest at scale**: full dry-run over the real 6,871-row CSV
  converts 2,461 recipes — 66% of the 3,705 plan-eligible rows (sides,
  snacks and desserts are excluded by design). Tune coverage only via
  `scripts/ingredient_tables.py`.
- **The Agmarknet snapshot is one day** (04/08/2026). It seeds current
  prices; it cannot and must not feed the volatility model.
- **The posterior has no real data by default.** The pantry screen's
  "Spoiled"/"Used up" buttons call `POST /pantry/spoilage`, so
  `learned_from_observations` flips true only after a user reports outcomes.
- Not built, from the original design: nutritional constraints, YOLOv8 photo
  ingestion, spice cosine similarity, geolocation. ARIMA/GARCH price
  forecasting IS built (feature d, advisories only), and JWT auth IS built
  (stdlib-only, see routers/auth.py).

## When something fails

- Report the failing command and its output before changing anything.
- A failure in `test_optimizer`, `test_decay` or the variety tests means an
  algorithm changed — **revert, do not patch the test**.
- Import or version errors are usually stale `__pycache__` or a missing
  dependency; clear caches and check `requirements.txt` first.
- If a fix would touch a guardrail file, stop and ask before proceeding.
