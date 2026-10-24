import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type BetRow, type BetSummary, type PaperRow } from "../lib/api";
import { american, money, pct } from "../lib/format";
import { ErrorNote, Loading } from "./common";

export function BetsPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["bets"], queryFn: () => api<{ summary: BetSummary; bets: BetRow[]; paper?: PaperRow[] }>("/bets") });
  const patch = useMutation({
    mutationFn: ({ id, result }: { id: number; result: string }) => api(`/bets/${id}`, { method: "PATCH", body: JSON.stringify({ result }) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["bets"] }),
  });
  if (q.error) return <main><h1>Bets</h1><ErrorNote error={q.error} /></main>;
  if (!q.data) return <main><h1>Bets</h1><Loading /></main>;
  const { summary: s, bets } = q.data;
  const paper = q.data.paper ?? [];
  const signed = (n: number | null) => (n == null ? "–" : `${n > 0 ? "+" : n < 0 ? "−" : ""}${Math.abs(n * 100).toFixed(1)}%`);
  return (
    <main>
      <h1>Bets</h1>
      <section className="sheet" style={{ marginTop: 12 }}>
        <div className="decision">
          <div><div className="who">Profit / loss</div><div className={`big fig ${s.pnl > 0 ? "pos" : s.pnl < 0 ? "neg" : ""}`}>{s.pnl > 0 ? "+" : ""}{money(s.pnl)}</div></div>
          <div className="right"><div className="who">Avg CLV</div><div className="big fig">{signed(s.avg_clv)}</div></div>
        </div>
        <table className="fig"><tbody>
          <tr><td>Settled</td><td>{s.settled} ({s.won} won)</td></tr>
          <tr><td>Staked</td><td>{money(s.staked)}</td></tr>
          <tr><td>ROI</td><td>{signed(s.roi)}</td></tr>
          <tr><td>Open</td><td>{s.open}</td></tr>
        </tbody></table>
        {s.settled > 0 && s.settled < 50 && (
          <p className="flag-note">{s.settled} settled bet{s.settled === 1 ? "" : "s"}: far too few for profit or ROI to mean anything yet. Watch CLV instead.</p>
        )}
        <p className="pad meta" style={{ margin: 0 }}>Closing-line value tells you more than P&amp;L over a few events: beating the closing price consistently is the sign of a real edge.</p>
      </section>

      <h3>Every pick, bet or not</h3>
      <section className="sheet">
        {paper.length ? (
          <table className="fig"><thead><tr><th>Edge from</th><th>Picks</th><th>Avg CLV</th><th>Beat close</th><th>Flat ROI</th></tr></thead>
            <tbody>{paper.map((r) => (
              <tr key={r.source}>
                <td>{r.source}</td><td>{r.picks}</td>
                <td className={r.avg_clv == null ? "" : r.avg_clv > 0 ? "pos" : "neg"}>{signed(r.avg_clv)}</td>
                <td>{r.beat_close == null ? "–" : pct(r.beat_close, 0)}</td>
                <td>{signed(r.flat_roi)} <span className="meta">({r.settled})</span></td>
              </tr>
            ))}</tbody></table>
        ) : <p className="pad meta" style={{ margin: 0 }}>No picks recorded yet.</p>}
        <p className="pad meta" style={{ margin: 0 }}>Every pick and value alert is scored against the de-vigged closing price. After about 50 picks this shows whether the edge from line shopping or from the model is real.</p>
      </section>

      <h3>History</h3>
      {bets.length ? (
        <section className="sheet">
          <ul className="list">
            {bets.map((b) => (
              <li key={b.id}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                  <span className="name" style={{ fontSize: "var(--t-sm)" }}>{b.pick} {american(b.decimal)}</span>
                  <span className={`fig ${b.pnl == null ? "muted" : b.pnl > 0 ? "pos" : b.pnl < 0 ? "neg" : ""}`}>
                    {b.result === "open" ? `stake ${money(b.stake)}` : `${b.result} ${b.pnl! > 0 ? "+" : ""}${money(b.pnl)}`}</span>
                </div>
                <p className="meta fig" style={{ margin: "2px 0 0" }}>
                  {b.fight} · {b.book || "book?"}{b.live ? " · live" : ""} · close {american(b.closing_decimal)} · CLV {b.clv == null ? "–" : pct(b.clv, 1)}
                </p>
                {b.result === "open" && (
                  <div className="row" style={{ marginTop: 8 }}>
                    {["won", "lost", "void"].map((r) => (
                      <button key={r} className="btn" style={{ minHeight: 40 }} onClick={() => patch.mutate({ id: b.id, result: r })}>Mark {r}</button>
                    ))}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </section>
      ) : <p className="notice">No bets logged yet. Open a fight and use "Log a bet" after you place one with your book.</p>}
    </main>
  );
}
