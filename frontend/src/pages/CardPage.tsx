import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, post, type EventDetail, type FightSummary, type LiveState, type SettingsData } from "../lib/api";
import { ago, american, pct, pts, surname } from "../lib/format";
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

function Bout({ f }: { f: FightSummary }) {
  const live = useQuery({
    queryKey: ["live", f.id], enabled: f.status !== "scheduled",
    queryFn: () => api<LiveState>(`/fights/${f.id}/live`),
  });
  const p = f.live?.p_live ?? f.p_final;
  return (
    <Link to={`/fight/${f.id}`} className="bout">
      <div className="bout-names">
        <span className="name">{f.a}</span><span className="prob fig">{pct(p)}</span>
        <span className="name b">{f.b}</span><span className="prob fig muted">{pct(p == null ? null : 1 - p)}</span>
      </div>
      <div className="bout-foot">
        <RoundStrip rounds={f.rounds} scored={live.data?.rounds ?? []} a={f.a} b={f.b} />
        <span className="figs fig">
          <span>Model <b>{pct(f.p_model)}</b></span>
          <span>Market <b>{pct(f.p_market)}</b></span>
          {f.live && <span>Live <b>{pct(f.live.p_live)}</b></span>}
        </span>
      </div>
      <div className="bout-foot">
        {f.pick ? (
          <span className="value fig">{surname(f.pick.side === 0 ? f.a : f.b)} {american(f.pick.decimal)} · {pts(f.pick.edge)}</span>
        ) : (
          <span className="meta">{f.p_market == null ? "No market price yet" : "No bet"}</span>
        )}
        <span className="meta">
          {f.status === "live" ? <span className="live-dot">LIVE</span>
            : f.status === "finished" ? `Final${f.winner != null ? ` · ${surname(f.winner === 0 ? f.a : f.b)} won` : ""}`
            : `${f.weightclass}${f.title ? " · title" : ""} · ${f.rounds} rds`}
        </span>
      </div>
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
  if (error) return <main><h1>Octagon Edge</h1><ErrorNote error={error} /></main>;
  if (!event.data) return <main><h1>Octagon Edge</h1><Loading /></main>;
  const ev = event.data;
  const picks = ev.fights.filter((f) => f.pick);
  const updated = ev.fights.map((f) => f.prediction_ts).filter(Boolean).sort().at(-1);
  return (
    <main>
      <p className="meta">{new Date(ev.date + "T12:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })} · {ev.venue}</p>
      <h1>{ev.name}</h1>
      <p className="meta">Updated {ago(updated)}</p>

      <section className="sheet" aria-labelledby="value-h" style={{ marginTop: 16 }}>
        <div className="sheet-head"><h2 id="value-h">Worth a bet now</h2><span className="meta fig">{picks.length}</span></div>
        {picks.length ? (
          <ul className="list">
            {picks.map((f) => (
              <li key={f.id}>
                <Link to={`/fight/${f.id}`} style={{ textDecoration: "none" }}>
                  <span className="value fig">{f.pick!.side === 0 ? f.a : f.b} {american(f.pick!.decimal)} @ {f.pick!.book}</span>
                  <p className="meta fig" style={{ margin: "4px 0 0" }}>Edge {pts(f.pick!.edge)} · stake up to {Math.round(f.pick!.stake)}{f.confidence === "low" ? " · low confidence" : ""}</p>
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <p className="pad meta" style={{ margin: 0 }}>Nothing yet. No sportsbook price beats our estimate by the edge threshold, and passing is the right call.</p>
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
