"""Deadline math: par. 37 Abs. 2 SGB X, par. 26 Abs. 3 SGB X, no wall clock.

Part 08 dispatches nothing, so nothing here is called on a gated path yet. It
ships tested because part 10 stamps the dispatch timestamp and needs the
absolute deadline at that moment, and because deadline arithmetic is the kind of
code that is wrong in exactly the two places nobody checks: the end of a month
and the end of a year.

The property that matters more than any single case: **no function in
``engine.draft.bekanntgabe`` reads a clock.** Every date is a parameter. A test
that had to be re-written on a Tuesday would be a bug report about the code.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from engine.draft import (
    BEKANNTGABE_DAYS,
    bekanntgabe_date,
    is_working_day,
    next_working_day,
    response_deadline,
)

#: Two real fixed holidays, injected the way a deployment would inject them.
#: The module ships with NO holiday table: German holidays are Land-specific
#: and the repository cannot cite which ones apply where a letter is served.
NEUJAHR = date(2027, 1, 1)
TAG_DER_ARBEIT = date(2027, 5, 1)

dates = st.dates(min_value=date(2020, 1, 1), max_value=date(2035, 12, 31))
windows = st.integers(min_value=1, max_value=365)


def test_the_fiction_is_four_days() -> None:
    """par. 37 Abs. 2 S. 1 SGB X in its version in force since 1 January 2025."""
    assert BEKANNTGABE_DAYS == 4
    # Monday 2026-08-03 + 4 = Friday 2026-08-07, a working day: no shift.
    assert bekanntgabe_date(date(2026, 8, 3)) == date(2026, 8, 7)


@pytest.mark.parametrize(
    ("dispatch", "expected"),
    [
        # +4 lands on a Saturday -> Monday (par. 26 Abs. 3 SGB X: Sonnabend).
        (date(2026, 8, 4), date(2026, 8, 10)),
        # +4 lands on a Sunday -> Monday.
        (date(2026, 8, 5), date(2026, 8, 10)),
        # Month boundary: 28 Aug + 4 = 1 Sep.
        (date(2026, 8, 28), date(2026, 9, 1)),
        # Year boundary: 29 Dec + 4 = 2 Jan 2027, a Saturday -> Monday 4 Jan.
        (date(2026, 12, 29), date(2027, 1, 4)),
        # Leap day: 27 Feb 2028 + 4 = 2 Mar (2028 has a 29 February).
        (date(2028, 2, 27), date(2028, 3, 2)),
        # Non-leap year: 25 Feb 2027 + 4 = 1 Mar.
        (date(2027, 2, 25), date(2027, 3, 1)),
    ],
)
def test_the_fiction_over_month_year_and_leap_boundaries(
    dispatch: date, expected: date
) -> None:
    assert bekanntgabe_date(dispatch) == expected


def test_an_injected_holiday_moves_the_fiction_and_the_deadline() -> None:
    """1 January 2027 is a Friday; with it as a holiday the fiction is 4 Jan."""
    assert NEUJAHR.weekday() == 4
    assert bekanntgabe_date(date(2026, 12, 28)) == NEUJAHR
    assert bekanntgabe_date(date(2026, 12, 28), holidays={NEUJAHR}) == date(2027, 1, 4)


def test_the_full_deadline_names_the_three_dates_and_what_moved() -> None:
    deadline = response_deadline(date(2026, 8, 4), window_days=30)
    assert deadline.dispatch_date == date(2026, 8, 4)
    assert deadline.bekanntgabe_date == date(2026, 8, 10)  # Sat -> Mon
    assert deadline.deadline == date(2026, 9, 9)
    assert deadline.window_days == 30
    assert deadline.shifted == ("bekanntgabe",)
    assert deadline.total_days == 36
    payload = deadline.as_payload()
    assert payload["deadline"] == "2026-09-09"
    assert payload["basis"] == "par. 37 Abs. 2 SGB X, par. 26 Abs. 3 SGB X"


def test_both_ends_can_shift() -> None:
    """A dispatch whose fiction AND whose deadline fall on a weekend."""
    deadline = response_deadline(
        date(2027, 4, 28), window_days=30, holidays={TAG_DER_ARBEIT}
    )
    # 28 Apr 2027 (Wed) + 4 = Sat 2 May... 1 May is the Saturday, 2 May Sunday.
    assert deadline.bekanntgabe_date == date(2027, 5, 3)
    assert deadline.deadline == date(2027, 6, 2)
    assert "bekanntgabe" in deadline.shifted


def test_a_deadline_landing_on_a_sunday_moves_to_monday() -> None:
    deadline = response_deadline(date(2026, 8, 3), window_days=30)
    assert deadline.bekanntgabe_date == date(2026, 8, 7)  # Friday
    assert deadline.deadline == date(2026, 9, 7)  # 6 Sep is a Sunday
    assert deadline.shifted == ("deadline",)


def test_a_window_of_zero_days_is_not_a_window() -> None:
    with pytest.raises(ValueError, match="not a window"):
        response_deadline(date(2026, 8, 3), window_days=0)


def test_a_holiday_set_covering_a_whole_week_is_refused() -> None:
    """Bounded rather than a while-loop: a broken table may not hang a request."""
    start = date(2026, 8, 3)
    everything = {start + timedelta(days=offset) for offset in range(30)}
    with pytest.raises(ValueError, match="cannot be right"):
        next_working_day(start, holidays=everything)


def test_working_days_are_monday_to_friday_minus_injected_holidays() -> None:
    assert is_working_day(date(2026, 8, 7))  # Friday
    assert not is_working_day(date(2026, 8, 8))  # Saturday
    assert not is_working_day(date(2026, 8, 9))  # Sunday
    assert not is_working_day(NEUJAHR, holidays={NEUJAHR})


@given(dates, windows)
def test_a_deadline_is_always_a_working_day_at_or_after_the_fiction(
    dispatch: date, window: int
) -> None:
    """The property, over fifteen years of calendar."""
    deadline = response_deadline(dispatch, window_days=window)
    assert is_working_day(deadline.bekanntgabe_date)
    assert is_working_day(deadline.deadline)
    assert deadline.bekanntgabe_date >= dispatch + timedelta(days=BEKANNTGABE_DAYS)
    assert deadline.deadline >= deadline.bekanntgabe_date + timedelta(days=window)
    # Never more than two days of shifting at either end without a holiday set.
    assert deadline.total_days <= BEKANNTGABE_DAYS + window + 4


@given(dates)
def test_next_working_day_is_idempotent(day: date) -> None:
    once = next_working_day(day)
    assert next_working_day(once) == once
