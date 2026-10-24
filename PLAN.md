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
