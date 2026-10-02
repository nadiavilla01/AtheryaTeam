"""Connettore di Atherya per SAP ECC 6.0: RFC/BAPI al posto di OData.

Stesse azioni tipizzate (stessi nomi, stesso motore dei permessi, stesso executor), implementate
con le BAPI. Le differenze con S/4HANA che il connettore assorbe qui, e che nessun agente deve vedere:

- unità logica di lavoro: ogni azione apre una LUW e chiude con BAPI_TRANSACTION_COMMIT (WAIT = 'X');
  su qualsiasi errore, BAPI_TRANSACTION_ROLLBACK. Un'azione = un commit, così la saga sa sempre cosa
  è stato scritto e cosa no;
- gli errori arrivano nella tabella RETURN: un E/A diventa un'eccezione, mai un silenzio;
- chiavi in formato interno (ALPHA): il connettore converte sempre, in entrata e in uscita;
- niente ETag: la concorrenza si gestisce con i blocchi enqueue (MC 601 → SAPLockedError) e con il
  valore atteso passato al modulo cliente Z_ATHERYA_PRODORD_OPR_CHANGE (→ SAPConflictError);
- niente campo YY1_AtheryaRef: il riferimento viaggia in EXTENSIONIN verso un campo cliente
  ZZ_ATHERYA_REF (append su QMEL, AUFK, RKPF, EBAN) e si cerca con RFC_READ_TABLE;
- testi lunghi: si leggono con BAPI_ALM_NOTIF_GET_DETAIL (READ_TEXT non è abilitato a RFC).

`conn` è una pyrfc.Connection in produzione, una pyrfc_sim.Connection nella sandbox: stessa interfaccia.
"""

import contextlib
import datetime as dt
import re
from dataclasses import dataclass

from .client import SAPConflictError, SAPError, SAPPermissionError, SAPUnavailableError


class SAPLockedError(SAPConflictError):
    """Documento aperto da un altro utente (enqueue). Si riprova più tardi, non si forza."""


# ---------------------------------------------------------------- conversioni

def alpha(value, length: int) -> str:
    s = "" if value is None else str(value).strip()
    return s.zfill(length) if s.isdigit() else s


def unalpha(value) -> str:
    s = "" if value is None else str(value)
    return (s.lstrip("0") or "0") if s.isdigit() else s


def d8(d: dt.date) -> str:
    return d.strftime("%Y%m%d")


def _where_lines(where: str) -> list[dict]:
    """Spezza la clausola WHERE in righe da 72 caratteri senza tagliare i token."""
    lines, current = [], ""
    for tok in re.findall(r"'(?:[^']|'')*'|\S+", where):
        if len(current) + len(tok) + 1 > 72:
            lines.append(current)
            current = tok
        else:
            current = f"{current} {tok}".strip()
    if current:
        lines.append(current)
    return [{"TEXT": line} for line in lines]


# ---------------------------------------------------------------- client

class ECCClient:
    def __init__(self, conn):
        self.conn = conn

    def call(self, fm: str, **params) -> dict:
        try:
            return self.conn.call(fm, **params)
        except Exception as e:  # le classi arrivano da pyrfc o da pyrfc_sim: si riconoscono dal nome
            kind, key = type(e).__name__, getattr(e, "key", "")
            msg = getattr(e, "message", str(e))
            if kind in ("CommunicationError", "RFCLibError"):
                raise SAPUnavailableError(503, key or "RFC_COMMUNICATION_FAILURE", msg) from e
            if kind == "LogonError" or key in ("RFC_NO_AUTHORITY", "NOT_AUTHORIZED"):
                raise SAPPermissionError(403, key or "RFC_LOGON_FAILURE", msg) from e
            if kind in ("ABAPApplicationError", "ABAPRuntimeError", "ExternalRuntimeError"):
                raise SAPError(400, key, msg) from e
            raise

    def bapi(self, fm: str, **params) -> dict:
        out = self.call(fm, **params)
        for name in ("RETURN", "ET_RETURN", "DETAIL_RETURN"):
            rows = out.get(name) or []
            rows = [rows] if isinstance(rows, dict) else rows
            for r in rows:
                if r.get("TYPE") in ("E", "A"):
                    self._raise(r)
        return out

    @staticmethod
    def _raise(r: dict):
        code = f"{r.get('ID', '')}/{r.get('NUMBER', '')}" if "ID" in r else str(r.get("CODE", "")).replace(" ", "/")
        msg = r.get("MESSAGE", "")
        if code == "MC/601":
            raise SAPLockedError(423, code, msg)
        if code.startswith("ZATHERYA/001"):
            raise SAPConflictError(412, code, msg)
        if code in ("IW/100",) or "autorizzazione" in msg.lower():
            raise SAPPermissionError(403, code, msg)
        raise SAPError(400, code, msg)

    def commit(self) -> None:
        self.bapi("BAPI_TRANSACTION_COMMIT", WAIT="X")

    def rollback(self) -> None:
        with contextlib.suppress(SAPError):
            self.call("BAPI_TRANSACTION_ROLLBACK")

    @contextlib.contextmanager
    def luw(self):
        """Unità logica di lavoro: commit se tutto va bene, rollback altrimenti."""
        try:
            yield self
        except SAPUnavailableError:
            raise  # connessione persa: il sistema annulla da solo; l'esito va verificato (find_existing)
        except Exception:
            self.rollback()
            raise
        self.commit()

    def read_table(self, table: str, fields: list[str], where: str = "", max_rows: int = 0) -> list[dict]:
        """RFC_READ_TABLE a larghezza fissa (senza delimitatore: i testi possono contenere qualsiasi carattere)."""
        out = self.call("RFC_READ_TABLE", QUERY_TABLE=table, FIELDS=[{"FIELDNAME": f} for f in fields],
                        OPTIONS=_where_lines(where), ROWCOUNT=max_rows)
        meta = [(f["FIELDNAME"], int(f["OFFSET"]), int(f["LENGTH"])) for f in out["FIELDS"]]
        return [{name: row["WA"][off:off + ln].strip() for name, off, ln in meta} for row in out["DATA"]]


# ---------------------------------------------------------------- azioni (stessi nomi della versione OData)

COST_CENTER = "MANUT1000"


def _find(sap: ECCClient, table: str, key_field: str, ref: str, label: str, target: str):
    rows = sap.read_table(table, [key_field], f"ZZ_ATHERYA_REF = '{ref}'", 1)
    return {"label": label, "target": target, "key": unalpha(rows[0][key_field])} if rows else None


def _ext(ref: str) -> list[dict]:
    return [{"STRUCTURE": "ZATHERYA", "VALUEPART1": ref}]


@dataclass(frozen=True)
class CreateNotification:
    equipment: str
    plant: str
    text: str
    long_text: str
    priority: str = "2"
    notif_type: str = "M2"
    domain = "manutenzione"

    def preview(self) -> str:
        return (f"Avviso {self.notif_type} su apparecchiatura {self.equipment}: «{self.text}» (priorità {self.priority}) "
                f"· BAPI_ALM_NOTIF_CREATE + BAPI_ALM_NOTIF_SAVE")

    def execute(self, sap: ECCClient, ref: str, ctx: dict) -> dict:
        lines = [{"FORMAT_COL": "*", "TEXT_LINE": self.long_text[i:i + 132]} for i in range(0, len(self.long_text), 132)]
        with sap.luw():
            r = sap.bapi("BAPI_ALM_NOTIF_CREATE", NOTIF_TYPE=self.notif_type,
                         NOTIFHEADER={"EQUIPMENT": alpha(self.equipment, 18), "SHORT_TEXT": self.text[:40],
                                      "PRIORITY": self.priority, "PLANPLANT": self.plant},
                         LONGTEXTS=lines, EXTENSIONIN=_ext(ref))
            saved = sap.bapi("BAPI_ALM_NOTIF_SAVE", NUMBER=r["NOTIFHEADER_EXPORT"]["NOTIF_NO"])
        return {"label": "Avviso", "target": "QMEL", "key": unalpha(saved["NOTIFHEADER"]["NOTIF_NO"])}

    def find_existing(self, sap: ECCClient, ref: str):
        return _find(sap, "QMEL", "QMNUM", ref, "Avviso", "QMEL")

    def compensate(self, sap: ECCClient, result: dict) -> str:
        with sap.luw():
            sap.bapi("BAPI_ALM_NOTIF_CLOSE", NUMBER=alpha(result["key"], 12),
                     SYSSTAT={"LANGU": "I", "REFDATE": d8(dt.date.today()), "REFTIME": "000000"})
        return f"Avviso {result['key']} chiuso (in ECC il contrassegno di cancellazione non ha una BAPI standard)"


@dataclass(frozen=True)
class CreateMaintenanceOrder:
    equipment: str
    plant: str
    work_center: str
    start: object
    description: str
    notification_step: int | None = 0
    order_type: str = "PM01"
    domain = "manutenzione"

    def preview(self) -> str:
        return (f"Ordine {self.order_type} «{self.description}» su {self.equipment}, centro {self.work_center}, "
                f"inizio {self.start:%d/%m/%Y}, collegato all'avviso del passo {self.notification_step + 1}, stato CRTD "
                f"· BAPI_ALM_ORDER_MAINTAIN")

    def execute(self, sap: ECCClient, ref: str, ctx: dict) -> dict:
        header = {"ORDERID": "%00000000001", "ORDER_TYPE": self.order_type, "PLANPLANT": self.plant, "PLANT": self.plant,
                  "MN_WK_CTR": self.work_center, "EQUIPMENT": alpha(self.equipment, 18), "SHORT_TEXT": self.description[:40],
                  "START_DATE": d8(self.start), "PRIORITY": "2"}
        if self.notification_step is not None:
            header["NOTIF_NO"] = alpha(ctx["results"][self.notification_step]["key"], 12)
        with sap.luw():
            r = sap.bapi("BAPI_ALM_ORDER_MAINTAIN",
                         IT_METHODS=[{"REFNUMBER": "000001", "OBJECTTYPE": "HEADER", "METHOD": "CREATE", "OBJECTKEY": "%00000000001"},
                                     {"REFNUMBER": "000001", "OBJECTTYPE": "OPERATION", "METHOD": "CREATE", "OBJECTKEY": "%000000000010010"},
                                     {"REFNUMBER": "000001", "OBJECTTYPE": "", "METHOD": "SAVE", "OBJECTKEY": ""}],
                         IT_HEADER=[header],
                         IT_OPERATION=[{"ACTIVITY": "0010", "CONTROL_KEY": "PM01", "WORK_CNTR": self.work_center, "PLANT": self.plant,
                                        "DESCRIPTION": self.description[:40], "WORK_ACTIVITY": 3, "UN_WORK": "STD"}],
                         EXTENSION_IN=_ext(ref))
        return {"label": "Ordine di manutenzione", "target": "AUFK", "key": unalpha(r["ET_NUMBERS"][0]["AUFNR_NEW"])}

    def find_existing(self, sap: ECCClient, ref: str):
        return _find(sap, "AUFK", "AUFNR", ref, "Ordine di manutenzione", "AUFK")

    def compensate(self, sap: ECCClient, result: dict) -> str:
        key = alpha(result["key"], 12)
        with sap.luw():
            sap.bapi("BAPI_ALM_ORDER_MAINTAIN",
                     IT_METHODS=[{"REFNUMBER": "000001", "OBJECTTYPE": "HEADER", "METHOD": "LOCK", "OBJECTKEY": key},
                                 {"REFNUMBER": "000001", "OBJECTTYPE": "", "METHOD": "SAVE", "OBJECTKEY": ""}])
        return f"Ordine {result['key']} bloccato (LKD): non può più essere rilasciato"


@dataclass(frozen=True)
class ReserveMaterial:
    material: str
    plant: str
    storage_location: str
    quantity: int
    requirement_date: object
    domain = "manutenzione"

    def preview(self) -> str:
        return (f"Prenotazione di {self.quantity} × {self.material} dal magazzino {self.storage_location}, "
                f"per il {self.requirement_date:%d/%m/%Y} · BAPI_RESERVATION_CREATE1")

    def execute(self, sap: ECCClient, ref: str, ctx: dict) -> dict:
        with sap.luw():
            r = sap.bapi("BAPI_RESERVATION_CREATE1",
                         RESERVATIONHEADER={"RES_DATE": d8(self.requirement_date), "MOVE_TYPE": "201", "COSTCENTER": COST_CENTER},
                         RESERVATIONITEMS=[{"MATERIAL": self.material, "PLANT": self.plant, "STGE_LOC": self.storage_location,
                                            "ENTRY_QNT": self.quantity, "REQ_DATE": d8(self.requirement_date)}],
                         EXTENSIONIN=_ext(ref))
        return {"label": "Prenotazione", "target": "RKPF", "key": unalpha(r["RESERVATION"])}

    def find_existing(self, sap: ECCClient, ref: str):
        return _find(sap, "RKPF", "RSNUM", ref, "Prenotazione", "RKPF")

    def compensate(self, sap: ECCClient, result: dict) -> str:
        with sap.luw():
            sap.bapi("BAPI_RESERVATION_DELETE", RESERVATION=alpha(result["key"], 10))
        return f"Prenotazione {result['key']} cancellata"


@dataclass(frozen=True)
class CreatePurchaseRequisition:
    material: str
    plant: str
    quantity: int
    delivery_date: object
    domain = "manutenzione"

    def preview(self) -> str:
        return f"Richiesta d'acquisto di {self.quantity} × {self.material}, consegna entro il {self.delivery_date:%d/%m/%Y} · BAPI_PR_CREATE"

    def execute(self, sap: ECCClient, ref: str, ctx: dict) -> dict:
        item = {"PREQ_ITEM": "00010", "MATERIAL": self.material, "PLANT": self.plant, "QUANTITY": self.quantity,
                "DELIV_DATE": d8(self.delivery_date), "PUR_GROUP": "001"}
        with sap.luw():
            r = sap.bapi("BAPI_PR_CREATE", PRHEADER={"PR_TYPE": "NB"}, PRHEADERX={"PR_TYPE": "X"},
                         PRITEM=[item], PRITEMX=[{k: ("X" if k != "PREQ_ITEM" else v) for k, v in item.items()}],
                         EXTENSIONIN=_ext(ref))
        return {"label": "Richiesta d'acquisto", "target": "EBAN", "key": unalpha(r["NUMBER"])}

    def find_existing(self, sap: ECCClient, ref: str):
        return _find(sap, "EBAN", "BANFN", ref, "Richiesta d'acquisto", "EBAN")

    def compensate(self, sap: ECCClient, result: dict) -> str:
        with sap.luw():
            sap.bapi("BAPI_REQUISITION_DELETE", NUMBER=alpha(result["key"], 10),
                     REQUISITION_ITEMS_TO_DELETE=[{"PREQ_ITEM": "00010", "DELETE_IND": "X"}])
        return f"Richiesta d'acquisto {result['key']} contrassegnata per la cancellazione"


@dataclass(frozen=True)
class RescheduleOperation:
    order: str
    operation: str
    from_work_center: str
    to_work_center: str
    expected_etag: str  # in ECC: il centro di lavoro letto al momento della proposta
    domain = "scheduling"

    def preview(self) -> str:
        return (f"Ordine di produzione {self.order}, operazione {self.operation}: centro di lavoro {self.from_work_center} → "
                f"{self.to_work_center} · Z_ATHERYA_PRODORD_OPR_CHANGE (modulo cliente)")

    def _change(self, sap, target, expected):
        with sap.luw():
            sap.bapi("Z_ATHERYA_PRODORD_OPR_CHANGE", IV_AUFNR=alpha(self.order, 12), IV_VORNR=self.operation,
                     IV_ARBPL=target, IV_EXPECTED_ARBPL=expected)

    def execute(self, sap: ECCClient, ref: str, ctx: dict) -> dict:
        self._change(sap, self.to_work_center, self.expected_etag)
        return {"label": "Riprogrammazione", "target": "AFVC", "key": f"{self.order}/{self.operation}"}

    def _current(self, sap) -> str:
        out = sap.bapi("BAPI_PRODORD_GET_DETAIL", NUMBER=alpha(self.order, 12), ORDER_OBJECTS={"OPERATIONS": "X"})
        return next((o["WORK_CENTER"] for o in out["OPERATION"] if o["OPERATION_NUMBER"] == self.operation), "")

    def find_existing(self, sap: ECCClient, ref: str):
        if self._current(sap) == self.to_work_center:
            return {"label": "Riprogrammazione", "target": "AFVC", "key": f"{self.order}/{self.operation}"}
        return None

    def compensate(self, sap: ECCClient, result: dict) -> str:
        try:
            self._change(sap, self.from_work_center, self.to_work_center)
        except SAPConflictError:
            return f"Operazione {self.order}/{self.operation} già modificata da altri: lasciata com'è"
        return f"Operazione {self.order}/{self.operation} riportata su {self.from_work_center}"


@dataclass(frozen=True)
class ReleaseMaintenanceOrder:
    """Come nella versione OData: esiste per dimostrare che il motore dei permessi la blocca."""
    order_step: int
    domain = "manutenzione"

    def preview(self) -> str:
        return f"Rilascio dell'ordine creato al passo {self.order_step + 1}"

    def execute(self, sap: ECCClient, ref: str, ctx: dict) -> dict:
        key = alpha(ctx["results"][self.order_step]["key"], 12)
        with sap.luw():
            sap.bapi("BAPI_ALM_ORDER_MAINTAIN",
                     IT_METHODS=[{"REFNUMBER": "000001", "OBJECTTYPE": "HEADER", "METHOD": "RELEASE", "OBJECTKEY": key},
                                 {"REFNUMBER": "000001", "OBJECTTYPE": "", "METHOD": "SAVE", "OBJECTKEY": ""}])
        return {"label": "Rilascio", "target": "AUFK", "key": unalpha(key)}

    def find_existing(self, sap, ref):
        return None

    def compensate(self, sap, result):
        return "Rilascio non compensabile automaticamente"


# ---------------------------------------------------------------- letture per il planner

class Backend:
    """Le tre letture che servono al planner, nel dialetto ECC."""

    actions = None  # impostato sotto

    @staticmethod
    def stock(sap: ECCClient, material: str, plant: str) -> float:
        rows = sap.read_table("MARD", ["LABST"], f"MATNR = '{material}' AND WERKS = '{plant}'")
        return sum(float(r["LABST"] or 0) for r in rows)

    @staticmethod
    def operation_token(sap: ECCClient, order: str, operation: str) -> str:
        out = sap.bapi("BAPI_PRODORD_GET_DETAIL", NUMBER=alpha(order, 12), ORDER_OBJECTS={"OPERATIONS": "X"})
        return next(o["WORK_CENTER"] for o in out["OPERATION"] if o["OPERATION_NUMBER"] == operation)

    @staticmethod
    def notes(sap: ECCClient, equipment: str, limit: int = 10) -> list[tuple[str, str]]:
        rows = sap.read_table("VIQMEL", ["QMNUM", "QMTXT"], f"EQUNR = '{alpha(equipment, 18)}'")
        out = []
        for r in rows[-limit:]:
            d = sap.bapi("BAPI_ALM_NOTIF_GET_DETAIL", NUMBER=r["QMNUM"])
            text = " ".join(line["TEXT_LINE"] for line in d.get("NOTLONGTXT", [])) or r["QMTXT"]
            out.append((unalpha(r["QMNUM"]), text))
        return out


class _Actions:
    CreateNotification = CreateNotification
    CreateMaintenanceOrder = CreateMaintenanceOrder
    ReserveMaterial = ReserveMaterial
    CreatePurchaseRequisition = CreatePurchaseRequisition
    RescheduleOperation = RescheduleOperation


Backend.actions = _Actions
