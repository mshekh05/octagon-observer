import { Link } from "react-router-dom";
import { ApiError } from "../lib/api";

export function Loading({ rows = 3 }: { rows?: number }) {
  return <div aria-busy="true" aria-label="Loading">{Array.from({ length: rows }, (_, i) => <div key={i} className="skeleton" />)}</div>;
}

export function ErrorNote({ error }: { error: unknown }) {
  if (error instanceof ApiError && error.status === 401) {
    return <p className="notice">This server needs your app token. Add it on the <Link to="/settings">Settings</Link> tab.</p>;
  }
  const msg = error instanceof Error ? error.message : String(error);
  return <p className="notice error">Couldn't reach the server: {msg}. Check that the homelab is up and try again.</p>;
}
