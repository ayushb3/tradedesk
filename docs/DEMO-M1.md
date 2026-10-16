# DEMO — M1: Skeleton runs

**Goal:** backend + frontend boot together; the mock provider serves candles; the chart renders AAPL 1-min with EMAs.

## One-time setup
```bash
make install        # backend venv + frontend node_modules
```

## Run
```bash
make dev            # backend :9000, frontend :5173 (Ctrl-C stops both)
```

## Verify (click-path)
1. Open **http://localhost:5173** — dark UI, left nav (Replay / Journal / Daily Review).
2. **Replay** page loads by default with a candlestick chart of **AAPL, 2026-07-01**.
3. You should see:
   - ~960 one-minute candles (extended hours 04:00–20:00 ET),
   - **EMA9** (orange) and **EMA21** (blue) overlays, legend top-left, "960 bars".
4. Change **SYMBOL** to RIVN or HOOD → chart reloads with that ticker's deterministic candles.
5. Change **DATE** → different (still deterministic) session.
6. Sanity on the API directly:
   ```bash
   curl "http://localhost:9000/api/health"
   curl "http://localhost:9000/api/candles?symbol=AAPL&date=2026-07-01" | head -c 200
   ```

## What's mocked / not yet real
- Candles are **deterministic synthetic** data (no Webull needed). Same symbol+date ⇒ identical bars.
- EMAs are computed **client-side** in M1 as a placeholder — M2 replaces them with server-authoritative *causal* series (no lookahead).
- Replay controls (play/pause/step/speed) are visible but disabled — wired in M2.
- Journal / Daily Review are "coming soon" shells — built in M3.

## Notes
- Chart times currently display in UTC; M2 switches the axis to ET (America/New_York).
