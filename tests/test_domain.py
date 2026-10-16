from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal as D

import pytest

from swing.broker import AlpacaDisabled
from swing.calendar import bounds, is_session, next_session
from swing.domain import Bar, Settings, decisions, fill_price, protective_exit, size_position
from swing.fixtures import synthetic_dataset
from swing.store import validate_import


def bar(o="100", high="110", low="90", close="100"):
    return Bar(
        "SPY",
        date(2026, 1, 2),
        D(o),
        D(high),
        D(low),
        D(close),
        1000,
        datetime(2026, 1, 2, 21, 5, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    ("day", "open_hour", "close_hour"),
    [
        ("2026-03-06", 14, 21),
        ("2026-03-09", 13, 20),
        ("2026-11-27", 14, 18),
        ("2026-12-24", 14, 18),
        ("2027-11-26", 14, 18),
        ("2028-07-03", 13, 17),
    ],
)
def test_calendar_timezone_and_early_close(day, open_hour, close_hour):
    opened, closed = bounds(date.fromisoformat(day))
    assert (opened.hour, closed.hour) == (open_hour, close_hour)
    assert opened.minute == 30


def test_holidays_and_bounded_calendar():
    assert not is_session(date(2026, 7, 3))
    assert next_session(date(2026, 7, 2)) == date(2026, 7, 6)
    assert not is_session(date(2026, 4, 3))
    with pytest.raises(ValueError, match="2026"):
        is_session(date(2029, 1, 2))
    with pytest.raises(ValueError):
        bounds(date(2026, 1, 3))


@pytest.mark.parametrize(
    ("candle", "price", "reason"),
    [
        (bar(), "95", "Stop prioritaire"),
        (bar(o="90"), "90", "Gap"),
        (bar(o="110"), "105", "Objectif"),
        (bar(low="96"), "105", "Objectif"),
    ],
)
def test_conservative_exits(candle, price, reason):
    outcome = protective_exit(candle, D("95"), D("105"))
    assert outcome[0] == D(price)
    assert reason in outcome[1]


def test_no_exit_when_no_level_touched():
    assert protective_exit(bar(high="104", low="96"), D("95"), D("105")) is None


def test_adverse_fills_and_fee_risk_budget():
    s = Settings()
    assert fill_price(D("100"), True, s) == D("100.05")
    assert fill_price(D("100"), False, s) == D("99.95")
    q = size_position(D("10000"), D("10000"), D("100.05"), D("95"), D(0), s)
    assert q > 0
    assert D(q) * (D("100.05") - fill_price(D("95"), False, s)) + 2 * s.fee <= D("50")
    assert D(q) * D("100.05") + s.fee <= D("2000")
    assert size_position(D("1"), D("10000"), D("100"), D("95"), D(0), s) == 0
    assert size_position(D("10000"), D("10000"), D("100"), D("95"), D("200"), s) == 0


def test_all_three_strategies_produce_signals_on_fabricated_histories():
    _, bars = validate_import(synthetic_dataset(), datetime(2026, 10, 16, tzinfo=UTC))
    found = set()
    for symbol in ("SPY", "MSFT", "IWM"):
        history = [b for b in bars if b.symbol == symbol]
        for end in range(25, len(history) + 1):
            found.update(d.strategy for d in decisions(history[:end]) if d.status == "candidate")
    assert found == {"pullback", "breakout", "mean_reversion"}


def test_error_is_not_abstention_and_warmup_distinct():
    assert {d.status for d in decisions([bar()])} == {"insufficient"}
    flat = replace(bar(), open=D(100), high=D(100), low=D(100), close=D(100))
    assert {d.status for d in decisions([flat] * 25)} == {"error"}


def test_broker_disabled_unconditionally():
    with pytest.raises(RuntimeError, match="désactivé"):
        AlpacaDisabled().submit_long("SPY", 1, D(100))


def test_cash_sizing_reserves_exit_fee_even_if_price_gaps_to_zero():
    s = Settings(
        risk_pct=D("0.02"), max_exposure_pct=D("0.25"), max_total_risk_pct=D("0.05"), fee=D("1")
    )
    qty = size_position(D("20"), D("10000"), D("1"), D("0.99"), D(0), s)
    assert qty == 18
    assert D("20") - qty * D(1) - s.fee - s.fee >= 0
