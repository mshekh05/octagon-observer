interface Point { t: number; p: number }
export interface Line { label: string; points: Point[] }

/** Probability-over-time chart for fighter A (0–100%). Lines are told apart by dash pattern, not colour alone. */
export function ProbChart({ lines, height = 160, xLabel }: { lines: Line[]; height?: number; xLabel?: (t: number) => string }) {
  const W = 600, H = height, L = 34, R = 10, T = 10, B = 22;
  const all = lines.flatMap((l) => l.points);
  if (all.length < 2) return <p className="notice" style={{ margin: 14 }}>Not enough price history yet. The chart fills in as the worker polls prices.</p>;
  const t0 = Math.min(...all.map((p) => p.t)), t1 = Math.max(...all.map((p) => p.t));
  const x = (t: number) => L + ((t - t0) / Math.max(t1 - t0, 1)) * (W - L - R);
  const y = (p: number) => T + (1 - p) * (H - T - B);
  const path = (pts: Point[]) => pts.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.p).toFixed(1)}`).join("");
  const fmt = xLabel ?? ((t: number) => new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));
  return (
    <>
      <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Win probability over time">
        {[0, 0.25, 0.5, 0.75, 1].map((g) => (
          <g key={g}>
            <line className="grid" x1={L} x2={W - R} y1={y(g)} y2={y(g)} />
            <text className="axis" x={L - 6} y={y(g) + 4} textAnchor="end">{g * 100}</text>
          </g>
        ))}
        <text className="axis" x={L} y={H - 4}>{fmt(t0)}</text>
        <text className="axis" x={W - R} y={H - 4} textAnchor="end">{fmt(t1)}</text>
        {lines.map((l, i) => l.points.length > 1 && <path key={l.label} className={`s${i}`} d={path(l.points)} />)}
        {lines.map((l, i) => l.points.length === 1 && (
          <circle key={l.label} cx={x(l.points[0].t)} cy={y(l.points[0].p)} r={3} className={`s${i}`} />
        ))}
      </svg>
      <div className="legend">{lines.filter((l) => l.points.length).map((l, i) => <span key={l.label}><i className={`s${i}`} />{l.label}</span>)}</div>
    </>
  );
}
