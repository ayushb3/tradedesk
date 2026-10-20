"""SQLAlchemy Core table definitions for the TradeDesk SQLite store.

Implements the full data model from `docs/PLAN.md` §2 plus the M6 training
tables from `docs/PLAN-M6-TRAINING.md` §3. We use SQLAlchemy **Core** (not the
ORM) — a single shared `MetaData` holds every `Table`.

Storage conventions (per PLAN §2 notes):
- All timestamps are UTC. `ts_utc`/`open_ts_utc`/`close_ts_utc` use `DateTime`;
  SQLite stores them as ISO-8601 text and round-trips to naive UTC `datetime`.
- `session_date` is the ET trading date, stored as `Date`.
- JSON columns (`value_json`, `raw_json`, `checklist_json`, `screenshots_json`)
  use SQLAlchemy's `JSON`, which serializes to TEXT on SQLite.

M6 additions (task 6.0.2):
- `scenarios`, `exercise_attempts`, `debriefs` tables added below.
- `trades.context` TEXT column (real|sim|training) is added via an idempotent
  ALTER TABLE in `connection.py` because `create_all` is a no-op for existing
  tables. New rows get 'real' by default; training drills use 'training'.
"""
from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

metadata = MetaData()


# config(key PK, value_json) — watch tickers, OR window, tz, etc.
config = Table(
    "config",
    metadata,
    Column("key", String, primary_key=True),
    Column("value_json", JSON),
)


# candles_cache — PK (symbol, timeframe, ts_utc)
candles_cache = Table(
    "candles_cache",
    metadata,
    Column("symbol", String, primary_key=True),
    Column("timeframe", String, primary_key=True),
    Column("ts_utc", DateTime, primary_key=True),
    Column("open", Float, nullable=False),
    Column("high", Float, nullable=False),
    Column("low", Float, nullable=False),
    Column("close", Float, nullable=False),
    Column("volume", Float, nullable=False),
    Column("provider", String),
    Column("session_date", Date),
)


# option_bars_cache — synthesized/estimated option premium bars (M7b).
# PK (contract_key, timeframe, ts_utc). `contract_key` is the canonical
# SYMBOL_YYMMDD_C|P_STRIKE grouping key. `synthetic`/`method` record provenance:
# provider-supplied bars are (synthetic=False, method='provider'); the server-
# side Black-Scholes fallback is (synthetic=True, method='bs_interp').
# `source` is the provider that produced the row ("mock", "alpaca", "webull"):
# reads MUST filter on it so one provider's rows are never served to another.
# NULL = a row written before the column existed (unknown origin) and is never
# served. The PK has no source, so a later write for the same bar overwrites it.
option_bars_cache = Table(
    "option_bars_cache",
    metadata,
    Column("contract_key", String, primary_key=True),
    Column("timeframe", String, primary_key=True),
    Column("ts_utc", DateTime, primary_key=True),
    Column("open", Float, nullable=False),
    Column("high", Float, nullable=False),
    Column("low", Float, nullable=False),
    Column("close", Float, nullable=False),
    Column("volume", Float, nullable=False),
    Column("synthetic", Boolean, nullable=False, default=False),
    Column("method", String, nullable=False),
    Column("source", String),
)


# second_bars_cache — 1-second bars aggregated from tick trades (Replay "Seconds").
# PK (source, series_key, ts_utc). `source` is the provider that produced the row
# (reads MUST filter on it); `series_key` is the option contract_key or the stock
# symbol that was fetched. Only 1s bars are stored: coarser bars are re-bucketed on
# read. Seconds with no prints have no row. Rows are written only once the session
# is closed, together with a coverage row, because an empty second is
# indistinguishable from "not fetched" without one.
second_bars_cache = Table(
    "second_bars_cache",
    metadata,
    Column("source", String, primary_key=True),
    Column("series_key", String, primary_key=True),
    Column("ts_utc", DateTime, primary_key=True),
    Column("open", Float, nullable=False),
    Column("high", Float, nullable=False),
    Column("low", Float, nullable=False),
    Column("close", Float, nullable=False),
    Column("volume", Float, nullable=False),
)

# second_bars_coverage — the [start_utc, end_utc] windows already fetched in full.
second_bars_coverage = Table(
    "second_bars_coverage",
    metadata,
    Column("source", String, primary_key=True),
    Column("series_key", String, primary_key=True),
    Column("start_utc", DateTime, primary_key=True),
    Column("end_utc", DateTime, primary_key=True),
)


# fills — one execution; simulated=True for replay-generated fills.
fills = Table(
    "fills",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("provider_fill_id", String),
    Column("account", String),
    Column("symbol", String, nullable=False),
    Column("side", String, nullable=False),  # buy | sell
    Column("qty", Float, nullable=False),
    Column("price", Float, nullable=False),
    Column("ts_utc", DateTime, nullable=False),
    Column("fees", Float, nullable=False, default=0.0),
    Column("multiplier", Float, nullable=False, default=1.0),  # options contract multiplier
    # Option contract identity (NULL for stock fills).  Added in the per-contract
    # grouping fix; existing rows get NULL via the ALTER TABLE migration in connection.py.
    Column("option_type", String),   # "CALL" | "PUT"
    Column("strike", Float),          # strike price
    Column("expiry", String),         # ISO date string, e.g. "2026-07-09"
    Column("simulated", Boolean, nullable=False, default=False),
    Column("raw_json", JSON),
)


# trades — a round-trip built from ordered fills.
trades = Table(
    "trades",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("symbol", String, nullable=False),
    # Human-readable contract label: options → "SPXW 7500C 07/09", stocks → ticker.
    # Added in the per-contract grouping fix; existing rows get NULL via ALTER TABLE.
    Column("contract_label", String),
    Column("direction", String, nullable=False),  # long | short
    Column("open_ts_utc", DateTime, nullable=False),
    Column("close_ts_utc", DateTime),
    Column("session_date", Date, nullable=False),
    Column("qty", Float, nullable=False),
    Column("avg_entry", Float, nullable=False),
    Column("avg_exit", Float),
    Column("gross_pnl", Float),
    Column("fees", Float, nullable=False, default=0.0),
    Column("net_pnl", Float),
    Column("pnl_pct", Float),
    Column("stop_price", Float),
    Column("r_multiple", Float),
    Column("hold_time_s", Integer),
    Column("status", String, nullable=False, default="closed"),  # open | closed
    Column("simulated", Boolean, nullable=False, default=False),
    # M6 context filter: real | sim | training. Training-drill trades are excluded
    # from journal analytics by default. The ALTER TABLE for this column on
    # existing DBs lives in connection.py (_apply_column_migrations).
    Column("context", String, nullable=False, default="real"),
    # Stable identity (issue #3): hash of the opening fill's provider_fill_id.
    # `_rebuild_trades` reuses the existing `id` of the trade with the same key,
    # so integer FKs to trades.id never orphan. NULL for sim/training trades.
    # Unique index `uq_trades_trade_key` is created in connection.py.
    Column("trade_key", String),
    Index("uq_trades_trade_key", "trade_key", unique=True),
)


# trade_fills — ordered fill membership (FIFO lots).
trade_fills = Table(
    "trade_fills",
    metadata,
    Column("trade_id", Integer, ForeignKey("trades.id"), nullable=False),
    Column("fill_id", Integer, ForeignKey("fills.id"), nullable=False),
    Column("seq", Integer, nullable=False),
    UniqueConstraint("trade_id", "fill_id", name="uq_trade_fill"),
)


# tags(id PK, name UNIQUE, description, active)
tags = Table(
    "tags",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String, nullable=False, unique=True),
    Column("description", String),
    Column("active", Boolean, nullable=False, default=True),
)


# trade_tags — many-to-many between trades and tags.
trade_tags = Table(
    "trade_tags",
    metadata,
    Column("trade_id", Integer, ForeignKey("trades.id"), nullable=False),
    Column("tag_id", Integer, ForeignKey("tags.id"), nullable=False),
    UniqueConstraint("trade_id", "tag_id", name="uq_trade_tag"),
)


# trade_enrichment(trade_id PK/FK, notes, grade, checklist_json, screenshots_json)
trade_enrichment = Table(
    "trade_enrichment",
    metadata,
    Column("trade_id", Integer, ForeignKey("trades.id"), primary_key=True),
    Column("notes", String),
    Column("grade", String),  # A | B | C | null
    Column("checklist_json", JSON),  # [{"item": str, "checked": bool}]
    Column("screenshots_json", JSON),  # [path]
    # One-tap "whose idea": mentor | own | NULL (not recorded). Added by an idempotent
    # ALTER TABLE in connection.py for existing DBs.
    Column("call_source", String),
)


# checklist_template(id PK, item, sort_order, active)
checklist_template = Table(
    "checklist_template",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("item", String, nullable=False),
    Column("sort_order", Integer, nullable=False, default=0),
    Column("active", Boolean, nullable=False, default=True),
)


# ── M6 Training module (task 6.0.2) ─────────────────────────────────────── #

# scenarios — authored drill scenarios (real tape, synthetic, or debrief-authored).
# Note: trades.context column is added via idempotent ALTER in connection.py.
scenarios = Table(
    "scenarios",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("title", String, nullable=False),
    # gap-and-go | failed-breakout | chop | trend-day | custom
    Column("archetype", String, nullable=False, default="custom"),
    # real | synthetic | debrief
    Column("source_type", String, nullable=False),
    # decision-point | management-only | do-nothing | spot-setup | drawdown-script
    Column("drill_type", String, nullable=False, default="decision-point"),
    # real/debrief sources: reference into candles_cache (nullable for synthetic)
    Column("symbol", String),
    Column("session_date", Date),
    # synthetic source: deterministic regeneration parameters
    Column("synth_seed", Integer),
    Column("synth_params_json", JSON),
    # bar indices (relative to session start)
    Column("start_bar", Integer, nullable=False, default=0),
    Column("decision_bar", Integer),   # bar where drill pauses; None for management-only
    Column("end_bar", Integer),
    # teaching content — both stored; answer_key never returned on public GETs
    Column("annotations_json", JSON),       # [{bar, text, kind}]
    Column("answer_key_json", JSON, nullable=False),  # AnswerKey dict
    # optional link to a rule-adherence checklist
    Column("checklist_template_id", Integer),
    # debrief-authored: FK back to the trade that spawned this scenario
    Column("source_trade_id", Integer, ForeignKey("trades.id")),
    # blind mode: hide symbol/session_date on public GET when true
    Column("blind_default", Boolean, nullable=False, default=False),
    Column("active", Boolean, nullable=False, default=True),
    Column("created_at", DateTime, nullable=False),
)


# exercise_attempts — one user attempt at a scenario drill.
exercise_attempts = Table(
    "exercise_attempts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("scenario_id", Integer, ForeignKey("scenarios.id"), nullable=False),
    Column("started_at", DateTime, nullable=False),
    Column("finished_at", DateTime),
    # pre-drill mood: calm | rushed | frustrated | null (C3 psychology pillar)
    Column("mood_checkin", String),
    # REPLAYABLE bar-indexed event log: [{bar, action, price?, qty?, ts}]
    Column("actions_json", JSON),
    # score fields — null until POST /training/attempts/{id}/finish
    Column("process_score", Float),       # 0..1, rule adherence + decision correctness
    Column("outcome_pnl", Float),         # recorded but NEVER used for scoring/rewards
    Column("checklist_json", JSON),       # [{item, checked}]
    Column("verdict", String),
    Column("notes", String),
)


# debriefs — agent-guided trade self-analysis sessions (four-step arc).
debriefs = Table(
    "debriefs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("trade_id", Integer, ForeignKey("trades.id"), nullable=False),
    # none | claude-cli | api — backend that ran this debrief
    Column("llm_backend", String, nullable=False),
    # current step in the four-step arc: setup | plan | feelings | verdict | done
    Column("step", String, nullable=False, default="setup"),
    # full Q/A transcript (append-only): [{role: agent|user, step: str, text: str}]
    Column("transcript_json", JSON),
    # stated stop/target/R + any drift flags the agent detected
    Column("plan_json", JSON),
    # [{ts_or_phase: str, feeling: str}]
    Column("feelings_json", JSON),
    # setup-failed | failed-the-setup | good-loss | bad-win | clean
    Column("verdict", String),
    # FK to the emitted drill scenario (set by finish_debrief when emit_scenario=True)
    Column("scenario_id", Integer, ForeignKey("scenarios.id")),
    Column("created_at", DateTime, nullable=False),
)


# ── M8 knowledge base (M8a ingestion, task/issue #39) ──────────────────── #

# knowledge_lessons — one row per lesson transcript. The relational
# source of truth (PRD-M8 §5.1); the ChromaDB vectors under data/knowledge_chroma/ are a
# rebuildable artifact keyed back to these rows by `id`.
#
# `id` is the global lesson number parsed from the transcript filename (e.g. 001),
# stable across re-ingests. `module` is the module label derived from the
# module-file grouping ("0", "1", "3.2", "12.1", …). `seq` is the lesson's
# order within its module. `content_hash` (sha256 of raw_text) drives idempotent
# re-runs (R5): unchanged lessons are skipped.
# The optional "open original" link is computed from settings, not stored.
knowledge_lessons = Table(
    "knowledge_lessons",
    metadata,
    Column("id", Integer, primary_key=True),           # global lesson no.
    Column("module", String, nullable=False),          # module label, e.g. "7", "12.1", "0"
    Column("seq", Integer, nullable=False),            # order within the module (1..N)
    Column("title", String, nullable=False),
    Column("source_path", String, nullable=False),
    Column("raw_text", Text, nullable=False),
    Column("content_hash", String, nullable=False),    # sha256(raw_text) — R5 idempotency
    Column("embedder", String),                        # embedder name used for chunks
    Column("chunk_count", Integer, nullable=False, default=0),
    Column("ingested_at", DateTime, nullable=False),
)


# knowledge_lesson_progress — per-lesson reading progress for the M8b reader (issue #48).
# One row per lesson the user has engaged with; absence of a row means "unread".
# Brand-new table, so create_all picks it up on a fresh db (no ALTER needed).
knowledge_lesson_progress = Table(
    "knowledge_lesson_progress",
    metadata,
    Column("lesson_id", Integer, ForeignKey("knowledge_lessons.id"), primary_key=True),
    Column("status", String, nullable=False, default="unread"),  # unread|read|starred
    Column("updated_at", DateTime, nullable=False),
)


# ── M8c Concepts (issue #54, PR-1) ─────────────────────────────────────── #
# Concept index distilled from the lessons by an offline LLM pass. `concepts` is a
# controlled vocabulary: rows are either seeded (seeded=True, from
# seed_concepts.json) or drift-guarded to seeded slugs by the extraction pass, so
# the LLM can never invent a concept outside the taxonomy. All four tables are
# brand-new, so create_all picks them up on a fresh db (no ALTER needed).
concepts = Table(
    "concepts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("slug", String, nullable=False, unique=True),   # stable kebab key, e.g. "support-resistance"
    Column("name", String, nullable=False),                # display name
    Column("category", String, nullable=False),            # Strategy|Risk|Psychology|Process
    Column("definition", Text, nullable=False, default=""),
    Column("aliases_json", JSON),                          # ["S/R", "key levels"]
    Column("seeded", Boolean, nullable=False, default=False),
    Column("created_at", DateTime),
    Column("updated_at", DateTime),
)


# lesson_concepts — many-to-many between knowledge_lessons and concepts, with a weight
# (LLM-assigned salience 0..1+). Delete-then-insert per lesson keeps it idempotent.
lesson_concepts = Table(
    "lesson_concepts",
    metadata,
    Column("lesson_id", Integer, ForeignKey("knowledge_lessons.id"), nullable=False),
    Column("concept_id", Integer, ForeignKey("concepts.id"), nullable=False),
    Column("weight", Float, nullable=False, default=1.0),
    UniqueConstraint("lesson_id", "concept_id", name="uq_lesson_concept"),
)


# concept_links — directed relations between two concepts. `relation` ∈
# related | prerequisite | contrast | part_of. Seeded links + LLM-suggested links
# both land here (drift-guarded to seeded slugs).
concept_links = Table(
    "concept_links",
    metadata,
    Column("a_id", Integer, ForeignKey("concepts.id"), nullable=False),
    Column("b_id", Integer, ForeignKey("concepts.id"), nullable=False),
    Column("relation", String, nullable=False),  # related|prerequisite|contrast|part_of
    UniqueConstraint("a_id", "b_id", "relation", name="uq_concept_link"),
)


# knowledge_concept_runs — resumability marker for the concepts extraction pass. A lesson
# may legitimately produce zero concepts, so completion is tracked here (by
# content_hash) rather than inferred from lesson_concepts. One row per processed
# lesson; re-runs skip when content_hash is unchanged (unless --force).
knowledge_concept_runs = Table(
    "knowledge_concept_runs",
    metadata,
    Column("lesson_id", Integer, ForeignKey("knowledge_lessons.id"), primary_key=True),
    Column("content_hash", String, nullable=False),
    Column("model", String, nullable=False),
    Column("generated_at", DateTime, nullable=False),
)


# ── M8c Study Sheets (issue #54, PR-2) ─────────────────────────────────── #
# One study sheet per lesson, produced by an offline LLM pass. summary_md/rules_md
# are markdown; diagram_mermaid is Mermaid flowchart source (nullable — many
# auto-caption transcripts don't lend themselves to a clean diagram); quiz_json is
# a list of {q, choices, answer_idx, explanation}. `content_hash` (sha256 of the
# lesson's raw_text) IS the resumability marker — a sheet row always exists once
# generated, so no separate run-marker table is needed. Brand-new table, so
# create_all picks it up on a fresh db (no ALTER needed).
lesson_sheets = Table(
    "lesson_sheets",
    metadata,
    Column("lesson_id", Integer, ForeignKey("knowledge_lessons.id"), primary_key=True),
    Column("summary_md", Text, nullable=False, default=""),
    Column("rules_md", Text, nullable=False, default=""),
    Column("diagram_mermaid", Text),                       # nullable — valid Mermaid or None
    Column("quiz_json", JSON),                             # [{q, choices, answer_idx, explanation}]
    Column("content_hash", String, nullable=False),        # sha256(raw_text) — resumability marker
    Column("model", String, nullable=False),
    Column("generated_at", DateTime, nullable=False),
)


# ── M9a Agent sessions (issue #58) ─────────────────────────────────────────── #
# Conversation persistence for agent-driven flows (the M9a critic being the first
# `kind`). `agent_sessions` is one conversation; `agent_messages` is its append-only
# turn log. `subject_ref` is a free-form reference to what the session is about,
# e.g. "trade:42". Both tables are brand-new, so create_all picks them up on a
# fresh db (no ALTER needed).
#
# JSON payloads are stored as TEXT (json.dumps/json.loads in agents_store) rather
# than the JSON column type: `citations_json` is a JSON array of KnowledgeSearchHit dicts,
# `payload_json` is the structured CriticResult (additive/nullable by design).
agent_sessions = Table(
    "agent_sessions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("kind", Text, nullable=False),          # e.g. "critic"
    Column("subject_ref", Text, nullable=False),   # e.g. "trade:42"
    Column("started_at", DateTime, nullable=False),  # UTC
    Index("ix_agent_sessions_subject_ref", "subject_ref"),
)


# agent_messages — append-only turn log for an agent_session.
agent_messages = Table(
    "agent_messages",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("session_id", Integer, ForeignKey("agent_sessions.id"), nullable=False),
    Column("role", Text, nullable=False),          # "agent" | "user"
    Column("content_md", Text, nullable=False),
    Column("citations_json", Text),                # nullable — JSON array of KnowledgeSearchHit dicts
    Column("payload_json", Text),                  # nullable — JSON of the structured CriticResult
    Column("ts", DateTime, nullable=False),        # UTC
    Index("ix_agent_messages_session_id", "session_id"),
)


# -- Desk day_fields (issue #7) -- #
# One row per ISO date: the day self-report. Keyed on date, not trade, so it survives
# re-imports. callers_json is a JSON list stored as TEXT; tri-state booleans are
# nullable (NULL = not answered). updated_at is naive UTC in the DB (aware in the model).
day_fields = Table(
    "day_fields",
    metadata,
    Column("date", Text, primary_key=True),
    Column("why", Text, nullable=False, default=""),
    Column("callers_json", Text, nullable=False, default="[]"),
    Column("size_written_first", Boolean),
    Column("max_loss_declared", Float),
    Column("closed_by_window_end", Boolean),
    Column("stop_hit", Boolean),
    Column("target_hit", Boolean),
    Column("how_felt", Text, nullable=False, default=""),
    Column("notes", Text, nullable=False, default=""),
    Column("updated_at", DateTime, nullable=False),
)


# -- Desk plans (issue #9) -- #
# A written plan card per trade, with its time (rule 4). created_at is naive UTC in the
# DB; the API shows it as PT. matched_trade_id points at trades.id, which is stable across
# re-imports (issue #3), so plans survive a full re-import. `late` is set when the plan
# was written after the opening fill of the trade it matches.
plans = Table(
    "plans",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("created_at", DateTime, nullable=False),  # UTC
    Column("session_date", Text, nullable=False),    # ISO date, PT trading day
    Column("caller", Text, nullable=False),
    Column("symbol", Text, nullable=False),
    Column("option_type", Text),                      # CALL | PUT | NULL
    Column("strike", Float),
    Column("expiry", Text),                           # ISO date
    Column("size_usd", Float, nullable=False),
    Column("max_loss_usd", Float, nullable=False),
    Column("target", Text),
    Column("note", Text, nullable=False, default=""),
    Column("status", Text, nullable=False, default="open"),  # open | matched | unmatched
    Column("late", Boolean, nullable=False, default=False),
    Column("matched_trade_id", Integer, ForeignKey("trades.id")),
    Index("ix_plans_session_date", "session_date"),
)


# -- Desk MCP audit log (issue #12) -- #
# One row per write-tool call, accepted or rejected. ts is naive UTC (shown as PT).
# args_json is sanitized before it gets here: nothing shaped like an account/order id.
audit = Table(
    "audit",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", DateTime, nullable=False),  # UTC
    Column("tool", Text, nullable=False),
    Column("args_json", Text, nullable=False, default="{}"),
    Column("result", Text, nullable=False),  # "ok" | "error: <reason>"
    Column("mode", Text, nullable=False),    # mock | real
    Index("ix_audit_ts", "ts"),
)
