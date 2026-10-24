import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api, post, type Alert, type FightDetail, type LiveState } from "../lib/api";
import { ago, pct, surname } from "../lib/format";
import { ProbChart } from "../components/ProbChart";
import { RoundStrip } from "../components/RoundStrip";
import { useActiveEvent } from "./CardPage";
import { ErrorNote, Loading } from "./common";

/** /live: jump to the fight that's on now, or the next one. */
export function LivePicker() {
  const { event, error } = useActiveEvent();
  if (error) return <main><h1>Live</h1><ErrorNote error={error} /></main>;
  if (!event.data) return <main><h1>Live</h1><Loading /></main>;
  const fights = [...event.data.fights].sort((x, y) => y.order - x.order); // card runs bottom-up
  const live = fights.filter((f) => f.status === "live");
  const next = fights.filter((f) => f.status === "scheduled");
  return (
    <main>
      <h1>Live</h1>
      <p className="meta">Pick the fight on screen and score each round as it ends.</p>
      {[["On now", live], ["Up next", next.slice(0, 4)]].map(([label, list]) => (list as typeof fights).length > 0 && (
        <section key={label as string} className="sheet" style={{ marginTop: 16 }}>
          <div className="sheet-head"><h2>{label as string}</h2></div>
          <ul className="list">
            {(list as typeof fights).map((f) => (
              <li key={f.id}><Link to={`/fight/${f.id}/live`} className="name" style={{ textDecoration: "none" }}>
                {surname(f.a)} <span className="muted">vs</span> {surname(f.b)}</Link>
                <span className="meta" style={{ float: "right" }}>{f.status === "live" ? <span className="live-dot">LIVE</span> : `${f.rounds} rds`}</span></li>
            ))}
          </ul>
        </section>
      ))}
      {!live.length && !next.length && <p className="notice" style={{ marginTop: 16 }}>Every fight on this card is finished.</p>}
    </main>
  );
}

function Stepper({ label, value, set }: { label: string; value: number; set: (n: number) => void }) {
  return (
    <label className="field">{label}
      <div className="stepper">
        <button type="button" aria-label={`Fewer ${label}`} onClick={() => set(Math.max(0, value - 1))}>−</button>
        <span className="fig" aria-live="polite">{value}</span>
        <button type="button" aria-label={`More ${label}`} onClick={() => set(value + 1)}>+</button>
      </div>
    </label>
  );
}

export function LiveScorer() {
  const id = Number(useParams().id);
  const qc = useQueryClient();
  const fight = useQuery({ queryKey: ["fight", id], queryFn: () => api<FightDetail>(`/fights/${id}`) });
  const live = useQuery({ queryKey: ["live", id], queryFn: () => api<LiveState>(`/fights/${id}/live`), refetchInterval: 20_000 });
  const alerts = useQuery({ queryKey: ["alerts"], queryFn: () => api<Alert[]>("/alerts?limit=30"), refetchInterval: 30_000 });
  const scored = live.data?.rounds ?? [];
  const [round, setRound] = useState<number | null>(null);
  const [winner, setWinner] = useState<0 | 1 | null | undefined>(undefined);
  const [kd, setKd] = useState<[number, number]>([0, 0]);
  const [td, setTd] = useState<[number, number]>([0, 0]);

  useEffect(() => {
    if (round == null && fight.data) setRound(Math.min(scored.length + 1, fight.data.rounds));
  }, [round, scored.length, fight.data]);
  useEffect(() => {
    const r = scored.find((x) => x.round === round);
    setWinner(r ? r.winner : undefined);
    setKd(r ? [r.kd_a, r.kd_b] : [0, 0]);
    setTd(r ? [r.td_a, r.td_b] : [0, 0]);
  }, [round, live.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = useMutation({
    mutationFn: () => post(`/fights/${id}/rounds`, { round, winner, kd_a: kd[0], kd_b: kd[1], td_a: td[0], td_b: td[1] }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["live", id] });
      qc.invalidateQueries({ queryKey: ["event"] });
      if (fight.data && round != null && round < fight.data.rounds) setRound(round + 1);
    },
  });
  const [method, setMethod] = useState<"KO" | "SUB" | "DEC" | null>(null);
  const [endRound, setEndRound] = useState<number | null>(null);
  const finish = useMutation({
    mutationFn: (w: 0 | 1 | null) => post(`/fights/${id}/status`, {
      status: "finished", winner: w, method, round: method === "DEC" ? fight.data?.rounds : endRound }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["live", id] }); qc.invalidateQueries({ queryKey: ["fight", id] }); qc.invalidateQueries({ queryKey: ["bets"] }); },
  });

  if (fight.error) return <main><ErrorNote error={fight.error} /></main>;
  if (!fight.data || !live.data) return <main><Loading /></main>;
  const f = fight.data;
  const est = live.data.estimates.at(-1);
  const p = est?.p_live ?? f.p_final;
  const mine = (alerts.data ?? []).filter((a) => a.fight_id === id).slice(0, 5);
  const chart = [
    { label: "Live estimate", points: live.data.estimates.map((e) => ({ t: new Date(e.ts).getTime(), p: e.p_live })) },
    { label: "Prediction market", points: live.data.estimates.filter((e) => e.p_market != null).map((e) => ({ t: new Date(e.ts).getTime(), p: e.p_market! })) },
  ];
  const finished = live.data.status === "finished";

  return (
    <main>
      <div className="topbar"><Link to={`/fight/${id}`} className="back">← Fight</Link>{live.data.status === "live" && <span className="live-dot">LIVE</span>}</div>
      <section className="sheet">
        <div className="decision" aria-live="polite">
          <div><div className="who">{surname(f.a)}</div><div className="big fig">{pct(p)}</div></div>
          <div className="right"><div className="who">{surname(f.b)}</div><div className="big fig muted">{pct(p == null ? null : 1 - p)}</div></div>
        </div>
        <p className="pad meta fig" style={{ paddingTop: 0, margin: 0 }}>
          {est ? <>After round {est.after_round} · rounds model {pct(est.p_rounds)}{est.p_market != null ? ` · market ${pct(est.p_market)}` : ""} · {ago(est.ts)}</>
            : <>Pre-fight estimate. Score round 1 when it ends.</>}
        </p>
      </section>

      <h3>{finished ? "Final scorecard" : "Score a round"}</h3>
      <RoundStrip big rounds={f.rounds} scored={scored} a={f.a} b={f.b} selected={finished ? null : round} onPick={finished ? undefined : setRound} />

      {!finished && round != null && (
        <section className="sheet pad stack" style={{ marginTop: 12 }} aria-label={`Round ${round}`}>
          <p style={{ margin: 0, fontWeight: 600 }}>Who won round {round}?</p>
          <div className="row" role="radiogroup" aria-label={`Round ${round} winner`}>
            {([[0, surname(f.a)], [null, "Even"], [1, surname(f.b)]] as [0 | 1 | null, string][]).map(([v, label]) => (
              <button key={label} type="button" role="radio" aria-checked={winner === v} className={`btn${winner === v ? " sel" : ""}`} onClick={() => setWinner(v)}>{label}</button>
            ))}
          </div>
          <div className="row">
            <Stepper label={`${surname(f.a)} knockdowns`} value={kd[0]} set={(n) => setKd([n, kd[1]])} />
            <Stepper label={`${surname(f.b)} knockdowns`} value={kd[1]} set={(n) => setKd([kd[0], n])} />
          </div>
          <details>
            <summary className="meta" style={{ cursor: "pointer", minHeight: 32 }}>Takedowns (optional)</summary>
            <div className="row" style={{ marginTop: 8 }}>
              <Stepper label={`${surname(f.a)} takedowns`} value={td[0]} set={(n) => setTd([n, td[1]])} />
              <Stepper label={`${surname(f.b)} takedowns`} value={td[1]} set={(n) => setTd([td[0], n])} />
            </div>
          </details>
          <button className="btn primary block" disabled={winner === undefined || save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? "Updating…" : `Save round ${round}`}
          </button>
          {save.error && <p className="error">{(save.error as Error).message}</p>}
        </section>
      )}

      <h3>Live probability</h3>
      <section className="sheet"><ProbChart lines={chart} /></section>

      <h3>Alerts for this fight</h3>
      <section className="sheet">
        {mine.length ? <ul className="list">{mine.map((a) => (
          <li key={a.id}><b className={a.urgent ? "value" : ""} style={{ display: "block" }}>{a.title}</b>
            <span className="meta" style={{ whiteSpace: "pre-line" }}>{a.body}</span></li>
        ))}</ul> : <p className="pad meta" style={{ margin: 0 }}>No alerts yet for this fight.</p>}
      </section>

      {!finished && <>
        <h3>Fight over?</h3>
        <p className="meta" style={{ margin: "0 0 8px" }}>How it ended (optional, settles the prop bets tonight; ufcstats fills it in later anyway)</p>
        <div className="row" role="radiogroup" aria-label="Method">
          {(["KO", "SUB", "DEC"] as const).map((m) => (
            <button key={m} type="button" role="radio" aria-checked={method === m} className={`btn${method === m ? " sel" : ""}`}
              onClick={() => setMethod(method === m ? null : m)}>{m === "KO" ? "KO/TKO" : m === "SUB" ? "Submission" : "Decision"}</button>
          ))}
        </div>
        {method && method !== "DEC" && (
          <div className="row" role="radiogroup" aria-label="Round" style={{ marginTop: 8 }}>
            {Array.from({ length: f.rounds }, (_, i) => i + 1).map((r) => (
              <button key={r} type="button" role="radio" aria-checked={endRound === r} className={`btn${endRound === r ? " sel" : ""}`}
                onClick={() => setEndRound(r)}>R{r}</button>
            ))}
          </div>
        )}
        <div className="row" style={{ marginTop: 8 }}>
          <button className="btn" onClick={() => finish.mutate(0)}>{surname(f.a)} won</button>
          <button className="btn" onClick={() => finish.mutate(1)}>{surname(f.b)} won</button>
        </div>
        <button className="btn block" style={{ marginTop: 8 }} onClick={() => finish.mutate(null)}>Draw or no contest</button>
        <p className="meta" style={{ marginTop: 8 }}>Settles your logged bets and the plan's bets on this fight, and scores the forecasts.</p>
      </>}
    </main>
  );
}
