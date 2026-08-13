"""Bekanntgabefiktion and response deadlines: pure calendar arithmetic.

**Nothing in this module reads a clock.** Every function takes the dates it
needs, which is the same discipline the completeness checker's absolute date
bounds follow (part 03): a frozen gold set may not start failing on a Tuesday,
and a deadline that depends on when the test ran is not a deadline.

Why the module exists in part 08 although nothing is dispatched here: the
absolute deadline of a Nachforderung depends on the DISPATCH date, and at draft
time there is no dispatch date - the draft is waiting for a caseworker. So the
letter states the window relatively ("innerhalb von X Tagen nach Bekanntgabe")
and this arithmetic ships tested, for part 10 to call at the moment it stamps
the dispatch timestamp into the journal.

The rules, and exactly which ones are calendar-deterministic:

* **par. 37 Abs. 2 S. 1 SGB X** - a written Verwaltungsakt posted within
  Germany counts as bekannt gegeben on the FOURTH day after it was handed to
  the post. Since the Postrechtsmodernisierungsgesetz (in force 1 January 2025)
  the period is four days rather than three, and a fiction date falling on a
  Saturday, Sunday or public holiday moves to the next working day.
* **par. 26 Abs. 3 SGB X** - the same shift applies to the end of the response
  period itself.
* **The list of public holidays is NOT in this module.** German holidays are
  Land-specific (Fronleichnam, Reformationstag, Mariae Himmelfahrt) and depend
  on where the letter is served, which is Verwaltungswissen this repository
  cannot cite. Holidays are therefore an injected set, empty by default, and
  the caller that knows the Land supplies it. An invented holiday table would
  compute a wrong deadline confidently, which is worse than a Saturday shift
  and no holidays.

What this module deliberately does NOT do: decide whether the fiction applies
at all. Par. 37 Abs. 2 S. 2 SGB X ("dies gilt nicht, wenn der Verwaltungsakt
nicht oder zu einem spaeteren Zeitpunkt zugegangen ist") is a question about
reality that only the case file can answer, and a function that quietly assumed
service would be asserting something it cannot know.
"""

from __future__ import annotations

from collections.abc import Container
from dataclasses import dataclass
from datetime import date, timedelta

#: par. 37 Abs. 2 S. 1 SGB X, in the version in force since 1 January 2025.
BEKANNTGABE_DAYS = 4

#: ``date.weekday()`` values that are not Werktage. Saturday counts as a
#: Sonnabend under par. 26 Abs. 3 SGB X, so both weekend days shift.
NON_WORKING_WEEKDAYS = frozenset({5, 6})

#: The upper bound on a response window is NOT here: it is a config question and
#: it has exactly one editable home, the ``response_window_days`` field of
#: ``DraftingConfig`` (ADR-009). This module refuses only what arithmetic cannot
#: mean - a window of zero or fewer days.


@dataclass(frozen=True)
class ResponseDeadline:
    """The three dates a dispatched Nachforderung produces, and why they moved.

    ``shifted`` names the steps that had to move onto a working day, so a
    caseworker reading the journal can see WHY a deadline is not simply
    dispatch plus four plus the window.
    """

    dispatch_date: date
    bekanntgabe_date: date
    deadline: date
    window_days: int
    shifted: tuple[str, ...] = ()

    @property
    def total_days(self) -> int:
        """Calendar days between dispatch and the deadline, after shifting."""
        return (self.deadline - self.dispatch_date).days

    def as_payload(self) -> dict[str, object]:
        """Journal-shaped description; dates only, never a person."""
        return {
            "dispatch_date": self.dispatch_date.isoformat(),
            "bekanntgabe_date": self.bekanntgabe_date.isoformat(),
            "deadline": self.deadline.isoformat(),
            "window_days": self.window_days,
            "total_days": self.total_days,
            "shifted": list(self.shifted),
            "basis": "par. 37 Abs. 2 SGB X, par. 26 Abs. 3 SGB X",
        }


def is_working_day(day: date, *, holidays: Container[date] = frozenset()) -> bool:
    """Whether ``day`` is a Werktag: not a weekend, not an injected holiday."""
    return day.weekday() not in NON_WORKING_WEEKDAYS and day not in holidays


def next_working_day(day: date, *, holidays: Container[date] = frozenset()) -> date:
    """``day`` itself when it is a Werktag, else the next one.

    Bounded rather than a ``while True``: a holiday set that covered every day
    of a year would otherwise hang a request. Eight steps clears any real run
    of weekend and holidays (Christmas into New Year is the longest in German
    practice) and a longer one is a broken holiday set, not a calendar.
    """
    candidate = day
    for _ in range(8):
        if is_working_day(candidate, holidays=holidays):
            return candidate
        candidate += timedelta(days=1)
    raise ValueError(
        f"no working day within 8 days of {day.isoformat()}; the injected "
        f"holiday set covers more than a week and cannot be right"
    )


def bekanntgabe_date(
    dispatch_date: date, *, holidays: Container[date] = frozenset()
) -> date:
    """When a letter posted on ``dispatch_date`` counts as bekannt gegeben.

    Four days later (par. 37 Abs. 2 S. 1 SGB X), moved to the next working day
    when that lands on a Saturday, Sunday or injected holiday.
    """
    return next_working_day(
        dispatch_date + timedelta(days=BEKANNTGABE_DAYS), holidays=holidays
    )


def response_deadline(
    dispatch_date: date,
    *,
    window_days: int,
    holidays: Container[date] = frozenset(),
) -> ResponseDeadline:
    """The absolute deadline a relative window produces once a letter is sent.

    Args:
        dispatch_date: the day the letter was handed to the post. Part 10
            passes the date it journals as the dispatch timestamp; nothing here
            invents it.
        window_days: the response window the drafting config states, the same
            number the letter printed as "innerhalb von X Tagen".
        holidays: public holidays at the place of service. Empty by default and
            deliberately not guessed.
    """
    if window_days < 1:
        raise ValueError(
            f"a response window of {window_days} days is not a window; the "
            f"upper bound is a config question and lives in DraftingConfig"
        )
    fiction_raw = dispatch_date + timedelta(days=BEKANNTGABE_DAYS)
    fiction = next_working_day(fiction_raw, holidays=holidays)
    deadline_raw = fiction + timedelta(days=window_days)
    deadline = next_working_day(deadline_raw, holidays=holidays)
    shifted: list[str] = []
    if fiction != fiction_raw:
        shifted.append("bekanntgabe")
    if deadline != deadline_raw:
        shifted.append("deadline")
    return ResponseDeadline(
        dispatch_date=dispatch_date,
        bekanntgabe_date=fiction,
        deadline=deadline,
        window_days=window_days,
        shifted=tuple(shifted),
    )
