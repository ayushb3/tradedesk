import { Outlet } from "react-router-dom";

import { QuickSearch } from "@/components/knowledge/QuickSearch";
import { SideNav } from "./SideNav";

/** Top-level frame: fixed left nav + routed content area. */
export function AppShell() {
  return (
    <div className="flex h-full w-full overflow-hidden">
      <SideNav />
      <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <Outlet />
      </main>
      {/* Global ⌘K quick-search over the course curriculum. */}
      <QuickSearch />
    </div>
  );
}
