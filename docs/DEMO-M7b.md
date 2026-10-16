# DEMO — M7b: Option premium (seam + synthetic BS fallback + premium chart + MFE/MAE)

**Goal:** on the Trade Review page, chart the **option's own premium** during a
trade — not just the underlying — with entry/exit markers on the premium curve
and MFE/MAE excursion readouts, so you can see how the contract you actually
held behaved.

Since neither the mock provider nor Webull OpenAPI supplies historical option
bars (OpenAPI has no historical option endpoint — ROADMAP §5), the **synthetic
Black-Scholes fallback** — derived from the underlying candles, anchored to the
recorded fill prices — is the **primary** data path, behind the provider-seam
method `get_option_bars`. A future third-party provider (Polygon, ThetaData)
could implement it for real premium bars without touching the UI.

Scope: PRD-M7 §M7b. Issue #37.

## Run
```bash
TRADEDESK_MODE=mock TRADEDESK_LLM=none make dev   # backend :9000 + frontend :5173
```
Everything demos with zero credentials. Share trades render the underlying
chart only; option trades add the premium chart below the header.

## Verify (click-path)

### 1. Open an option trade's review (`/trades/:id`)

1. Open **http://localhost:5173**, go to **Journal → a day with option trades**,
   and click an option trade (the mock set includes SPXW index-option trades,
   e.g. trade 42 — **SPXW 7500C 07/09**).
2. The header shows the contract descriptor, LONG/SHORT, entry→exit
   (`0.80 → 0.55`), P&L, and grade — the same review shell as M7a share trades.

### 2. Option premium chart + MFE/MAE (`estimated premium`)

1. Below the header, the **premium chart** renders the option's own premium
   over the session on a **premium-scale Y-axis** (sub-$1 to a few dollars for
   this contract — matching the fills), **not** the underlying index level.
2. **Entry/exit markers** land on the premium curve: `entry 1@0.80`,
   `exit 1@0.55`.
3. The **MFE / MAE** readout sits above the chart: `MFE 2.38 (297.5%)`,
   `MAE 0.00 (0.0%)`, labelled **PREMIUM BASIS · ESTIMATED**. Price lines mark
   the best/worst premium reached during the hold.
4. An **`estimated premium`** badge marks the series as synthetic
   (`method="bs_interp"`, `synthetic=true`) — a real provider's bars would show
   as provider-supplied instead.


### 3. Underlying below

Scroll down: the **SPXW underlying** chart is still available (with a timeframe
selector) beneath the premium chart, so you can compare the contract's premium
behavior against the underlying that drove it.


## API (extends the M7a review contract)

```
GET  /api/trades/{id}/review        → adds `option` block, `premium_bars`,
                                       and `excursion` (MFE/MAE, basis="premium",
                                       estimated=true) for option trades;
                                       share trades keep option=null and get
                                       underlying-basis excursion.
GET  /api/trades/{id}/option-bars    → premium OHLCV bars for the contract;
                                       provider-supplied (method="provider") or
                                       synthetic BS fallback (method="bs_interp",
                                       synthetic=true), cached in
                                       option_bars_cache. 404 for share trades.
```

## Data path & provenance

- **Provider seam**: `DataProvider.get_option_bars(contract, day, timeframe)`.
  Mock synthesizes premium via Black-Scholes from its own underlying candles
  (contract-seeded IV); Webull returns `None` (no historical option source),
  which triggers the server-side BS fallback anchored to the trade's fills.
- **Provenance**: synthesized/estimated bars are flagged `synthetic=true` /
  `method="bs_interp"` and surfaced as the `estimated premium` badge — the UI
  never presents an estimate as if it were real market data.

## Manual test plan

1. **Option trade** (e.g. `/trades/42`): premium chart Y-axis is on the fills'
   scale (sub-$1 here), entry/exit markers sit on the curve, MFE/MAE non-zero,
   `estimated premium` badge present.
2. **Share trade** (any equity trade): no premium chart; underlying chart only;
   review still renders (option block null).
3. **Scale sanity**: `curl -s localhost:9000/api/trades/42/review` → `premium_bars`
   closes are on the premium scale (≈0.5–7 here), **not** index level (~7300);
   `excursion` is non-null with `basis="premium"`, `estimated=true`.
4. **Provenance**: the same call shows `option.synthetic=true`,
   `option.method="bs_interp"`.

## Notes / known limits

- Premium is **estimated** (Black-Scholes from the underlying), never real OPRA
  data — deliberately, per ROADMAP §5. The badge and `estimated=true` flag make
  that explicit everywhere the curve appears.
- Fixed in this branch during demo verification: index-option premium was
  rendering on the underlying's index scale (BS applied to a mismatched
  underlying), and sub-minute holds produced a null excursion — both corrected
  with regression tests (see `2fe121b`).
