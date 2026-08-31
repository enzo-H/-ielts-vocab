"""SM-2 spaced repetition scheduler (stdlib only).

Grades (Anki-style, mapped to SM-2 quality):
    again=0  forgot the word
    hard=3   recalled with difficulty
    good=4   recalled normally
    easy=5   recalled instantly

State fields per word:
    repetitions  consecutive successful recalls
    easiness     EF factor, clamped to [1.3, 2.8]
    interval     current interval in days
    due          ISO date string (YYYY-MM-DD) the word is next reviewable
    lapses       total times marked "again"
    last_grade   last grade received
    last_review  ISO date of last review
"""

from __future__ import annotations

import datetime as _dt

MIN_EF = 1.3
MAX_EF = 2.8
LEECH_LAPSES = 8

GRADE_MAP = {"again": 0, "hard": 3, "good": 4, "easy": 5}


def normalize_grade(value) -> int:
    """Accept 'good'/4/'4'; raise ValueError otherwise."""
    if isinstance(value, str):
        key = value.strip().lower()
        if key in GRADE_MAP:
            return GRADE_MAP[key]
        if key.isdigit():
            value = int(key)
        else:
            raise ValueError(f"invalid grade: {value!r} (use again/hard/good/easy)")
    value = int(value)
    if value in (0, 3, 4, 5):
        return value
    raise ValueError(f"invalid grade: {value!r} (use again/hard/good/easy or 0/3/4/5)")


def today() -> _dt.date:
    return _dt.date.today()


def new_state() -> dict:
    return {
        "repetitions": 0,
        "easiness": 2.5,
        "interval": 0,
        "due": today().isoformat(),
        "lapses": 0,
        "last_grade": None,
        "last_review": None,
    }


def review(state: dict, grade_value) -> dict:
    """Apply SM-2 to one word's state; return the new state dict."""
    q = normalize_grade(grade_value)
    s = dict(state)
    ef = s.get("easiness", 2.5)
    rep = s.get("repetitions", 0)
    interval = s.get("interval", 0)

    # easiness update (standard SM-2 formula)
    ef = ef + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))
    ef = max(MIN_EF, min(MAX_EF, round(ef, 3)))

    if q >= 3:
        rep += 1
        if rep == 1:
            interval = 1
        elif rep == 2:
            interval = 6
        else:
            interval = max(1, round(interval * ef))
    else:
        rep = 0
        interval = 0  # due today: requeue in the same session
        s["lapses"] = s.get("lapses", 0) + 1

    s.update({
        "repetitions": rep,
        "easiness": ef,
        "interval": interval,
        "due": (today() + _dt.timedelta(days=interval)).isoformat(),
        "last_grade": q,
        "last_review": today().isoformat(),
    })
    return s


def is_due(state: dict, on: _dt.date | None = None) -> bool:
    d = on or today()
    try:
        return _dt.date.fromisoformat(state.get("due", "2000-01-01")) <= d
    except ValueError:
        return True


def is_leech(state: dict) -> bool:
    return state.get("lapses", 0) >= LEECH_LAPSES


def human_summary(state: dict) -> str:
    iv = state.get("interval", 0)
    if iv == 0:
        when = "today (relearn)"
    elif iv == 1:
        when = "tomorrow"
    else:
        when = f"in {iv} days"
    return f"EF {state.get('easiness', 2.5):.2f} · next {when} · due {state.get('due')}"
