import { NavLink } from "react-router-dom";
import {
  AlertTriangle,
  CandlestickChart,
  BookOpen,
  CalendarCheck,
  GraduationCap,
  BookMarked,
  Library,
  Network,
  Search,
  TrendingUp,
} from "lucide-react";

import { cn } from "@/lib/utils";

const MAIN_NAV_ITEMS = [
  { to: "/replay", label: "Replay", icon: CandlestickChart },
  { to: "/journal", label: "Journal", icon: BookOpen },
  { to: "/daily-review", label: "Daily Review", icon: CalendarCheck },
] as const;

// M6 training — Progress (6.4.2) + Scenarios/Drills unified (6.1.2+6.1.3) + Leaks (6.3.4)
const TRAINING_NAV_ITEMS = [
  { to: "/training/progress", label: "Progress", icon: TrendingUp },
  { to: "/training/scenarios", label: "Scenarios", icon: BookMarked },
  { to: "/training/leaks", label: "Leaks", icon: AlertTriangle },
] as const;

// M8a — Knowledge section (course curriculum). Concepts view lands in M8c.
const KNOWLEDGE_NAV_ITEMS = [
  { to: "/knowledge/modules", label: "Modules", icon: BookMarked },
  { to: "/knowledge/concepts", label: "Concepts", icon: Network },
  { to: "/knowledge/search", label: "Search", icon: Search },
] as const;

function NavItem({
  to,
  label,
  icon: Icon,
}: {
  to: string;
  label: string;
  icon: React.ElementType;
}) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        cn(
          "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
          isActive
            ? "bg-accent text-accent-foreground"
            : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
        )
      }
    >
      <Icon className="h-4 w-4" />
      {label}
    </NavLink>
  );
}

export function SideNav() {
  return (
    <aside className="flex h-full w-56 shrink-0 flex-col border-r border-border bg-card">
      <div className="flex h-14 items-center gap-2 border-b border-border px-4">
        <CandlestickChart className="h-5 w-5 text-primary" />
        <span className="font-semibold tracking-tight">TradeDesk</span>
      </div>
      <nav className="flex flex-col gap-1 p-2">
        {MAIN_NAV_ITEMS.map(({ to, label, icon }) => (
          <NavItem key={to} to={to} label={label} icon={icon} />
        ))}

        {/* Training section — M6 */}
        <div className="mt-3 flex items-center gap-1.5 px-3 pb-1">
          <GraduationCap className="h-3.5 w-3.5 text-muted-foreground/60" />
          <span className="text-label font-semibold uppercase tracking-widest text-muted-foreground/60">
            Training
          </span>
        </div>
        {TRAINING_NAV_ITEMS.map(({ to, label, icon }) => (
          <NavItem key={to} to={to} label={label} icon={icon} />
        ))}

        {/* Knowledge section — M8 */}
        <div className="mt-3 flex items-center gap-1.5 px-3 pb-1">
          <Library className="h-3.5 w-3.5 text-muted-foreground/60" />
          <span className="text-label font-semibold uppercase tracking-widest text-muted-foreground/60">
            Knowledge
          </span>
        </div>
        {KNOWLEDGE_NAV_ITEMS.map(({ to, label, icon }) => (
          <NavItem key={to} to={to} label={label} icon={icon} />
        ))}
      </nav>
      <div className="mt-auto p-3 text-label text-muted-foreground">
        <span className="tabular">local · single-user</span>
      </div>
    </aside>
  );
}
