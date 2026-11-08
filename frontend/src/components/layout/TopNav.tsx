import { NavLink, useLocation } from "react-router-dom";
import { BookOpen, CalendarDays, CandlestickChart, Settings } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * Slim top bar: the app name, the two jobs as tabs (review a day, learn) and the rules
 * gear. Replay is an action on a trade or a day, so the player (/replay?…) keeps Days lit.
 * `prefixes` keeps a tab lit on its sub-pages. Fits on one line at phone width: the name
 * and the gear's label drop below `sm`.
 */
const NAV_ITEMS = [
  { to: "/days", label: "Days", icon: CalendarDays, prefixes: ["/days", "/trades", "/replay"] },
  { to: "/learn", label: "Learn", icon: BookOpen, prefixes: ["/learn", "/knowledge"] },
] as const;

const focusRing =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background";

const isOn = (pathname: string, prefixes: readonly string[]) =>
  prefixes.some((p) => pathname === p || pathname.startsWith(`${p}/`));

export function TopNav() {
  const { pathname } = useLocation();
  const onRules = pathname.startsWith("/settings");
  return (
    <header className="flex h-11 shrink-0 items-center gap-1 border-b border-border bg-card px-2 sm:gap-3 sm:px-4">
      <NavLink
        to="/days"
        aria-label="TradeDesk home"
        className={cn("flex shrink-0 items-center gap-2 rounded-md px-1.5 py-1", focusRing)}
      >
        <CandlestickChart className="h-5 w-5 text-primary" />
        <span className="hidden font-semibold tracking-tight sm:inline">TradeDesk</span>
      </NavLink>

      <nav aria-label="Main" className="flex min-w-0 items-center gap-1">
        {NAV_ITEMS.map(({ to, label, icon: Icon, prefixes }) => {
          const active = isOn(pathname, prefixes);
          return (
            <NavLink
              key={to}
              to={to}
              aria-current={active ? "page" : undefined}
              className={cn(
                "flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm font-medium transition-colors",
                focusRing,
                active
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
              )}
            >
              <Icon className="h-4 w-4" />
              {label}
            </NavLink>
          );
        })}
      </nav>

      <NavLink
        to="/settings/rules"
        aria-label="Rules settings"
        title="Rules settings"
        aria-current={onRules ? "page" : undefined}
        className={cn(
          "ml-auto flex shrink-0 items-center gap-1.5 rounded-md px-2 py-1.5 text-label transition-colors",
          focusRing,
          onRules
            ? "bg-accent text-accent-foreground"
            : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
        )}
      >
        <Settings className="h-4 w-4" />
        <span className="hidden sm:inline">Rules</span>
      </NavLink>
    </header>
  );
}
