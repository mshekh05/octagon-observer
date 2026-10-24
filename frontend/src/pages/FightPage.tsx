import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api, post, type FightDetail, type LiveState, type Series, type SocialSide } from "../lib/api";
import { american, pct, pts, surname } from "../lib/format";
import { ProbChart } from "../components/ProbChart";
import { RoundStrip } from "../components/RoundStrip";
import { ErrorNote, Loading } from "./common";

const TAPE: [string, string, "pct" | number][] = [
  ["elo", "Elo rating", 0], ["sos", "Avg opponent Elo", 0], ["n_fights", "UFC fights", 0],
  ["slpm", "Strikes landed / min", 2], ["slpm_adj", "Landed vs opponents' norm / min", 2],
  ["sapm", "Strikes absorbed / min", 2], ["sapm_adj", "Absorbed vs opponents' norm / min", 2],
  ["str_acc", "Striking accuracy", "pct"], ["str_def", "Striking defence", "pct"],
  ["td15", "Takedowns / 15 min", 2], ["td_def", "Takedown defence", "pct"], ["sub15", "Sub attempts / 15 min", 2],
  ["ctrl_pct", "Control time", "pct"], ["kd15", "Knockdowns / 15 min", 2], ["age", "Age", 1], ["reach_in", "Reach (in)", 0],
  ["layoff_days", "Days since last fight", 0],
];

const fmt = (v: number | null | undefined, k: "pct" | number) =>
  v == null ? "–" : k === "pct" ? `${Math.round(v * 100)}%` : v.toFixed(k);

function LogBet({ f }: { f: FightDetail }) {
  const qc = useQueryClient();
  const sides = f.details.sides ?? [];
  const [side, setSide] = useState<0 | 1>(f.pick?.side ?? 0);
  const [price, setPrice] = useState(f.pick ? american(f.pick.decimal).replace("−", "-") : "");
  const [book, setBook] = useState(f.pick?.book ?? sides[side]?.book ?? "");
  const [stake, setStake] = useState(f.pick ? String(Math.round(f.pick.stake)) : "");
  const m = useMutation({
    mutationFn: () => post("/bets", { fight_id: f.id, side, book, american: Number(price), stake: Number(stake) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["bets"] }),
  });
  const valid = Math.abs(Number(price)) >= 100 && Number(stake) > 0;
  return (
    <form className="pad stack" onSubmit={(e) => { e.preventDefault(); if (valid) m.mutate(); }}>
      <div className="row" role="radiogroup" aria-label="Pick">
        {[f.a, f.b].map((n, i) => (
          <button key={n} type="button" role="radio" aria-checked={side === i} className={`btn${side === i ? " sel" : ""}`}
            onClick={() => { setSide(i as 0 | 1); setBook(sides[i]?.book ?? book); }}>{surname(n)}</button>
        ))}
      </div>
      <div className="row">
        <label className="field">Price (US odds)<input className="in fig" inputMode="numeric" value={price} placeholder="+150" onChange={(e) => setPrice(e.target.value)} /></label>
        <label className="field">Stake<input className="in fig" inputMode="decimal" value={stake} placeholder="20" onChange={(e) => setStake(e.target.value)} /></label>
      </div>
      <label className="field">Book<input className="in" value={book} placeholder="DraftKings" onChange={(e) => setBook(e.target.value)} /></label>
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
  if (fight.error) return <main><ErrorNote error={fight.error} /></main>;
  if (!fight.data) return <main><Loading /></main>;
  const f = fight.data;
  const d = f.details;
  const p = f.live?.p_live ?? f.p_final;
  const lines = Object.entries(odds.data?.series ?? {}).map(([label, pts]) => ({
    label: `${label} (${surname(f.a)})`, points: pts.map((x) => ({ t: new Date(x.ts).getTime(), p: x.p_a })),
  }));
  return (
    <main>
      <div className="topbar"><Link to="/" className="back">← Card</Link></div>
      <p className="meta">{f.weightclass}{f.title ? " title fight" : ""} · {f.rounds} rounds</p>
      <h1>{f.a} <span className="muted">vs</span> {f.b}</h1>
      <p className="meta fig">{d.record_a ?? "–"} · {d.record_b ?? "–"}</p>

      <section className="sheet" style={{ marginTop: 16 }}>
        <div className="decision">
          <div><div className="who">{surname(f.a)}</div><div className="big fig">{pct(p)}</div></div>
          <div className="right"><div className="who">{surname(f.b)}</div><div className="big fig muted">{pct(p == null ? null : 1 - p)}</div></div>
        </div>
        <div className="pad" style={{ paddingTop: 0 }}>
          <RoundStrip rounds={f.rounds} scored={live.data?.rounds ?? []} a={f.a} b={f.b} />
          {f.status !== "finished" && <Link to={`/fight/${f.id}/live`} className="btn block" style={{ marginTop: 12 }}>{f.status === "live" ? "Score rounds live" : "Open live scoring"}</Link>}
        </div>
        <table className="fig">
          <thead><tr><th>Probability</th><th>{surname(f.a)}</th><th>{surname(f.b)}</th></tr></thead>
          <tbody>
            <tr><td>Stats model</td><td>{pct(f.p_model)}</td><td>{pct(f.p_model == null ? null : 1 - f.p_model)}</td></tr>
            <tr><td>Market<br /><span className="meta">{f.market_source || "no prices yet"}</span></td><td>{pct(f.p_market)}</td><td>{pct(f.p_market == null ? null : 1 - f.p_market)}</td></tr>
            {Object.entries(f.prediction_markets).map(([src, v]) => (
              <tr key={src}><td>{src}</td><td>{pct(v.p_a)}</td><td>{pct(1 - v.p_a)}</td></tr>
            ))}
            {d.p_experts != null && (
              <tr><td>Handicappers<br /><span className="meta">{d.experts_source} · share picking, not a probability; not in the blend</span></td>
                <td>{pct(d.p_experts)}</td><td>{pct(1 - d.p_experts)}</td></tr>
            )}
            <tr><td>Blended</td><td>{pct(f.p_final)}</td><td>{pct(f.p_final == null ? null : 1 - f.p_final)}</td></tr>
            {d.sides && <>
              <tr><td>Best price</td>{d.sides.map((s, i) => <td key={i}>{american(s.decimal)} <span className="meta">{s.book ?? ""}</span></td>)}</tr>
              <tr><td>Edge</td>{d.sides.map((s, i) => <td key={i} className={s.edge == null ? "" : s.edge > 0 ? "pos" : "neg"}>{pts(s.edge)}</td>)}</tr>
              <tr><td>Edge vs market alone</td>{d.sides.map((s, i) => <td key={i} className={s.market_edge == null ? "" : s.market_edge > 0 ? "pos" : "neg"}>{pts(s.market_edge ?? null)}</td>)}</tr>
            </>}
          </tbody>
        </table>
        <p className="pad" style={{ margin: 0 }}>
          {f.pick ? <span className="value fig">{f.pick.side === 0 ? f.a : f.b} {american(f.pick.decimal)} @ {f.pick.book} · stake up to {Math.round(f.pick.stake)}</span>
            : <span className="meta">No bet. No price beats the blended probability by the edge threshold.</span>}
        </p>
      </section>

      <h3>Line movement</h3>
      <section className="sheet">{odds.data ? <ProbChart lines={lines} /> : <div className="pad"><Loading rows={1} /></div>}</section>

      {d.llm && <>
        <h3>Analyst breakdown</h3>
        <section className="sheet pad">
          <p>{d.llm.summary}</p>
          <ul style={{ paddingLeft: 18, margin: "8px 0" }}>{d.llm.key_factors.map((k) => <li key={k}>{k}</li>)}</ul>
          {d.llm.risks.length > 0 && <p className="meta">Risks: {d.llm.risks.join(" · ")}</p>}
          {d.llm.news_adjustment_a !== 0 && <p className="meta">News adjustment for {surname(f.a)}: {pts(d.llm.news_adjustment_a)}. {d.llm.adjustment_reason}</p>}
        </section>
      </>}

      <h3>Tale of the tape (UFC only)</h3>
      <section className="sheet scroll">
        <table className="fig">
          <thead><tr><th></th><th>{surname(f.a)}</th><th>{surname(f.b)}</th></tr></thead>
          <tbody>{TAPE.map(([k, label, kind]) => <tr key={k}><td>{label}</td><td>{fmt(d.snap_a?.[k], kind)}</td><td>{fmt(d.snap_b?.[k], kind)}</td></tr>)}</tbody>
        </table>
      </section>
      {d.notes && d.notes.length > 0 && <ul className="meta" style={{ paddingLeft: 18 }}>{d.notes.map((n) => <li key={n}>{n}</li>)}</ul>}

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
      <section className="sheet"><LogBet f={f} /></section>
    </main>
  );
}
