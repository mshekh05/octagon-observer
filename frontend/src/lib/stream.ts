import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { getToken, type Alert } from "./api";

export interface LiveTick {
  fight_id: number;
  p_live: number;
  after_round: number;
  ts: string;
}

/** Subscribes to the server's event stream and refreshes cached queries as alerts and live ticks arrive. */
export function useLiveStream(onAlert?: (a: Alert) => void) {
  const qc = useQueryClient();
  useEffect(() => {
    const token = getToken();
    const es = new EventSource(`/api/stream${token ? `?token=${encodeURIComponent(token)}` : ""}`);
    es.addEventListener("alert", (e) => {
      const a = JSON.parse((e as MessageEvent).data) as Alert;
      qc.invalidateQueries({ queryKey: ["alerts"] });
      if (a.fight_id) qc.invalidateQueries({ queryKey: ["fight", a.fight_id] });
      onAlert?.(a);
    });
    es.addEventListener("live", (e) => {
      const t = JSON.parse((e as MessageEvent).data) as LiveTick;
      qc.invalidateQueries({ queryKey: ["live", t.fight_id] });
      qc.invalidateQueries({ queryKey: ["event"] });
    });
    return () => es.close();
  }, [qc, onAlert]);
}
