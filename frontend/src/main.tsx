import { StrictMode, useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes, useNavigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "./styles.css";
import type { Alert } from "./lib/api";
import { useLiveStream } from "./lib/stream";
import { TabBar } from "./components/TabBar";
import { CardPage } from "./pages/CardPage";
import { FightPage } from "./pages/FightPage";
import { LivePicker, LiveScorer } from "./pages/LivePage";
import { BetsPage } from "./pages/BetsPage";
import { SettingsPage } from "./pages/SettingsPage";

const qc = new QueryClient({ defaultOptions: { queries: { staleTime: 10_000, retry: 1 } } });

function Shell() {
  const [toast, setToast] = useState<Alert | null>(null);
  const nav = useNavigate();
  const onAlert = useCallback((a: Alert) => setToast(a), []);
  useLiveStream(onAlert);
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 8000);
    return () => clearTimeout(t);
  }, [toast]);
  return (
    <div className="app">
      <Routes>
        <Route path="/" element={<CardPage />} />
        <Route path="/fight/:id" element={<FightPage />} />
        <Route path="/fight/:id/live" element={<LiveScorer />} />
        <Route path="/live" element={<LivePicker />} />
        <Route path="/bets" element={<BetsPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<main><h1>Not found</h1><p className="meta">That page doesn't exist.</p></main>} />
      </Routes>
      {toast && (
        <button className="toast" role="status" onClick={() => { if (toast.fight_id) nav(`/fight/${toast.fight_id}`); setToast(null); }}>
          <b>{toast.title}</b><span className="meta" style={{ color: "inherit" }}>{toast.body.split("\n")[0]}</span>
        </button>
      )}
      <TabBar />
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={qc}>
      <BrowserRouter><Shell /></BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
