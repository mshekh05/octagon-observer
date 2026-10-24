# Hand-off: getting Octagon Edge running on the homelab

You are a Claude Code session running on the owner's homelab via Remote Control. The code was built and tested in a cloud sandbox that could not reach the odds, prediction-market, news or alert services. Your job is to make it work for real here, before **UFC 333 on Sat 24 Oct 2026 (early prelims 10:00 ET)**.

Read `README.md` (sections "Quick start", "Saturday runbook" and "The app on your homelab") and `PLAN.md` for background. Then work through these steps in order and report back to the owner after each one.

## 1. Prerequisites
- Check `python3 --version` (need 3.11+). If it's missing, tell the owner how to install it; don't install system packages without asking.
- Check `docker compose version`. It is only needed for step 6.
- Check `tmux` is installed. It is needed for step 5.

## 2. Install and test
```bash
./scripts/setup.sh --no-send
```
- All tests must pass (31 at hand-off). If any fail here but passed in the sandbox, investigate. Likely causes are a different Python version or the platform.
- If `.env` was just created, ask the owner for these values and write them into `.env`. Never print the secrets back in chat.
  - `ODDS_API_KEY` (free at the-odds-api.com)
  - `NTFY_TOPIC` (they subscribe to it in the ntfy Android app), and/or `TELEGRAM_BOT_TOKEN` plus `TELEGRAM_CHAT_ID`
  - `BANKROLL`

## 3. Health check with real network access
```bash
source .venv/bin/activate
octagon-edge doctor
```
These have **never been verified against the real services**. Expect to fix code here:
- **The Odds API.** The response shape is assumed in `octagon_edge/ingest/odds_api.py` (`parse`). If fights don't match, compare the API's fighter names with `octagon_edge/data/cards/ufc-333.json` and `ingest/names.py`.
- **Kalshi.** The series ticker `KXUFCFIGHT` is a guess. `fetch_kalshi` falls back to searching open events for "UFC" and caches what it finds. If it still matches 0 fights, fetch one real market and fix `parse_kalshi` (in `ingest/prediction_markets.py`) to match.
- **Polymarket.** The gamma `tag_slug=ufc` filter is a guess. Fix `fetch_polymarket` or `parse_polymarket` the same way if needed.
- **RSS feeds and Reddit.** The feed URLs live in `ingest/news.py`. Warnings here are non-blocking.
- **Alerts.** Ask the owner to confirm the doctor's test alert actually arrived on their phone.

Rules for any fix:
- Add a test with a saved real response as a fixture under `tests/fixtures/`.
- Keep `pytest -q` green.
- Commit with a clear message.

The goal is PASS for the Odds API, at least one prediction market and at least one alert channel.

## 4. Report
```bash
octagon-edge report
```
- Check that `reports/ufc-333.html` shows real sportsbook prices for most fights.
- Sanity-check two or three fights against public odds (e.g. covers.com).
- If the card changed (late replacements), update `octagon_edge/data/cards/ufc-333.json`.

## 5. Event day
At about 09:30 ET on Saturday, start the watcher in tmux so it survives the terminal closing:
```bash
tmux new -d -s ufc 'cd ~/octagon-edge && . .venv/bin/activate && octagon-edge watch --targets examples/targets.json 2>&1 | tee -a data/watch.log'
```
- Confirm the "Watching UFC 333" alert reached the phone.
- Check `data/watch.log` occasionally for errors.
- Odds API spend must stay at or under 150 credits.

## 6. Optional: the full app (can wait until after Saturday)
`docker compose up -d`, then follow the README section "The app on your homelab".
- Set `APP_TOKEN` (`openssl rand -hex 24`), `VAPID_SUBJECT` and `POSTGRES_PASSWORD` in `.env`.
- Expose port 8000 with Tailscale (`tailscale serve --bg 8000`) or a Cloudflare Tunnel, as the owner prefers.
- Ask before changing any network or firewall setting.
- The Dockerfile has **never been built**, because the sandbox had no Docker daemon. Expect to fix build issues.

## Ground rules
- Don't place bets or touch sportsbook accounts. This tool only analyses prices and sends alerts.
- Don't commit `.env` or anything in `data/`.
- Push to GitHub only if the owner gives you a repo.
