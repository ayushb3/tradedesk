"""Learn drill builder: end to end into tmp_path with synthetic bars + synthetic course notes."""
from __future__ import annotations

import json
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.knowledge import course_index as ci
from app.knowledge import course_search as cs
from app.knowledge.embedder import HashingEmbedder
from app.learn import build as B
from app.learn import store
from tests import learn_synth as S

FIX = Path(__file__).parent / "fixtures"
FOR = date(2026, 10, 28)  # a Wednesday


@pytest.fixture()
def notes(tmp_path):
    shutil.copytree(FIX / "course_notes", tmp_path / "notes")
    return cs.CourseNotes(tmp_path / "notes", tmp_path / "cache" / ci.CACHE_NAME, HashingEmbedder())


@pytest.fixture()
def rules(notes):
    return B.RuleBook(notes)


def loader(ticker: str, day: date):
    """A different synthetic setup on each weekday so all three detectors fire."""
    pick = (day.toordinal() + (0 if ticker == "SPY" else 1)) % 3
    if pick == 0:
        return S.resistance_session(day, True, ticker)
    if pick == 1:
        return S.break_retest_session(day, True, ticker)
    return S.patience_session(day, True, ticker)


def _run(rules, root, for_date=FOR, days=6, **kw):
    return B.build(for_date, days, ["SPY", "QQQ"], loader=loader, rules=rules, root=root, **kw)


def test_build_writes_three_diverse_valid_drills(rules, tmp_path):
    root = tmp_path / "learn"
    res = _run(rules, root)
    assert len(res["drills"]) == 3
    assert sum(res["candidates"].values()) >= 6
    assert len({d.detector for d in res["drills"]}) == 3        # diversity of detector
    files = sorted((root / "drills" / "2026-10-28").glob("*.json"))
    assert len(files) == 3
    for f in files:
        d = store.Drill.model_validate_json(f.read_text(encoding="utf-8"))   # validates
        assert d.id == f.stem and d.set_date == "2026-10-28" and d.timeframe == "1m"
        assert d.bars_from < d.pause_at < d.bars_until
        assert d.pause_at.second == 0
        assert d.date < "2026-10-28" and datetime.fromisoformat(d.date).weekday() < 5
        assert d.question_steps == ["level", "enter", "stop", "target"]
        assert d.topic in notes_topics() and d.lessons
        assert set(d.lessons) <= {"1.1", "1.2", "2.1", "2.3", "3.1.1"}   # only course lessons
        refs = d.grading_rubric.rule_refs
        assert set(refs) == {"level", "enter", "stop", "target"} and all(refs.values())
        for ref in refs.values():                                          # "<lesson id> § section"
            assert ref.split(" ")[0] in {"1.1", "1.2", "2.1", "2.3", "3.1.1"} or ref.startswith("topic ")
        assert any(ref in d.explanation for ref in refs.values())
        k = d.answer_key
        if k.enter == "enter":
            if k.side == "long":
                assert k.stop < k.level < k.target
            else:
                assert k.stop > k.level > k.target
        else:
            assert (k.side, k.stop, k.target) == (None, None, None)


def notes_topics():
    return {"stops", "trade-management", "position-sizing", "premarket-routine"}


def test_pause_is_right_after_the_setup_bar_and_bars_end_after_it(rules, tmp_path):
    res = _run(rules, tmp_path / "learn")
    for d in res["drills"]:
        day = date.fromisoformat(d.date)
        bars = loader(d.ticker, day)
        idx = [i for i, b in enumerate(bars) if b.ts + timedelta(minutes=1) == d.pause_at]
        assert idx, "pause_at must sit one bar after a real bar"
        assert d.bars_until == min(d.pause_at + timedelta(minutes=B.LOOKAHEAD_MIN),
                                   S.session_open(day) + timedelta(minutes=390))


def test_review_flag_for_first_two_nights_then_false(rules, tmp_path):
    root = tmp_path / "learn"
    flags = []
    for fd in (date(2026, 10, 28), date(2026, 10, 29), date(2026, 10, 30)):
        res = _run(rules, root, for_date=fd)
        assert res["drills"], fd
        flags.append({d.review for d in store.load_set(root, fd.isoformat())})
    assert flags == [{True}, {True}, {False}]
    st = store.read_state(root)
    assert st["nights_built"] == 3 and st["last_built_for"] == "2026-10-30"


def test_rerun_same_date_overwrites_without_double_counting(rules, tmp_path):
    root = tmp_path / "learn"
    _run(rules, root)
    first = sorted(p.name for p in (root / "drills" / "2026-10-28").glob("*.json"))
    _run(rules, root)
    _run(rules, root)
    assert sorted(p.name for p in (root / "drills" / "2026-10-28").glob("*.json")) == first
    st = store.read_state(root)
    assert st["nights_built"] == 1 and st["last_built_for"] == "2026-10-28"
    assert {d.review for d in store.load_set(root, "2026-10-28")} == {True}
    _run(rules, root, for_date=date(2026, 10, 29))
    assert store.read_state(root)["nights_built"] == 2
    # rebuilding night 1 after night 2 keeps night 1's review flag and the count
    _run(rules, root)
    assert store.read_state(root)["nights_built"] == 2
    assert {d.review for d in store.load_set(root, "2026-10-28")} == {True}


def test_candidates_without_course_support_are_dropped(rules, tmp_path, monkeypatch):
    q = {k: dict(v) for k, v in B.RULE_QUERIES.items()}
    q["break_retest"]["topic"] = "zzqx wvvk"      # no hit in the notes
    monkeypatch.setattr(B, "RULE_QUERIES", q)
    res = _run(B.RuleBook(rules.notes), tmp_path / "learn")
    assert res["candidates"]["break_retest"] > 0
    assert res["dropped_no_course_support"] >= res["candidates"]["break_retest"]
    assert res["drills"] and all(d.detector != "break_retest" for d in res["drills"])
    assert len(res["drills"]) <= 3


def test_weak_topic_score_drops_everything_and_writes_nothing(rules, tmp_path):
    root = tmp_path / "learn"
    res = _run(B.RuleBook(rules.notes, min_score=99.0), root)
    assert res["drills"] == [] and res["dropped_no_course_support"] > 0
    assert not root.exists()                    # no drills, no state, no folder


def test_fewer_than_three_is_fine(rules, tmp_path, monkeypatch):
    monkeypatch.setattr(B.det, "DETECTORS", {"level_touches": B.det.level_touches})
    only_one = lambda t, d: S.resistance_session(d, True, t) if (t, d) == ("SPY", date(2026, 10, 27)) else []
    res = B.build(FOR, 3, ["SPY"], loader=only_one, rules=rules, root=tmp_path / "learn")
    assert len(res["drills"]) == 1 and res["sessions"] == 1 and res["sessions_skipped"] == 2


def test_dry_run_and_candidates_only_write_nothing(rules, tmp_path):
    root = tmp_path / "learn"
    a = _run(rules, root, dry_run=True)
    assert a["drills"] and a["written"] == [] and not root.exists()
    b = _run(rules, root, candidates_only=True)
    assert len(b["candidates_detail"]) >= 3 and not root.exists()
    assert {"detector", "ticker", "date", "pause_at", "level", "side", "strength"} <= set(b["candidates_detail"][0])


def test_select_prefers_strength_then_spreads_out():
    now = datetime(2026, 10, 14, 15, tzinfo=timezone.utc)
    info = B.RuleInfo("stops", 1.0, ["3.1.1"], {})

    def cand(det, tk, day, s):
        return (B.det.Candidate(det, tk, day, now, 600.0, "long", s), info)

    pool = [cand("level_touches", "SPY", "2026-10-14", 0.90),
            cand("level_touches", "SPY", "2026-10-14", 0.89),
            cand("break_retest", "QQQ", "2026-10-13", 0.70),
            cand("patience_candle_at_level", "SPY", "2026-10-12", 0.62)]
    picked = B.select(pool)
    assert [c.strength for c, _ in picked] == [0.90, 0.70, 0.62]


def test_next_weekday_in_pt_skips_weekends():
    fri_pm = datetime(2026, 10, 30, 22, 0, tzinfo=timezone(timedelta(hours=-7)))  # Fri 10pm PT
    assert B.next_weekday_pt(fri_pm) == date(2026, 11, 2)
    assert B.next_weekday_pt(datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)) == date(2026, 10, 28)


def test_explanation_and_key_for_a_wait_setup(rules):
    c = B.det.level_touches(S.resistance_session(date(2026, 10, 14), False), "SPY", "2026-10-14")[0]
    info = rules.lookup(c.detector)
    d = B.draft_drill(c, info, "2026-10-28", True, "x-1")
    assert d.answer_key.enter == "wait" and d.answer_key.stop is None
    assert "Wait" in d.explanation or "wait" in d.explanation


# ----------------------------------------------------------------------- CLI

@pytest.fixture()
def cli_env(monkeypatch, tmp_path, isolated_db):
    """Cache-only run: synthetic sessions in a temp candles_cache, no market-data keys."""
    from sqlalchemy import insert

    from app.config import get_settings
    from app.db.connection import get_engine, init_db
    from app.db.schema import candles_cache

    shutil.copytree(FIX / "course_notes", tmp_path / "notes")
    monkeypatch.setenv("TRADEDESK_COURSE_NOTES_DIR", str(tmp_path / "notes"))
    monkeypatch.setenv("TRADEDESK_LEARN_DIR", str(tmp_path / "learn"))
    monkeypatch.setenv("KNOWLEDGE_EMBEDDER", "hashing")
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    get_settings.cache_clear()
    eng = get_engine()
    init_db(eng)
    rows = []
    for tk in ("SPY", "QQQ"):
        for d in B.recent_weekdays(FOR, 6):
            for c in loader(tk, d):
                rows.append(dict(symbol=tk, timeframe="1m", ts_utc=c.ts.replace(tzinfo=None),
                                 open=c.open, high=c.high, low=c.low, close=c.close,
                                 volume=c.volume, provider="alpaca", session_date=d))
    with eng.begin() as conn:
        conn.execute(insert(candles_cache), rows)
    return tmp_path


def test_cli_dry_run_prints_json_and_writes_nothing(cli_env, capsys):
    rc = B.main(["--for-date", "2026-10-28", "--days", "6", "--dry-run"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["for_date"] == "2026-10-28" and len(out["drills"]) == 3 and out["written"] == []
    assert not (cli_env / "learn").exists()


def test_cli_writes_set_and_state(cli_env, capsys):
    assert B.main(["--for-date", "2026-10-28", "--days", "6", "--tickers", "SPY,QQQ"]) == 0
    capsys.readouterr()
    assert len(list((cli_env / "learn" / "drills" / "2026-10-28").glob("*.json"))) == 3
    assert store.read_state(cli_env / "learn")["nights_built"] == 1


def test_cli_candidates_only_emits_candidates(cli_env, capsys):
    assert B.main(["--for-date", "2026-10-28", "--days", "6", "--candidates-only"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["candidates_detail"] and not (cli_env / "learn").exists()


def test_cli_without_course_notes_builds_nothing(cli_env, monkeypatch, capsys):
    from app.config import get_settings

    monkeypatch.setenv("TRADEDESK_COURSE_NOTES_DIR", "")
    get_settings.cache_clear()
    assert B.main(["--for-date", "2026-10-28", "--days", "6"]) == 2
    assert not (cli_env / "learn").exists()
    assert "course notes not configured" in capsys.readouterr().err


def test_cli_without_learn_dir_refuses_to_write(cli_env, monkeypatch, capsys):
    from app.config import get_settings

    monkeypatch.setenv("TRADEDESK_LEARN_DIR", "")
    get_settings.cache_clear()
    assert B.main(["--for-date", "2026-10-28", "--days", "6"]) == 2
    assert B.main(["--for-date", "2026-10-28", "--days", "6", "--dry-run"]) == 0
