# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack
delegated: Vite + React + TypeScript installable PWA (vite-plugin-pwa), served by the project's FastAPI backend. Chosen because the backend is Python and the only client is an Android phone, which installs PWAs and receives Web Push without an app store.

## Users
One person: the owner, betting on UFC events with US sportsbooks. During a card they watch on TV, usually late at night in a dark room, holding the phone in one hand and glancing at it between rounds. Alerts buzz the phone when something changes.

## Product Purpose
Octagon Edge tells the owner when a UFC bet is worth placing and how much to stake. It compares a de-vigged market probability with a stats model and live round-by-round state, and alerts when a sportsbook price beats that estimate. Success is fewer, better bets: positive closing-line value over time, never forced action.

## Positioning
A personal, self-hosted tool that shows its working. Every number traces back to its source (which books, which model, which rounds), and the tool says plainly when it has no edge or no data.

## Operating Context
- Before the card: read the fight list, open individual fights for the tale of the tape, market vs model, news flags and the written breakdown.
- During the card: a push alert arrives, the owner opens the fight, checks the live probability against the price, and decides within seconds. Between rounds they tap who won the round.
- After a bet: log it (book, price, stake); the app records the closing price and later the result.
- Runs on the owner's homelab behind a tunnel; data sources are free tiers and can be missing or stale.

## Capabilities and Constraints
- Event card with model, market and blended probability per fight; value picks with quarter-Kelly stakes.
- Fight detail: tale of the tape (UFC-only stats), line movement across sportsbooks and prediction markets, sentiment and news flags, optional LLM breakdown.
- Live mode: manual round input, live win probability, alert feed (server-sent events).
- Bet log with CLV, P&L and ROI.
- Settings: app token, bankroll and thresholds, push notifications.
- Constraints: single user, no accounts; odds data is rationed (Odds API free tier) and may be absent; the stats model is weaker than the market and must be presented as such.

## Evidence on Hand
- Backtest numbers produced by the code (`octagon-edge backtest`): stats model log loss 0.639 / 64.8% accuracy on 1,587 unseen fights; markets typically ~0.60. Live round model log loss 0.474 after round 2 vs 0.646 pre-fight only.
- No user testimonials, track record or profit history exist. Do not fabricate win rates, ROI or "proven" claims.

## Product Principles
- No edge, no bet: an empty state that says "nothing worth betting" is a correct answer, not a failure.
- Show the source and the uncertainty next to every probability.
- One-glance readability at arm's length in the dark; the decision-critical number is always the largest thing on screen.
- Guardrails over excitement: stakes are capped and framed as upper bounds.

## Accessibility & Inclusion
Used one-handed in low light: large touch targets at the bottom of the screen, high contrast without glare, no reliance on colour alone for favourite/underdog or positive/negative edge.
