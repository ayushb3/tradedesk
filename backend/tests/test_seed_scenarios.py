"""Bit-rot guard for the seed-scenario library (M6 task 6.1.4).

Imports the module-level SEED_SCENARIOS list from scripts.seed_scenarios and
asserts each scenario:

  * Has a valid archetype, source_type, and drill_type (enum-typed by pydantic
    already, so we mostly assert the values are non-null and coherent).
  * Has at least one bar-pinned annotation.
  * Has a non-empty answer_key.explanation.
  * References a symbol in the MockProvider's known-ticker set when source is
    'real' (protects against typos that would 404 on /session).

Also asserts we ship at least 10 seeds (the M6 6.1.4 acceptance criterion).

No DB writes, no HTTP. Pure static inspection of the constant.
"""
from __future__ import annotations

import pytest

from app.config import Settings
from app.models.training import (
    Archetype,
    AttemptActionType,
    DrillType,
    ScenarioSource,
)
from scripts.seed_scenarios import SEED_SCENARIOS


# The mock provider defaults to RIVN,HOOD but the setting is overridable — we
# check against the live setting so a user with a different watch list will
# see a helpful failure rather than a mystery 404 at drill time.
_MOCK_TICKERS: set[str] = set(Settings().watch_list)


def test_ships_at_least_ten_scenarios() -> None:
    """Acceptance criterion 6.1.4: '~10 seed scenarios'."""
    assert len(SEED_SCENARIOS) >= 10, (
        f"Expected at least 10 seed scenarios, found {len(SEED_SCENARIOS)}"
    )


@pytest.mark.parametrize("sc", SEED_SCENARIOS, ids=lambda s: s.title)
def test_each_scenario_has_valid_shape(sc) -> None:
    """Every seed must be minimally well-formed."""
    # Enum-typed fields (pydantic already validates at construction, but a
    # None slipping through would be a data-model surprise).
    assert isinstance(sc.archetype, Archetype), sc.title
    assert isinstance(sc.source_type, ScenarioSource), sc.title
    assert isinstance(sc.drill_type, DrillType), sc.title

    # Every seed teaches something — an empty explanation defeats the drill.
    assert sc.answer_key is not None, sc.title
    assert sc.answer_key.explanation.strip(), (
        f"Empty answer_key.explanation in {sc.title!r}"
    )

    # At least one annotation — annotations are the "why" for the player.
    assert len(sc.annotations) >= 1, (
        f"Scenario {sc.title!r} has no annotations"
    )

    # Correct actions must reference the enum (defensive; pydantic enforces).
    assert all(isinstance(a, AttemptActionType) for a in sc.answer_key.correct_actions), (
        f"Non-enum action in {sc.title!r}"
    )
    assert len(sc.answer_key.correct_actions) >= 1, (
        f"Scenario {sc.title!r} has no correct_actions"
    )


@pytest.mark.parametrize(
    "sc",
    [s for s in SEED_SCENARIOS if s.source_type == ScenarioSource.real],
    ids=lambda s: s.title,
)
def test_real_source_symbols_are_in_mock_provider(sc) -> None:
    """Real-source seeds must reference a ticker the mock provider serves,
    otherwise /session will 404 when the drill lobby tries to open them."""
    assert sc.symbol is not None, sc.title
    assert sc.symbol in _MOCK_TICKERS, (
        f"Scenario {sc.title!r} references {sc.symbol!r}, not in "
        f"the mock provider's watch list {_MOCK_TICKERS}"
    )
    assert sc.session_date is not None, sc.title


@pytest.mark.parametrize("sc", SEED_SCENARIOS, ids=lambda s: s.title)
def test_bar_range_is_ordered(sc) -> None:
    """start_bar <= decision_bar (when set) <= end_bar (when set)."""
    if sc.decision_bar is not None:
        assert sc.decision_bar >= sc.start_bar, sc.title
    if sc.end_bar is not None:
        assert sc.end_bar >= sc.start_bar, sc.title
        if sc.decision_bar is not None:
            assert sc.decision_bar <= sc.end_bar, sc.title
