import { NavLink } from "react-router-dom";

import { cn } from "@/lib/utils";
import { useKnowledgeConcepts } from "@/lib/queries";

/**
 * Sub-navigation inside Learn: Modules and Search always; Concepts only once
 * the concept index has content (hidden-until-content, routes stay reachable).
 */
export function LearnTabs() {
  const concepts = useKnowledgeConcepts();
  const showConcepts = Boolean(concepts.data?.available && concepts.data.concept_count > 0);
  const tabs = [
    { to: "/knowledge/modules", label: "Modules" },
    { to: "/knowledge/search", label: "Search" },
    ...(showConcepts ? [{ to: "/knowledge/concepts", label: "Concepts" }] : []),
  ];
  return (
    <nav className="flex items-center gap-1" aria-label="Learn sections">
      {tabs.map((t) => (
        <NavLink
          key={t.to}
          to={t.to}
          className={({ isActive }) =>
            cn(
              "rounded-md px-2.5 py-1 text-body font-medium transition-colors",
              isActive ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:text-foreground",
            )
          }
        >
          {t.label}
        </NavLink>
      ))}
    </nav>
  );
}
