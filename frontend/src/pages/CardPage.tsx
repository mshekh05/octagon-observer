import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, post, type EventDetail, type FightSummary, type LiveState, type Parlay, type SettingsData } from "../lib/api";
import { ago, american, orBetter, pct, pts, surname } from "../lib/format";
import { RoundStrip } from "../components/RoundStrip";
import { ErrorNote, Loading } from "./common";

const SEGMENTS: [FightSummary["segment"], string][] = [["main", "Main card"], ["prelims", "Prelims"], ["early", "Early prelims"]];

export function useActiveEvent() {
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => api<SettingsData>("/settings") });
  const slug = settings.data?.active_card;
  const event = useQuery({
    queryKey: ["event", slug], enabled: !!slug, refetchInterval: 30_000,
    queryFn: () => api<EventDetail>(`/events/${slug}`),
  });
  return { settings, event, error: settings.error ?? event.error };
}

/** The side closest to the edge threshold, for a quiet "how close" hint on fights with no bet. */
function nearMiss(f: FightSummary): { side: 0 | 1; edge: number } | null {
  const sides = f.sides ?? [];
  const best = ([0, 1] as const).map((i) => ({ side: i, edge: sides[i]?.edge ?? null }))
    .filter((s): s is { side: 0 | 1; edge: number } => s.edge != null && s.edge > 0)
    .sort((x, y) => y.edge - x.edge)[0];
  return best ?? null;
}

function PriceCell({ f, side }: { f: FightSummary; side: 0 | 1 }) {
  const s = f.sides?.[side];
  const pr = f.pricing;
  const target = f.targets?.[side];
  return (
    <span className="price fig">
      <b>{s?.decimal ? american(s.decimal) : "–"}</b>{s?.book && <span className="meta"> {s.book}</span>}
      {target ? <span className="meta" title="Price alert set"> · alert {american(target)}</span> : null}
      {pr && <span className="sub">fair {american(pr.fair_dec[side])}{pr.bet_at_dec[side] ? ` · bet ≥ ${american(pr.bet_at_dec[side])}` : ""}</span>}
    </span>
  );
}

function Bout({ f }: { f: FightSummary }) {
  const live = useQuery({
    queryKey: ["live", f.id], enabled: f.status !== "scheduled",
    queryFn: () => api<LiveState>(`/fights/${f.id}/live`),
  });
  const pr = f.pricing;
  const miss = f.pick ? null : nearMiss(f);
  const youth = pr && Math.abs(pr.youth_delta) >= 0.0005 ? pr.youth_delta : null;
  return (
    <Link to={`/fight/${f.id}`} className="bout">
      <div className="bout-names">
        <span className="name">{f.a}</span><PriceCell f={f} side={0} />
        <span className="name b">{f.b}</span><PriceCell f={f} side={1} />
      </div>
      <div className="bout-foot">
        <RoundStrip rounds={f.rounds} scored={live.data?.rounds ?? []} a={f.a} b={f.b} />
        <span className="figs fig">
          <span>Market <b>{pct(f.p_market)}</b></span>
          {youth != null && <span>Youth <b>{youth > 0 ? "+" : "−"}{Math.abs(youth * 100).toFixed(1)} pts</b></span>}
          {f.live && <span>Live <b>{pct(f.live.p_live)}</b></span>}
        </span>
      </div>
      <div className="bout-foot">
        {f.pick ? (
          <span className="value fig">{surname(f.pick.side === 0 ? f.a : f.b)} {american(f.pick.decimal)} · {pts(f.pick.edge)}</span>
        ) : miss && pr ? (
          <span className="meta fig">Closest: {surname(miss.side === 0 ? f.a : f.b)} {pts(miss.edge)} of {pts(pr.threshold)}</span>
        ) : (
          <span className="meta">{f.p_market == null ? "No market price yet" : ""}</span>
        )}
        <span className="meta">
          {f.status === "live" ? <span className="live-dot">LIVE</span>
            : f.status === "finished" ? `Final${f.winner != null ? ` · ${surname(f.winner === 0 ? f.a : f.b)} won` : ""}`
            : `${f.weightclass}${f.title ? " · title" : ""} · ${f.rounds} rds`}
        </span>
      </div>
      {(f.prop_flags ?? []).map((p) => (
        <p key={`${p.point}${p.side}`} className="prop-flag fig">
          Prop: {p.side} {p.point} rounds {american(p.decimal)} @ {p.book} · {pts(p.edge)}
        </p>
      ))}
    </Link>
  );
}

export function CardPage() {
  const { settings, event, error } = useActiveEvent();
  const qc = useQueryClient();
  const refresh = useMutation({
    mutationFn: () => post(`/events/${settings.data!.active_card}/refresh`, {}),
    onSuccess: () => setTimeout(() => qc.invalidateQueries({ queryKey: ["event"] }), 8000),
  });
  const slug = settings.data?.active_card;
  const parlays = useQuery({ queryKey: ["parlays", slug], enabled: !!slug, queryFn: () => api<Parlay[]>(`/events/${slug}/parlays`) });
  if (error) return <main><h1>Octagon Edge</h1><ErrorNote error={error} /></main>;
  if (!event.data) return <main><h1>Octagon Edge</h1><Loading /></main>;
  const ev = event.data;
  const home = ev.home_book || "your book";
  const picks = ev.fights.filter((f) => f.pick);
  const propFlags = ev.fights.flatMap((f) => (f.prop_flags ?? []).map((p) => ({ f, p })));
  const updated = ev.fights.map((f) => f.prediction_ts).filter(Boolean).sort().at(-1);
  return (
    <main>
      <p className="meta">{new Date(ev.date + "T12:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })} · {ev.venue}</p>
      <h1>{ev.name}</h1>
      <p className="meta">Updated {ago(updated)} · prices are the best at the books you use; "bet" is the worst price still worth taking at {home}</p>

      <section className="sheet" aria-labelledby="value-h" style={{ marginTop: 16 }}>
        <div className="sheet-head"><h2 id="value-h">Worth a bet now</h2><span className="meta fig">{picks.length + propFlags.length}</span></div>
        {picks.length || propFlags.length ? (
          <ul className="list">
            {picks.map((f) => {
              const side = f.pick!.side;
              const s = f.sides?.[side];
              const betAt = f.pricing?.bet_at_dec[side];
              return (
                <li key={f.id}>
                  <Link to={`/fight/${f.id}`} style={{ textDecoration: "none" }}>
                    <span className="value fig">{side === 0 ? f.a : f.b} {american(f.pick!.decimal)} @ {f.pick!.book}</span>
                    <p className="meta fig" style={{ margin: "4px 0 0" }}>
                      {s?.source === "line shopping" ? "Line shopping: this book is off the others" : s?.source === "youth" ? "Youth: the younger fighter is underpriced" : "Model"}
                      {" · "}edge {pts(f.pick!.edge)} · stake up to {Math.round(f.pick!.stake)}{f.confidence === "low" ? " · low confidence" : ""}
                    </p>
                    <p className="meta fig" style={{ margin: "2px 0 0" }}>
                      At {home}: {orBetter(betAt)} · prices {ago(f.prices_ts)}
                    </p>
                  </Link>
                </li>
              );
            })}
            {propFlags.map(({ f, p }) => (
              <li key={`${f.id}-${p.point}-${p.side}`}>
                <Link to={`/fight/${f.id}`} style={{ textDecoration: "none" }}>
                  <span className="value fig">{surname(f.a)}–{surname(f.b)}: {p.side} {p.point} rounds {american(p.decimal)} @ {p.book}</span>
                  <p className="meta fig" style={{ margin: "4px 0 0" }}>Prop line shopping · edge {pts(p.edge)} over the other books' consensus</p>
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <p className="pad meta" style={{ margin: 0 }}>Nothing yet. No price at your books beats our estimate by the edge threshold, and passing is the right call. Each fight shows the price worth taking at {home}.</p>
        )}
      </section>

      {SEGMENTS.map(([seg, label]) => {
        const fights = ev.fights.filter((f) => f.segment === seg);
        return fights.length ? (
          <section key={seg} className="sheet" aria-label={label}>
            <div className="sheet-head"><h2>{label}</h2><span className="meta fig">{fights.length} fights</span></div>
            {fights.map((f) => <Bout key={f.id} f={f} />)}
          </section>
        ) : null;
      })}

      <section className="sheet" style={{ marginTop: 12 }}>
        <Link to="/parlays" className="bout" style={{ borderTop: 0 }}>
          <div className="bout-foot" style={{ marginTop: 0 }}>
            <span><b>Parlays</b> <span className="meta">· entertainment, built only from the picks above</span></span>
            <span className="meta fig">{parlays.data ? `${parlays.data.length} combos` : ""} →</span>
          </div>
        </Link>
      </section>

      <h3>Data sources</h3>
      <ul className="meta" style={{ paddingLeft: 18, margin: 0 }}>
        {Object.entries(ev.sources).map(([k, v]) => (
          <li key={k}>{k}: <span className={v.startsWith("ok") ? "" : "error"}>{v}</span></li>
        ))}
        {!Object.keys(ev.sources).length && <li>No analysis run yet.</li>}
      </ul>
      <button className="btn block" style={{ marginTop: 16 }} onClick={() => refresh.mutate()} disabled={refresh.isPending}>
        {refresh.isPending ? "Starting…" : refresh.isSuccess ? "Refreshing in the background" : "Refresh analysis now"}
      </button>
    </main>
  );
}
