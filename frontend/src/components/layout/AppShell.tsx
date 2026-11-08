import { Outlet } from "react-router-dom";

import { QuickSearch } from "@/components/knowledge/QuickSearch";
import { TopNav } from "./TopNav";

/** Top-level frame: slim top bar + routed content at full width. */
export function AppShell() {
  return (
    <div className="flex h-full w-full flex-col overflow-hidden">
      <TopNav />
      <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        <Outlet />
      </main>
      {/* Global ⌘K quick-search over the course curriculum. */}
      <QuickSearch />
    </div>
  );
}
