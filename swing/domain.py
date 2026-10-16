"""Pure financial functions: no I/O, no wall clock, no future bar access."""

from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Any

D = Decimal
CENT = D("0.01")
STRATEGIES = ("pullback", "breakout", "mean_reversion")
LABELS = {
    "pullback": "Repli de tendance",
    "breakout": "Cassure de consolidation",
    "mean_reversion": "Retour à la moyenne",
}


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Bar:
    symbol: str
    day: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    published_at: datetime


@dataclass(frozen=True)
class Settings:
    capital: Decimal = D("10000")
    risk_pct: Decimal = D("0.005")
    max_exposure_pct: Decimal = D("0.20")
    max_total_risk_pct: Decimal = D("0.02")
    max_positions: int = 4
    fee: Decimal = D("1.00")
    slippage_bps: Decimal = D("5")
    max_gap_pct: Decimal = D("0.03")
    max_hold: int = 10

    def json(self) -> dict[str, Any]:
        return {k: str(v) if isinstance(v, Decimal) else v for k, v in asdict(self).items()}


@dataclass(frozen=True)
class Decision:
    strategy: str
    status: str
    reason: str
    stop: Decimal | None = None
    target: Decimal | None = None


def ema(values: list[Decimal], length: int) -> Decimal:
    result = values[0]
    weight = D(2) / D(length + 1)
    for value in values[1:]:
        result += weight * (value - result)
    return result


def atr(bars: list[Bar], length: int = 14) -> Decimal:
    ranges = [
        max(b.high - b.low, abs(b.high - a.close), abs(b.low - a.close))
        for a, b in zip(bars[:-1], bars[1:], strict=True)
    ]
    return sum(ranges[-length:], D(0)) / D(length)


def decisions(history: list[Bar]) -> list[Decision]:
    if len(history) < 25:
        return [
            Decision(s, "insufficient", "Historique insuffisant : 25 séances requises")
            for s in STRATEGIES
        ]
    b, prev = history[-1], history[-2]
    closes = [v.close for v in history]
    fast, slow = ema(closes, 10), ema(closes, 20)
    previous_slow = ema(closes[:-1], 20)
    volatility = atr(history)
    if volatility <= 0:
        return [Decision(s, "error", "ATR nul : risque non calculable") for s in STRATEGIES]
    window = history[-11:-1]
    top, bottom = max(v.high for v in window), min(v.low for v in window)
    avg_volume = sum(v.volume for v in window) / 10
    average = sum(closes[-21:-1], D(0)) / D(20)
    prior_mean = sum(closes[-22:-2], D(0)) / D(20)
    checks = {
        "pullback": (
            fast > slow > previous_slow and b.low <= fast and b.close > fast and b.close > prev.high
        ),
        "breakout": (
            (top - bottom) / bottom <= D("0.06")
            and b.close > top
            and D(b.volume) >= D(str(avg_volume)) * D("1.5")
        ),
        "mean_reversion": (
            prev.close < prior_mean - volatility * D("1.5")
            and b.close > prev.high
            and b.close < average
        ),
    }
    reasons = {
        "pullback": (
            "EMA10 > EMA20 en hausse, repli sur EMA10 et clôture au-dessus du haut précédent"
        ),
        "breakout": "Consolidation 10 séances ≤ 6 %, clôture au-dessus du haut et volume ≥ 1,5×",
        "mean_reversion": "Écart précédent > 1,5 ATR sous la moyenne, reprise du haut précédent",
    }
    result = []
    for strategy, passed in checks.items():
        stop = (b.close - volatility * D("1.5")).quantize(CENT, rounding=ROUND_FLOOR)
        target = (
            average if strategy == "mean_reversion" else b.close + (b.close - stop) * 2
        ).quantize(CENT, rounding=ROUND_FLOOR)
        passed = passed and D(0) < stop < b.close < target
        result.append(
            Decision(
                strategy,
                "candidate" if passed else "abstention",
                reasons[strategy] if passed else "Conditions non réunies : " + reasons[strategy],
                stop if passed else None,
                target if passed else None,
            )
        )
    return result


def fill_price(raw: Decimal, buy: bool, settings: Settings) -> Decimal:
    multiplier = 1 + settings.slippage_bps / D(10000) * (1 if buy else -1)
    return (raw * multiplier).quantize(CENT, rounding=ROUND_CEILING if buy else ROUND_FLOOR)


def size_position(
    cash: Decimal,
    equity: Decimal,
    entry: Decimal,
    stop: Decimal,
    open_risk: Decimal,
    settings: Settings,
) -> int:
    risk_unit = entry - fill_price(stop, False, settings)
    if risk_unit <= 0 or cash <= settings.fee:
        return 0
    budget = (
        min(equity * settings.risk_pct, equity * settings.max_total_risk_pct - open_risk)
        - settings.fee * 2
    )
    if budget <= 0:
        return 0
    return max(
        0,
        int(
            min(
                (cash - settings.fee * 2) / entry,
                (equity * settings.max_exposure_pct - settings.fee) / entry,
                budget / risk_unit,
            )
        ),
    )


def protective_exit(bar: Bar, stop: Decimal, target: Decimal) -> tuple[Decimal, str] | None:
    # Overnight gaps first. If both levels cross intraday, stop has priority.
    if bar.open <= stop:
        return bar.open, "Gap sous le stop"
    if bar.open >= target:
        return target, "Objectif à l'ouverture (prix limité conservateur)"
    if bar.low <= stop:
        return stop, "Stop prioritaire si deux seuils touchés"
    if bar.high >= target:
        return target, "Objectif atteint"
    return None
