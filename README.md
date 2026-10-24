# Octagon Edge

A UFC betting analyser you host yourself. It runs in two ways:
- **The app** (Phases 1–5): an Android-installable PWA with live round scoring and push alerts, backed by an API, a background worker and Postgres. See "The app on your homelab" below.
- **The command-line tool** (Phase 0): `octagon-edge report`, `watch` and `doctor`, enough on its own for an event night. See "Quick start".

For each fight it compares:
- the **market's** true win probability: US sportsbook odds with the margin removed
- a **stats model** trained on every UFC fight since 2005
- once the fight starts, a **live model** that updates after every round you score

It flags a **value bet** when a sportsbook price beats the blended probability, and alerts your phone.

## The app on your homelab

```bash
cp .env.example .env    # set APP_TOKEN, ODDS_API_KEY, VAPID_SUBJECT, PUBLIC_URL (+ optional keys)
docker compose up -d    # db + api (port 8000) + worker
docker compose run --rm cli doctor
```

1. **Expose it over HTTPS.** Push alerts and installing the app both need HTTPS. Either:
   - Cloudflare Tunnel: `cloudflared tunnel --url http://localhost:8000`, or a named tunnel on your domain
   - Tailscale: `tailscale serve --bg 8000`, which gives a `https://<machine>.<tailnet>.ts.net` address on your devices only

   Put that address in `PUBLIC_URL`.
2. **Install it on your phone.**
   - Open the address in Chrome on Android, then menu → **Install app**.
   - In the app, open **Settings**, paste `APP_TOKEN`, then tap **Turn on push alerts** and **Send a test alert**.
   - Lock the phone and check the test alert arrives.
3. **Set your books.** In **Settings → Your sportsbooks**, set your main book (default Fanatics) and tick the feed books you can bet at. Picks and "best price" only use those.
   - **Fanatics isn't in The Odds API feed.** Every fight shows the worst price still worth taking there ("bet ≥ +127"). On the Fight page, **Check a Fanatics price** tells you at once whether a price clears the bar, and what to stake.
4. **Reading a fight:**
   - **Card:** each fighter's best price at your books, our fair price and the "bet at" price. Picks say where the edge comes from (line shopping or youth). Fights without a pick show how close they are.
   - **Fight page:** the verdict first, then how we priced it (market → youth → our price), prices by book, props, price alerts, live scoring, and the tale of the tape (▲ marks the better number).
   - **Props:** Kalshi "goes the distance" and method of victory as fair prices (no margin), plus total-rounds line shopping across sportsbooks, flagged when one book beats the others' consensus by the edge threshold. Total rounds cost an extra Odds API credit per fetch, so on event day they're fetched every 30 minutes (`TOTALS_EVERY`).
   - **Price alerts:** set "alert me at +150" on a fighter from the Fight page; a push arrives when a book in the feed reaches it.
   - **Parlays** (link at the bottom of the Card) are built only from the single-bet picks.
5. **On fight night:**
   - The worker polls Kalshi and Polymarket every 20 seconds and the Odds API every 4 minutes. Before the event day it checks the Odds API every 3 hours in fight week (most line movement happens then) and every 12 hours before that.
   - Open **Live**, pick the fight on screen, and tap the round box to score each round. A knockdown counts for more.
   - The live probability updates straight away, and alerts arrive as push notifications.
6. **After a bet:**
   - Log it from the fight page (the book defaults to your main book).
   - The closing price is captured when the fight starts, and the bet settles when you tap the winner.
   - **Bets** shows profit/loss, ROI and closing-line value (CLV).
   - It also scores every pick and value alert the engine made, bet or not, against the de-vigged closing price ("paper CLV"), split by line shopping vs model.

Want to see the app without real data? Seed a demo database. Every price in it is simulated and labelled as such:

```bash
DATABASE_URL=sqlite:///data/demo.db python scripts/seed_demo.py
DATABASE_URL=sqlite:///data/demo.db uvicorn octagon_edge.api:app   # after: cd frontend && npm ci && npm run build
```

**The live model.**
- It is trained on about 16,000 historical rounds.
- On fights since 2023 it never saw, its log loss after round 2 is 0.474, against 0.646 for the pre-fight probability alone.
- Historical round "winners" come from the stats: strikes, knockdowns, takedowns and control time.

**Sentiment.**
- Sources: Reddit, Bluesky, YouTube comments and the RSS feeds.
- Scoring uses a small word list, or the cardiffnlp RoBERTa model if you `pip install -e '.[ml]'`.
- It is shown as a hype gauge and a source of red flags. It never feeds the probability.

**Design.**
- `PRODUCT.md` and `DESIGN.md` record the design ("Judge's scorecard").
- The impeccable and taste-skill design skills are copied into `.claude/skills/`.
- CI runs `impeccable detect`.

## Quick start (homelab or laptop, Python 3.11+)

```bash
pip install -e '.[dev]'
cp .env.example .env              # fill in what you have; blanks are skipped
octagon-edge test-alert           # check your phone receives a notification
octagon-edge report               # writes reports/ufc-333.html
octagon-edge watch --targets examples/targets.json   # leave running during the event
```

Or with Docker:

```bash
docker compose run --rm report
docker compose up -d watch
```

## Keys and settings (all free)

| Setting | Where to get it | Without it |
|---|---|---|
| `ODDS_API_KEY` | the-odds-api.com, free plan (500 credits a month) | No sportsbook prices, so no value picks. You still get the model, Kalshi/Polymarket and reference odds. |
| `NTFY_TOPIC` | Install the ntfy Android app and subscribe to a hard-to-guess topic name | Use Telegram, or alerts only print to the console |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Create a bot with @BotFather, message it once, then read `chat.id` from `https://api.telegram.org/bot<TOKEN>/getUpdates` | Use ntfy |
| `LLM_PROVIDER` | `ollama` (local; set `OLLAMA_URL` and `OLLAMA_MODEL`) or `claude` (needs `ANTHROPIC_API_KEY`). In Docker, `localhost` is the container: point `OLLAMA_URL` at the Ollama machine's address. | No written breakdown |

**Odds API budget.**
- A call costs 1 credit per region and returns every MMA fight at once.
- `watch` polls every 4 minutes. A full card (about 7 hours from early prelims to main event) uses about 100 credits.
- `ODDS_API_BUDGET` sets a hard cap on spending (default 150). The tool records what it has spent in `data/odds_api_budget.json`.
- Before the next event, delete that file or raise the cap.

## How a pick is made
1. **Market probability.** Each US book's moneyline is de-vigged with the power method, and the median across books is taken.
2. **Model probability.** A logistic regression on A-minus-B differences in each fighter's UFC-only stats before the fight:
   - Elo
   - strikes landed and absorbed per minute
   - striking accuracy and defence
   - takedown rate, accuracy and defence
   - submissions, knockdowns and control time
   - finish rates, age, reach and layoff
   - opponent-adjusted striking (landed and absorbed against what each opponent usually allows and lands) and strength of schedule (average opponent Elo)
3. **Blend.** The price comes from the market (`MODEL_WEIGHT=0`). The stats model is shown on the Fight page to explain the fight, but in both backtests blending it in made the market worse, even at 5%. Any `MODEL_WEIGHT` above 0 mixes it back in, in log-odds space.
   - The model's weight is halved when a fighter has no UFC history.
   - A small shift toward the younger fighter (`YOUTH_ADJ`), because bookmakers under-price youth.
   - Expert picks from the [SplitPicks Kings Table](https://splitpicks.com/kings) are found automatically for the active card and shown on the Fight page, but they don't move the probability (`EXPERT_WEIGHT=0`). On 312 fights from Apr–Oct 2026, the Kalshi favourite won 74.2% and the expert consensus picked 67.4%; when they disagreed, the experts were right 26.7% of the time, and any weight made the engine worse (`octagon-edge backtest-experts`).
   - The LLM news nudge is off by default (`LLM_MAX_ADJUST=0`): short-notice and missed-weight news is already in the price.
4. **Value.**
   - Before the fight: an edge of at least 4 points (`EDGE_PREFIGHT`) over the best US price. The threshold is 1.5× higher for low-confidence fights, including when the model and market disagree by more than 25 points (`MAX_MODEL_GAP`).
   - Each pick says where the edge comes from: **line shopping** (the price beats the market consensus on its own) or **youth** (**model** if `MODEL_WEIGHT` > 0).
   - During the fight: the book price lags the Kalshi/Polymarket price by at least 6 points (`EDGE_LIVE`).
5. **Stake.** A quarter of the Kelly-formula stake, capped at 3% of bankroll (`KELLY_FRACTION`, `MAX_STAKE_PCT`), and shrunk further the more the model and market disagree (half at a 10-point gap).

**Honest numbers.**
- On 1,587 fights since 2023 that it never saw during training, the stats model scores log loss 0.630 and picks the winner 65.9% of the time.
- Against 5,563 fights with a historical price (2014–2026), the de-vigged market scores 0.608 and the stats model 0.655: the model alone does **not** beat the market. On 2021+ fights the market plus the youth shift scores 0.5923, against 0.5936 for the market alone; adding the model at 5–15% changes that by less than 0.0003.
- No bet rule showed a statistically significant profit in the simulation at historical prices. Treat flagged edges as "worth a closer look", not as guaranteed value, and watch the paper CLV on the **Bets** screen.
- Against Kalshi closing prices on 312 fights from 2026, the youth shift helped again (−2.9 thousandths of log loss, 90% CI −5.3 to −0.5) but the stats model hurt at every weight tried: +1.8 at 5% (90% CI +0.2 to +3.2), +7.3 at 17%. That's why `MODEL_WEIGHT` is 0.
- With the model out of the price, a pick's edge comes from **line shopping** (one book off the consensus) or the **youth** shift.
- Run `octagon-edge backtest`, `octagon-edge backtest-market` and `octagon-edge backtest-experts` to reproduce these numbers. The second downloads historical moneylines from the [ultimate_ufc_dataset](https://github.com/shortlikeafox/ultimate_ufc_dataset) (Apache-2.0).

## Alerts sent by `watch`
- `VALUE`: a pre-fight price beats the blended probability.
- `LIVE VALUE`: a sportsbook's live price lags the prediction market.
- `SWING`: the prediction market moves 8 or more points within 5 minutes (a knockdown, a big round, or similar).
- `LINE MOVE`: the market's fair probability has moved 5 or more points since the watch started.
- `TARGET HIT`: a price you set in `--targets` is reached.

Each alert has a 10-minute cooldown. It repeats sooner only if the edge grows by 2 or more points. Every price snapshot and alert is logged to `data/snapshots.db`, for backtesting later.

## Data sources and limits
- **Stats:** the Greco1899/scrape_ufc_stats CSVs, refreshed daily from ufcstats.com. Only the CSV data is used, no GPL code. They cover UFC fights only, so prospects from other promotions show little or no history.
- **Card:** `octagon_edge/data/cards/ufc-333.json`, taken from press previews on 23 Oct. Update it if bouts change. The press reference odds there are only used as a fallback and never produce a pick.
- **Kalshi and Polymarket:** public read-only endpoints. Their UFC market layout isn't documented, so parsing is defensive. **Check the "Data sources" panel** in the report for matched fights.
- **News:** MMA RSS feeds and Reddit's public JSON (rate-limited). X/Twitter has no free read access and isn't used. Fan "tone" comes from a crude word list; read it as colour, not signal.

## Saturday runbook (times ET)
1. **Before 9:00.**
   - Run `octagon-edge test-alert` and `octagon-edge report`.
   - Open `reports/ufc-333.html` on your phone.
   - Check the Data sources panel for any failed source.
2. **9:30.** Start `octagon-edge watch --targets your-targets.json` (or `docker compose up -d watch`).
3. **During the card.**
   - In Chrome on a laptop, open the UFC.com fight tracker with DevTools → Network.
   - Save any `…/fight/live/*.json` response (right-click → Save all as HAR). This tells us if live in-fight stats are possible for Phase 4.
4. **After the event.** Keep `data/snapshots.db`. It's the first real line-movement data for backtesting.

Bet only what you can afford to lose, and check that sports betting is legal where you are.
