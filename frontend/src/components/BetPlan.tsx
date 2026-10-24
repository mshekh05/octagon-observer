import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, type BetPlan as Plan, type PlanBet, type SettingsData } from "../lib/api";
import { american, money, pct, pts, surname } from "../lib/format";

const toAmerican = (d: number) => Math.round(d >= 2 ? (d - 1) * 100 : -100 / (d - 1));

const WHY: Record<string, string> = {
  "line shopping": "this book is off the others",
  youth: "the younger fighter is underpriced",
  model: "the stats model sees value",
};

function Row({ b, home }: { b: PlanBet; home: string }) {
  const qc = useQueryClient();
  const target = useMutation({
    mutationFn: () => api(`/fights/${b.fight_id}/target`, { method: "PUT", body: JSON.stringify({ side: Number(b.outcome), american: toAmerican(b.bet_at!) }) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["event"] }),
  });
  return (
    <li>
      <Link to={`/fight/${b.fight_id}`} style={{ textDecoration: "none" }}>
        <span className={`fig ${b.tier === "value" ? "value" : "lean"}`}>{b.label} {american(b.decimal)} @ {b.book}</span>
        <p className="meta fig" style={{ margin: "4px 0 0" }}>
          <b className="stake">Stake {money(b.stake)}</b> · chance {pct(b.p)} · edge {pts(b.edge)}{WHY[b.source] ? ` · ${WHY[b.source]}` : ""}
        </p>
        {b.bet_at && <p className="meta fig" style={{ margin: "2px 0 0" }}>At {home}: worth it at {american(b.bet_at)} or better</p>}
      </Link>
      {b.tier === "lean" && b.market === "moneyline" && b.bet_at && (
        <button className="btn" style={{ marginTop: 8, minHeight: 40 }} disabled={target.isPending || target.isSuccess} onClick={() => target.mutate()}>
          {target.isSuccess ? `Alert set at ${american(b.bet_at)}` : `Alert me when it's value (${american(b.bet_at)})`}
        </button>
      )}
    </li>
  );
}

export function BetPlanSection({ slug }: { slug: string }) {
  const qc = useQueryClient();
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => api<SettingsData>("/settings") });
  const plan = useQuery({ queryKey: ["plan", slug], queryFn: () => api<Plan>(`/events/${slug}/plan`), refetchInterval: 60_000 });
  const [budget, setBudget] = useState("");
  useEffect(() => { if (plan.data && !budget) setBudget(String(plan.data.budget)); }, [plan.data, budget]);
  const save = useMutation({
    mutationFn: () => api("/settings", { method: "PUT", body: JSON.stringify({ card_budget: Number(budget) }) }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["plan", slug] }); qc.invalidateQueries({ queryKey: ["settings"] }); },
  });
  if (!plan.data) return null;
  const p = plan.data;
  const home = p.home_book || settings.data?.home_book || "your book";
  const value = p.bets.filter((b) => b.tier === "value");
  const leans = p.bets.filter((b) => b.tier === "lean");
  const checksByFight = Object.entries(p.checks.reduce<Record<number, PlanBet[]>>((acc, c) => {
    (acc[c.fight_id] ??= []).push(c);
    return acc;
  }, {}));
  return (
    <section className="sheet" aria-labelledby="plan-h" style={{ marginTop: 16 }}>
      <div className="sheet-head"><h2 id="plan-h">Your bet plan</h2><span className="meta fig">{money(p.spent)} of {money(p.budget)}</span></div>
      <form className="pad row" onSubmit={(e) => { e.preventDefault(); if (Number(budget) > 0) save.mutate(); }} style={{ alignItems: "end" }}>
        <label className="field" style={{ flex: 2 }}>Budget for this card
          <input className="in fig" inputMode="decimal" value={budget} onChange={(e) => setBudget(e.target.value)} />
        </label>
        <button className="btn" disabled={save.isPending || Number(budget) <= 0}>{save.isPending ? "…" : "Update"}</button>
      </form>

      {value.length > 0 && <><h3 className="pad" style={{ margin: 0, paddingBottom: 0 }}>Value bets</h3><ul className="list">{value.map((b) => <Row key={`${b.fight_id}${b.market}${b.outcome}`} b={b} home={home} />)}</ul></>}
      {leans.length > 0 && <><h3 className="pad" style={{ margin: 0, paddingBottom: 0 }}>Leans <span className="meta">· smaller edge ({pts(p.lean_edge)} to {pts(p.threshold)}), half stakes</span></h3>
        <ul className="list">{leans.map((b) => <Row key={`${b.fight_id}${b.market}${b.outcome}`} b={b} home={home} />)}</ul></>}
      {!value.length && !leans.length && (
        <p className="pad meta" style={{ margin: 0 }}>No bet has a positive edge at your books right now, so the plan spends nothing. That's the engine working, not failing. Check the props below at {home}, or set an alert on a fight.</p>
      )}
      {p.unspent > 0 && (value.length > 0 || leans.length > 0) && (
        <p className="pad meta fig" style={{ margin: 0 }}>{money(p.unspent)} unspent: not enough value on this card to place it. Bets are capped at 30% of the budget each and 40% per fight.</p>
      )}

      {checksByFight.length > 0 && <>
        <h3 className="pad" style={{ margin: 0, paddingBottom: 0 }}>Props to check at {home} <span className="meta">· bet only at these prices or better</span></h3>
        <ul className="list">{checksByFight.map(([fid, cs]) => (
          <li key={fid}>
            <Link to={`/fight/${fid}`} style={{ textDecoration: "none" }}>
              <span className="fig" style={{ fontWeight: 600 }}>{cs[0].fight.split(" vs ").map(surname).join(" vs ")}</span>
              {cs.map((c) => (
                <p key={c.outcome} className="meta fig" style={{ margin: "4px 0 0" }}>
                  {c.label}: {pct(c.p)} · <b>{home} ≥ {american(c.bet_at)}</b> · stake {money(c.stake)}
                </p>
              ))}
            </Link>
          </li>
        ))}</ul>
      </>}

      {p.favourites.length > 0 && (
        <details>
          <summary className="pad meta" style={{ cursor: "pointer" }}>Most likely winners (for comparison; no edge at these prices)</summary>
          <ul className="list">{p.favourites.map((f) => (
            <li key={f.fight_id} className="fig">
              {f.label} · <b>{pct(f.p)}</b> <span className="meta">· fair {american(f.fair_dec)} · best {american(f.decimal)} {f.book ?? ""} · edge {pts(f.edge)}</span>
            </li>
          ))}</ul>
          <p className="pad meta" style={{ margin: 0 }}>Backing favourites wins often but, at fair prices minus the bookmaker margin, loses slowly over time. The plan only stakes where the price is better than our chance.</p>
        </details>
      )}
    </section>
  );
}
