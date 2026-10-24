import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api, post, type Books, type FightDetail, type LiveState, type Props, type Series, type SettingsData,
  type SocialSide } from "../lib/api";
import { american, decimalFrom, orBetter, pct, pts, surname } from "../lib/format";
import { FighterPhoto, usePhoto } from "../components/FighterPhoto";
import { ProbChart } from "../components/ProbChart";
import { RoundStrip } from "../components/RoundStrip";
import { ErrorNote, Loading } from "./common";

// [key, label, digits or "pct", which side is better: 1 = higher, -1 = lower, 0 = neither]
type TapeRow = [string, string, "pct" | number, 1 | -1 | 0];
const TAPE_KEY: TapeRow[] = [
  ["age", "Age", 1, -1], ["slpm_adj", "Landed vs opponents' norm / min", 2, 1],
  ["sapm_adj", "Absorbed vs opponents' norm / min", 2, -1], ["td15", "Takedowns / 15 min", 2, 1],
  ["td_def", "Takedown defence", "pct", 1], ["sos", "Avg opponent Elo", 0, 1], ["elo", "Elo rating", 0, 1],
];
const TAPE_MORE: TapeRow[] = [
  ["n_fights", "UFC fights", 0, 0], ["slpm", "Strikes landed / min", 2, 1], ["sapm", "Strikes absorbed / min", 2, -1],
  ["str_acc", "Striking accuracy", "pct", 1], ["str_def", "Striking defence", "pct", 1],
  ["sub15", "Sub attempts / 15 min", 2, 1], ["ctrl_pct", "Control time", "pct", 1], ["kd15", "Knockdowns / 15 min", 2, 1],
  ["reach_in", "Reach (in)", 0, 1], ["layoff_days", "Days since last fight", 0, 0],
];

const fmt = (v: number | null | undefined, k: "pct" | number) =>
  v == null ? "–" : k === "pct" ? `${Math.round(v * 100)}%` : v.toFixed(k);

const SOURCE_TEXT: Record<string, string> = {
  "line shopping": "Line shopping: this book's price beats the other books' consensus on its own.",
  youth: "Youth: the younger fighter is underpriced by the market.",
  model: "Model: the stats model sees value the market doesn't.",
};

function PhotoCredits({ names }: { names: string[] }) {
  const a = usePhoto(names[0]), b = usePhoto(names[1]);
  const credits = [a.data, b.data].filter((p) => p?.url && p.credit);
  if (!credits.length) return null;
  return (
    <p className="meta" style={{ textAlign: "center", margin: "4px 0 0" }}>
      Photos: {credits.map((p, i) => <span key={p!.name}>{i ? " · " : ""}<a href={p!.credit!} target="_blank" rel="noreferrer">Wikimedia Commons</a></span>)}
    </p>
  );
}

function TapeTable({ f, rows }: { f: FightDetail; rows: TapeRow[] }) {
  const a = f.details.snap_a, b = f.details.snap_b;
  return (
    <table className="fig">
      <thead><tr><th></th><th>{surname(f.a)}</th><th>{surname(f.b)}</th></tr></thead>
      <tbody>{rows.map(([k, label, kind, dir]) => {
        const va = a?.[k], vb = b?.[k];
        const adv = dir && va != null && vb != null && va !== vb ? ((va > vb) === (dir > 0) ? 0 : 1) : null;
        return (
          <tr key={k}><td>{label}</td>
            <td className={adv === 0 ? "adv" : ""}>{fmt(va, kind)}</td>
            <td className={adv === 1 ? "adv" : ""}>{fmt(vb, kind)}</td></tr>
        );
      })}</tbody>
    </table>
  );
}

/** Type the price your book offers; see straight away whether it clears the bar and what to stake. */
function PriceCheck({ f, home, tuning }: { f: FightDetail; home: string; tuning?: SettingsData["tuning"] }) {
  const [side, setSide] = useState<0 | 1>(f.pick?.side ?? 0);
  const [text, setText] = useState("");
  const p = f.p_final == null ? null : side === 0 ? f.p_final : 1 - f.p_final;
  const dec = decimalFrom(text);
  const threshold = f.pricing?.threshold ?? 0.04;
  let result = null;
  if (p != null && dec) {
    const edge = p - 1 / dec;
    const kelly = Math.max(((dec - 1) * p - (1 - p)) / (dec - 1), 0);
    const stake = tuning ? Math.min(kelly * tuning.kelly_fraction, tuning.max_stake_pct) * tuning.bankroll : null;
    result = edge >= threshold
      ? <p className="value fig" style={{ margin: 0 }}>Worth it: edge {pts(edge)}, EV {pct(p * dec - 1, 1)}{stake != null ? ` · stake up to ${Math.round(stake)}` : ""}</p>
      : <p className="meta fig" style={{ margin: 0 }}>Not enough: edge {pts(edge)}, needs {pts(threshold)}. Worth it at {orBetter(f.pricing?.bet_at_dec[side])}.</p>;
  }
  return (
    <div className="pad stack">
      <div className="row" role="radiogroup" aria-label="Fighter">
        {[f.a, f.b].map((n, i) => (
          <button key={n} type="button" role="radio" aria-checked={side === i} className={`btn${side === i ? " sel" : ""}`}
            onClick={() => setSide(i as 0 | 1)}>{surname(n)}</button>
        ))}
      </div>
      <label className="field">{home} price (US odds)
        <input className="in fig" inputMode="numeric" value={text} placeholder={f.pricing ? american(f.pricing.fair_dec[side]) : "+150"}
          onChange={(e) => setText(e.target.value)} />
      </label>
      {result ?? <p className="meta" style={{ margin: 0 }}>Our fair price is {f.pricing ? american(f.pricing.fair_dec[side]) : "–"}; worth it at {orBetter(f.pricing?.bet_at_dec[side])}.</p>}
    </div>
  );
}

function PriceAlerts({ f }: { f: FightDetail }) {
  const qc = useQueryClient();
  const [text, setText] = useState<[string, string]>(["", ""]);
  const m = useMutation({
    mutationFn: (b: { side: 0 | 1; american: number | null }) => api(`/fights/${f.id}/target`, { method: "PUT", body: JSON.stringify(b) }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["fight", f.id] }); qc.invalidateQueries({ queryKey: ["event"] }); },
  });
  return (
    <div className="pad stack">
      {([0, 1] as const).map((i) => {
        const current = f.targets?.[i];
        const dec = decimalFrom(text[i]);
        return (
          <form key={i} className="row" onSubmit={(e) => {
            e.preventDefault();
            if (dec) m.mutate({ side: i, american: Number(text[i].replace("−", "-").replace("+", "")) });
          }}>
            <label className="field" style={{ flex: 2 }}>{surname(i === 0 ? f.a : f.b)}{current ? ` · alert at ${american(current)}` : ""}
              <input className="in fig" inputMode="numeric" value={text[i]} placeholder={f.pricing ? american(f.pricing.bet_at_dec[i]) : "+150"}
                onChange={(e) => setText(i === 0 ? [e.target.value, text[1]] : [text[0], e.target.value])} />
            </label>
            <button className="btn" style={{ alignSelf: "end" }} disabled={!dec || m.isPending}>Set</button>
            {current ? <button type="button" className="btn" style={{ alignSelf: "end" }} onClick={() => m.mutate({ side: i, american: null })}>Clear</button> : null}
          </form>
        );
      })}
      <p className="meta" style={{ margin: 0 }}>Push alert when a book in the feed reaches that price or better. Your own book isn't in the feed, so check it when the alert comes.</p>
      {m.error && <p className="error">{(m.error as Error).message}</p>}
    </div>
  );
}

function PropsSection({ f, home }: { f: FightDetail; home: string }) {
  const q = useQuery({ queryKey: ["props", f.id], queryFn: () => api<Props>(`/fights/${f.id}/props`), refetchInterval: 60_000 });
  if (!q.data) return <section className="sheet"><div className="pad"><Loading rows={1} /></div></section>;
  const d = q.data;
  if (!d.outcomes.length && !d.totals.length) {
    return <p className="notice">No prop prices yet. Kalshi props and sportsbook total rounds are fetched with the price feed.</p>;
  }
  const groups: [string, (o: string) => boolean][] = [
    ["Goes the distance", (o) => o === "distance" || o === "finish"],
    ["Method of victory", (o) => /^[ab]_(ko|sub|dec)$/.test(o) || o === "draw"],
    ["Round of a finish", (o) => /^ends_r\d$/.test(o)],
    ["Who wins in which round", (o) => /^[ab]_r\d$/.test(o)],
  ];
  return (
    <section className="sheet">
      {groups.map(([title, match]) => {
        const rows = d.outcomes.filter((o) => match(o.outcome)).sort((x, y) => y.p - x.p);
        return rows.length ? (
          <table key={title} className="fig waterfall">
            <thead><tr><th>{title}</th><th>Chance</th><th>Model</th><th>{home} ≥</th></tr></thead>
            <tbody>{rows.map((o) => (
              <tr key={o.outcome}>
                <td>{o.label}<br /><span className="meta">{o.source}{o.p_kalshi != null && o.source !== "Kalshi" ? ` · Kalshi ${pct(o.p_kalshi)}` : ""}</span></td>
                <td>{pct(o.p)}</td><td className="meta">{pct(o.p_model)}</td><td>{o.bet_at_dec ? american(o.bet_at_dec) : "–"}</td>
              </tr>
            ))}</tbody>
          </table>
        ) : null;
      })}
      {d.totals.map((t) => (
        <table key={t.point} className="fig">
          <thead><tr><th>Total {t.point} rounds ({t.books} books)</th><th>Over</th><th>Under</th></tr></thead>
          <tbody>
            <tr><td>Fair chance</td><td>{pct(t.fair_over)}</td><td>{pct(1 - t.fair_over)}</td></tr>
            <tr><td>Fair price</td><td>{american(1 / t.fair_over)}</td><td>{american(1 / (1 - t.fair_over))}</td></tr>
            <tr><td>Best at your books</td>
              {[t.over, t.under].map((b, i) => <td key={i}>{b ? <>{american(b.decimal)} <span className="meta">{b.book}</span></> : "–"}</td>)}</tr>
            <tr><td>Edge (needs {pts(d.threshold)})</td>
              {[t.over, t.under].map((b, i) => <td key={i} className={b && b.edge >= d.threshold ? "pos" : ""}>{b ? pts(b.edge) : "–"}</td>)}</tr>
          </tbody>
        </table>
      ))}
      <p className="pad meta" style={{ margin: 0 }}>
        Chances come from Kalshi where it has a market (it beat our prop model on 2026 fights); the round split uses the model's history of when finishes happen.
        "{home} ≥" is the worst price still worth taking. Book props usually carry a big margin (in the backtest every blind prop bet lost money except
        "favourite by decision"). Total-rounds edges are flagged only against a consensus of 3+ books.
      </p>
    </section>
  );
}

function LogBet({ f, home }: { f: FightDetail; home: string }) {
  const qc = useQueryClient();
  const [side, setSide] = useState<0 | 1>(f.pick?.side ?? 0);
  const [price, setPrice] = useState(f.pick ? american(f.pick.decimal).replace("−", "-") : "");
  const [book, setBook] = useState(home);
  const [stake, setStake] = useState(f.pick ? String(Math.round(f.pick.stake)) : "");
  // "moneyline", or "prop:b_ko" / "total_rounds:over 2.5", so a prop bet settles on the method and round
  const [bet, setBet] = useState("moneyline");
  const props = useQuery({ queryKey: ["props", f.id], queryFn: () => api<Props>(`/fights/${f.id}/props`) });
  const [market, outcome] = bet === "moneyline" ? ["moneyline", ""] : [bet.slice(0, bet.indexOf(":")), bet.slice(bet.indexOf(":") + 1)];
  const m = useMutation({
    mutationFn: () => post("/bets", { fight_id: f.id, side, market, outcome, book, american: Number(price), stake: Number(stake) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["bets"] }),
  });
  const valid = Math.abs(Number(price)) >= 100 && Number(stake) > 0;
  return (
    <form className="pad stack" onSubmit={(e) => { e.preventDefault(); if (valid) m.mutate(); }}>
      <label className="field">Bet type
        <select className="in" value={bet} onChange={(e) => setBet(e.target.value)}>
          <option value="moneyline">Who wins</option>
          {(props.data?.outcomes ?? []).map((o) => <option key={o.outcome} value={`prop:${o.outcome}`}>{o.label}</option>)}
          {(props.data?.totals ?? []).flatMap((t) => ["over", "under"].map((s) => (
            <option key={`${s}${t.point}`} value={`total_rounds:${s} ${t.point}`}>{s === "over" ? "Over" : "Under"} {t.point} rounds</option>)))}
        </select>
      </label>
      {market === "moneyline" && <div className="row" role="radiogroup" aria-label="Pick">
        {[f.a, f.b].map((n, i) => (
          <button key={n} type="button" role="radio" aria-checked={side === i} className={`btn${side === i ? " sel" : ""}`}
            onClick={() => setSide(i as 0 | 1)}>{surname(n)}</button>
        ))}
      </div>}
      <div className="row">
        <label className="field">Price (US odds)<input className="in fig" inputMode="numeric" value={price} placeholder="+150" onChange={(e) => setPrice(e.target.value)} /></label>
        <label className="field">Stake<input className="in fig" inputMode="decimal" value={stake} placeholder="20" onChange={(e) => setStake(e.target.value)} /></label>
      </div>
      <label className="field">Book<input className="in" value={book} placeholder={home} onChange={(e) => setBook(e.target.value)} /></label>
      <button className="btn block" disabled={!valid || m.isPending}>{m.isSuccess ? "Logged. See Bets" : m.isPending ? "Saving…" : "Log this bet"}</button>
      {m.error && <p className="error">{(m.error as Error).message}</p>}
    </form>
  );
}

export function FightPage() {
  const id = Number(useParams().id);
  const fight = useQuery({ queryKey: ["fight", id], queryFn: () => api<FightDetail>(`/fights/${id}`) });
  const odds = useQuery({ queryKey: ["odds", id], queryFn: () => api<Series>(`/fights/${id}/odds`), refetchInterval: 60_000 });
  const social = useQuery({ queryKey: ["social", id], queryFn: () => api<{ a: SocialSide; b: SocialSide }>(`/fights/${id}/social`) });
  const live = useQuery({ queryKey: ["live", id], queryFn: () => api<LiveState>(`/fights/${id}/live`) });
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => api<SettingsData>("/settings") });
  const books = useQuery({ queryKey: ["books"], queryFn: () => api<Books>("/books") });
  if (fight.error) return <main><ErrorNote error={fight.error} /></main>;
  if (!fight.data) return <main><Loading /></main>;
  const f = fight.data;
  const d = f.details;
  const pr = f.pricing;
  const home = settings.data?.home_book || pr?.home_book || "your book";
  const usable = books.data?.usable ?? [];
  const pickSide = f.pick?.side;
  const pickSource = pickSide != null ? f.sides?.[pickSide]?.source : undefined;
  const lines = Object.entries(odds.data?.series ?? {}).map(([label, pts]) => ({
    label: `${label} (${surname(f.a)})`, points: pts.map((x) => ({ t: new Date(x.ts).getTime(), p: x.p_a })),
  }));
  const younger = pr?.age_gap ? (pr.age_gap > 0 ? f.b : f.a) : null;
  const pBeforeYouth = f.p_final != null && pr ? f.p_final - pr.youth_delta : null;
  return (
    <main>
      <div className="topbar"><Link to="/" className="back">← Card</Link></div>
      <p className="meta">{f.weightclass}{f.title ? " title fight" : ""} · {f.rounds} rounds</p>
      <h1 className="sr-only">{f.a} vs {f.b}</h1>
      <div className="faceoff">
        <div><FighterPhoto name={f.a} size={88} /><span className="nm">{f.a}</span><span className="meta fig">{d.record_a ?? "–"}</span></div>
        <span className="vs">vs</span>
        <div><FighterPhoto name={f.b} size={88} /><span className="nm">{f.b}</span><span className="meta fig">{d.record_b ?? "–"}</span></div>
      </div>
      <PhotoCredits names={[f.a, f.b]} />

      <section className="sheet" style={{ marginTop: 16 }}>
        <div className="verdict">
          {f.pick
            ? <div className="call yes fig">Bet {surname(pickSide === 0 ? f.a : f.b)} {american(f.pick.decimal)}</div>
            : <div className="call no">No bet</div>}
          <p className="meta" style={{ margin: "6px 0 0" }}>
            {f.pick ? `${f.pick.book} · edge ${pts(f.pick.edge)} · stake up to ${Math.round(f.pick.stake)}. ${SOURCE_TEXT[pickSource ?? ""] ?? ""}`
              : `No price at your books beats our estimate by ${pts(pr?.threshold ?? null)}.`}
          </p>
        </div>
        <div className="sides2">
          {([0, 1] as const).map((i) => {
            const s = f.sides?.[i];
            return (
              <div key={i}>
                <div className="who">{surname(i === 0 ? f.a : f.b)} · {pct(f.p_final == null ? null : i === 0 ? f.p_final : 1 - f.p_final)}</div>
                <div className="kv fig">
                  <span>Fair</span><b>{pr ? american(pr.fair_dec[i]) : "–"}</b>
                  <span>{home}</span><b>{pr?.bet_at_dec[i] ? `≥ ${american(pr.bet_at_dec[i])}` : "no price"}</b>
                  <span>Best</span><b>{s?.decimal ? american(s.decimal) : "–"}</b>
                  <span></span><span className="meta" style={{ textAlign: "right" }}>{s?.book ?? ""}</span>
                  <span>Edge</span><b className={s?.edge != null && pr && s.edge >= pr.threshold ? "pos" : ""}>{pts(s?.edge ?? null)}</b>
                </div>
              </div>
            );
          })}
        </div>
        {pr?.arb && <p className="flag-note">The best prices on each side at different books add up to under 100%: the books disagree (possible arbitrage, often a stale line).</p>}
        {d.notes && d.notes.length > 0 && <ul className="meta" style={{ padding: "0 14px 12px 32px", margin: 0 }}>{d.notes.map((n) => <li key={n}>{n}</li>)}</ul>}
      </section>

      <h3>Check a {home} price</h3>
      <section className="sheet"><PriceCheck f={f} home={home} tuning={settings.data?.tuning} /></section>

      <h3>How we priced it</h3>
      <section className="sheet">
        <table className="fig waterfall">
          <thead><tr><th></th><th>{surname(f.a)}</th><th>{surname(f.b)}</th></tr></thead>
          <tbody>
            <tr><td>Market<br /><span className="meta">{f.market_source || "no prices yet"}</span></td><td>{pct(f.p_market, 1)}</td><td>{pct(f.p_market == null ? null : 1 - f.p_market, 1)}</td></tr>
            {Object.entries(f.prediction_markets).map(([src, v]) => (
              <tr key={src}><td>&nbsp;&nbsp;{src}</td><td>{pct(v.p_a, 1)}</td><td>{pct(1 - v.p_a, 1)}</td></tr>
            ))}
            {pr && pr.model_weight > 0 && (
              <tr><td>Stats model × {pr.model_weight.toFixed(2)}</td><td>{pct(f.p_model, 1)}</td><td>{pct(f.p_model == null ? null : 1 - f.p_model, 1)}</td></tr>
            )}
            {pr && pBeforeYouth != null && (
              <tr><td>Youth{younger ? <><br /><span className="meta">{surname(younger)} {Math.abs(pr.age_gap!).toFixed(1)} yrs younger</span></> : null}</td>
                <td>{pts(pr.youth_delta)}</td><td>{pts(-pr.youth_delta)}</td></tr>
            )}
            <tr className="total"><td>Our price</td><td>{pct(f.p_final, 1)}</td><td>{pct(f.p_final == null ? null : 1 - f.p_final, 1)}</td></tr>
          </tbody>
        </table>
        <table className="fig waterfall">
          <thead><tr><th>Other opinions, not in the price</th><th>{surname(f.a)}</th><th>{surname(f.b)}</th></tr></thead>
          <tbody>
            {(!pr || pr.model_weight === 0) && <tr><td>Stats model</td><td>{pct(f.p_model)}</td><td>{pct(f.p_model == null ? null : 1 - f.p_model)}</td></tr>}
            {d.p_experts != null && (
              <tr><td>Handicappers<br /><span className="meta">{d.experts_source} · share picking</span></td><td>{pct(d.p_experts)}</td><td>{pct(1 - d.p_experts)}</td></tr>
            )}
          </tbody>
        </table>
        <p className="pad meta" style={{ margin: 0 }}>In backtests the market alone beat the stats model and the handicappers; only the youth shift improved it.</p>
      </section>

      <h3>Prices by book</h3>
      <section className="sheet scroll">
        {d.books && Object.keys(d.books).length ? (
          <table className="fig">
            <thead><tr><th>Book</th><th>{surname(f.a)}</th><th>{surname(f.b)}</th></tr></thead>
            <tbody>
              {Object.entries(d.books).map(([b, [da, db]]) => {
                const mine = !usable.length || usable.includes(b);
                return <tr key={b} className={mine ? "" : "muted"}><td>{b}{mine ? "" : " (not yours)"}</td><td>{american(da)}</td><td>{american(db)}</td></tr>;
              })}
              <tr><td>{home} <span className="meta">(not in feed: worth it at)</span></td>
                <td>{orBetter(pr?.bet_at_dec[0])}</td><td>{orBetter(pr?.bet_at_dec[1])}</td></tr>
            </tbody>
          </table>
        ) : <p className="pad meta" style={{ margin: 0 }}>No sportsbook prices yet.</p>}
      </section>

      <h3>Props</h3>
      <PropsSection f={f} home={home} />

      <h3>Price alerts</h3>
      <section className="sheet"><PriceAlerts f={f} /></section>

      <h3>Live</h3>
      <section className="sheet pad">
        <RoundStrip rounds={f.rounds} scored={live.data?.rounds ?? []} a={f.a} b={f.b} />
        {f.status !== "finished" && <Link to={`/fight/${f.id}/live`} className={`btn block${f.status === "live" ? " primary" : ""}`} style={{ marginTop: 12 }}>{f.status === "live" ? "Score rounds live" : "Open live scoring"}</Link>}
      </section>

      <h3>Line movement</h3>
      <section className="sheet">{odds.data ? <ProbChart lines={lines} /> : <div className="pad"><Loading rows={1} /></div>}</section>

      {d.llm && <>
        <h3>Analyst breakdown</h3>
        <section className="sheet pad">
          <p>{d.llm.summary}</p>
          <ul style={{ paddingLeft: 18, margin: "8px 0" }}>{d.llm.key_factors.map((k) => <li key={k}>{k}</li>)}</ul>
          {d.llm.risks.length > 0 && <p className="meta">Risks: {d.llm.risks.join(" · ")}</p>}
        </section>
      </>}

      <h3>Tale of the tape (UFC only)</h3>
      <section className="sheet scroll">
        <TapeTable f={f} rows={TAPE_KEY} />
        <details>
          <summary className="pad meta" style={{ cursor: "pointer" }}>All stats</summary>
          <TapeTable f={f} rows={TAPE_MORE} />
        </details>
        <p className="pad meta" style={{ margin: 0 }}>▲ marks the better number. The stats explain a fight; they don't set the price.</p>
      </section>

      <h3>Fan sentiment and news</h3>
      <section className="sheet">
        {social.data ? (
          <>
            <table className="fig">
              <thead><tr><th></th><th>{surname(f.a)}</th><th>{surname(f.b)}</th></tr></thead>
              <tbody>
                <tr><td>Posts</td><td>{social.data.a.summary.n}</td><td>{social.data.b.summary.n}</td></tr>
                <tr><td>Tone (−1 to +1)</td><td>{social.data.a.summary.mean.toFixed(2)}</td><td>{social.data.b.summary.mean.toFixed(2)}</td></tr>
                <tr><td>Red flags</td><td>{social.data.a.flags.join(", ") || "none"}</td><td>{social.data.b.flags.join(", ") || "none"}</td></tr>
              </tbody>
            </table>
            <ul className="list">
              {[...social.data.a.posts, ...social.data.b.posts].filter((p) => p.flags.length).slice(0, 6).map((p) => (
                <li key={p.link}><a href={p.link} target="_blank" rel="noreferrer">{p.title}</a> <span className="meta">{p.source} · {p.flags.join(", ")}</span></li>
              ))}
            </ul>
            <p className="pad meta" style={{ margin: 0 }}>Tone is a hype gauge, not a prediction. A heavily backed favourite can be the worse price.</p>
          </>
        ) : <div className="pad"><Loading rows={1} /></div>}
      </section>

      <h3>Log a bet</h3>
      <section className="sheet"><LogBet f={f} home={home} /></section>
    </main>
  );
}
