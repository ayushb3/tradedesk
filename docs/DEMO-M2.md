# DEMO — M2: Replay

**Goal:** full bar replay with speed controls; all levels/indicators drawn and recomputing per session; simulated trades marked during replay persist to the DB — with **no lookahead leakage**.

## Run
```bash
make dev            # backend :9000, frontend :5173
```

## Verify (click-path)
1. Open **http://localhost:5173** → **Replay** page, AAPL / 2026-07-01.
2. **Transport controls** (top-right): restart ⟲, step-back ◀, play ▶, step-forward ▶▶, and a speed selector (0.5× / 1× / 2× / 4× / Max).
3. Press **play** — candles reveal bar-by-bar; the legend shows the current **bar N / 960**, the **ET** clock, and live **EMA9/21/50/200, VWAP, RSI** values. Nothing to the right of the playhead is drawn (no future bars).
4. **Indicators:** EMA9/21/50/200 + VWAP overlay the price; **RSI(14)** is in a subchart below with 30/70 guides. (EMA200 and VWAP are intentionally blank early — EMA200 needs 200 bars; VWAP is null before the 09:30 ET anchor.)
5. **Levels (horizontal lines):** PDH/PDL (prior day), PMH/PML (premarket, freeze at 09:30), OR-high/OR-low (09:30–09:35), day-high/day-low (grow as replay advances). Change the **DATE** → levels recompute for that session.
6. **Simulated trade:** while replaying, in the right panel pick **Direction/Size**, click **Mark Entry** (captures current bar price/time), advance a few bars, click **Mark Exit**, optionally set a **Stop**, then **Log trade**. The trade appears in **Session trades** with net P&L and R. It's saved to SQLite (`simulated=true`).
7. Confirm persistence:
   ```bash
   curl "http://localhost:9000/api/sim/trades"
   ```

## No-lookahead guarantee
- **Server:** every indicator/level array is causal — value at bar `i` uses only bars `≤ i`; verified by `backend/tests/test_lookahead.py` (prefix-stability under truncation).
- **UI:** a single `currentIndex` gates all reads; overlays/levels/markers never index past it.

## Tests
```bash
make test           # 65 tests: indicators vs hand-computed fixtures, lookahead, levels, sim, API
```
