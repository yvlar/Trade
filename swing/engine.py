"""Incremental replay orchestrator. One atomic, restartable transaction per US session."""

import hashlib
import json
import sqlite3
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from swing.calendar import bounds, next_session
from swing.domain import Settings, decisions, fill_price, money, protective_exit, size_position
from swing.store import Store, bar_json, encode, read_bar

D = Decimal


def get_settings(state: dict[str, Any]) -> Settings:
    parsed: dict[str, Any] = {
        k: v if k in ("max_positions", "max_hold") else D(v) for k, v in state["settings"].items()
    }
    return Settings(**parsed)


def portfolio_equity(state: dict[str, Any], marks: dict[str, Decimal]) -> Decimal:
    return D(state["cash"]) + sum(
        (money(marks[p["symbol"]] * p["quantity"]) for p in state["positions"]), D(0)
    )


def sell(
    db: sqlite3.Connection,
    state: dict[str, Any],
    p: dict[str, Any],
    raw: Decimal,
    reason: str,
    day: str,
    settings: Settings,
) -> None:
    price = fill_price(raw, False, settings)
    proceeds = money(price * p["quantity"] - settings.fee)
    pnl = proceeds - D(p["cost"])
    state["cash"] = str(money(D(state["cash"]) + proceeds))
    trade = {
        **p,
        "exit_date": day,
        "exit": str(price),
        "pnl": str(pnl),
        "exit_fee": str(settings.fee),
        "exit_reason": reason,
    }
    state["trades"].append(trade)
    state["positions"].remove(p)
    Store.event(db, "exit", trade, day)


def advance(store: Store, expected_cursor: str | None) -> dict[str, Any]:
    try:
        return _advance(store, expected_cursor)
    except ValueError as exc:
        with store.transaction() as db:
            Store.event(db, "error", {"message": str(exc)})
        raise


def _advance(store: Store, expected_cursor: str | None) -> dict[str, Any]:
    with store.transaction() as db:
        state = store.state(db)
        if state["cursor"] != expected_cursor:
            return {"status": "unchanged", "cursor": state["cursor"]}
        span = db.execute("SELECT MIN(day),MAX(day) FROM bars").fetchone()
        if span[0] is None:
            raise ValueError("Importer des bougies D1 clôturées avant de démarrer")
        if state["cursor"] is not None and state["cursor"] >= span[1]:
            return {"status": "finished", "cursor": state["cursor"]}
        day = (
            date.fromisoformat(span[0])
            if state["cursor"] is None
            else next_session(date.fromisoformat(state["cursor"]))
        )
        label = day.isoformat()
        if label > span[1]:
            return {"status": "finished", "cursor": state["cursor"]}
        current = {
            row[0]: read_bar(row[1])
            for row in db.execute(
                "SELECT symbol,data FROM bars WHERE day=? ORDER BY symbol", (label,)
            )
        }
        universe = [row[0] for row in db.execute("SELECT symbol FROM assets ORDER BY symbol")]
        missing = sorted(set(universe) - current.keys())
        if missing:
            raise ValueError(
                f"Séance {label} manquante pour {', '.join(missing)} ; reprise suspendue"
            )
        opened, closed = bounds(day)
        decision_at = closed + timedelta(minutes=30)
        for bar in current.values():
            if bar.published_at > decision_at:
                raise ValueError(
                    f"Publication tardive {bar.symbol} {label} : indisponible à clôture + 30 min"
                )
        settings = get_settings(state)
        # Only prices known at the open may fund or size another open entry.
        for p in list(state["positions"]):
            bar = current[p["symbol"]]
            if bar.open <= D(p["stop"]):
                sell(db, state, p, bar.open, "Gap sous le stop", label, settings)
            elif bar.open >= D(p["target"]):
                sell(db, state, p, D(p["target"]), "Objectif à l'ouverture", label, settings)
            elif p["age"] >= settings.max_hold:
                sell(db, state, p, bar.open, "Durée maximale : ouverture suivante", label, settings)
        for order in state["pending"]:
            bar = current[order["symbol"]]
            reason = ""
            entry = fill_price(bar.open, True, settings)
            if order["signal_date"] >= label or order["ready_at"] >= opened.isoformat():
                raise ValueError("Violation causale : signal indisponible avant l'entrée")
            if any(p["symbol"] == bar.symbol for p in state["positions"]):
                reason = "Position déjà ouverte"
            elif len(state["positions"]) >= settings.max_positions:
                reason = "Limite de positions atteinte"
            elif not D(order["stop"]) < entry < D(order["target"]):
                reason = "Ouverture hors stop/objectif"
            elif abs(bar.open / D(order["reference"]) - 1) > settings.max_gap_pct:
                reason = "Gap supérieur à la limite configurée"
            equity = portfolio_equity(state, {s: b.open for s, b in current.items()})
            open_risk = sum(
                (
                    max(D(0), current[p["symbol"]].open - fill_price(D(p["stop"]), False, settings))
                    * p["quantity"]
                    + settings.fee
                    for p in state["positions"]
                ),
                D(0),
            )
            qty = size_position(
                D(state["cash"]) - settings.fee * len(state["positions"]),
                equity,
                entry,
                D(order["stop"]),
                open_risk,
                settings,
            )
            if qty == 0:
                reason = reason or "Budget de risque ou espèces insuffisants"
            if reason:
                Store.event(db, "risk_rejection", {**order, "reason": reason}, label)
                continue
            cost = money(entry * qty + settings.fee)
            state["cash"] = str(money(D(state["cash"]) - cost))
            p = {
                **order,
                "entry_date": label,
                "entry": str(entry),
                "quantity": qty,
                "cost": str(cost),
                "entry_fee": str(settings.fee),
                "age": 0,
                "mark": str(entry),
            }
            state["positions"].append(p)
            Store.event(db, "entry", p, label)
        state["pending"] = []
        for p in list(state["positions"]):
            bar = current[p["symbol"]]
            outcome = protective_exit(bar, D(p["stop"]), D(p["target"]))
            if outcome:
                sell(db, state, p, *outcome, label, settings)
            else:
                p["age"] += 1
                p["mark"] = str(bar.close)
        state["decisions"] = []
        for symbol in universe:
            history = [
                read_bar(r[0])
                for r in db.execute(
                    "SELECT data FROM bars WHERE symbol=? AND day<=? ORDER BY day", (symbol, label)
                )
            ]
            if any(b.published_at > decision_at for b in history):
                raise ValueError("Historique non disponible à la date de décision")
            fingerprint = hashlib.sha256(
                encode([bar_json(b) for b in history]).encode()
            ).hexdigest()
            selected = False
            for d in decisions(history):
                detail = {
                    "symbol": symbol,
                    "strategy": d.strategy,
                    "status": d.status,
                    "reason": d.reason,
                    "stop": str(d.stop) if d.stop else None,
                    "target": str(d.target) if d.target else None,
                    "signal_date": label,
                    "ready_at": decision_at.isoformat(),
                    "reference": str(history[-1].close),
                    "evidence_hash": fingerprint,
                }
                state["decisions"].append(detail)
                if d.status == "candidate" and not selected:
                    # One order per symbol; fixed, documented strategy precedence.
                    state["pending"].append(detail)
                    selected = True
                Store.event(db, "decision", detail, label)
        equity = money(portfolio_equity(state, {s: b.close for s, b in current.items()}))
        if D(state["cash"]) < 0 or any(p["quantity"] <= 0 for p in state["positions"]):
            raise ValueError("Invariant cash/positions violé")
        realized = sum((D(t["pnl"]) for t in state["trades"]), D(0))
        unrealized = sum(
            (money(D(p["mark"]) * p["quantity"]) - D(p["cost"]) for p in state["positions"]),
            D(0),
        )
        if equity != settings.capital + realized + unrealized:
            raise ValueError("Conservation du capital violée")
        state["equity_history"].append({"date": label, "equity": str(equity)})
        state["cursor"] = label
        db.execute("INSERT INTO sessions VALUES(?)", (label,))
        Store.save(db, state)
        Store.event(db, "session", {"equity": str(equity), "cash": state["cash"]}, label)
        return {"status": "advanced", "cursor": label}


def snapshot(store: Store) -> dict[str, Any]:
    with store.transaction() as db:
        state = Store.state(db)
        metadata = db.execute("SELECT value FROM metadata WHERE key='dataset'").fetchone()
        state["dataset"] = json.loads(metadata[0]) if metadata else None
        state["assets"] = [
            json.loads(r[0]) for r in db.execute("SELECT data FROM assets ORDER BY symbol")
        ]
        summary = db.execute(
            "SELECT COUNT(*),MIN(day),MAX(day),MAX(received_at) FROM bars"
        ).fetchone()
        state["data"] = dict(zip(("count", "first", "last", "received_at"), summary, strict=True))
        state["batches"] = [
            dict(r) for r in db.execute("SELECT * FROM batches ORDER BY received_at")
        ]
        state["journal"] = [
            {**dict(r), "detail": json.loads(r["detail"])}
            for r in db.execute("SELECT * FROM journal ORDER BY id DESC LIMIT 250")
        ]
        state["journal_count"] = db.execute("SELECT COUNT(*) FROM journal").fetchone()[0]
        history = state["equity_history"]
        capital = D(state["settings"]["capital"])
        equity = D(history[-1]["equity"]) if history else capital
        peak, drawdown = capital, D(0)
        for item in history:
            value = D(item["equity"])
            peak = max(peak, value)
            drawdown = max(drawdown, (peak - value) / peak)
        pnl = sum((D(t["pnl"]) for t in state["trades"]), D(0))
        state["stats"] = {
            "equity": str(equity),
            "realized": str(pnl),
            "unrealized": str(equity - capital - pnl),
            "return_pct": str((equity / capital - 1) * 100),
            "drawdown_pct": str(drawdown * 100),
            "trades": len(state["trades"]),
            "wins": sum(D(t["pnl"]) > 0 for t in state["trades"]),
        }
        return state
