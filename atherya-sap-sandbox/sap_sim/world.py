"""Stato del sistema simulato: tabelle, numerazioni, registro modifiche, orologio, cronaca.

Tutte le chiavi delle tabelle sono tuple. I nomi dei campi delle righe sono quelli delle API
SAP (dove noti); i campi che iniziano con "_" sono interni e non escono mai dalle API.
"""

import datetime as dt
import random
import secrets

OP_LABELS = {"create": "Creazione", "update": "Modifica", "delete": "Cancellazione"}


class BusinessError(Exception):
    """Errore di business nel formato SAP: codice classe/numero messaggio, testo, stato HTTP."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def new_etag() -> str:
    return f'W/"{secrets.token_hex(6)}"'


class World:
    def __init__(self, start: dt.datetime = dt.datetime(2026, 10, 1, 6, 0), seed: int = 7):
        self.start = start
        self.now = start
        self.rng = random.Random(seed)
        self.tables: dict[str, dict] = {}
        self.counters: dict[str, int] = {}
        self.log: list[dict] = []
        self.events: list[dict] = []
        self.users: dict[str, dict] = {}
        self.csrf: dict[str, str] = {}
        self.faults: dict[str, bool] = {}
        self.machines: dict[str, dict] = {}   # verità simulata, invisibile a SAP
        self.running = False                  # orologio in tempo reale
        self.kpi: dict[str, float] = {}

    # ---------- tabelle ----------
    def t(self, name: str) -> dict:
        return self.tables.setdefault(name, {})

    def get(self, table: str, key: tuple) -> dict:
        row = self.t(table).get(key)
        if row is None:
            raise BusinessError("SIM/404", f"Documento {'/'.join(key)} non trovato", 404)
        return row

    def number(self, range_name: str, start: int) -> str:
        self.counters.setdefault(range_name, start)
        self.counters[range_name] += 1
        return str(self.counters[range_name])

    def insert(self, table: str, key: tuple, row: dict, user: str, quiet: bool = False) -> dict:
        if key in self.t(table):
            raise BusinessError("SIM/DUP", f"Documento {'/'.join(key)} già esistente")
        row.update({"_etag": new_etag(), "_by": user, "_at": self.now, "_changed_by": user})
        self.t(table)[key] = row
        if not quiet:
            self.audit("create", table, key, user)
        return row

    def update(self, table: str, key: tuple, changes: dict, user: str, quiet: bool = False) -> dict:
        row = self.get(table, key)
        row.update(changes)
        row["_etag"] = new_etag()
        row["_changed_by"] = user
        if not quiet:
            self.audit("update", table, key, user, changes)
        return row

    def delete(self, table: str, key: tuple, user: str, quiet: bool = False) -> None:
        self.get(table, key)
        del self.t(table)[key]
        if not quiet:
            self.audit("delete", table, key, user)

    # ---------- tracciabilità ----------
    def audit(self, op: str, table: str, key: tuple, user: str, changes: dict | None = None) -> None:
        shown = {k: v for k, v in (changes or {}).items() if not k.startswith("_")}
        self.log.append({
            "Id": str(len(self.log) + 1), "ts": self.now, "Operation": OP_LABELS[op], "Table": table,
            "Document": "/".join(key), "CreatedByUser": user,
            "Changes": ", ".join(f"{k} = {v}" for k, v in shown.items())[:300],
        })
        if len(self.log) > 30000:
            del self.log[:5000]

    def event(self, area: str, text: str, ref: str = "") -> None:
        """Cronaca leggibile di ciò che succede in fabbrica (solo simulazione)."""
        self.events.append({"Id": str(len(self.events) + 1), "ts": self.now, "Area": area, "Text": text, "Ref": ref})
        if len(self.events) > 10000:
            del self.events[:2000]

    # ---------- tempo ----------
    @property
    def today(self) -> dt.date:
        return self.now.date()
