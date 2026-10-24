// Typed client for the Octagon Edge API. The app token lives in localStorage.
const TOKEN_KEY = "oe.token";

export function getToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* storage unavailable: token lasts for this page only */
  }
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init.headers,
    },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export const post = <T>(path: string, body: unknown) =>
  api<T>(path, { method: "POST", body: JSON.stringify(body) });

export interface Pick {
  side: 0 | 1;
  book: string;
  decimal: number;
  edge: number;
  stake: number;
}

export interface FightSummary {
  id: number;
  a: string;
  b: string;
  weightclass: string;
  segment: "main" | "prelims" | "early";
  rounds: number;
  title: boolean;
  status: "scheduled" | "live" | "finished";
  winner: 0 | 1 | null;
  order: number;
  record_a: string | null;
  record_b: string | null;
  p_model: number | null;
  p_market: number | null;
  p_final: number | null;
  market_source: string;
  confidence: string | null;
  pick: Pick | null;
  prediction_ts: string | null;
  prediction_markets: Record<string, { p_a: number; ts: string }>;
  live: { p_live: number; after_round: number; p_rounds: number | null; p_market: number | null } | null;
  sides?: Side[] | null;
  pricing?: Pricing | null;
  prices_ts?: string | null;
  targets?: [number | null, number | null];
  prop_flags?: PropFlag[];
}

/** How the engine priced a fight (Prediction.details.pricing). Index 0 = fighter A. */
export interface Pricing {
  p_market: number | null;
  market_source: string;
  model_weight: number;
  youth_delta: number;
  age_gap: number | null;
  fair_dec: [number, number];
  bet_at_dec: [number | null, number | null];
  threshold: number;
  home_book: string;
  arb: boolean;
}

export interface PropFlag {
  point: number;
  side: "over" | "under";
  book: string;
  decimal: number;
  edge: number;
}

export interface TotalsLine {
  point: number;
  books: number;
  fair_over: number;
  ts: string;
  prices: Record<string, [number, number]>;
  over: { book: string; decimal: number; edge: number } | null;
  under: { book: string; decimal: number; edge: number } | null;
}

export interface PropOutcome {
  outcome: string;
  label: string;
  p: number;
  source: string;
  p_kalshi: number | null;
  p_model: number | null;
  fair_dec: number | null;
  bet_at_dec: number | null;
}

export interface Props {
  distance: { p: number; ts: string } | null;
  method: { outcome: string; label: string; p: number }[];
  totals: TotalsLine[];
  threshold: number;
  flags: PropFlag[];
  outcomes: PropOutcome[];
}

export interface PlanBet {
  fight_id: number;
  fight: string;
  market: "moneyline" | "total_rounds" | "prop";
  outcome: string;
  label: string;
  p: number;
  decimal: number | null;
  book: string | null;
  bet_at: number | null;
  source: string;
  tier: "value" | "lean" | "check";
  edge: number | null;
  ev: number | null;
  stake: number;
}

export interface BetPlan {
  budget: number;
  spent: number;
  unspent: number;
  bets: PlanBet[];
  checks: PlanBet[];
  favourites: { fight_id: number; fight: string; label: string; p: number; decimal: number | null; book: string | null; edge: number | null; fair_dec: number }[];
  threshold: number;
  lean_edge: number;
  home_book: string;
}

export interface Scorecard {
  markets: { market: string; n: number; log_loss: number; brier: number; market_log_loss: number | null; model_log_loss: number | null }[];
  tiers: Record<string, { picks: number; settled: number; won: number; staked: number; profit: number; roi: number | null }>;
  events: { event: string; settled: number; staked: number; profit: number }[];
  settled_fights: number;
}

export interface Books {
  seen: string[];
  usable: string[];
  home: string;
  home_in_feed: boolean;
}

export interface EventDetail {
  slug: string;
  name: string;
  date: string;
  venue: string;
  note: string;
  sources: Record<string, string>;
  home_book?: string;
  fights: FightSummary[];
}

export interface Side {
  book: string | null;
  decimal: number | null;
  edge: number | null;
  ev: number | null;
  stake: number;
  stake_pct: number;
  market_edge?: number | null;
  source?: string;
}

export interface FightDetail extends FightSummary {
  event: string;
  details: {
    record_a?: string;
    record_b?: string;
    snap_a?: Record<string, number | null>;
    snap_b?: Record<string, number | null>;
    notes?: string[];
    llm?: { summary: string; key_factors: string[]; risks: string[]; news_adjustment_a: number; adjustment_reason: string } | null;
    books?: Record<string, [number, number]>;
    sides?: Side[];
    sources?: Record<string, string>;
    p_experts?: number | null;
    experts_source?: string;
  };
}

export interface Series {
  series: Record<string, { ts: string; p_a: number }[]>;
  books: Record<string, { a: number; b: number; ts: string }>;
}

export interface SocialSide {
  name: string;
  summary: { n: number; mean: number; positive_share: number | null };
  flags: string[];
  posts: { source: string; title: string; link: string; flags: string[]; sentiment: number | null; ts: string }[];
}

export interface RoundRow {
  round: number;
  winner: 0 | 1 | null;
  kd_a: number;
  kd_b: number;
  td_a: number;
  td_b: number;
  sig_a: number | null;
  sig_b: number | null;
  ctrl_a: number;
  ctrl_b: number;
}

export interface LiveState {
  status: FightSummary["status"];
  winner: 0 | 1 | null;
  rounds: RoundRow[];
  estimates: { ts: string; after_round: number; p_live: number; p_rounds: number | null; p_market: number | null }[];
}

export interface Alert {
  id: number;
  fight_id: number | null;
  ts: string;
  kind: string;
  title: string;
  body: string;
  urgent: boolean;
}

export interface BetRow {
  id: number;
  fight_id: number;
  fight: string;
  pick: string;
  market: "moneyline" | "prop" | "total_rounds";
  outcome: string;
  side: 0 | 1;
  book: string;
  decimal: number;
  stake: number;
  live: boolean;
  placed_at: string;
  closing_decimal: number | null;
  clv: number | null;
  result: "open" | "won" | "lost" | "void";
  pnl: number | null;
  note: string;
}

export interface BetSummary {
  bets: number;
  open: number;
  settled: number;
  won: number;
  staked: number;
  pnl: number;
  roi: number | null;
  avg_clv: number | null;
  clv_n: number;
}

export interface PaperRow {
  source: string;
  picks: number;
  clv_n: number;
  avg_clv: number | null;
  beat_close: number | null;
  settled: number;
  flat_roi: number | null;
}

export interface ParlayLeg {
  fight_id: number;
  fight: string;
  name: string;
  side: 0 | 1;
  p: number;
  decimal: number;
  book: string;
  single_edge: number;
}

export interface Parlay {
  decimal: number;
  american: number;
  p: number;
  edge: number;
  ev: number;
  stake: number;
  stake_pct: number;
  legs: ParlayLeg[];
}

export interface SettingsData {
  tuning: Record<"bankroll" | "kelly_fraction" | "max_stake_pct" | "edge_prefight" | "edge_live" | "model_weight" | "card_budget" | "lean_edge", number>;
  channels: string[];
  llm_provider: string;
  odds_api: boolean;
  vapid_public_key: string;
  active_card: string;
  home_book?: string;
}
