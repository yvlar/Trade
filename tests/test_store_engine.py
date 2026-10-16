from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal as D

import pytest

from swing.domain import money
from swing.engine import advance, snapshot
from swing.fixtures import synthetic_dataset
from swing.store import Store

NOW = datetime(2026, 10, 16, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "test.sqlite3")


def replay(store, count=100):
    with store.transaction() as db:
        cursor = Store.state(db)["cursor"]
    for _ in range(count):
        result = advance(store, cursor)
        cursor = result["cursor"]
        if result["status"] == "finished":
            break
    return snapshot(store)


def test_import_idempotent_immutable_provenance_and_received_at(store):
    payload = synthetic_dataset()
    assert store.ingest(payload, NOW)["inserted"] == 300
    assert store.ingest(payload, NOW)["duplicate"]
    snap = snapshot(store)
    assert snap["data"]["received_at"] == NOW.isoformat()
    revised = deepcopy(payload)
    revised["bars"][-1]["volume"] += 1
    with pytest.raises(ValueError, match="Révision"):
        store.ingest(revised, NOW)
    assert snapshot(store)["data"]["count"] == 300
    assert len(snapshot(store)["batches"]) == 1


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("complete", False),
        ("open", "NaN"),
        ("high", "Infinity"),
        ("low", "-1"),
        ("volume", 0),
        ("volume", True),
        ("volume", 1.5),
        ("symbol", "BTC/USD"),
        ("published_at", "2026-01-02T20:00:00+00:00"),
        ("published_at", "2026-01-02T21:05:00"),
        ("date", "2026-01-03"),
        ("close", "100.00001"),
        ("published_at", "2027-01-01T00:00:00+00:00"),
    ],
)
def test_invalid_import_is_atomic(store, key, value):
    payload = synthetic_dataset()
    payload["bars"][-1][key] = value
    with pytest.raises(ValueError):
        store.ingest(payload, NOW)
    snap = snapshot(store)
    assert snap["data"]["count"] == 0
    assert not snap["assets"]
    assert snap["dataset"] is None


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("market", "CA"),
        ("currency", "CAD"),
        ("kind", "FOREX"),
        ("kind", "CRYPTO"),
        ("leveraged", True),
    ],
)
def test_universe_restricted(store, key, value):
    payload = synthetic_dataset()
    payload["assets"][0][key] = value
    with pytest.raises(ValueError):
        store.ingest(payload, NOW)


def test_duplicate_and_corporate_actions_rejected(store):
    payload = synthetic_dataset()
    payload["bars"].append(payload["bars"][0])
    with pytest.raises(ValueError, match="Doublon"):
        store.ingest(payload, NOW)
    payload = synthetic_dataset()
    payload["corporate_actions"] = "split"
    with pytest.raises(ValueError, match="corporatifs"):
        store.ingest(payload, NOW)


def test_full_replay_conservation_restart_and_entry_after_signal(store):
    store.ingest(synthetic_dataset(), NOW)
    partial = replay(store, 46)
    reopened = Store(store.path)
    assert snapshot(reopened)["cursor"] == partial["cursor"]
    final = replay(reopened)
    assert len(final["equity_history"]) == 100
    assert final["trades"]
    assert {t["strategy"] for t in final["trades"]} == {"pullback", "breakout", "mean_reversion"}
    assert all(t["entry_date"] > t["signal_date"] for t in final["trades"])
    assert all(t["exit_date"] >= t["entry_date"] for t in final["trades"])
    assert D(final["cash"]) >= 0
    assert D(final["stats"]["equity"]) == D(final["cash"]) + sum(
        (money(D(p["mark"]) * p["quantity"]) for p in final["positions"]), D(0)
    )
    assert D(final["stats"]["equity"]) == D("10000") + D(final["stats"]["realized"]) + D(
        final["stats"]["unrealized"]
    )
    assert snapshot(reopened)["trades"] == final["trades"]


def test_future_mutations_do_not_change_any_past_decision_or_fill(tmp_path):
    left, right = Store(tmp_path / "left.db"), Store(tmp_path / "right.db")
    original = synthetic_dataset()
    changed = deepcopy(original)
    for b in changed["bars"][150:]:
        for k in ("open", "high", "low", "close"):
            b[k] = str(D(b[k]) * 3)
        b["volume"] *= 7
    left.ingest(original, NOW)
    right.ingest(changed, NOW)
    a, b = replay(left, 50), replay(right, 50)
    for key in ("decisions", "pending", "positions", "trades", "cash", "equity_history"):
        assert a[key] == b[key]


def test_missing_session_is_error_then_append_and_resume(store):
    payload = synthetic_dataset()
    missing = payload["bars"][3:6]
    payload["bars"] = payload["bars"][:3] + payload["bars"][6:]
    store.ingest(payload, NOW)
    advance(store, None)
    with pytest.raises(ValueError, match="manquante"):
        advance(store, "2026-01-02")
    snap = snapshot(store)
    assert snap["cursor"] == "2026-01-02"
    assert snap["journal"][0]["kind"] == "error"
    payload["bars"] = missing
    store.ingest(payload, NOW)
    assert advance(store, "2026-01-02")["cursor"] == "2026-01-05"


def test_late_publication_is_not_an_abstention(store):
    payload = synthetic_dataset()
    payload["bars"][0]["published_at"] = "2026-01-02T23:00:00+00:00"
    store.ingest(payload, NOW)
    with pytest.raises(ValueError, match="tardive"):
        advance(store, None)
    assert snapshot(store)["cursor"] is None


def test_atomic_failure_rolls_back_fills_and_session(store, monkeypatch):
    store.ingest(synthetic_dataset(), NOW)
    before = replay(store, 40)

    def fail(*args):
        raise RuntimeError("disk simulation")

    monkeypatch.setattr(Store, "save", fail)
    with pytest.raises(RuntimeError, match="disk"):
        advance(store, before["cursor"])
    after = snapshot(store)
    for key in ("cash", "positions", "trades", "cursor", "journal_count"):
        assert before[key] == after[key]


def test_concurrent_and_repeated_step_are_idempotent(store):
    store.ingest(synthetic_dataset(), NOW)
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _: advance(store, None), range(4)))
    assert sorted(r["status"] for r in results) == [
        "advanced",
        "unchanged",
        "unchanged",
        "unchanged",
    ]
    assert len(snapshot(store)["equity_history"]) == 1


def test_settings_lock_and_bounds(store):
    settings = snapshot(store)["settings"]
    bad = {**settings, "risk_pct": "0.8"}
    with pytest.raises(ValueError):
        store.configure(bad)
    store.configure({**settings, "capital": "5000"})
    assert snapshot(store)["cash"] == "5000"
    store.ingest(synthetic_dataset(), NOW)
    advance(store, None)
    with pytest.raises(ValueError, match="figés"):
        store.configure(settings)


def test_four_decimal_prices_preserve_cent_accounting(store):
    payload = synthetic_dataset()
    for b in payload["bars"]:
        for k in ("open", "high", "low", "close"):
            b[k] = str(D(b[k]) + D("0.0001"))
    store.ingest(payload, NOW)
    final = replay(store)
    assert len(final["equity_history"]) == 100
    assert final["trades"]


def test_end_of_calendar_is_finished_not_error(store):
    payload = synthetic_dataset()
    payload["bars"] = payload["bars"][:3]
    for bar in payload["bars"]:
        bar["date"] = "2028-12-29"
        bar["published_at"] = "2028-12-29T21:05:00+00:00"
    store.ingest(payload, datetime(2028, 12, 30, tzinfo=UTC))
    assert advance(store, None)["status"] == "advanced"
    assert advance(store, "2028-12-29")["status"] == "finished"


def test_entry_uses_open_only_not_same_day_high_low_close(tmp_path):
    payload = synthetic_dataset()
    baseline = Store(tmp_path / "baseline.db")
    baseline.ingest(payload, NOW)
    state = replay(baseline, 26)
    # Deterministic injection of an already known signal tests execution independently.
    state["positions"], state["pending"], state["trades"] = (
        [],
        [
            {
                "symbol": "SPY",
                "strategy": "pullback",
                "signal_date": state["cursor"],
                "ready_at": "2026-02-09T21:30:00+00:00",
                "reference": "107",
                "stop": "100",
                "target": "120",
            }
        ],
        [],
    )
    state["cash"] = "10000"
    day = payload["bars"][78]["date"]
    state["pending"][0]["ready_at"] = state["cursor"] + "T21:30:00+00:00"
    outputs = []
    for name, mutate in (("a", False), ("b", True)):
        db = Store(tmp_path / (name + ".db"))
        data = deepcopy(payload)
        if mutate:
            for b in data["bars"]:
                if b["date"] == day and b["symbol"] == "SPY":
                    b["high"], b["low"], b["close"] = "200", "50", "180"
        db.ingest(data, NOW)
        with db.transaction() as conn:
            Store.save(conn, state)
        advance(db, state["cursor"])
        with db.transaction() as conn:
            outputs.append(
                conn.execute("SELECT detail FROM journal WHERE kind='entry'").fetchall()[0][0]
            )
    assert outputs[0] == outputs[1]
