<title>MealMind — Test Guide & User Manual</title>

# MealMind — Test Guide & User Manual

Accounts, login and sign-up are **built and verified**: 101 automated tests plus
51 live end-to-end checks pass. This document is how you test it and how you
use it.

---

## Part 1 · Start the app

Two terminals.

**Terminal 1 — backend**
```bash
cd backend
./run.sh
```
Wait for `Application startup complete.` It replaces any old instance on port
8000 automatically, so "address already in use" can't happen.

**Terminal 2 — frontend**
```bash
cd frontend
python3 serve.py
```

Then open **http://localhost:8080**. You should land on the **sign-in screen**,
not the meal planner.

> **If you still see the meal planner instead of the login screen**, your browser
> is holding a cached copy of the old app. Hard-refresh once: **Cmd+Shift+R**
> (Mac) or **Ctrl+Shift+R**. This is now self-correcting — the service worker
> was rewritten so a stale copy can never again hide a new screen — but the very
> first load after this change needs that one refresh.

---

## Part 2 · Test the login and sign-up

Twenty-two checks. Each says exactly what to do and what should happen.

### Sign up

| # | Do this | Expected |
|---|---|---|
| 1 | Open http://localhost:8080 | Full-screen entry page: app icon, "MealMind", value line, three ✓ points, **Sign in / Create account** toggle. No meal planner, no bottom tab bar. |
| 2 | Tap **Create account** | Form gains a *Household name* field; the password label gains "· at least 8 characters"; button reads **Create account**. |
| 3 | Press **Create account** with everything blank | Red text under *both* fields: "Enter your email address." / "Enter your password." Cursor lands in the **email** field. |
| 4 | Type `notanemail`, any password, submit | Under email: "That doesn't look like an email address." No network call. |
| 5 | Fix the email, set password `short`, submit | Under password: "Use at least 8 characters." |
| 6 | Enter a real email + `password123` + a household name, submit | Button shows a spinner and "Creating account…", then the app opens on the Plan screen with a **"Start with a plan, not a pantry"** card and an "Account created" toast. |
| 7 | Sign out (Settings tab → **Sign out**), then try to create the *same* account again | Pink banner: "That email already has an account." with a **Sign in instead** link that switches modes and keeps your typed email. |

### Sign in

| # | Do this | Expected |
|---|---|---|
| 8 | Sign out, enter your email with a **wrong** password | Banner: "Wrong email or password." Your typed password is *not* cleared. |
| 9 | Enter the correct password and press **Enter** on the keyboard (don't click) | Signs in — Enter submits the form. |
| 10 | Tap **Show** next to the password | Password becomes readable; button reads **Hide**. Resets to hidden after signing in. |
| 11 | Sign in with your email in **CAPITALS** | Works — email matching is case-insensitive. |
| 12 | Tap **Use the demo account** (bottom of the screen) | Signs straight in as the seeded demo household with a full pantry. |

### Sessions and isolation — the ones that matter

| # | Do this | Expected |
|---|---|---|
| 13 | Sign in, generate a plan, then **Sign out** and sign in as a *different* account | The new account sees an **empty** Plan screen — no trace of the previous account's meals, shopping list or pantry. |
| 14 | Signed in, close the tab, reopen http://localhost:8080 | Goes **straight into the app** — the session is remembered for 30 days. |
| 15 | Open DevTools → Application → Local Storage → delete `mm_token`, then reload | Back to the sign-in screen. |

### Pantry and cooking

| # | Do this | Expected |
|---|---|---|
| 16 | Generate a plan, tap **"I already have some of this…"**, tick two items, Apply | Toast confirms they were added to your pantry. Menu identical. |
| 17 | Open the **Pantry** tab | The ticked items are there with an urgency meter, aged from today. |
| 18 | Reopen the tick dialog | It still lists **every** item the plan needs — the ones you ticked are pre-checked with their pantry amount. It never empties out. |
| 18b | **Untick** something and Apply | It is removed from your pantry, and the shopping list adds it back. |
| 19 | On the Plan screen tap **"I cooked this"** on a meal | Card turns green, toast lists the grams used. |
| 20 | Check the Pantry tab | Those ingredients are reduced or gone. |
| 21 | Reload the page and reopen the plan | The meal is still marked cooked. |
| 22 | Try to cook the same meal twice | Blocked — the button is disabled once cooked. |

### Automated suites

```bash
cd backend && python3 -m pytest -q          # 105 tests, ~2s, fully offline
```

Expected: `105 passed`. Six cover auth specifically (registration, duplicate
email, wrong password, tampered tokens, cross-user isolation, salted hashing)
and six cover the pantry lifecycle (ticks persisting, idempotency, cooking
drawing stock down oldest-first, double-cook rejection, and censored
observations reaching the decay model).

---

## Part 3 · User manual

### Creating your household

Sign up with an email and a password of at least 8 characters. The household
name is optional. Every account is completely separate: its own pantry, plans,
preferences and spoilage history. The recipe corpus and prices are shared.

**Demo account** — `demo@mealmind.app` / `demo1234`. It owns a pre-filled
pantry with ageing spinach and paneer, so the spoilage model has something to
work with. The "Use the demo account" button only appears on localhost or a
home network, never on a deployed instance.

### The core idea: plan first, pantry second

Most planners make you inventory your kitchen first. MealMind is the reverse:

1. **Set your budget, days and family size** → **Generate plan**
2. You get meals *and* a complete shopping list
3. Tap **"I already have some of this…"** — the dialog lists *everything* the
   plan needs, with anything already in your pantry pre-ticked. Tick what you
   have, untick what you don't, adjust the grams, and filter with the search box
4. The shopping list shrinks and shows what you saved — **the menu never changes**

That last point is deliberate: ticking a box you own should never alter what
you're cooking. Only the **Swap** button changes meals.

### If it says "can't afford this plan"

A hard budget ceiling means some baskets are simply impossible — 30-odd
commodities each needing a whole pack adds up fast. When that happens the app
now says so explicitly and tells you the minimum the basket would cost, e.g.
*"This plan needs about ₹1,461 to buy — ₹1,000 isn't enough."*

Your options: raise the budget, cut the days, or tick more of what you already
have. Previously this case silently reported **₹0 spent** and **"within
budget"**, which read as a free shopping trip — that was a reporting bug, now
fixed and covered by a regression test.

Separately, when the budget is merely *tight*, the solver drops **optional**
items (spices, ghee, garnish) to fit. The app now lists what it left off.

### When the plan says "heuristic planner"

`PlanResponse.planner` reports which arranger ran. If it says **heuristic**,
hover the badge (or read the toast) for the reason — usually one of:

- `no LLM_API_KEY configured` — the offline path, working as designed
- `HTTP 503 …` — the model provider was overloaded. Free Gemini tiers do this
  regularly. The client now **retries three times with backoff** before giving
  up, so this is much rarer than it was
- `daily free-tier quota exhausted` — **the most common one.** Gemini's free
  tier allows only **20 requests per model per day**. The cap is *per model*,
  so the client now automatically falls through to the models listed in
  `LLM_FALLBACK_MODELS` before giving up. Add more model names there for more
  headroom, or wait for the daily reset (midnight US Pacific)
- `the model did not return a usable plan` — the answer failed grounding
  validation (an unknown recipe id, a missing slot, or too many non-veg meals)

The heuristic is a correct, deterministic fallback — not a failure. Every plan
it produces respects the same budget, diet and spoilage constraints.

### The Plan screen

- **Budget / Days / Family size** — the budget is a hard ceiling, never exceeded.
- **Diet** — vegetarian, vegan, eggetarian or non-vegetarian.
- **Non-veg meals (max)** — a cap *and* a target. "2" over three days gives you
  two meat or fish meals, spread across days, at lunch or dinner. "0" gives a
  vegetarian week from a non-vegetarian corpus.
- **Meal cards** — tap any card to open the full recipe: ingredients scaled to
  your family size, and the method as numbered steps. Tap 🔒 to lock a meal
  before swapping.
- **Shopping list** — for each item: what the recipes need, what to buy, the
  **surplus** that pack sizes force on you, and the cost. Price signals
  (▲ buy now / ▼ can wait) come from 30 years of price history and are advisory
  only — they never move your budget.
- **Use-it-up: next 48 hours** — extra meals chosen to consume that surplus plus
  anything in your pantry that's about to spoil.
- **Variety** and **Spoilage risk** — the entropy measure and per-item urgency.

### Marking a meal as cooked

Every meal card has an **"I cooked this"** button (also inside the recipe sheet
as **"I made this"**). Tap it once you've actually made the dish and the app
draws that recipe's ingredients out of your pantry — **oldest stock first**,
because the item nearest to spoiling is the one that should be eaten.

- The card turns green and reads "Cooked — pantry updated".
- A toast tells you exactly what was used: *"Pantry updated — used 250 g Poha,
  150 g Onion, 100 g Potato"*.
- Ingredients you never had in the pantry (they came off the shopping list) are
  simply not deducted.
- Anything you finish completely quietly teaches the spoilage model — an item
  eaten before it went bad is exactly the *right-censored observation* the
  Bayesian model wants, so cooking normally makes the predictions better
  without you reporting anything.
- There is **no undo**. Marking the same meal twice is rejected, so a double-tap
  can't drain your pantry twice.

### Ticking "I already have this"

The tick dialog on the Plan screen now **writes to your pantry for real**. What
you tick becomes a pantry item, starts ageing that day, and the spoilage model
begins tracking it. Re-ticking the same thing updates the quantity rather than
adding a duplicate.

> **Your total can go *up* after ticking, and that's correct.** When the budget
> is tight the solver drops optional items (ghee, spices) to stay under the
> ceiling. Freeing money by ticking lets it afford them again — so you get a
> more complete basket for slightly more money. The app says which item came
> back rather than showing you a baffling larger number.

### The Pantry screen

Anything you tick as already-owned lands here, and the spoilage model starts
tracking it from that day. Each item shows an urgency meter — the probability it
spoils in the next 48 hours, given it has survived this long.

Two buttons per item:
- **Spoiled** — it went bad. The model learns to expect faster spoilage.
- **Used up** — you cooked it in time. The model learns to trust longer shelf life.

These genuinely retrain the model for your kitchen (a Bayesian posterior
update). A "learned" badge appears once an item class has real data.

### The Settings screen

Household defaults that fill in whenever you leave a field blank on the Plan
screen: diet, region, maximum cooking time, dislikes, family size. Also where
you sign out.

---

## Part 4 · Testing on your phone

1. Phone and laptop on the **same Wi-Fi**
2. Start the backend with `./run.sh` (it already listens on all interfaces)
3. Open the LAN URL that `serve.py` prints, e.g. **http://10.4.199.145:8080**
4. Type `http://` explicitly — phones silently try `https://`, which this dev
   server doesn't speak
5. Safari → Share → **Add to Home Screen** to install it as an app

If it won't load, work down `frontend/DEBUG_PHONE.md`. On a campus network the
usual culprit is client isolation — use your phone's hotspot instead.

---

## Part 5 · Connecting a quick-commerce price API

By default prices come from the seeded table (built from the Agmarknet mandi
snapshot). MealMind can instead fetch **live** prices — that's step 5 of the
pipeline, and it prefers `live` → `cache` → `seed`.

### The three tiers

Every shopping-list item reports which tier priced it, in `price_source`:

| Tier | Source | When it's used |
|---|---|---|
| `live` | QuickCommerce API | only when `QUICKCOMMERCE_API_KEY` is set |
| `cache` | `price_packs` rows saved from a previous live fetch | live is off or the call failed |
| `seed` | Agmarknet-derived seeded prices | the always-available fallback |

### Seeing which one was used

The shopping list shows it two ways:

- **A summary next to the heading** — *"— 12 items to buy · `LIVE` 30 priced
  live from quick commerce"*, or `OFFLINE` when using built-in prices.
- **A "Price from" badge on every row** — `LIVE` (green, fetched just now),
  `CACHED` (pale green, saved from an earlier fetch because the API wasn't
  reachable), or `SEED` (grey, built-in Agmarknet data). Hover any badge for
  the explanation.

So if you switch to live and a row still says `SEED`, that commodity simply
wasn't found by the provider — the fallback did its job rather than failing.

### Switching, in one command

```bash
cd backend
python3 -m scripts.prices status        # which tier is in use right now
python3 -m scripts.prices live          # live prices via the bundled mock
python3 -m scripts.prices offline       # back to seeded prices
python3 -m scripts.prices clear-cache   # drop cached live prices
```

Point it at a real provider instead of the mock:
```bash
python3 -m scripts.prices live --url https://api.provider.com/v1 --key YOUR_KEY
```

Restart the backend after switching (`./run.sh`) — `.env` is read at startup.

> **Gotcha:** cached live prices outrank seeded ones, so turning live *off*
> isn't enough to get pure seeded prices — run `clear-cache` too. The script
> warns you about this.

### Try it right now, with the bundled mock provider

No Indian quick-commerce platform (Blinkit, Zepto, Instamart, BigBasket)
publishes a public product API, so the client was written against a generic
contract. A mock provider ships with the project so you can prove the path
works end to end:

```bash
# Terminal 3
cd backend
python3 -m scripts.mock_quickcommerce      # serves on :9000 from your seeded prices
```

Then in `backend/.env`:
```bash
QUICKCOMMERCE_API_KEY=mock-key
QUICKCOMMERCE_BASE_URL=http://localhost:9000
```

Restart `./run.sh` and generate a plan. Every shopping-list row's
**`price_source` flips from `seed` to `live`**, and the fetched packs are
written to the `price_packs` cache. Verified: 28/28 items resolved live, budget
still respected.

**Turn it off again** by blanking `QUICKCOMMERCE_API_KEY`, and clear the mock
prices — cached live rows outrank seeded ones:
```bash
python3 -c "import sqlite3;d=sqlite3.connect('mealmind.db');d.execute(\"delete from price_packs where source='live'\");d.commit()"
```

### Connecting a real provider

Only one method needs changing — `QuickCommerceClient._search` in
`backend/app/services/quickcommerce.py`. Everything else (matching, quantity
parsing, caching, fallback) is provider-agnostic.

The contract it expects:
```
GET {QUICKCOMMERCE_BASE_URL}/products/search?q=toor%20dal
Authorization: Bearer {QUICKCOMMERCE_API_KEY}

{"products": [{"name": "Toor Dal", "quantity": "500 g", "price": 78.0}, ...]}
```
`quantity` is free text — the parser handles `g`, `kg`, `ml`, `L`, `pcs`,
`dozen` and `2 x 250g`, and skips anything it can't parse rather than guessing.

To adapt: rewrite `_search` to call your provider's endpoint with its own auth
scheme, and map its response into that `{name, quantity, price}` shape. If a
call fails for any reason, `fetch_packs` returns `[]` and pricing falls back to
cache then seed — the app never breaks because a provider is down.

**Before trusting it with real money**, add a negative-keyword list. Matching is
token overlap, so "Tomato Ketchup" currently scores as a perfect match for
"tomato" (documented and tested in `test_ketchup_limitation_is_real`).

Realistic options for a real deployment: an official retailer partnership,
BigBasket/Blinkit affiliate feeds where available, or a scheduled scrape you own
the legal risk of. Each one is a different `_search` implementation.

---

## Part 6 · Housekeeping

**Reset the database to a clean demo state** (deletes all accounts and plans,
restores the demo household):
```bash
cd backend
rm mealmind.db
python3 -m scripts.seed
python3 -m scripts.ingest_recipes --keep-seed    # restores the 2,019-recipe corpus
```

**Presentation mode with no backend at all** — set `window.MOCK = true` near the
top of `frontend/index.html`. The app then runs on canned data, skips login
entirely, and nothing can fail on stage.

**After editing `frontend/index.html`**, bump `VERSION` in `frontend/sw.js` so
installed phones pick up the change.

### Security notes, stated plainly

- Passwords are stored as salted PBKDF2-HMAC-SHA256 hashes (200,000 iterations).
  Plain passwords are never stored or logged.
- Sessions are stateless HS256 JWTs, valid 30 days, signed with `JWT_SECRET`
  from `backend/.env`. A machine-unique secret has been generated for you.
  Changing it signs everyone out.
- Login returns one identical message for a wrong email and a wrong password, so
  the app never reveals which addresses have accounts.
- There is **no password reset** — there's no mail transport in this project. To
  regain access to a forgotten account, create a new one or reset the database.
