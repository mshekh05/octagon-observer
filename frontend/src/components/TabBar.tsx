import { NavLink } from "react-router-dom";

const icons = {
  card: <path d="M4 5h16v14H4zM4 10h16M9 10v9M14 10v9" />,
  live: <><circle cx="12" cy="12" r="3" /><path d="M5.6 5.6a9 9 0 0 0 0 12.8M18.4 5.6a9 9 0 0 1 0 12.8M8.5 8.5a4.5 4.5 0 0 0 0 7M15.5 8.5a4.5 4.5 0 0 1 0 7" /></>,
  bets: <path d="M5 4h14v16l-3-2-2 2-2-2-2 2-2-2-3 2zM9 9h6M9 13h6" />,
  parlays: <path d="M6 3v7a6 6 0 0 0 12 0V3M4 3h5M15 3h5M9 21h6M12 17v4" />,
  settings: <><circle cx="12" cy="12" r="3" /><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1" /></>,
};

const tabs: [string, string, keyof typeof icons][] = [
  ["/", "Card", "card"],
  ["/live", "Live", "live"],
  ["/parlays", "Parlays", "parlays"],
  ["/bets", "Bets", "bets"],
  ["/settings", "Settings", "settings"],
];

export function TabBar() {
  return (
    <nav className="tabs" aria-label="Main">
      {tabs.map(([to, label, icon]) => (
        <NavLink key={to} to={to} end={to === "/"} className={({ isActive }) => (isActive ? "active" : "")}>
          <svg viewBox="0 0 24 24" aria-hidden="true">{icons[icon]}</svg>
          {label}
        </NavLink>
      ))}
    </nav>
  );
}
