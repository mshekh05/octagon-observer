import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, type Parlay } from "../lib/api";
import { american, americanInt, money, pct, pts, surname } from "../lib/format";
import { useActiveEvent } from "./CardPage";
import { ErrorNote, Loading } from "./common";

function ParlayCard({ p }: { p: Parlay }) {
  return (
    <li className="bout">
      <div className="bout-names" style={{ flexWrap: "wrap", gap: 4 }}>
        {p.legs.map((leg, i) => (
          <span key={leg.fight_id} className="name" style={{ width: "auto" }}>
            {i > 0 && <span className="meta"> + </span>}
            {surname(leg.name)}
          </span>
        ))}
        <span className="prob fig">{americanInt(p.american)}</span>
      </div>
      <div className="bout-foot">
        <span className="figs fig">
          <span>Win prob <b>{pct(p.p, 1)}</b></span>
          <span>Edge <b>{pts(p.edge)}</b></span>
          <span>EV <b>{pct(p.ev, 1)}</b></span>
        </span>
      </div>
      <div className="bout-foot">
        <span className="meta">{p.legs.map((l) => `${surname(l.name)} ${american(l.decimal)} @ ${l.book}`).join(" · ")}</span>
      </div>
      <div className="bout-foot">
        <span className="meta">Suggested stake <b className="fig">{money(p.stake)}</b> ({pct(p.stake_pct, 1)} of bankroll)</span>
      </div>
    </li>
  );
}

export function ParlaysPage() {
  const { settings, error: activeError } = useActiveEvent();
  const slug = settings.data?.active_card;
  const parlays = useQuery({
    queryKey: ["parlays", slug], enabled: !!slug, refetchInterval: 30_000,
    queryFn: () => api<Parlay[]>(`/events/${slug}/parlays`),
  });
  const error = activeError ?? parlays.error;
  if (error) return <main><h1>Parlays</h1><ErrorNote error={error} /></main>;
  if (!parlays.data) return <main><h1>Parlays</h1><Loading /></main>;
  return (
    <main>
      <div className="topbar"><Link to="/" className="back">← Card</Link></div>
      <h1>Parlays</h1>
      <p className="meta">Combos of today's single-bet picks only, so a leg is never something too thin to bet on its own. Treat them as entertainment: a parlay multiplies any error in our estimate along with the payout, and the backtests found no proven edge even in single bets. Suggested stakes are capped at a quarter of the single-bet cap.</p>
      <section className="sheet" aria-labelledby="parlay-h" style={{ marginTop: 16 }}>
        <div className="sheet-head"><h2 id="parlay-h">Ranked by expected value</h2><span className="meta fig">{parlays.data.length}</span></div>
        {parlays.data.length ? (
          <ul className="list">{parlays.data.map((p, i) => <ParlayCard key={i} p={p} />)}</ul>
        ) : (
          <p className="pad meta" style={{ margin: 0 }}>No combos: a parlay needs at least two single-bet picks on <Link to="/">the card</Link>, and right now there {"aren't"} enough.</p>
        )}
      </section>
    </main>
  );
}
