import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

interface Photo { name: string; url: string | null; credit: string | null }

export function usePhoto(name: string) {
  return useQuery({
    queryKey: ["photo", name], staleTime: 24 * 3600_000, retry: false,
    queryFn: () => api<Photo>(`/fighters/image?name=${encodeURIComponent(name)}`),
  });
}

const initials = (name: string) => name.split(" ").filter(Boolean).map((w) => w[0]).slice(0, 2).join("").toUpperCase();

/** A fighter's free photo (Wikimedia Commons), or their initials when there isn't one. */
export function FighterPhoto({ name, size = 72 }: { name: string; size?: number }) {
  const q = usePhoto(name);
  const [broken, setBroken] = useState(false);
  const url = q.data?.url && !broken ? q.data.url : null;
  return (
    <span className="photo" style={{ width: size, height: size, fontSize: size * 0.32 }}>
      {url ? <img src={url} alt={name} loading="lazy" onError={() => setBroken(true)} /> : <span aria-label={name}>{initials(name)}</span>}
    </span>
  );
}
