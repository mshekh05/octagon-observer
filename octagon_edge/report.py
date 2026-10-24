"""Self-contained, mobile-first HTML fight report."""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape

from .analysis import SHOWN_STATS, FightAnalysis
from .engine.odds_math import decimal_to_american
from .ingest.card import Card

CSS = """
:root{--bg:#121417;--panel:#1a1d21;--line:#2a2e34;--ink:#e8e6e1;--mute:#9a9790;
--a:#6aa6e8;--b:#e0915a;--good:#5fbf8a;--bad:#d9665b;--warn:#d8b45a}
@media (prefers-color-scheme: light){:root:not([data-theme="dark"]){--bg:#f6f4ef;--panel:#fffdf8;
--line:#e2ded5;--ink:#22201c;--mute:#6b675f;--a:#2f6db3;--b:#b8612a;--good:#2e8a57;--bad:#b3443a;--warn:#9a7a20}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 "IBM Plex Sans","Segoe UI",Roboto,sans-serif;-webkit-font-smoothing:antialiased}
main{max-width:860px;margin:0 auto;padding:20px 16px 64px}
h1{font:600 26px/1.2 "IBM Plex Sans Condensed","IBM Plex Sans",sans-serif;margin:8px 0 4px;letter-spacing:-.01em}
h2{font:600 19px/1.25 "IBM Plex Sans Condensed","IBM Plex Sans",sans-serif;margin:0}
h3{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--mute);margin:18px 0 6px;font-weight:600}
.meta{color:var(--mute);font-size:13px}.num{font-variant-numeric:tabular-nums}
section.fight{border-top:1px solid var(--line);padding:22px 0}
.vs{display:flex;justify-content:space-between;gap:12px;align-items:baseline;flex-wrap:wrap}
.a{color:var(--a)}.b{color:var(--b)}.good{color:var(--good)}.bad{color:var(--bad)}.warn{color:var(--warn)}
.bar{display:flex;height:10px;border-radius:5px;overflow:hidden;margin:10px 0 4px;background:var(--line)}
.bar span:first-child{background:var(--a)}.bar span:last-child{background:var(--b)}
table{width:100%;border-collapse:collapse;font-size:14px}td,th{padding:6px 4px;border-bottom:1px solid var(--line);text-align:right}
th{font-weight:500;color:var(--mute);font-size:12px}td:first-child,th:first-child{text-align:left;color:var(--mute)}
.pick{display:inline-block;padding:4px 10px;border-radius:999px;font-weight:600;font-size:13px;
background:color-mix(in srgb,var(--good) 18%,transparent);color:var(--good)}
.nopick{color:var(--mute);font-size:13px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:14px 0}
ul{margin:6px 0;padding-left:18px}li{margin:3px 0}a{color:inherit}
.scroll{overflow-x:auto}.seg{font-size:12px;color:var(--mute);text-transform:uppercase;letter-spacing:.06em}
"""


def pct(p: float | None) -> str:
    return "–" if p is None else f"{p * 100:.1f}%"


def american(dec: float | None) -> str:
    if not dec:
        return "–"
    a = decimal_to_american(dec)
    return f"+{a}" if a > 0 else str(a)


def _fmt(v, kind) -> str:
    if v is None or v != v:
        return "–"
    if kind == "%":
        return f"{v * 100:.0f}%"
    return f"{v:.{kind}f}" if kind else f"{v:,.0f}"


def _fight(fa: FightAnalysis, bankroll: float) -> str:
    f = fa.fight
    pa = fa.p_final if fa.p_final is not None else 0.5
    h = [f'<section class="fight" id="{escape(f.key)}">',
         f'<div class="seg">{escape(f.segment)} · {escape(f.weightclass)}'
         f'{" · title" if f.title else ""} · {f.rounds} rds</div>',
         f'<div class="vs"><h2><span class="a">{escape(f.a)}</span> <span class="meta">vs</span> '
         f'<span class="b">{escape(f.b)}</span></h2>'
         f'<span class="meta num">{escape(fa.record_a)} · {escape(fa.record_b)}</span></div>',
         f'<div class="bar"><span style="width:{pa * 100:.1f}%"></span><span style="width:{(1 - pa) * 100:.1f}%"></span></div>']

    if fa.pick is not None:
        sv = fa.sides[fa.pick]
        h.append(f'<p><span class="pick">Value: {escape(fa.name(fa.pick))} {american(sv.decimal)} @ {escape(sv.book or "")}'
                 f' · edge {sv.edge * 100:+.1f} pts · stake {sv.stake:,.0f} ({sv.stake_pct * 100:.1f}% of {bankroll:,.0f})</span></p>')
    else:
        h.append('<p class="nopick">No bet: no price beats the blended probability by the edge threshold'
                 f'{" (or no live sportsbook odds yet)" if not any(k != "Reference" for k in fa.books) else ""}.</p>')

    rows = [("Stats model", fa.p_model), (f"Market — {fa.market_source}", fa.p_market)]
    if fa.p_experts is not None:
        rows.append((f"Handicappers — {fa.experts_source}", fa.p_experts))
    rows.append(("Blended (used for edge)", fa.p_final))
    h.append('<div class="scroll"><table class="num"><tr><th></th><th class="a">' + escape(f.a.split()[-1])
             + '</th><th class="b">' + escape(f.b.split()[-1]) + '</th></tr>')
    for label, p in rows:
        h.append(f"<tr><td>{escape(label)}</td><td>{pct(p)}</td><td>{pct(None if p is None else 1 - p)}</td></tr>")
    a, b = fa.sides
    h.append(f"<tr><td>Best price</td><td>{american(a.decimal)} <span class='meta'>{escape(a.book or '')}</span></td>"
             f"<td>{american(b.decimal)} <span class='meta'>{escape(b.book or '')}</span></td></tr>")
    cls = lambda e: "good" if e is not None and e > 0 else "bad" if e is not None else ""  # noqa: E731
    fmt = lambda e: "–" if e is None else f"{e * 100:+.1f} pts"  # noqa: E731
    h.append(f"<tr><td>Edge vs best price</td><td class='{cls(a.edge)}'>{fmt(a.edge)}</td>"
             f"<td class='{cls(b.edge)}'>{fmt(b.edge)}</td></tr>")
    for mp in fa.prediction_markets:
        h.append(f"<tr><td>{escape(mp.source)}</td><td>{pct(mp.p_a)}</td><td>{pct(1 - mp.p_a)}</td></tr>")
    h.append("</table></div>")

    if len([k for k in fa.books if k != "Reference"]) > 1:
        h.append('<h3>Sportsbooks</h3><div class="scroll"><table class="num"><tr><th>Book</th>'
                 f'<th class="a">{escape(f.a.split()[-1])}</th><th class="b">{escape(f.b.split()[-1])}</th></tr>')
        for book, (da, db) in sorted(fa.books.items()):
            h.append(f"<tr><td>{escape(book)}</td><td>{american(da)}</td><td>{american(db)}</td></tr>")
        h.append("</table></div>")

    h.append('<h3>Tale of the tape (UFC only)</h3><div class="scroll"><table class="num">'
             f'<tr><th></th><th class="a">{escape(f.a.split()[-1])}</th><th class="b">{escape(f.b.split()[-1])}</th></tr>')
    for key, label, kind in SHOWN_STATS:
        h.append(f"<tr><td>{label}</td><td>{_fmt(fa.snap_a.get(key), kind)}</td><td>{_fmt(fa.snap_b.get(key), kind)}</td></tr>")
    h.append("</table></div>")

    if fa.llm:
        h.append('<div class="panel"><h3 style="margin-top:0">Analyst breakdown</h3>'
                 f"<p>{escape(fa.llm.get('summary', ''))}</p>")
        if fa.llm.get("key_factors"):
            h.append("<ul>" + "".join(f"<li>{escape(x)}</li>" for x in fa.llm["key_factors"]) + "</ul>")
        if fa.llm.get("risks"):
            h.append('<h3>Risks</h3><ul>' + "".join(f"<li>{escape(x)}</li>" for x in fa.llm["risks"]) + "</ul>")
        adj = fa.llm.get("news_adjustment_a") or 0
        if adj:
            h.append(f"<p class='warn'>News adjustment for {escape(f.a)}: {adj * 100:+.1f} pts — "
                     f"{escape(fa.llm.get('adjustment_reason', ''))}</p>")
        h.append("</div>")

    if fa.notes:
        h.append("<h3>Caveats</h3><ul>" + "".join(f"<li>{escape(n)}</li>" for n in fa.notes) + "</ul>")

    news = [(f.a, n) for n in fa.news_a] + [(f.b, n) for n in fa.news_b]
    flagged = [(who, n) for who, n in news if n.flags]
    if news:
        h.append(f'<h3>News & chatter</h3><p class="meta">{fa.tone_a.get("mentions", 0)} items on {escape(f.a)}, '
                 f'{fa.tone_b.get("mentions", 0)} on {escape(f.b)}. Fan tone (lexicon, rough): '
                 f'{fa.tone_a.get("score", 0):+.2f} / {fa.tone_b.get("score", 0):+.2f}</p>')
        show = flagged[:6] or news[:4]
        h.append("<ul>" + "".join(
            f'<li>{"<span class=warn>[" + escape(", ".join(n.flags)) + "]</span> " if n.flags else ""}'
            f'<a href="{escape(n.link)}">{escape(n.title[:140])}</a> <span class="meta">{escape(n.source)}</span></li>'
            for _, n in show) + "</ul>")
    h.append("</section>")
    return "\n".join(h)


def render(card: Card, analyses: list[FightAnalysis], bankroll: float, backtest_summary: str,
           sources_status: dict[str, str]) -> str:
    picks = [fa for fa in analyses if fa.pick is not None]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out = ["<!doctype html><html lang='en'><head><meta charset='utf-8'>",
           "<meta name='viewport' content='width=device-width,initial-scale=1'>",
           f"<title>{escape(card.event)}</title>",
           "<link rel='preconnect' href='https://fonts.googleapis.com'>",
           "<link href='https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Sans+Condensed:wght@600&display=swap' rel='stylesheet'>",
           f"<style>{CSS}</style></head><body><main>",
           f"<div class='meta'>{escape(card.date)} · {escape(card.venue)}</div>",
           f"<h1>{escape(card.event)}</h1>",
           f"<p class='meta'>Generated {now}. {escape(card.source_note)}</p>"]
    out.append("<div class='panel'><h3 style='margin-top:0'>Value spots</h3>")
    if picks:
        out.append("<ul>" + "".join(
            f"<li><a href='#{escape(fa.fight.key)}'>{escape(fa.name(fa.pick))}</a> "
            f"{american(fa.sides[fa.pick].decimal)} ({escape(fa.sides[fa.pick].book or '')}) — edge "
            f"<span class='good num'>{fa.sides[fa.pick].edge * 100:+.1f} pts</span>, stake "
            f"{fa.sides[fa.pick].stake:,.0f}{' · low confidence' if fa.confidence == 'low' else ''}</li>"
            for fa in picks) + "</ul>")
    else:
        out.append("<p class='nopick'>None yet. Edges are only flagged against live sportsbook prices "
                   "(set ODDS_API_KEY), never against press reference odds.</p>")
    out.append("<h3>Data sources this run</h3><ul>" + "".join(
        f"<li>{escape(k)}: <span class='{'good' if v.startswith('ok') else 'warn'}'>{escape(v)}</span></li>"
        for k, v in sources_status.items()) + "</ul></div>")
    out += [_fight(fa, bankroll) for fa in analyses]
    out.append("<section class='fight'><h3>How to read this</h3><ul>"
               "<li><b>Market</b> is the bookmaker price with the margin removed (power method), median across US books.</li>"
               "<li><b>Stats model</b> is a logistic regression on UFC-only career stats, Elo, age, reach and layoff, "
               "trained on fights since 2005.</li>"
               "<li><b>Blended</b> mixes the two in log-odds space, leaning on the market, because the market is sharper than the model.</li>"
               "<li>Stakes are quarter-Kelly, capped per bet. Treat them as an upper bound, not advice.</li></ul>"
               f"<pre class='meta' style='white-space:pre-wrap'>Model backtest (fights since 2023, unseen in training):\n{escape(backtest_summary)}\n"
               "Markets typically score log loss ≈ 0.60 on UFC fights, so the model alone does not beat the market.</pre>"
               "</section></main></body></html>")
    return "\n".join(out)
