import type { RoundRow } from "../lib/api";

interface Props {
  rounds: number;
  scored: RoundRow[];
  a: string;
  b: string;
  big?: boolean;
  selected?: number | null;
  onPick?: (round: number) => void;
}

const initial = (name: string) => name.split(" ").slice(-1)[0].slice(0, 1).toUpperCase();

/** The scorecard strip: one ruled box per round, filled with the round winner's initial. */
export function RoundStrip({ rounds, scored, a, b, big, selected, onPick }: Props) {
  const byRound = new Map(scored.map((r) => [r.round, r]));
  return (
    <div className={`strip${big ? " big" : ""}`} style={{ ["--n" as string]: rounds }} role={onPick ? "group" : "img"}
      aria-label={`Rounds scored: ${scored.length} of ${rounds}`}>
      {Array.from({ length: rounds }, (_, i) => {
        const n = i + 1;
        const r = byRound.get(n);
        const cls = !r ? "" : r.winner === 0 ? "a" : r.winner === 1 ? "b" : "even";
        const label = !r ? `R${n}` : r.winner === 0 ? initial(a) : r.winner === 1 ? initial(b) : "=";
        const kd = r ? r.kd_a + r.kd_b : 0;
        const desc = !r ? `Round ${n}, not scored` : `Round ${n}: ${r.winner === 0 ? a : r.winner === 1 ? b : "even"}`;
        const inner = (
          <>
            {big && <small>R{n}</small>}
            {big && !r ? "+" : label}
            {kd > 0 && <span className="kd">KD{kd > 1 ? `×${kd}` : ""}</span>}
          </>
        );
        return onPick ? (
          <button key={n} type="button" className={`rb ${cls}${selected === n ? " sel" : ""}`} onClick={() => onPick(n)}
            aria-label={desc} aria-pressed={selected === n}>{inner}</button>
        ) : (
          <span key={n} className={`rb ${cls}`} title={desc}>{inner}</span>
        );
      })}
    </div>
  );
}
