"""Fabricated OHLCV; not quotations and never evidence of investment performance."""

import math
from datetime import date, timedelta
from typing import Any

from swing.calendar import bounds, next_session


def synthetic_dataset() -> dict[str, Any]:
    symbols = ("SPY", "MSFT", "IWM")
    rows = []
    day = date(2026, 1, 2)
    previous = dict.fromkeys(symbols, 100.0)
    for i in range(100):
        for symbol in symbols:
            if symbol == "SPY":
                close = 100 + i * 0.25 + 1.2 * math.sin(i * 0.9)
                volume = 1000000
            elif symbol == "MSFT":
                close = 100 + (i // 20) * 4 + 0.35 * math.sin(i)
                volume = 2400000 if i % 20 == 0 else 1000000
            else:
                phase = i % 30
                discount = {23: 4, 24: 8, 25: 3}.get(phase, 0)
                close = 100 - discount + math.sin(i) * 0.2
                volume = 1000000
            opened = previous[symbol] + 0.12 * math.sin(i * 2)
            if symbol == "MSFT" and i % 20 == 0:
                opened = close - 0.2
            # Deliberate small overnight differences; full-range OHLC stays coherent.
            rows.append(
                {
                    "symbol": symbol,
                    "date": str(day),
                    "open": f"{opened:.2f}",
                    "high": f"{max(opened, close) + 0.40:.2f}",
                    "low": f"{min(opened, close) - 0.40:.2f}",
                    "close": f"{close:.2f}",
                    "volume": volume,
                    "complete": True,
                    "published_at": (bounds(day)[1] + timedelta(minutes=5)).isoformat(),
                }
            )
            previous[symbol] = close
        day = next_session(day)
    return {
        "source": "SYNTHÉTIQUE — générateur déterministe v1, aucun cours réel",
        "synthetic": True,
        "price_basis": "raw",
        "corporate_actions": "none_attested",
        "assets": [
            {
                "symbol": s,
                "kind": "EQUITY" if s == "MSFT" else "ETF",
                "market": "US",
                "currency": "USD",
                "leveraged": False,
            }
            for s in symbols
        ],
        "bars": rows,
    }
