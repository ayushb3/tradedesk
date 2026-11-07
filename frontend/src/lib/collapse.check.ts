/** Checks for lib/collapse.ts: stored open state, defaults, and storage that throws. */
import { readOpen, writeOpen } from "./collapse";

let failed = 0;
function eq(name: string, got: unknown, want: unknown) {
  if (got === want) console.log(`ok   ${name}`);
  else {
    failed++;
    console.log(`FAIL ${name}: got ${String(got)}, want ${String(want)}`);
  }
}

const store = new Map<string, string>();
const g = globalThis as unknown as { window: { localStorage: unknown } };
g.window = {
  localStorage: {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
  },
};
eq("default when unset (open)", readOpen("a", true), true);
eq("default when unset (closed)", readOpen("a", false), false);
writeOpen("a", false);
eq("stored closed wins over default", readOpen("a", true), false);
writeOpen("a", true);
eq("stored open wins over default", readOpen("a", false), true);

g.window = {
  localStorage: {
    getItem: () => {
      throw new Error("blocked");
    },
    setItem: () => {
      throw new Error("blocked");
    },
  },
};
eq("blocked storage reads the default", readOpen("a", true), true);
writeOpen("a", false); // must not throw
eq("blocked storage write is a no-op", readOpen("a", false), false);

if (failed) {
  console.log(`collapse: ${failed} failed`);
  process.exit(1);
}
console.log("collapse: all checks passed");
