"""SQLite adapter. Each import and each replay session is one durable transaction."""

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from swing.calendar import bounds
from swing.domain import Bar, Settings


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("Nombre décimal attendu")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Nombre décimal invalide") from exc
    if not number.is_finite():
        raise ValueError("Les valeurs NaN et infinies sont interdites")
    return number


def instant(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Horodatage ISO avec fuseau requis")
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("Fuseau horaire obligatoire")
    return result.astimezone(UTC)


def exact_keys(obj: Any, keys: set[str]) -> None:
    if not isinstance(obj, dict) or set(obj) != keys:
        raise ValueError("Champs attendus : " + ", ".join(sorted(keys)))


def validate_import(payload: Any, now: datetime) -> tuple[list[dict[str, Any]], list[Bar]]:
    exact_keys(
        payload, {"source", "synthetic", "price_basis", "corporate_actions", "assets", "bars"}
    )
    if not isinstance(payload["source"], str) or not 3 <= len(payload["source"]) <= 120:
        raise ValueError("Provenance obligatoire (3 à 120 caractères)")
    if type(payload["synthetic"]) is not bool or payload["price_basis"] != "raw":
        raise ValueError("synthetic doit être booléen ; seuls les prix bruts sont acceptés")
    if payload["corporate_actions"] != "none_attested":
        raise ValueError("Événements corporatifs non gérés : attestation d'absence requise")
    assets = payload["assets"]
    if not isinstance(assets, list) or not 1 <= len(assets) <= 20:
        raise ValueError("Entre 1 et 20 instruments requis")
    symbols = set()
    for asset in assets:
        exact_keys(asset, {"symbol", "kind", "market", "currency", "leveraged"})
        symbol = asset["symbol"]
        if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", symbol):
            raise ValueError("Symbole US invalide")
        if symbol in symbols:
            raise ValueError("Instrument dupliqué")
        symbols.add(symbol)
        if (
            asset["kind"] not in ("EQUITY", "ETF")
            or asset["market"] != "US"
            or asset["currency"] != "USD"
            or asset["leveraged"] is not False
        ):
            raise ValueError("Actions/FNB US en USD sans levier uniquement")
    rows = payload["bars"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 20000:
        raise ValueError("Entre 1 et 20 000 bougies par import")
    seen = set()
    bars = []
    for row in rows:
        exact_keys(
            row,
            {
                "symbol",
                "date",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "published_at",
                "complete",
            },
        )
        if not isinstance(row["symbol"], str) or row["symbol"] not in symbols:
            raise ValueError("Bougie sans instrument déclaré")
        if not isinstance(row["date"], str):
            raise ValueError("Date ISO requise")
        day = date.fromisoformat(row["date"])
        _, closed = bounds(day)
        published = instant(row["published_at"])
        if row["complete"] is not True or not closed <= published <= now:
            raise ValueError("Bougie incomplète, non clôturée ou publication invalide")
        key = (row["symbol"], day)
        if key in seen:
            raise ValueError("Doublon dans le fichier : import atomique refusé")
        seen.add(key)
        o, h, lo, c = [decimal(row[k]) for k in ("open", "high", "low", "close")]
        if (
            any(x <= 0 or x > 1000000 or x != x.quantize(Decimal("0.0001")) for x in (o, h, lo, c))
            or not lo <= min(o, c) <= max(o, c) <= h
        ):
            raise ValueError("OHLC incohérent, non positif ou précision > 4 décimales")
        volume = row["volume"]
        if type(volume) is not int or not 0 < volume <= 10**12:
            raise ValueError("Volume entier strictement positif requis")
        bars.append(Bar(row["symbol"], day, o, h, lo, c, volume, published))
    return assets, sorted(bars, key=lambda b: (b.day, b.symbol))


def bar_json(bar: Bar) -> dict[str, Any]:
    return {
        "symbol": bar.symbol,
        "date": bar.day.isoformat(),
        "open": str(bar.open),
        "high": str(bar.high),
        "low": str(bar.low),
        "close": str(bar.close),
        "volume": bar.volume,
        "published_at": bar.published_at.isoformat(),
    }


def read_bar(raw: str) -> Bar:
    row = json.loads(raw)
    return Bar(
        row["symbol"],
        date.fromisoformat(row["date"]),
        Decimal(row["open"]),
        Decimal(row["high"]),
        Decimal(row["low"]),
        Decimal(row["close"]),
        row["volume"],
        instant(row["published_at"]),
    )


def initial_state(settings: Settings) -> dict[str, Any]:
    return {
        "cursor": None,
        "cash": str(settings.capital),
        "positions": [],
        "pending": [],
        "decisions": [],
        "trades": [],
        "equity_history": [],
        "settings": settings.json(),
    }


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS assets(symbol TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS bars(
                    symbol TEXT NOT NULL, day TEXT NOT NULL, data TEXT NOT NULL,
                    received_at TEXT NOT NULL, batch TEXT NOT NULL, PRIMARY KEY(symbol,day));
                CREATE TABLE IF NOT EXISTS batches(
                    hash TEXT PRIMARY KEY, received_at TEXT NOT NULL, rows INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY CHECK(id=1), data TEXT);
                CREATE TABLE IF NOT EXISTS journal(
                    id INTEGER PRIMARY KEY, recorded_at TEXT NOT NULL, day TEXT,
                    kind TEXT NOT NULL, detail TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions(day TEXT PRIMARY KEY);
            """)
            db.execute(
                "INSERT OR IGNORE INTO state VALUES(1,?)", (encode(initial_state(Settings())),)
            )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("BEGIN IMMEDIATE")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def state(db: sqlite3.Connection) -> dict[str, Any]:
        result: dict[str, Any] = json.loads(
            db.execute("SELECT data FROM state WHERE id=1").fetchone()[0]
        )
        return result

    @staticmethod
    def save(db: sqlite3.Connection, state: dict[str, Any]) -> None:
        db.execute("UPDATE state SET data=? WHERE id=1", (encode(state),))

    @staticmethod
    def event(db: sqlite3.Connection, kind: str, detail: Any, day: str | None = None) -> None:
        db.execute(
            "INSERT INTO journal(recorded_at,day,kind,detail) VALUES(?,?,?,?)",
            (timestamp(), day, kind, encode(detail)),
        )

    def ingest(self, payload: Any, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        assets, bars = validate_import(payload, now)
        digest = hashlib.sha256(encode(payload).encode()).hexdigest()
        received = now.isoformat()
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM batches WHERE hash=?", (digest,)).fetchone():
                return {"inserted": 0, "duplicate": True, "hash": digest}
            state = self.state(db)
            meta = {
                key: payload[key]
                for key in ("source", "synthetic", "price_basis", "corporate_actions")
            }
            previous = db.execute("SELECT value FROM metadata WHERE key='dataset'").fetchone()
            if previous and json.loads(previous[0]) != meta:
                raise ValueError(
                    "Un seul jeu de données/provenance par base : créer une autre base"
                )
            db.execute("INSERT OR IGNORE INTO metadata VALUES('dataset',?)", (encode(meta),))
            existing = {r[0]: r[1] for r in db.execute("SELECT symbol,data FROM assets")}
            supplied = {a["symbol"]: encode(a) for a in assets}
            if existing and existing != supplied:
                raise ValueError(
                    "Univers immuable : déclarer les mêmes instruments à chaque import"
                )
            for symbol, data in supplied.items():
                db.execute("INSERT OR IGNORE INTO assets VALUES(?,?)", (symbol, data))
            inserted = 0
            for bar in bars:
                day, data = bar.day.isoformat(), encode(bar_json(bar))
                old = db.execute(
                    "SELECT data FROM bars WHERE symbol=? AND day=?", (bar.symbol, day)
                ).fetchone()
                if old:
                    if json.loads(old[0]) != json.loads(data):
                        raise ValueError(f"Révision interdite : {bar.symbol} {day}")
                    continue
                if state["cursor"] is not None and day <= state["cursor"]:
                    raise ValueError("Import antérieur au curseur interdit")
                db.execute(
                    "INSERT INTO bars VALUES(?,?,?,?,?)", (bar.symbol, day, data, received, digest)
                )
                inserted += 1
            db.execute("INSERT INTO batches VALUES(?,?,?)", (digest, received, inserted))
            self.event(db, "import", {"rows": inserted, "hash": digest, **meta})
        return {"inserted": inserted, "duplicate": False, "hash": digest}

    def configure(self, payload: Any) -> None:
        defaults = Settings().json()
        exact_keys(payload, set(defaults))
        parsed: dict[str, Any] = {}
        for key, value in payload.items():
            number = decimal(value)
            if key in ("max_positions", "max_hold"):
                if number != number.to_integral_value():
                    raise ValueError("Nombre entier requis")
                parsed[key] = int(number)
            else:
                parsed[key] = number
        settings = Settings(**parsed)
        limits = {
            "capital": (100, 1000000),
            "risk_pct": (0.001, 0.02),
            "max_exposure_pct": (0.01, 0.25),
            "max_total_risk_pct": (0.001, 0.05),
            "max_positions": (1, 10),
            "fee": (0, 100),
            "slippage_bps": (0, 100),
            "max_gap_pct": (0, 0.10),
            "max_hold": (1, 60),
        }
        for key, (low, high) in limits.items():
            if not Decimal(str(low)) <= parsed[key] <= Decimal(str(high)):
                raise ValueError(f"{key} hors limites [{low}, {high}]")
        if settings.capital != settings.capital.quantize(Decimal("0.01")):
            raise ValueError("Capital : deux décimales maximum")
        if settings.fee != settings.fee.quantize(Decimal("0.01")):
            raise ValueError("Frais : deux décimales maximum")
        with self.transaction() as db:
            if self.state(db)["cursor"] is not None:
                raise ValueError("Réglages figés après la première séance ; nouvelle base requise")
            self.save(db, initial_state(settings))
            self.event(db, "settings", settings.json())
