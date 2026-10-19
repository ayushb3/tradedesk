import { Link, NavLink, useLocation } from "react-router-dom";
import { BookOpen, CalendarDays, CandlestickChart, Settings } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * The three jobs: review a day (Days), replay a trade (Replay), learn (Learn).
 * `prefixes` keeps an item lit on its sub-pages (a trade review is part of Days).
 * Progress, Scenarios and Concepts are hidden until they have content; their
 * routes still work.
 */
const NAV_ITEMS = [
  { to: "/journal", label: "Days", icon: CalendarDays, prefixes: ["/journal", "/trades", "/training/leaks"] },
  { to: "/replay", label: "Replay", icon: CandlestickChart, prefixes: ["/replay"] },
  { to: "/knowledge/modules", label: "Learn", icon: BookOpen, prefixes: ["/knowledge"] },
] as const;

function NavItem({
  to,
  label,
  icon: Icon,
  active,
}: {
  to: string;
  label: string;
  icon: React.ElementType;
  active: boolean;
}) {
  return (
    <NavLink
      to={to}
      aria-current={active ? "page" : undefined}
      className={cn(
        "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
        active
          ? "bg-accent text-accent-foreground"
          : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
      )}
    >
      <Icon className="h-4 w-4" />
      {label}
    </NavLink>
  );
}

export function SideNav() {
  const { pathname } = useLocation();
  const onRules = pathname.startsWith("/settings");
  return (
    <aside className="flex h-full w-56 shrink-0 flex-col border-r border-border bg-card">
      <div className="flex h-14 items-center gap-2 border-b border-border px-4">
        <CandlestickChart className="h-5 w-5 text-primary" />
        <span className="font-semibold tracking-tight">TradeDesk</span>
      </div>
      <nav className="flex flex-col gap-1 p-2">
        {NAV_ITEMS.map(({ to, label, icon, prefixes }) => (
          <NavItem
            key={to}
            to={to}
            label={label}
            icon={icon}
            active={prefixes.some((p) => pathname === p || pathname.startsWith(`${p}/`))}
          />
        ))}
      </nav>
      <div className="mt-auto p-2">
        <Link
          to="/settings/rules"
          aria-label="Rules settings"
          title="Rules settings"
          className={cn(
            "flex items-center gap-2 rounded-md px-3 py-2 text-label transition-colors",
            onRules
              ? "bg-accent text-accent-foreground"
              : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
          )}
        >
          <Settings className="h-4 w-4" />
          Rules
        </Link>
      </div>
    </aside>
  );
}
