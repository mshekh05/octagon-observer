import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, getToken, post, setToken, type SettingsData } from "../lib/api";
import { enablePush, pushState, type PushState } from "../lib/push";
import { ErrorNote } from "./common";

const FIELDS: [keyof SettingsData["tuning"], string, string, number][] = [
  ["bankroll", "Bankroll", "Total you set aside for betting", 1],
  ["edge_prefight", "Pre-fight edge threshold (%)", "Alert when our probability beats the price by this much", 100],
  ["edge_live", "Live edge threshold (%)", "Higher, because live prices move fast", 100],
  ["kelly_fraction", "Kelly fraction (%)", "Share of the full Kelly stake to suggest", 100],
  ["max_stake_pct", "Max stake (% of bankroll)", "Hard cap per bet", 100],
  ["model_weight", "Stats model weight (%)", "The rest comes from the market, which is usually sharper", 100],
];

export function SettingsPage() {
  const qc = useQueryClient();
  const [token, setTok] = useState(getToken());
  const q = useQuery({ queryKey: ["settings"], queryFn: () => api<SettingsData>("/settings"), retry: false });
  const [form, setForm] = useState<Record<string, string>>({});
  const [push, setPush] = useState<PushState | "busy">("off");
  useEffect(() => { pushState().then(setPush); }, []);
  useEffect(() => {
    if (q.data) setForm(Object.fromEntries(FIELDS.map(([k, , , m]) => [k, String(+(q.data.tuning[k] * m).toFixed(2))])));
  }, [q.data]);
  const save = useMutation({
    mutationFn: () => api<SettingsData>("/settings", { method: "PUT", body: JSON.stringify(
      Object.fromEntries(FIELDS.map(([k, , , m]) => [k, Number(form[k]) / m]))) }),
    onSuccess: (d) => qc.setQueryData(["settings"], d),
  });
  const test = useMutation({ mutationFn: () => post<{ delivered: string[] }>("/push/test", {}) });

  return (
    <main>
      <h1>Settings</h1>
      <h3>Server access</h3>
      <form className="sheet pad stack" onSubmit={(e) => { e.preventDefault(); setToken(token.trim()); qc.invalidateQueries(); }}>
        <label className="field">App token (APP_TOKEN from the server's .env)
          <input className="in" type="password" autoComplete="off" value={token} onChange={(e) => setTok(e.target.value)} placeholder="Paste token" />
        </label>
        <button className="btn block">Save token</button>
        {q.error ? <ErrorNote error={q.error} /> : q.data && <p className="meta" style={{ margin: 0 }}>Connected. Odds API {q.data.odds_api ? "on" : "off (no key)"} · analyst {q.data.llm_provider} · card {q.data.active_card}</p>}
      </form>

      <h3>Alerts on this phone</h3>
      <section className="sheet pad stack">
        {push === "unsupported" && <p className="meta">This browser can't receive push alerts. On Android, open the site in Chrome and use "Install app".</p>}
        {push === "denied" && <p className="meta">Notifications are blocked. Allow them in Android settings → Apps → Octagon Edge → Notifications.</p>}
        {(push === "off" || push === "busy") && q.data && (
          <button className="btn primary block" disabled={push === "busy"} onClick={async () => {
            setPush("busy");
            try { setPush(await enablePush(q.data!.vapid_public_key)); qc.invalidateQueries({ queryKey: ["settings"] }); } catch { setPush("off"); }
          }}>Turn on push alerts</button>
        )}
        {push === "on" && <p style={{ margin: 0 }}>Push alerts are on for this phone.</p>}
        <p className="meta" style={{ margin: 0 }}>Channels: {q.data?.channels.join(", ") || "none yet"}</p>
        <button className="btn block" onClick={() => test.mutate()} disabled={test.isPending || !q.data}>Send a test alert</button>
        {test.data && <p className="meta" style={{ margin: 0 }}>Delivered via: {test.data.delivered.join(", ") || "nothing. Check the server's channel settings"}</p>}
      </section>

      <h3>Betting rules</h3>
      {q.data && (
        <form className="sheet pad stack" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
          {FIELDS.map(([k, label, help]) => (
            <label key={k} className="field">{label}
              <input className="in fig" inputMode="decimal" value={form[k] ?? ""} onChange={(e) => setForm({ ...form, [k]: e.target.value })} />
              <span className="muted">{help}</span>
            </label>
          ))}
          <button className="btn block" disabled={save.isPending}>{save.isSuccess ? "Saved" : "Save rules"}</button>
          {save.error && <p className="error">{(save.error as Error).message}</p>}
        </form>
      )}
      <p className="meta" style={{ marginTop: 16 }}>Bet only what you can afford to lose. Suggested stakes are upper bounds, not advice.</p>
    </main>
  );
}
