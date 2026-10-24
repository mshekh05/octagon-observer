# Design: Judge's scorecard

The app is a working tool (impeccable "Operate" mode), used one-handed in a dark room between rounds. Its visual world is the judge's 10-point-must scorecard: a ruled sheet with round boxes, filled in by hand, read at a glance.

## Tokens
| Token | Dark (default) | Light | Job |
|---|---|---|---|
| `--ground` | `#151311` warm off-black | `#f4f1ea` card stock | page |
| `--sheet` | `#1d1a17` | `#fbf9f4` | the scorecard sheet (one level only, never nested) |
| `--rule` | `#35302a` | `#d9d3c7` | ruled lines, dividers |
| `--ink` | `#efeae0` chalk | `#1f1b16` | figures, names |
| `--ink-2` | `#b3aa9c` | `#5e564b` | labels, secondary |
| `--red` | `#e0483a` referee red | `#c2321f` | value bets, alerts, live dot, primary action. Nothing else. |
| `--good` / `--bad` | `#6fbf8e` / `#e2796b` | `#24804c` / `#b23a2a` | positive / negative edge, always with a sign (+/−) |
| `--a` / `--b` | ink / ink-2 hatched | | fighter A vs B in charts: drawn as solid vs dashed lines, never colour alone |

Type: IBM Plex Sans (body, 400/500/600) and IBM Plex Sans Condensed (names, figures 600). Self-hosted via @fontsource, so it works offline. All numbers use `font-variant-numeric: tabular-nums`. Scale is fixed rem with a 1.2 ratio: 0.8125 / 0.9375 / 1.125 / 1.35 / 1.62 / 2.5 rem. The decision number on each screen gets the 2.5 rem step.

## Signature move: the round strip
Every fight carries a strip of round boxes (R1…R3 or R1…R5), ruled like the scorecard.
- **Before the fight:** empty boxes.
- **Live:** tap a box to score it; the winner's initial fills the box.
- **Knockdowns:** marked with a small "KD" tick beneath the box.
- **On the card list:** the strip shows how far a live fight has gone.
On the Live screen the strip is the input control itself, sized for thumbs (min 56px boxes).

## Layout
- **Navigation:** a bottom tab bar (Card, Live, Bets, Settings), 64px high plus the safe-area inset.
- **Card list:** one ruled sheet per segment (Main, Prelims, Early), each fight a row group. No cards inside cards.
- **Value picks:** shown in red text with a "VALUE" label and a + sign, not just colour.
- **Motion:** 150–200 ms state transitions only. The live probability figure cross-fades when it changes.

## Refusals
No gradient text, no glassmorphism, no eyebrow kicker labels, no coloured left borders on rows, no emoji as icons (icons are inline SVG, 1.75px stroke).
