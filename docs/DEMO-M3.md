# DEMO — M3: Journal

**Goal:** import fills (mock) → round-trip trade construction → analytics dashboard → tagging + notes + grades → daily review.

## Run
```bash
make dev
```

## Verify (click-path)
1. Open **http://localhost:5173** → **Journal** (left nav).
2. Click **Import fills** (top-right). Toast reports `24 fills → 10 trades`. (Uses the mock provider; idempotent — re-import doesn't duplicate.)
3. **Stat cards:** Net P&L, Win rate, Profit factor, Avg R, Expectancy, Trades — plus avg win/loss/hold. P&L is green/red.
4. **P&L calendar:** each day tinted by net P&L (green/red intensity), shows pnl + trade count; prev/next month nav; month total in header. Click a day → Daily Review for that date.
5. **Breakdowns:** "By ticker" and "By time of day" bar lists (P&L, trade count, win rate).
6. **Trades table:** date / symbol / direction / qty / entry / exit / net P&L (colored) / R / grade / tags. Sortable. Click a row →
7. **Trade detail (drawer):** facts (qty, entry→exit, net/gross/fees, P&L%, R). Edit: **tags** (5 default setup tags: PDH-break, PMH-break, OR-breakout, VWAP-reclaim, EMA-pullback — plus add-your-own), **grade** A/B/C, **stop price** (recomputes R on save), **rule checklist**, **notes**, **screenshot** paths. **Save** → PATCH; R updates.
8. **Daily Review** (left nav): pick a date → that day's trades + summary + journal prompts.
9. API sanity:
   ```bash
   curl -X POST http://localhost:9000/api/import/fills
   curl "http://localhost:9000/api/analytics/summary"
   curl "http://localhost:9000/api/analytics/calendar?month=2026-07"
   ```

## Notes
- Round-trip builder is **FIFO**; handles scale-in/out and cross-through-zero (a flip splits into a closing trade + a new opposite trade, fees apportioned pro-rata). Open tail positions show as `status=open`.
- Simulated trades from Replay (M2) share this journal — filter with `?simulated=true`.
- `Avg R` is blank for imported trades (no stop logged); set a stop in trade detail to populate R (per DECISIONS #4).

## Tests
```bash
make test    # 88 tests (M2 65 + M3 23): round-trip FIFO edge cases, analytics, journal API
```
