# Plan: "Octagon Edge": UFC live betting analyzer (PWA on the homelab)

## Context
You want an app you can install on Android (a PWA) that does four things:
- tracks UFC odds from US sportsbooks and fighter stats
- adds social sentiment and an AI breakdown of each fight
- runs a core reasoning engine that decides when a bet has value
- sends a push notification when a good bet opens up, before or during a fight

**Constraints**
- No paid data APIs. Free tiers are OK.
- Hosted on the homelab with Docker.
- The LLM is a config switch: Ollama locally or Claude API.
- Design uses the `impeccable` and `taste-skill` Claude Code skills. `hyperframes` is optional, for video clips.
- **This weekend is UFC 333 (Sat 24 Oct 2026, Abu Dhabi). Early prelims start at 10am ET.** So the work is phased: a lean prep tool by Saturday morning, then the full app.

The working directory (`/home/user`) is empty, so this is a new project.

### What research found (it shapes the design)
- **Odds**
  - The Odds API free tier gives 500 credits a month. The `mma_mixed_martial_arts` sport includes live fights. One call returns every fight on the card and costs 1 credit per market per region.
  - Budget: about 120 credits per event (pre-fight snapshots, then one call every 3 minutes during the card). That covers about 4 events a month.
  - Kalshi and Polymarket have public read-only market data, priced as probabilities. They are the only free source that updates close to live.
  - Pinnacle's guest API is dead. Scraping sportsbook JSON breaks their terms and is geo-fenced, so it is skipped.
  - BestFightOdds has opening and line-history data. It is optional, a gray area, and must be rate-limited politely.
- **Stats**
  - ufcstats.com has per-round stats, but only after the event.
  - `Greco1899/scrape_ufc_stats` CSVs (GPL-3.0) give a historical bootstrap. Their last full refresh was Nov 2025, so recent events need our own scraper.
  - `balaustrada/ufcscraper` covers both stats and BestFightOdds.
  - **There is no free official live in-fight stats feed.** UFC.com's Fight Tracker reportedly loads an undocumented JSON file (CloudFront `/api/v3/fight/live/{id}.json`). It is unverified, so we confirm it during UFC 333. The fallback is manual round input in the app.
- **Social**
  - X/Twitter has no free read access since Feb 2026. Nitter, snscrape and similar tools are dead or banned. **X is dropped.**
  - Replacements:
    - Reddit (r/MMA fight threads, r/MMAbetting): free OAuth, 100 requests a minute. Apply for an app now, because approval may take a while.
    - Bluesky public search.
    - YouTube comments: 10k quota units a day.
    - RSS news (MMA Junkie, MMA Fighting, Sherdog) for injuries, weight misses and short-notice replacements.
- **Push**
  - Web Push (VAPID) works for an installed PWA on Android, with no third party involved. It needs HTTPS and a service worker. Send with `Urgency: high`.
  - Self-hosted ntfy and a Telegram bot are free backup channels that are easy to get working.
- **Design skills**
  - `impeccable`: `/plugin marketplace add pbakaus/impeccable`, or `npx impeccable install`. Its installer adds a post-edit detector hook to `.claude/settings.local.json`. **Telemetry is on by default**, so run `npx impeccable telemetry off`. CI uses `npx impeccable detect --json`, which exits with code 2 when it finds issues.
  - `taste-skill`: `/plugin marketplace add Leonxlnx/taste-skill`, then `/plugin install taste-skill@taste-skill`. Needs Node 22+ and Chromium (already preinstalled). Its rules include "colour only for data" and nav that folds away on phones, and it screenshots its output at 3 widths to check itself.
  - `hyperframes`: renders HTML to MP4 using headless Chrome and FFmpeg. Rendering takes seconds per clip, so it only suits pre-fight and post-fight clips, not live use.

---

## Architecture
```
Android PWA (React)  ──HTTPS (Cloudflare Tunnel)──▶  Caddy
                                                      ├─ /        static PWA build
                                                      └─ /api     FastAPI (REST + SSE live stream)
worker (APScheduler) ── ingest jobs ──▶ Postgres ◀── reasoning engine ──▶ notifier (WebPush / ntfy / Telegram)
                      └─ LLM provider (Ollama | Claude API, via config)
```
**Stack:**
- Python 3.12 backend: FastAPI, SQLAlchemy, APScheduler, httpx, selectolax, pandas, scikit-learn/LightGBM, pywebpush
- Frontend: Vite, React, TypeScript, `vite-plugin-pwa`, TanStack Query, and a light chart library
- Docker Compose services: `db`, `api`, `worker`, `web`/Caddy, `ntfy`, plus `ollama`, or point at an Ollama you already run

**Repo layout**
- `backend/app/`
  - `ingest/`
    - `odds_api.py`, `kalshi.py`, `polymarket.py`, `bfo.py`: odds and price sources
    - `ufcstats.py`: stats scraper
    - `fight_tracker.py`: UFC.com live JSON
    - `reddit.py`, `bluesky.py`, `youtube.py`, `rss.py`: social and news
  - `engine/`
    - `odds_math.py`: de-vig and implied probability
    - `ratings.py`: Elo/Glicko
    - `model.py`: pre-fight model
    - `live.py`: in-fight update
    - `edge.py`: edge, EV and Kelly
    - `rules.py`: alert rules
  - `llm/`: a `provider.py` interface, with `ollama.py`, `claude.py` and `prompts/`
  - `sentiment/`: a transformer classifier plus aggregation
  - `notify/`: webpush, ntfy, telegram
  - `api/`: routes
  - `models.py`, `config.py`
- `backend/tests/`
- `frontend/src/`
  - `pages/`: Card, Fight, Live, Bets, Settings
  - `sw.ts`: the service worker
- `PRODUCT.md`, created by `/impeccable init`
- `docker-compose.yml`, `.env.example`, `notebooks/`, `.github/workflows/ci.yml`

**Core tables:**
- event, fight, fighter, fighter_stats_snapshot, fight_round_stats
- odds_snapshot (book, market, price, ts), market_price (kalshi/polymarket)
- model_prediction, live_state
- social_post, sentiment_agg, news_item
- alert, bet (your logged bets, closing-line value (CLV), P&L)
- push_subscription, settings

## Core reasoning engine (a deterministic core, with the LLM as advisor)
1. **Market fair price** (`odds_math.py`)
   - Convert American odds to implied probability and remove the bookmaker margin (power or Shin method) for each US book.
   - Take the consensus fair probability. Use the best available price for EV.
   - Track line movement and the opening-to-current difference.
2. **Pre-fight model** (`ratings.py`, `model.py`)
   - Elo/Glicko within each division.
   - A LightGBM or logistic model on the stat differentials between the two fighters:
     - strikes landed and absorbed per minute (SLpM/SApM), striking accuracy and defence
     - takedown average, accuracy and defence; submission average
     - reach, age, layoff, stance, finish rates, short notice
   - Train on the historical CSVs plus our scrape. Calibrate it (isotonic), then backtest against closing odds using log loss, Brier score and simulated ROI.
   - **Only bet on the model if it beats the market's de-vigged closing line in the backtest.** Otherwise the model's output carries little weight.
3. **Blend**
   - final p = a weighted blend of the market fair probability and the model. The model's weight is learned in the backtest.
   - The LLM may apply a *bounded* adjustment, at most ±3 points of probability, for structured news flags such as an injury, a weight miss or a short-notice replacement. It must return JSON with its reasons.
4. **Live update** (`live.py`)
   - Inputs:
     - round results from the Fight Tracker JSON, or from manual taps in the app (round winner, knockdown, takedowns, control)
     - Kalshi/Polymarket prices, polled every 15–30 seconds during the fight
     - Odds API live h2h prices, every 3 minutes
   - Use a Bayesian update of the pre-fight probability. Round-state likelihoods come from historical round-by-round data in ufcstats.
5. **Edge and stake** (`edge.py`)
   - edge = p − 1/decimal_odds
   - Flag a bet when edge ≥ 4% pre-fight or ≥ 6% live, and model confidence passes a gate.
   - Stake = ¼ Kelly, capped at a configurable share of bankroll, with a daily stop-loss.
6. **Rules and alerts** (`rules.py`)
   - Alert types: value appears, line moves sharply, live swing after a round, or the price you set as a target is hit.
   - Each alert has a cooldown and is deduplicated.
   - Every alert carries the book, price, edge, stake and a 2-line LLM reason, and deep-links into the Fight page.
7. **LLM provider**
   - `LLM_PROVIDER=ollama|claude`. Ollama default is e.g. `qwen2.5:14b`. For Claude, use the latest Sonnet-class model.
   - Jobs it does: fight breakdowns, news-flag extraction, sentiment summaries, explaining alerts.
   - It never produces the raw probability.

## Sentiment
- Collect posts per fighter from Reddit, Bluesky, YouTube comments of breakdown videos, and RSS.
- Classify each post with `cardiffnlp/twitter-roberta-base-sentiment-latest`, which runs on CPU.
- Aggregate into: share of positive vs negative posts per fighter, volume, change over time, and a "public side" indicator.
- Use it as a **contrarian or hype signal and a source of news flags**, not as a direct input to the probability. The LLM summarises the themes.

## PWA screens (designed with the skills)
- **Card**: every fight, showing model vs market probability, an edge badge and the start time.
- **Fight**:
  - tale of the tape and stat differentials
  - line-movement chart across books and Kalshi
  - sentiment panel
  - the LLM breakdown, and video clips once Phase 6 is done
- **Live**:
  - the current fight and its live probability chart, streamed over Server-Sent Events (SSE)
  - big round-input buttons
  - the alert feed
- **Bets**: log a bet, then track CLV, P&L and ROI by bet type.
- **Settings**: bankroll, edge thresholds, which books, notification channels, LLM provider.

**Design workflow**
1. `/impeccable init` writes `PRODUCT.md`: a dark, data-dense live dashboard used one-handed at night.
2. taste-skill picks the art direction.
3. Run `/impeccable shape`, then `craft` for each screen.
4. Run `/impeccable adapt`, `harden` and `audit`.
5. CI runs `npx impeccable detect --json` and the taste-skill `inspect.mjs --app`.

---

## Phases

### Phase 0: UFC 333 prep (tonight, ready by Sat 10am ET)
Keep it lean: a Python package plus a script, with no frontend yet.
1. Ingest:
   - the full UFC 333 card (14 fights)
   - Odds API US h2h (4–6 credits in total)
   - Kalshi and Polymarket markets
   - each fighter's stats from ufcstats
2. Baseline model:
   - Elo plus a quick logistic regression on the Greco CSVs, with fresh scrapes added for fights after Nov 2025
   - de-vigged market probabilities, edge, and ¼ Kelly stakes
3. Generate `reports/ufc333.html`, with one section per fight:
   - market vs model probability and the edge
   - key stat differentials
   - Reddit and RSS news flags
   - the LLM breakdown
4. Live watcher during the event (`python -m app.live_watch`):
   - polls Kalshi every 20 seconds and the Odds API every 3–5 minutes
   - pushes to **ntfy and Telegram** when edge ≥ threshold or a price target is hit
5. Discovery: while UFC 333 runs, capture and save the UFC.com Fight Tracker JSON. This tells us whether real live stats are possible.

### Phase 1: Foundation (week 1)
- Docker Compose stack, Postgres schema with Alembic, config.
- Ingest jobs on schedules, with an Odds API credit tracker that reads `x-requests-remaining` and enforces a hard budget.
- Cloudflare Tunnel to expose it over HTTPS.

### Phase 2: Engine
- Full historical scrape and feature pipeline.
- Model, calibration and backtest notebook.
- The blend and edge logic, and the alert rules.

### Phase 3: PWA
- Install the design skills, then build the screens.
- Web Push: VAPID keys, a subscribe endpoint and service worker handling.
- Install the PWA on Android and test notifications with the screen locked.

### Phase 4: Live mode
- Fight Tracker ingest if the endpoint is viable, plus the manual round input.
- The live Bayesian update and the SSE stream to the Live screen.

### Phase 5: Sentiment and news
- Reddit, Bluesky, YouTube and RSS collectors.
- The classifier, aggregation, and LLM theme summaries.

### Phase 6: Extras
- hyperframes 9:16 pre-fight preview and post-event recap clips: a worker job renders them and the Fight page plays them.
- A bet tracker report on CLV.

---

## Engine review and upgrade plan (23 Oct 2026)

A review of the reasoning engine, the fighter stats it uses, and published UFC betting research. The numbers below come from running the current model on the Greco data (8,792 fights; trained on 2005–2022, tested on 1,587 fights since 2023 where both fighters had UFC history).

### Where the engine stands
- **The build is careful.** Features only use earlier fights, small samples are shrunk to league averages, the model is symmetric, de-vig uses the power method, model and market blend in log-odds, and stakes are ¼ Kelly with a 3% cap.
- **The stats model is decent but unproven against the market.** Test log loss 0.639, accuracy 64.8% (Elo alone: 0.682, 55.6%; coin flip: 0.693). Phase 2 said "only bet on the model if it beats the market's de-vigged closing line in the backtest" — that backtest was never built, so `MODEL_WEIGHT=0.35` is a guess.
- **The local LLM is not in use.** `LLM_PROVIDER` defaults to `none`. Even when set to `ollama`, the default `OLLAMA_URL` (`localhost`) points inside the Docker container, not at the GPU guest (101, .196), and the default model `qwen2.5:14b` isn't the one pulled there (`gemma4:12b`). When on, it only writes text and nudges the probability by ±3 points every 6 hours.
- **Bugs found:**
  1. Expert pick *share* is treated as a win probability ([experts.py:84](octagon_edge/ingest/experts.py), blended at [analysis.py:136](octagon_edge/analysis.py)). A unanimous 10/10 panel gives `p_a = 1.0`, which at 15% weight lifts a 75% favourite to about 95% and creates fake value on heavy favourites.
  2. Pre-fight push alerts use a different probability from the app ([alerts.py:118](octagon_edge/alerts.py)): no expert picks, no LLM nudge, no half weight or higher threshold for fighters with no UFC fights.
  3. The live model was trained with the stats-only pre-fight probability but is fed the market-blended `p_final` ([services.py:264](octagon_edge/services.py)), which is sharper than what it learned from.

### Fighter stats review
Measured on the 2023+ test set. "Drop-one" is how much worse log loss gets without the stat (in thousandths; positive = the stat helps on unseen fights).

| Stat | Drop-one | Verdict |
|---|---|---|
| Age difference | **+12.0** | By far the strongest stat. Elo + age alone gets most of the model's skill (0.682 → 0.652). A fighter 6+ years older won only 28% of test fights. Research also says bookmakers under-price youth, so this is the most likely source of a real edge. |
| Strikes absorbed / min (SApM) | +4.1 | Keep. Strong. |
| Elo | +3.9 | Keep, but weak on its own (see below). |
| Strikes landed / min (SLpM) | +1.9 | Keep. |
| Takedowns / 15 min | +1.4 | Keep. |
| Win rate, takedown defence | +1.0 each | Keep. |
| Got-finished rate, layoff | +0.7 each | Keep (a "chin" and ring-rust proxy). |
| Striking accuracy, UFC fights, streak, knockdowns, sub attempts, last 3, height | 0 to +0.3 | Noise. Candidates to drop. |
| KO-win rate, sub-win rate, control share | about 0 | Noise. Drop. |
| Reach, striking defence, takedown accuracy | **−0.8 to −1.3** | Hurt on unseen fights (they overlap with SApM and takedowns). Drop. |

Other findings:
- **A lean 8-stat model is as good or better:** Elo, age, SApM, SLpM, takedown defence, takedowns/15, got-finished rate, layoff (log loss 0.637 vs 0.639 with all 22). Fewer stats means less noise and easier explanations.
- **The model is too timid on recent fights.** Calibration slope on the test set is 1.22 (1.0 is right). When it says 57.5%, the favourite won 63%; at 62.4%, they won 67%. Regularisation isn't the cause (changing `C` does nothing); recent fights are more predictable than the 2005–2015 ones it mostly learned from. A timid model blended with the market drags favourites toward 50% and creates **fake value on underdogs**.
- **Elo is weak** (0.682 alone) because every newcomer starts at 1500 and the data has no pre-UFC record. Split decisions count as full wins.
- **Missing bio data is rare** (2–3% for age, reach, height), so the "missing = equal" fill is fine.
- **Women's fights are more predictable** (log loss 0.606 vs 0.645 for men), consistent with research that favourites in women's fights are under-priced.
- **47.5% of fights go to decision**, so what judges reward (output, control, close rounds) matters as much as finishing ability.

Data in the Greco CSVs that the model doesn't use yet:
- strike targets and positions: `HEAD`, `BODY`, `LEG`, `DISTANCE`, `CLINCH`, `GROUND`
- `REV.` (reversals) and `TOTAL STR.`
- `DETAILS` (judges' scorecards, so split vs unanimous decisions), `REFEREE`
- `STANCE` and `WEIGHT` (loaded but unused)

### What the betting research says
- **UFC odds are close to efficient.** No reliable favourite-longshot bias. Small effects (youth, travel, favourites in women's fights) mostly fade out of sample. ([Miller & Nichols 2026](https://ideas.repec.org/a/spr/jecfin/v50y2026i1d10.1007_s12197-026-09757-x.html))
- **The best academic UFC model matched the bookmakers** and showed some positive returns. It simulates fights from in-fight stats. ([Holmes, McHale & Żychaluk 2023](https://www.sciencedirect.com/science/article/pii/S0169207022000073))
- **News is already priced.** Late replacements won 36.2% against 35.3% implied ([Action Network](https://www.actionnetwork.com/mma/ufc-vegas-74-odds-picks-best-bets-kai-kara-france-amir-albazisaturday-june-3)); missed weight shows no consistent effect ([AgentMMA](https://agentmma.com/mma-lab/ufc-missed-weight-win-rate)).
- **Bet smaller when probabilities are estimates** ([Baker & McHale 2013](https://ideas.repec.org/a/inm/ordeca/v10y2013i3p189-199.html)).
- **Lines move about 2.8 points**, mostly 1–7 days out, and favourites shorten toward the close ([betbetter](https://betbetter.world/studies/line-movement)).
- **Home crowds sway judges slightly:** about +3.6 points per judged round with fans present ([summary of Holmes's thesis](https://agentmma.com/mma-lab/ufc-hometown-judging-bias)).
- **Props may be softer** because they're thinner, but no data shows it.

### Upgrade plan

**Step 0 — before UFC 333 (config only, no code)**
- Set `EXPERT_WEIGHT=0` until bug 1 is fixed.
- Lower `MODEL_WEIGHT` (no backtest supports 0.35) and treat model-driven edges as unproven; prefer edges where one book beats the consensus.
- Log every bet so CLV starts accumulating.

**Step 1 — bug fixes**
- Expert picks: cap the pick share (for example to 0.2–0.8) or turn it into a small log-odds nudge, and skip it for unanimous panels.
- Alerts: make `AlertEngine` use the stored `p_final` and confidence gate from the latest `Prediction`, so the phone and the app agree.
- Live model: train it on the same kind of pre-fight probability it is fed (the blended one), or feed it the stats-only one.
- LLM: if you want the local model, set `LLM_PROVIDER=ollama`, `OLLAMA_URL=http://<gpu guest IP>:11434` (or route through LiteLLM on core), `OLLAMA_MODEL=gemma4:12b`, then run `octagon-edge doctor`.
- Done when: tests cover a unanimous panel, alert/app parity, and `doctor` passes the LLM check.

**Step 2 — backtest against the market (the most important step)**
- Data: the [Ultimate UFC Dataset](https://baselight.app/u/kaggle/dataset/mdabbert_ultimate_ufc_dataset) (ufcstats + BestFightOdds lines). Match fights by date and fighter names (`ingest/names.py`).
- Measure on held-out events: log loss and Brier for model, de-vigged closing market, and blend.
- Fit the outcome on `logit(model)` and `logit(market)` together; the fitted weights become `MODEL_WEIGHT`. If the model's weight is near zero, it has no edge and should only explain, not price.
- Simulate the bet rule (edge threshold, stake) at closing and opening prices, and report ROI with confidence intervals.
- Done when: a `octagon-edge backtest-market` command prints these, and `MODEL_WEIGHT` is set from it.

**Step 3 — stats model upgrades** (keep a change only if it lowers held-out log loss *and* doesn't hurt the market backtest)
- Switch to the lean 8-stat set; drop reach, striking defence, takedown accuracy, KO/sub-win rates and control share.
- Fix the timidity: weight recent fights more in training (time-decay sample weights), or calibrate on the most recent 2 years.
- Candidates to test, in order of expected value:
  1. **Recency-weighted stats** (exponential decay instead of career totals) — career totals hide decline, which age shows is the biggest signal.
  2. **Age curve by weight class** (non-linear age, for example age² or over-35 flag; lighter classes decline earlier).
  3. **Opponent-adjusted stats** (strikes landed vs what that opponent usually absorbs) and **strength of schedule** (average opponent Elo).
  4. **Damage taken**: knockdowns absorbed per 15 min and head strikes absorbed (from `HEAD`).
  5. **Better Elo**: tune K, give split decisions partial credit (from `DETAILS`), carry Elo across weight-class moves.
  6. **Position mix**: share of strikes at distance / clinch / ground.
  7. **Five-round cardio**: output in round 3 vs round 1.
  8. **Stance matchup** (southpaw vs orthodox) and **weight-class move** (up or down since last fight).
- Done when: each candidate has a recorded drop-one result in this table.

**Step 4 — decision rules**
- Anchor on the market: line-shopping edges (best price beats the de-vigged consensus) need no model.
- Treat model-vs-market gaps over about 10 points as a warning, not value: cap or skip them.
- Shrink stakes when the model disagrees strongly with the market (Baker & McHale).
- Use an EV threshold instead of a flat 4 points, so longshots and favourites get comparable bars.
- Set the LLM news nudge to 0, or apply it only when the news is newer than the latest price move.
- Hide parlays or label them entertainment.

**Step 5 — measurement**
- Record "paper CLV" for every alert and flagged pick, even unbet ones, against the de-vigged closing consensus (today [services.py:338](octagon_edge/services.py) compares only placed bets to the same book's price).
- Show CLV by source of edge (line-shopping vs model) on the Bets screen.
- Done when: after ~50 picks you can see which source beats the close.

**Step 6 — timing**
- Alert on opening lines early in fight week, not only on event day; take favourite edges early since favourites tend to shorten.

**Step 7 — low-evidence experiments** (only with Step 2 in place)
- Favourites in women's fights; home crowd in close decision fights; method-of-victory and "goes the distance" props using the finish-rate stats.

### Results (24 Oct 2026, branch `engine-upgrade`)
All steps were implemented and tested (52 tests pass; the frontend type-checks and builds). Historical prices: 5,563 UFC fights from 2014–2026 matched from the [ultimate_ufc_dataset](https://github.com/shortlikeafox/ultimate_ufc_dataset) (Apache-2.0, BestFightOdds lines; one price per fighter, timing not documented). The stats model is always scored out of sample (walk-forward, retrained each year).

**Step 0.** `MODEL_WEIGHT` default is now 0.17 (from the backtest). The expert bug is fixed, so `EXPERT_WEIGHT` can stay at 0.15. If the app's Settings screen saved a `model_weight` override, it still wins over the default: set it to 0.17 there.

**Step 1 — done.**
- Expert pick share is clamped to 25–75% before blending (`engine/edge.py: pick_share_prob`), so a unanimous panel on a 75% favourite changes nothing.
- One blend function (`final_prob`) is used by the analysis, the app's alert poller and the CLI watcher; alerts read the stored blend inputs (weight, gate, experts, LLM, youth) from the latest prediction.
- The live model now adds round evidence on top of the blended prior (`LiveModel.predict_from_prior`). On 2,786 rounds since 2023 with a market prior, log loss after round 1: 0.549 (new) vs 0.558 (old) vs 0.591 (market only); round 2 about equal; round 3 0.423 vs 0.419. The old method shrank the prior to 61% of its strength.
- Ollama default model is `gemma4:12b`; `.env.example` and `doctor` explain that `localhost` inside Docker is the container.

**Step 2 — done.** `octagon-edge backtest-market`:

| On 2021+ fights (2,371) | Log loss | Accuracy |
|---|---|---|
| Market alone (de-vigged) | 0.5936 | 68.0% |
| Old engine: blend at 0.35 | 0.5979 | 69.0% |
| New engine: blend at 0.17 + youth | **0.5927** | 68.8% |

- The stats model alone (0.655 over all years) is far behind the market (0.608). The old 0.35 weight made the engine *worse* than the market.
- Simulated flat bets at the historical price: no rule is significantly profitable. The old setting (weight 0.35, edge ≥ 2%) lost 8.3% (90% CI −15.8% to −0.6%). The new engine at edge ≥ 4% made +11.9% on 69 bets, but the 90% CI is −11.8% to +35.2%: unproven.

**Step 3 — done.** Candidates, each added to a lean 8-stat base (test log loss on 2023+ fights; lower is better; base 0.6374):

| Candidate | Test log loss | Kept? |
|---|---|---|
| Opponent-adjusted strikes landed / absorbed | **0.6308** | Yes |
| Strength of schedule (avg opponent Elo) | **0.6338** | Yes |
| Head strikes absorbed / min | 0.6366 | No (tiny) |
| Recency-weighted strike and takedown rates | 0.6366–0.6369 | No (tiny) |
| Southpaw / switch stance | 0.6369 | No |
| Age over 32 (non-linear age) | 0.6370 | No |
| Knockdowns absorbed / 15 min | 0.6379 | No |
| Distance / ground strike share | 0.6377 | No |
| Weight-class move | 0.6378 | No |
| Elo with split decisions as partial wins, single K | 0.6387 | No |
| Time-decay sample weights (4y, 8y half-life) | 0.6376–0.6379 | No (doesn't fix the timidity either) |

- The lean 8-stat set was as good alone but slightly worse once blended with the market (+0.85 ± 0.52 thousandths), so the final model keeps the original 22 stats and adds the three winners: test log loss 0.6301 (was 0.6386), accuracy 65.9% (was 64.8%).
- Saved models are now `model-v2.joblib` / `live_model-v2.joblib`, so a homelab with the old files retrains instead of crashing on new columns.
- Not tested: five-round cardio (needs round-level history in `History`).
- **On top of the market, no stat change mattered** (all within ±1 thousandth). The market already knows what the stats know — except youth (Step 7).

**Step 4 — done.**
- Each side shows "edge vs market alone" and whether the pick is **line shopping** or **model**.
- Model/market gap over 25 points (`MAX_MODEL_GAP`) → low confidence (1.5× threshold) with a note.
- Stakes shrink with model/market disagreement: ×0.5 at 10 points, ×0.2 at 20 (`disagreement_shrink`).
- LLM news nudge off by default (`LLM_MAX_ADJUST=0`, max 0.03).
- Parlays: stake cap is a quarter of a single bet's; the page says they're entertainment.
- EV threshold instead of flat points: tested, no difference (both noise), so the flat 4 points stays.

**Step 5 — done.** New `paper_pick` table records every pick and value alert once per price, captures the de-vigged closing probability when the fight goes live, settles with the result, and the Bets screen shows CLV, beat-close rate and flat ROI by source.

**Step 6 — done.** Odds API polling: every 3h in the 7 days before the event (`FIGHT_WEEK_ODDS_EVERY`), every 12h before that (was 6h throughout). Pre-fight value alerts on favourites note that the price may not last. About +28 credits per event.

**Step 7 — done.**
- **Youth is the one real signal.** After the market price, each 5 years older costs −0.10 log-odds (90% CI −0.15 to −0.06); with the stats model also included, −0.07, consistent in 2019/2021/2023 holdouts. Added as `YOUTH_ADJ=0.07`. Betting the younger fighter whenever the gap is ≥ 6 years: +2.8% ROI on 1,719 fights (90% CI −0.7% to +6.3%) — close, not proven.
- Favourites in women's fights: −3.0% ROI (no edge).
- Method props at the historical price: "favourite wins by decision" ≈ break-even (−0.6%); every other prop loses 9–48% (heavy margin, worst on submissions and underdogs). No prop edge.
- Home crowd: not testable — the odds data has the event country but not fighters' nationalities.

### Expert backtest (24 Oct 2026, branch `expert-backtest`)
Data:
- Past SplitPicks Kings Tables for 27 finished events (Apr 4 – Oct 17 2026), parsed with the app's own parser: 312 bouts and 4,281 picks with a Kalshi price.
- Kalshi closing prices from the public API: settled markets from `/markets` and, for markets settled before 25 Aug, `/historical/markets`. The price used is the bid/ask mid of the last hourly candle at least 10 minutes before the fight.
- Caveat: each handicapper's weight uses the YTD record printed on the page now, which can carry a little hindsight. That would favour the experts, and they still lose.

| Who picks the winner | Hit rate |
|---|---|
| Kalshi favourite | 74.2% |
| Expert consensus | 67.4% |
| Stats model | 62.6% |
| Experts, on the 45 fights where they disagree with Kalshi | 26.7% |

| Engine with EXPERT_WEIGHT | Log loss (vs none, thousandths, 90% CI by event) |
|---|---|
| 0 | 0.5344 |
| 0.05 | +0.7 (−0.5 to +1.9) |
| 0.15 (old default) | +2.9 (−0.7 to +6.4) |
| 0.30 | +8.0 (+0.8 to +14.9) |
| 0.50 | +18.8 (+7.0 to +30.6) |

- Leave-one-event-out cross-validation picked 0 for every event, so `EXPERT_WEIGHT` is now 0.
- The picks are still fetched and shown (a "Handicappers" row on the Fight page and in the report), labelled as a pick share outside the blend.
- `SPLITPICKS_URL` blank now finds the active card's page from https://splitpicks.com/kings (exact slug, else the main-event surnames); `off` disables it.

**Also found — the stats model against Kalshi closing prices** (same 312 bouts, model out of sample):

| vs Kalshi alone (thousandths of log loss, 90% CI by event) | |
|---|---|
| Market + youth | **−2.9** (−5.3 to −0.5): youth confirmed on new data |
| Market + model at 0.17 | **+7.1** (+1.8 to +12.2): the model hurts |
| Market + model 0.17 + youth (current engine) | +4.5 (−1.6 to +9.8) |
| Market + model at 0.10 | +3.7 (+0.6 to +6.7) |

This agrees with the larger 2021+ backtest, where the 0.17 blend *without* youth was already slightly worse than the market alone (0.5940 vs 0.5936). Kalshi closing prices are sharper than the BestFightOdds lines used there.

**Decision: `MODEL_WEIGHT` = 0** (agreed with the owner, 24 Oct 2026).

| Engine formula (blend, then youth) | BestFightOdds, 2021+ (log loss) | Kalshi 2026, vs weight 0 (thousandths, 90% CI) |
|---|---|---|
| Weight 0 | 0.5923 | — |
| Weight 0.05 | 0.5922 | +1.8 (+0.2 to +3.2) |
| Weight 0.10 | 0.5923 | +3.9 (+0.8 to +6.8) |
| Weight 0.17 | 0.5927 | +7.3 (+2.0 to +12.4) |

- On BestFightOdds lines the weights are tied within 0.0004. Against the sharper Kalshi close, every weight above 0 is measurably worse.
- With the weight at 0, the stats model is still computed and shown, but the model-only rules switch off: the disagreement stake shrink, and the low-confidence flag for big model/market gaps (the note stays).
- Edges are labelled "line shopping" or "youth".
- `backtest-market` now searches the weight with the engine's own formula, tuned before the holdout. It prints the whole grid, and may name 0.05–0.10 there because the BestFightOdds sample can't tell them apart.

**What to do next**
1. Deploy the branch to the homelab after UFC 333, then run `octagon-edge backtest-market` there.
2. Let paper CLV build for a few events (~50 picks) before trusting either source of edge.
3. If youth-sourced picks show negative CLV over ~50 picks while line shopping is positive, set `YOUTH_ADJ` lower.
4. Re-run `backtest-experts` every few months: its Kalshi sample grows with each event.

## Risks and guardrails
- **Free live odds are thin.** Kalshi and Polymarket are the near-live signal. Odds API live calls are rationed, and live pricing on US books will lag.
- Scraping is limited to ufcstats and BestFightOdds, with low request rates, caching and a clear User-Agent. Sportsbook sites are not scraped.
- GPL-3.0 applies if we copy code from Greco1899. Using its CSV data is fine; we keep its code separate, or simply don't reuse it.
- Betting guardrails are built in: a bankroll cap, a daily stop-loss, every bet logged with CLV, and a reminder that the model is unproven until the backtest beats the closing line. Check that sports betting is legal where you are.

## Verification
- **Unit tests (`pytest backend/tests`):**
  - de-vig maths against hand-computed examples
  - Kelly sizing and the caps
  - rule cooldown and deduplication
  - parsers, run against saved HTML/JSON fixtures
- **Backtest notebook:**
  - log loss and Brier score for the model vs the de-vigged closing line
  - simulated ROI at each threshold, on held-out events
- **Phase 0 dry run (Fri night):**
  - run the ingest and the report against UFC 333 and check the odds match those posted on Covers or Yahoo
  - send a test alert to ntfy and Telegram on your phone
- **During UFC 333:**
  - the watcher logs Kalshi prices, Odds API credits used (should stay under 150) and the alerts it sends
  - the Fight Tracker JSON is captured
- **Phase 3:**
  - `docker compose up`, then open the site over the tunnel on Android and Install app
  - lock the phone, fire a test Web Push, and confirm it arrives
  - Lighthouse PWA audit
  - `npx impeccable detect` exits with code 0

## Things you need to provide or do
- An Odds API free key, a Reddit app (apply now), a Telegram bot token (from @BotFather), optionally a YouTube API key, and a Claude API key if you choose Claude.
- On the homelab: Docker, a Cloudflare Tunnel or Tailscale, and optionally a GPU for Ollama.
