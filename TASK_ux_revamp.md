# TASK: UX revamp — plan first, tick second  (STATUS: implemented)

The original flow made users inventory their kitchen before seeing a plan.
Nobody weighs their dal. The revamp inverts it:

1. **Plan first.** `POST /api/v1/plans/generate` assumes the stored pantry
   and produces meals + a full shopping list immediately.
2. **Tick second.** The plan screen offers "I already have some of this…" —
   a checklist derived from the shopping list. Ticks go to
   `POST /api/v1/plans/{plan_id}/pantry`, which re-solves **stages 4–7 only**
   (requirements → prices → purchase ILP → leftover ILP). **Recipes are
   frozen**: a pantry tick must never change which meals are suggested; the
   shopping list just shrinks and the saved amount is shown.
3. **Swap when wanted.** `POST /api/v1/plans/{plan_id}/swap` refills every
   unlocked slot with fresh recipes (excluding the ones being swapped away),
   keeps locked slots byte-identical, then re-solves stages 4–7.

User-initiated swaps change recipes; ticking a pantry box never does. The two
code paths are separate on purpose: `apply_pantry` reuses the persisted meal
state via `presenter.meals_from_state`, `swap` goes through
`planner.refill_slots`. Do not merge them.

Also added:

- **Household preferences** (`GET/PUT /api/v1/preferences`): diet, region,
  max cooking time, dislikes (commodity keys), family size. Request fields
  override preferences per call; preferences fill the gaps.
- **Slot locking** in the frontend (🔒 on a meal card) feeding `swap`'s
  `locked` list.
- Plan persistence (`plans` table: request/state/response JSON) so both
  endpoints can re-solve against the frozen selection.

Where things live:

- `backend/app/routers/plans.py` — both endpoints, thin.
- `backend/app/services/planner.py` — `solve_economics` (stages 4–7,
  shared), `refill_slots` (swap).
- `backend/app/presenter.py` — meal state (de)serialisation.
- `frontend/index.html` — tick dialog, lock toggles, swap button.

Tests guarding the contract: `test_pantry_tick_freezes_recipes`,
`test_swap_changes_only_unlocked` in `backend/tests/test_mealmind.py`.
