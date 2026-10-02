"""Moduli funzione RFC della facciata ECC: BAPI di PM, PP, MM, WM più le funzioni tecniche.

Comportamenti del sistema vero che un connettore deve gestire, e che qui sono riprodotti:
- le BAPI non scrivono nulla finché non arriva BAPI_TRANSACTION_COMMIT (unità logica di lavoro);
  chiudere la connessione o chiamare BAPI_TRANSACTION_ROLLBACK annulla tutto;
- gli errori di business arrivano nella tabella RETURN (TYPE E/A), non come eccezioni: chi non
  la controlla crede di aver scritto e non ha scritto nulla;
- alcuni moduli (L_TO_*, MEASUREM_DOCUM_RFC_SINGLE_001, RFC_READ_TABLE) usano invece eccezioni ABAP;
- chiavi in formato interno: '000004000101', non '4000101'; date AAAAMMGG; unità interne (ST);
- blocchi enqueue: se qualcuno ha l'ordine aperto in CO02/IW32, la BAPI fallisce (MC 601);
- autorizzazioni S_RFC per modulo funzione, più i controlli di business (rilascio).

Nomi dei moduli e dei parametri: quelli standard di ECC 6.0 ricordati dalla documentazione;
vanno verificati sul sistema del cliente (FIELD_MAP.md, sezione ECC).
"""

import datetime as dt
import re
import secrets
import time as _time

from .. import inventory as inv
from .. import pm, pp
from ..masterdata import PLANT, SLOC
from ..world import BusinessError
from . import conv, tables
from .conv import alpha, d8, t6, unalpha, unit, user as ecc_user

# ---------------------------------------------------------------- errori ed eccezioni


class ABAPException(Exception):
    """Eccezione classica di un modulo funzione (RAISING ...): arriva al chiamante come ABAPApplicationError."""

    def __init__(self, key: str, message: str = "", msg_class: str = "", msg_number: str = ""):
        super().__init__(message or key)
        self.key, self.message, self.msg_class, self.msg_number = key, message or key, msg_class, msg_number


class NoAuthority(Exception):
    """Manca S_RFC per il modulo funzione."""


class LogonFailed(Exception):
    pass


def ret(type_: str, id_: str, number: str, message: str, *values, parameter: str = "", row: int = 0, field: str = "") -> dict:
    """Riga BAPIRET2."""
    v = [str(x) for x in values] + ["", "", "", ""]
    return {"TYPE": type_, "ID": id_, "NUMBER": number, "MESSAGE": message[:220], "LOG_NO": "", "LOG_MSG_NO": "000000",
            "MESSAGE_V1": v[0][:50], "MESSAGE_V2": v[1][:50], "MESSAGE_V3": v[2][:50], "MESSAGE_V4": v[3][:50],
            "PARAMETER": parameter, "ROW": row, "FIELD": field, "SYSTEM": f"{conv.SYSID}CLNT{conv.CLIENT}"}


def ret_old(type_: str, code: str, message: str) -> dict:
    """Riga BAPIRETURN (formato più vecchio, usato da alcune BAPI MM)."""
    return {"TYPE": type_, "CODE": code, "MESSAGE": message[:220], "LOG_NO": "", "LOG_MSG_NO": "000000",
            "MESSAGE_V1": "", "MESSAGE_V2": "", "MESSAGE_V3": "", "MESSAGE_V4": ""}


NONNUM = {"REL": "100", "DEL": "101", "DUP": "102", "LKD": "103", "404": "404"}


def from_business(e: BusinessError) -> dict:
    code = e.code.replace("/SCWM/", "")
    if "/" in code:
        cls, num = code.rsplit("/", 1)
    else:
        cls, num = code, "000"
    num = num if num.isdigit() else NONNUM.get(num, "999")
    return ret("E", cls, num.zfill(3)[-3:], e.message)


def has_error(rows) -> bool:
    if isinstance(rows, dict):
        rows = [rows]
    return any(r.get("TYPE") in ("E", "A") for r in rows or [])


# ---------------------------------------------------------------- utenti e autorizzazioni

READ_FMS = {"RFC_PING", "RFC_SYSTEM_INFO", "RFC_READ_TABLE", "DDIF_FIELDINFO_GET", "BAPI_ALM_NOTIF_GET_DETAIL", "BAPI_ALM_ORDER_GET_DETAIL",
            "BAPI_ALM_ORDERHEAD_GET_LIST", "BAPI_EQUI_GETDETAIL", "BAPI_PRODORD_GET_DETAIL", "BAPI_PLANNEDORDER_GET_DETAIL",
            "BAPI_MATERIAL_STOCK_REQ_LIST", "BAPI_MATERIAL_AVAILABILITY", "BAPI_MATERIAL_GET_DETAIL",
            "BAPI_TRANSACTION_COMMIT", "BAPI_TRANSACTION_ROLLBACK"}
ATHERYA_WRITE_FMS = {"BAPI_ALM_NOTIF_CREATE", "BAPI_ALM_NOTIF_SAVE", "BAPI_ALM_NOTIF_CLOSE", "BAPI_ALM_ORDER_MAINTAIN",
                     "BAPI_RESERVATION_CREATE1", "BAPI_RESERVATION_DELETE", "BAPI_PR_CREATE", "BAPI_REQUISITION_DELETE",
                     "Z_ATHERYA_PRODORD_OPR_CHANGE", "MEASUREM_DOCUM_RFC_SINGLE_001"}
# Tabelle che l'utente tecnico può leggere con RFC_READ_TABLE (S_TABU_DIS / S_TABU_NAM)
ATHERYA_TABLES = set(tables.TABLES)


def seed_users(w) -> None:
    w.users.setdefault("PLANNER", {"password": "demo", "auths": set()})
    w.users["PLANNER"]["ecc"] = {"fms": "*", "tables": "*"}
    w.users["ATHERYA_RFC"] = {"password": "demo", "auths": set(),
                              "ecc": {"fms": READ_FMS | ATHERYA_WRITE_FMS, "tables": ATHERYA_TABLES}}


def can_call(w, u: str, fm: str) -> bool:
    fms = w.users.get(u, {}).get("ecc", {}).get("fms", set())
    return fms == "*" or fm in fms


def can_read_table(w, u: str, table: str) -> bool:
    t = w.users.get(u, {}).get("ecc", {}).get("tables", set())
    return t == "*" or table in t


def logon(w, u: str, password: str, client: str) -> None:
    acc = w.users.get(u or "")
    if client != conv.CLIENT or not acc or acc.get("password") != password or "ecc" not in acc:
        raise LogonFailed("Nome o password non corretti (ripetere l'accesso)")


# ---------------------------------------------------------------- sessioni RFC e unità logica di lavoro

class Session:
    def __init__(self, w, u: str, kind: str = "RFC"):
        self.id = secrets.token_hex(8)
        self.user, self.kind = u, kind
        self.journal: list = []
        self.notifs: dict[str, dict] = {}      # avvisi creati e non ancora salvati (numero temporaneo)
        self.opened = w.now
        self.last_call = _time.time()
        self.calls = 0
        self.last_fm = ""

    @property
    def dirty(self) -> bool:
        return any(e[0] == "row" for e in self.journal)


def sessions(w) -> dict[str, Session]:
    return w.__dict__.setdefault("_rfc_sessions", {})


def open_session(w, u: str, kind: str = "RFC") -> Session:
    s = Session(w, u, kind)
    sessions(w)[s.id] = s
    return s


def _release_locks(w, s: Session) -> None:
    for k in [k for k, l in w.locks.items() if l.get("session") == s.id]:
        del w.locks[k]


def commit(w, s: Session) -> None:
    s.journal = []
    s.notifs = {}
    _release_locks(w, s)


def rollback(w, s: Session) -> None:
    if s.journal:
        w.rollback(s.journal)
        pp.schedule_all(w)
    s.journal = []
    s.notifs = {}
    _release_locks(w, s)


def close_session(w, sid: str) -> None:
    s = sessions(w).pop(sid, None)
    if s:
        rollback(w, s)  # connessione chiusa senza commit: il sistema annulla


def expire_sessions(w, idle_seconds: int = 1800) -> None:
    now = _time.time()
    for sid in [sid for sid, s in sessions(w).items() if now - s.last_call > idle_seconds]:
        close_session(w, sid)


def lock(w, s: Session, obj: tuple) -> None:
    """Enqueue: un oggetto aperto da un altro utente o da un'altra sessione non si può modificare."""
    held = w.locks.get(obj)
    if held and held.get("session") != s.id:
        who = ecc_user(held["user"])
        raise BusinessError("MC/601", f"L'oggetto {obj[0]} {unalpha(obj[1])} è bloccato dall'utente {who}")
    w.locks[obj] = {"user": s.user, "session": s.id, "tcode": "RFC", "since": w.now, "until": None}


# ---------------------------------------------------------------- dispatcher

FUNCTIONS: dict[str, object] = {}


def fm(name):
    def deco(fn):
        FUNCTIONS[name] = fn
        return fn
    return deco


def call(w, s: Session, name: str, params: dict) -> dict:
    """Esegue un modulo funzione nella sessione. Solleva NoAuthority, ABAPException, KeyError (funzione inesistente)."""
    name = name.upper()
    if name not in FUNCTIONS:
        raise ABAPException("FU_NOT_FOUND", f"Modulo funzione {name} non trovato")
    if not can_call(w, s.user, name):
        raise NoAuthority(f"Nessuna autorizzazione RFC per il modulo funzione {name} (oggetto S_RFC)")
    s.last_call, s.calls, s.last_fm = _time.time(), s.calls + 1, name
    mark = len(s.journal)
    w.journal = s.journal
    try:
        out = FUNCTIONS[name](w, s, {k.upper(): v for k, v in (params or {}).items()})
    except ABAPException:
        _undo_since(w, s, mark)
        raise
    finally:
        w.journal = None
    # Una BAPI che risponde con un errore non lascia nulla nel buffer di aggiornamento.
    if has_error(out.get("RETURN")) or has_error(out.get("ET_RETURN")) or has_error(out.get("DETAIL_RETURN")):
        _undo_since(w, s, mark)
    return out


def _undo_since(w, s: Session, mark: int) -> None:
    part = s.journal[mark:]
    if part:
        w.rollback(part)
        del s.journal[mark:]
        pp.schedule_all(w)


def _p(params, name, default=None):
    v = params.get(name, default)
    return default if v is None else v


def _x(v) -> bool:
    return str(v or "").strip().upper() == "X"


def _num(v) -> float:
    try:
        return float(str(v).strip() or 0)
    except ValueError:
        return 0.0


def _ref_from_extension(rows) -> str:
    for r in rows or []:
        if str(r.get("STRUCTURE", "")).upper() == "ZATHERYA":
            return str(r.get("VALUEPART1", "")).strip()
    return ""


def _strict(value, length: int, table: str, label: str) -> str:
    """Le BAPI vogliono la chiave in formato interno: '4000101' non trova l'ordine '000004000101'."""
    v = str(value or "")
    if v.isdigit() and len(v) != length:
        raise BusinessError(f"{table}/404", f"{label} {v} non esiste")
    return unalpha(v)


# ---------------------------------------------------------------- tecniche

@fm("RFC_PING")
def _ping(w, s, p):
    return {}


@fm("RFC_SYSTEM_INFO")
def _sysinfo(w, s, p):
    return {"RFCSI_EXPORT": {"RFCPROTO": "011", "RFCCHARTYP": "4103", "RFCINTTYP": "LIT", "RFCFLOTYP": "IE3", "RFCDEST": f"eccsim_{conv.SYSID}_00",
                             "RFCHOST": "eccsim", "RFCSYSID": conv.SYSID, "RFCDATABS": conv.SYSID, "RFCDBHOST": "eccsim", "RFCDBSYS": "ORACLE",
                             "RFCSAPRL": "740", "RFCMACH": "390", "RFCOPSYS": "Linux", "RFCTZONE": "3600", "RFCDAYST": "X",
                             "RFCIPADDR": "127.0.0.1", "RFCKERNRL": "753", "RFCSI_RESV": "", "RFCIPV6ADDR": "::1"},
            "CURRENT_RESOURCES": 8, "MAXIMAL_RESOURCES": 10, "RECOMMENDED_DELAY": 0}


@fm("BAPI_TRANSACTION_COMMIT")
def _commit(w, s, p):
    commit(w, s)
    return {"RETURN": ret("", "", "000", "")}


@fm("BAPI_TRANSACTION_ROLLBACK")
def _rollback(w, s, p):
    rollback(w, s)
    return {"RETURN": ret("", "", "000", "")}


@fm("DDIF_FIELDINFO_GET")
def _fieldinfo(w, s, p):
    name = str(_p(p, "TABNAME", "")).upper()
    if name not in tables.TABLES:
        raise ABAPException("NOT_FOUND", f"Tabella {name} non attiva nel dizionario")
    offset, out = 0, []
    for i, f in enumerate(tables.fields_of(name), start=1):
        out.append({"TABNAME": name, "FIELDNAME": f.name, "POSITION": f"{i:04d}", "OFFSET": f"{offset:06d}", "LENG": f"{f.length:06d}",
                    "INTTYPE": f.type, "DATATYPE": {"C": "CHAR", "N": "NUMC", "D": "DATS", "T": "TIMS", "P": "QUAN", "F": "FLTP"}[f.type],
                    "FIELDTEXT": f.text, "KEYFLAG": "X" if i == 1 else ""})
        offset += f.length
    return {"DFIES_TAB": out, "X030L_WA": {"TABNAME": name}, "DDOBJTYPE": "TRANSP"}


# ---------------------------------------------------------------- RFC_READ_TABLE

_TOKEN = re.compile(r"\s*(?:(\()|(\))|('(?:[^']|'')*')|(<=|>=|<>|=|<|>)|([A-Za-z_][A-Za-z0-9_\-]*)|(-?\d+(?:\.\d+)?)|(,))")
OPS = {"EQ": "=", "NE": "<>", "GT": ">", "GE": ">=", "LT": "<", "LE": "<="}


def _tokens(text: str) -> list[tuple[str, str]]:
    pos, out = 0, []
    text = text.strip()
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise ABAPException("OPTION_NOT_VALID", f"Condizione non valida vicino a «{text[pos:pos + 20]}»")
        lp, rp, s, op, word, num, comma = m.groups()
        if lp:
            out.append(("(", "("))
        elif rp:
            out.append((")", ")"))
        elif s is not None:
            out.append(("lit", s[1:-1].replace("''", "'")))
        elif op:
            out.append(("op", op))
        elif word:
            up = word.upper()
            if up in OPS:
                out.append(("op", OPS[up]))
            elif up in ("AND", "OR", "NOT", "LIKE", "IN", "BETWEEN"):
                out.append((up, up))
            else:
                out.append(("field", up))
        elif num:
            out.append(("lit", num))
        else:
            out.append((",", ","))
        pos = m.end()
    return out


class _Where:
    """Interprete della clausola WHERE di RFC_READ_TABLE (sottoinsieme di Open SQL)."""

    def __init__(self, text: str, fields: dict):
        self.toks, self.i, self.fields = _tokens(text), 0, fields
        self.tree = self._or() if self.toks else None
        if self.i < len(self.toks):
            raise ABAPException("OPTION_NOT_VALID", "Condizione non valida")

    def _peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def _take(self, kind=None):
        t = self._peek()
        if kind and t[0] != kind:
            raise ABAPException("OPTION_NOT_VALID", f"Atteso {kind} nella condizione")
        self.i += 1
        return t

    def _or(self):
        node = self._and()
        while self._peek()[0] == "OR":
            self._take()
            node = ("or", node, self._and())
        return node

    def _and(self):
        node = self._not()
        while self._peek()[0] == "AND":
            self._take()
            node = ("and", node, self._not())
        return node

    def _not(self):
        if self._peek()[0] == "NOT":
            self._take()
            return ("not", self._not())
        if self._peek()[0] == "(":
            self._take()
            node = self._or()
            self._take(")")
            return node
        return self._cmp()

    def _cmp(self):
        _, field = self._take("field")
        if field not in self.fields:
            raise ABAPException("OPTION_NOT_VALID", f"Il campo {field} non esiste nella tabella")
        kind, val = self._peek()
        if kind == "op":
            self._take()
            return ("cmp", field, val, self._take("lit")[1])
        if kind == "LIKE":
            self._take()
            return ("like", field, self._take("lit")[1])
        if kind == "IN":
            self._take()
            self._take("(")
            vals = [self._take("lit")[1]]
            while self._peek()[0] == ",":
                self._take()
                vals.append(self._take("lit")[1])
            self._take(")")
            return ("in", field, vals)
        if kind == "BETWEEN":
            self._take()
            lo = self._take("lit")[1]
            self._take("AND")
            return ("between", field, lo, self._take("lit")[1])
        raise ABAPException("OPTION_NOT_VALID", f"Operatore mancante dopo {field}")

    def match(self, row) -> bool:
        return True if self.tree is None else self._eval(self.tree, row)

    def _val(self, field, row, lit):
        v, t = row.get(field), self.fields[field].type
        if t in ("P", "F"):
            return float(v or 0), float(lit or 0)
        return str(v or ""), lit

    def _eval(self, n, row):
        k = n[0]
        if k == "or":
            return self._eval(n[1], row) or self._eval(n[2], row)
        if k == "and":
            return self._eval(n[1], row) and self._eval(n[2], row)
        if k == "not":
            return not self._eval(n[1], row)
        if k == "cmp":
            a, b = self._val(n[1], row, n[3])
            return {"=": a == b, "<>": a != b, ">": a > b, ">=": a >= b, "<": a < b, "<=": a <= b}[n[2]]
        if k == "like":
            pattern = "^" + re.escape(n[2]).replace("%", ".*").replace("_", ".") + "$"
            return re.match(pattern, str(row.get(n[1]) or "")) is not None
        if k == "in":
            return str(row.get(n[1]) or "") in n[2]
        if k == "between":
            a, lo = self._val(n[1], row, n[2])
            _, hi = self._val(n[1], row, n[3])
            return lo <= a <= hi
        return False


def fmt_field(f: tables.F, v) -> str:
    if f.type == "P":
        return f"{float(v or 0):.3f}".rjust(f.length)[-f.length:]
    if f.type == "F":
        return f"{float(v or 0):.16E}".rjust(f.length)[-f.length:]
    return str(v if v is not None else "").ljust(f.length)[:f.length]


# Nel sistema vero queste tabelle hanno decine o centinaia di campi: senza FIELDS, RFC_READ_TABLE fallisce.
WIDE_TABLES = {"MARA", "MARC", "EQUI", "QMEL", "VIQMEL", "QMIH", "AUFK", "AFIH", "AFKO", "AFPO", "AFVC", "AFVV", "AFRU",
               "RESB", "RKPF", "EBAN", "MKPF", "MSEG", "PLAF", "LQUA", "LTAK", "LTAP", "V_EQUI", "IMPTT", "IMRG", "MPLA", "MPOS", "MHIS"}


@fm("RFC_READ_TABLE")
def _read_table(w, s, p):
    name = str(_p(p, "QUERY_TABLE", "")).upper().strip()
    if name not in tables.TABLES:
        raise ABAPException("TABLE_NOT_AVAILABLE", f"Tabella {name} non disponibile")
    if not can_read_table(w, s.user, name):
        raise ABAPException("NOT_AUTHORIZED", f"Nessuna autorizzazione a visualizzare la tabella {name}")
    all_fields = {f.name: f for f in tables.fields_of(name)}
    req = [str(r.get("FIELDNAME", "")).upper().strip() for r in _p(p, "FIELDS", []) or [] if str(r.get("FIELDNAME", "")).strip()]
    for f in req:
        if f not in all_fields:
            raise ABAPException("FIELD_NOT_VALID", f"Campo {f} non presente nella tabella {name}")
    if not req and name in WIDE_TABLES:
        raise ABAPException("DATA_BUFFER_EXCEEDED", f"La tabella {name} nel sistema vero supera i 512 caratteri per riga: indicare i campi in FIELDS")
    chosen = [all_fields[f] for f in req] if req else list(all_fields.values())
    delim = str(_p(p, "DELIMITER", "") or "")
    width = sum(f.length for f in chosen) + len(delim) * (len(chosen) - 1)
    if width > 512:
        raise ABAPException("DATA_BUFFER_EXCEEDED", "La riga richiesta supera i 512 caratteri: selezionare meno campi")
    lines = [str(r.get("TEXT", "")) for r in _p(p, "OPTIONS", []) or []]
    if any(len(line) > 72 for line in lines):
        raise ABAPException("OPTION_NOT_VALID", "Ogni riga di OPTIONS può contenere al massimo 72 caratteri")
    where = _Where(" ".join(lines), all_fields)
    rows = [r for r in tables.rows_of(w, name) if where.match(r)]
    skip = int(_p(p, "ROWSKIPS", 0) or 0)
    count = int(_p(p, "ROWCOUNT", 0) or 0)
    rows = rows[skip:skip + count] if count else rows[skip:]
    if _x(_p(p, "NO_DATA", "")):
        rows = []
    out_fields, off = [], 0
    for f in chosen:
        out_fields.append({"FIELDNAME": f.name, "OFFSET": f"{off:06d}", "LENGTH": f"{f.length:06d}", "TYPE": f.type, "FIELDTEXT": f.text})
        off += f.length + len(delim)
    data = [{"WA": delim.join(fmt_field(f, r.get(f.name)) for f in chosen)} for r in rows]
    return {"FIELDS": out_fields, "DATA": data, "OPTIONS": [{"TEXT": line} for line in lines]}


# ---------------------------------------------------------------- PM: avvisi

def _equipment(w, equnr: str) -> dict:
    v = _strict(equnr, 18, "IW", "Apparecchiatura")
    eq = w.t("equipment").get((v,))
    if not eq:
        raise BusinessError("IW/002", f"Apparecchiatura {equnr} non esiste")
    return eq


@fm("BAPI_ALM_NOTIF_CREATE")
def _notif_create(w, s, p):
    h = _p(p, "NOTIFHEADER", {}) or {}
    ntype = str(_p(p, "NOTIF_TYPE", "")).strip()
    try:
        if ntype not in ("M1", "M2", "M3"):
            raise BusinessError("IM/005", f"Tipo avviso {ntype or '(vuoto)'} non previsto")
        if not str(h.get("SHORT_TEXT", "")).strip():
            raise BusinessError("IM/001", "Inserire un testo breve")
        eq = _equipment(w, h.get("EQUIPMENT", ""))
    except BusinessError as e:
        return {"NOTIFHEADER_EXPORT": {}, "RETURN": [from_business(e)]}
    temp = f"%{len(s.notifs) + 1:011d}"
    text = "\n".join(str(r.get("TEXT_LINE", "")) for r in _p(p, "LONGTEXTS", []) or [])
    s.notifs[temp] = {"NotificationType": ntype, "NotificationText": str(h["SHORT_TEXT"])[:40], "MaintNotifLongText": text,
                      "TechnicalObject": eq["Equipment"], "TechObjIsEquipOrFuncnlLoc": "EAMS_EQUI",
                      "MaintenancePlanningPlant": str(h.get("PLANPLANT") or eq["MaintenancePlanningPlant"]),
                      "MaintPriority": str(h.get("PRIORITY") or "3"), "IsBreakdown": _x(h.get("BREAKDOWN")),
                      "MalfunctionStartDate": conv.parse_d8(h.get("STRMLFNDATE")), "MalfunctionStartTime": conv.parse_t6(h.get("STRMLFNTIME")),
                      "YY1_AtheryaRef": _ref_from_extension(_p(p, "EXTENSIONIN", []))}
    return {"NOTIFHEADER_EXPORT": {"NOTIF_NO": temp, "NOTIF_TYPE": ntype, "SHORT_TEXT": s.notifs[temp]["NotificationText"],
                                   "EQUIPMENT": alpha(eq["Equipment"], 18), "PRIORITY": s.notifs[temp]["MaintPriority"]},
            "RETURN": []}


@fm("BAPI_ALM_NOTIF_SAVE")
def _notif_save(w, s, p):
    temp = str(_p(p, "NUMBER", ""))
    body = s.notifs.pop(temp, None)
    if body is None:
        return {"NOTIFHEADER": {}, "RETURN": [ret("E", "IM", "100", f"Avviso {temp} non presente nel buffer: usare BAPI_ALM_NOTIF_CREATE", temp)]}
    try:
        num = pm.create_notification(w, {k: v for k, v in body.items() if v not in (None, "")}, s.user)[0]
        lock(w, s, ("QMEL", alpha(num, 12)))
    except BusinessError as e:
        return {"NOTIFHEADER": {}, "RETURN": [from_business(e)]}
    return {"NOTIFHEADER": {"NOTIF_NO": alpha(num, 12), "NOTIF_TYPE": body["NotificationType"], "SHORT_TEXT": body["NotificationText"]},
            "RETURN": []}


def _notif(w, number) -> tuple[str, dict]:
    v = _strict(number, 12, "IM", "Avviso")
    n = w.t("notif").get((v,))
    if not n:
        raise BusinessError("IM/404", f"Avviso {number} non esiste")
    return v, n


@fm("BAPI_ALM_NOTIF_GET_DETAIL")
def _notif_detail(w, s, p):
    try:
        num, n = _notif(w, _p(p, "NUMBER", ""))
    except BusinessError as e:
        return {"NOTIFHEADER_EXPORT": {}, "NOTLONGTXT": [], "RETURN": [from_business(e)]}
    lines = [{"OBJTYPE": "QMEL", "OBJKEY": alpha(num, 12), "FORMAT_COL": "*", "TEXT_LINE": line[:132]}
             for line in str(n.get("MaintNotifLongText") or "").splitlines()]
    return {"NOTIFHEADER_EXPORT": {"NOTIF_NO": alpha(num, 12), "NOTIF_TYPE": n["NotificationType"], "SHORT_TEXT": n["NotificationText"],
                                   "EQUIPMENT": alpha(n["TechnicalObject"], 18), "PRIORITY": n.get("MaintPriority", ""),
                                   "ORDERID": alpha(n.get("MaintenanceOrder"), 12), "NOTIF_DATE": d8(n["NotificationCreationDate"]),
                                   "NOTIFTIME": t6(n["NotificationCreationTime"]), "BREAKDOWN": "X" if n.get("IsBreakdown") else "",
                                   "STRMLFNDATE": d8(n.get("MalfunctionStartDate")), "PLANPLANT": n["MaintenancePlanningPlant"],
                                   "CREATED_BY": ecc_user(n["_by"]), "COMPLDATE": d8(n.get("NotificationCompletionDate"))},
            "SYSTEMSTATUS": conv.status_line(conv.notif_status(n)), "NOTLONGTXT": lines, "RETURN": []}


@fm("BAPI_ALM_NOTIF_CLOSE")
def _notif_close(w, s, p):
    try:
        num, _ = _notif(w, _p(p, "NUMBER", ""))
        lock(w, s, ("QMEL", alpha(num, 12)))
        w.touch("notif", (num,))
        if not _x(_p(p, "TESTRUN", "")):
            pm.complete_notification(w, num, s.user)
    except BusinessError as e:
        return {"SYSTEMSTATUS": "", "USERSTATUS": "", "RETURN": [from_business(e)]}
    return {"SYSTEMSTATUS": conv.status_line(conv.notif_status(w.get("notif", (num,)))), "USERSTATUS": "", "RETURN": []}


# ---------------------------------------------------------------- PM: ordini

HEADER_MAP = {"SHORT_TEXT": "MaintenanceOrderDesc", "START_DATE": "MaintOrdBasicStartDate", "PRIORITY": "MaintPriority",
              "MN_WK_CTR": "MainWorkCenter"}


def _morder(w, number) -> tuple[str, dict]:
    v = _strict(number, 12, "IW", "Ordine")
    o = w.t("morder").get((v,))
    if not o:
        raise BusinessError("IW/404", f"Ordine {number} non esiste")
    return v, o


@fm("BAPI_ALM_ORDER_MAINTAIN")
def _order_maintain(w, s, p):
    methods = _p(p, "IT_METHODS", []) or []
    headers = _p(p, "IT_HEADER", []) or []
    headers_up = _p(p, "IT_HEADER_UP", []) or []
    ops = _p(p, "IT_OPERATION", []) or []
    comps = _p(p, "IT_COMPONENT", []) or []
    ref = _ref_from_extension(_p(p, "EXTENSION_IN", []))
    if not any(str(m.get("METHOD", "")).upper() == "SAVE" for m in methods):
        return {"RETURN": [ret("I", "IWO_BAPI2", "120", "Nessun metodo SAVE in IT_METHODS: nessun dato registrato")], "ET_NUMBERS": []}
    out, numbers, temp_map = [], [], {}

    def row_of(table, m):
        try:
            return table[int(m.get("REFNUMBER") or 1) - 1]
        except (ValueError, IndexError):
            raise BusinessError("IWO_BAPI2/110", f"REFNUMBER {m.get('REFNUMBER')} non trovato")

    try:
        # creazioni di testata, con operazioni e componenti che la riferiscono
        for m in methods:
            if str(m.get("OBJECTTYPE", "")).upper() != "HEADER" or str(m.get("METHOD", "")).upper() != "CREATE":
                continue
            h = row_of(headers, m)
            temp = str(m.get("OBJECTKEY") or h.get("ORDERID") or "%00000000001")[:12]
            eq = _equipment(w, h.get("EQUIPMENT", ""))
            notif = h.get("NOTIF_NO")
            if notif:
                notif, _ = _notif(w, notif)
                lock(w, s, ("QMEL", alpha(notif, 12)))
                w.touch("notif", (notif,))
            my_ops = [row_of(ops, x) for x in methods if str(x.get("OBJECTTYPE", "")).upper() == "OPERATION"
                      and str(x.get("METHOD", "")).upper() == "CREATE" and str(x.get("OBJECTKEY", "")).startswith(temp)]
            my_comps = [row_of(comps, x) for x in methods if str(x.get("OBJECTTYPE", "")).upper() == "COMPONENT"
                        and str(x.get("METHOD", "")).upper() == "CREATE" and str(x.get("OBJECTKEY", "")).startswith(temp)]
            start = conv.parse_d8(h.get("START_DATE")) or w.today
            body = {"MaintenanceOrderType": str(h.get("ORDER_TYPE", "")), "MaintenanceOrderDesc": str(h.get("SHORT_TEXT", ""))[:40],
                    "Equipment": eq["Equipment"], "MaintenancePlanningPlant": str(h.get("PLANPLANT") or eq["MaintenancePlanningPlant"]),
                    "MainWorkCenter": str(h.get("MN_WK_CTR", "")), "MaintOrdBasicStartDate": start,
                    "MaintPriority": str(h.get("PRIORITY") or "3")}
            if notif:
                body["MaintenanceNotification"] = notif
            if ref:
                body["YY1_AtheryaRef"] = ref
            if my_ops:
                body["to_MaintenanceOrderOperation"] = {"results": [
                    {"OperationDescription": str(o.get("DESCRIPTION", ""))[:40], "WorkCenter": str(o.get("WORK_CNTR") or body["MainWorkCenter"]),
                     "PlannedWorkQuantity": _num(o.get("WORK_ACTIVITY") or 2)} for o in my_ops]}
            if my_comps:
                body["to_MaintenanceOrderComponent"] = {"results": [
                    {"Material": str(c.get("MATERIAL", "")), "RequiredQuantity": _num(c.get("REQUIREMENT_QUANTITY") or 1)} for c in my_comps]}
            num = pm.create_order(w, body, s.user)[0]
            lock(w, s, ("ORDER", alpha(num, 12)))
            temp_map[temp] = num
            numbers.append({"OBJECTTYPE": "HEADER", "AUFNR_OLD": temp, "AUFNR_NEW": alpha(num, 12)})
            out.append(ret("S", "IWO_BAPI2", "126", f"Ordine {num} salvato", alpha(num, 12)))
        # modifiche e funzioni su ordini esistenti
        for m in methods:
            otype, method = str(m.get("OBJECTTYPE", "")).upper(), str(m.get("METHOD", "")).upper()
            if otype != "HEADER" or method in ("CREATE",):
                continue
            key = str(m.get("OBJECTKEY", ""))[:12]
            if key in temp_map:
                num = temp_map[key]
            else:
                num, _ = _morder(w, key)
            lock(w, s, ("ORDER", alpha(num, 12)))
            w.touch("morder", (num,))
            if method == "CHANGE":
                h = row_of(headers, m)
                up = row_of(headers_up, m) if headers_up else {}
                changes = {}
                for f, target in HEADER_MAP.items():
                    if _x(up.get(f)):
                        changes[target] = conv.parse_d8(h.get(f)) if f == "START_DATE" else str(h.get(f, ""))
                if changes:
                    pm.update_order(w, (num,), w.get("morder", (num,)), changes, s.user)
            elif method == "RELEASE":
                pm.release_order(w, num, s.user)
            elif method == "TECHNICALCOMPLETE":
                pm.technically_complete(w, num, s.user)
            elif method == "LOCK":
                w.update("morder", (num,), {"_locked": True}, s.user)
            elif method == "UNLOCK":
                w.update("morder", (num,), {"_locked": False}, s.user)
            else:
                raise BusinessError("IWO_BAPI2/112", f"Metodo {method} non supportato in questa simulazione")
            out.append(ret("S", "IWO_BAPI2", "126", f"Ordine {num}: {method} eseguito", alpha(num, 12)))
    except BusinessError as e:
        return {"RETURN": out + [from_business(e)], "ET_NUMBERS": []}
    return {"RETURN": out, "ET_NUMBERS": numbers}


@fm("BAPI_ALM_ORDER_GET_DETAIL")
def _order_detail(w, s, p):
    try:
        num, o = _morder(w, _p(p, "NUMBER", ""))
    except BusinessError as e:
        return {"ES_HEADER": {}, "ET_OPERATIONS": [], "ET_COMPONENTS": [], "RETURN": [from_business(e)]}
    header = {"ORDERID": alpha(num, 12), "ORDER_TYPE": o["MaintenanceOrderType"], "PLANPLANT": o["MaintenancePlanningPlant"],
              "MN_WK_CTR": o["MainWorkCenter"], "EQUIPMENT": alpha(o["Equipment"], 18), "SHORT_TEXT": o["MaintenanceOrderDesc"],
              "START_DATE": d8(o["MaintOrdBasicStartDate"]), "FINISH_DATE": d8(o.get("MaintOrdBasicEndDate")),
              "NOTIF_NO": alpha(o.get("MaintenanceNotification"), 12), "PRIORITY": o.get("MaintPriority", ""),
              "SYS_STATUS": conv.status_line(conv.order_status(w, o, "PM")), "ENTERED_BY": ecc_user(o["_by"]),
              "ENTER_DATE": d8(o["_at"]), "CHANGED_BY": ecc_user(o["_changed_by"]), "MAINTPLAN": alpha(o.get("MaintenancePlan"), 12)}
    opers = [{"ACTIVITY": op["MaintenanceOrderOperation"], "CONTROL_KEY": "PM01", "WORK_CNTR": op["WorkCenter"], "PLANT": PLANT,
              "DESCRIPTION": op["OperationDescription"], "WORK_ACTIVITY": op["PlannedWorkQuantity"], "UN_WORK": "STD",
              "ACT_WORK": op["ActualWorkQuantity"]} for op in o["to_MaintenanceOrderOperation"]["results"]]
    comps = [{"RESERV_NO": tables.rsnum_of_order(num), "RES_ITEM": c["MaintenanceOrderComponent"], "ACTIVITY": "0010",
              "MATERIAL": c["Material"], "REQUIREMENT_QUANTITY": c["RequiredQuantity"],
              "REQUIREMENT_QUANTITY_UNIT": tables.prod_unit(w, c["Material"]), "WITHDRAWN_QUANTITY": c["WithdrawnQuantity"],
              "PLANT": PLANT, "STGE_LOC": SLOC} for c in o["to_MaintenanceOrderComponent"]["results"]]
    return {"ES_HEADER": header, "ET_OPERATIONS": opers, "ET_COMPONENTS": comps, "RETURN": []}


@fm("BAPI_ALM_ORDERHEAD_GET_LIST")
def _order_list(w, s, p):
    ranges = _p(p, "IT_RANGES", []) or []

    def values(name):
        return [str(r.get("LOW_VALUE", "")) for r in ranges if str(r.get("FIELD_NAME", "")).upper() == name]

    eqs = {unalpha(v) for v in values("OPTIONS_FOR_EQUIPMENT")}
    types = set(values("OPTIONS_FOR_ORDER_TYPE"))
    plants = set(values("OPTIONS_FOR_PLANPLANT"))
    show_open, show_proc, show_done = (bool(values(n)) for n in ("SHOW_OPEN_DOCUMENTS", "SHOW_DOCUMENTS_IN_PROCESS", "SHOW_COMPLETED_DOCUMENTS"))
    if not (show_open or show_proc or show_done):
        show_open = show_proc = True
    res = []
    for num, o in tables._maint_orders(w):
        st = o["MaintOrdSystemStatus"]
        if eqs and o["Equipment"] not in eqs or types and o["MaintenanceOrderType"] not in types or plants and o["MaintenancePlanningPlant"] not in plants:
            continue
        if not ((st == "CRTD" and show_open) or (st == "REL" and show_proc) or (st == "TECO" and show_done)):
            continue
        res.append({"ORDERID": alpha(num, 12), "ORDER_TYPE": o["MaintenanceOrderType"], "SHORT_TEXT": o["MaintenanceOrderDesc"],
                    "EQUIPMENT": alpha(o["Equipment"], 18), "PLANPLANT": o["MaintenancePlanningPlant"], "MN_WK_CTR": o["MainWorkCenter"],
                    "START_DATE": d8(o["MaintOrdBasicStartDate"]), "S_STATUS": conv.status_line(conv.order_status(w, o, "PM")),
                    "NOTIF_NO": alpha(o.get("MaintenanceNotification"), 12)})
    return {"ET_RESULT": res, "RETURN": []}


@fm("BAPI_ALM_CONF_CREATE")
def _conf_create(w, s, p):
    out, detail = [], []
    try:
        for tt in _p(p, "TIMETICKETS", []) or []:
            num, _ = _morder(w, tt.get("ORDERID", ""))
            lock(w, s, ("ORDER", alpha(num, 12)))
            w.touch("morder", (num,))
            conf = pm.confirm_order(w, {"MaintenanceOrder": num, "MaintenanceOrderOperation": str(tt.get("OPERATION", "0010")),
                                        "ActualWorkQuantity": _num(tt.get("ACT_WORK")), "IsFinalConfirmation": _x(tt.get("FIN_CONF")),
                                        "ConfirmationText": str(tt.get("CONF_TEXT", ""))}, s.user)[0]
            detail.append({"TYPE": "S", "ID": "RU", "NUMBER": "100", "MESSAGE": f"Conferma {conf} registrata", "CONF_NO": alpha(conf, 10), "CONF_CNT": "00000001"})
    except BusinessError as e:
        r = from_business(e)
        return {"RETURN": r, "DETAIL_RETURN": detail + [{**{k: r[k] for k in ("TYPE", "ID", "NUMBER", "MESSAGE")}, "CONF_NO": "", "CONF_CNT": ""}]}
    return {"RETURN": ret("", "", "000", ""), "DETAIL_RETURN": detail}


@fm("MEASUREM_DOCUM_RFC_SINGLE_001")
def _measurement(w, s, p):
    point = str(_p(p, "MEASUREMENT_POINT", ""))
    if point.isdigit() and len(point) != 12:
        raise ABAPException("POINT_NOT_FOUND", f"Punto di misura {point} non trovato")
    pid = unalpha(point)
    mp = next((k[0] for k in w.t("measpoint") if unalpha(k[0]) == pid), None)
    if not mp:
        raise ABAPException("POINT_NOT_FOUND", f"Punto di misura {point} non trovato")
    value = str(_p(p, "RECORDED_VALUE", "")).replace(",", ".").strip()
    try:
        reading = float(value)
    except ValueError:
        raise ABAPException("CHAR_VALUE_NOT_ALLOWED", f"Valore {value!r} non numerico")
    doc = pm.create_measurement(w, {"MeasuringPoint": mp, "MeasurementReading": reading,
                                    "MeasurementDocumentText": str(_p(p, "SHORT_TEXT", ""))}, s.user)[0]
    if _x(_p(p, "COMMIT", "")):
        commit(w, s)
    return {"MEASUREMENT_DOCUMENT": alpha(doc, 20), "COMPLETE_DOCUMENT": {"MDOCM": alpha(doc, 20), "POINT": alpha(mp, 12)}}


@fm("BAPI_EQUI_GETDETAIL")
def _equi_detail(w, s, p):
    try:
        eq = _equipment(w, _p(p, "EQUIPMENT", ""))
    except BusinessError as e:
        return {"DATA_GENERAL_EXP": {}, "DATA_SPECIFIC_EXP": {}, "RETURN": from_business(e)}
    return {"DATA_GENERAL_EXP": {"DESCRIPT": eq["EquipmentName"], "OBJECTTYPE": eq.get("TechnicalObjectType", ""),
                                 "MAINTPLANT": eq["MaintenancePlanningPlant"], "PLANPLANT": eq["MaintenancePlanningPlant"],
                                 "WORK_CTR": eq["MainWorkCenter"]},
            "DATA_SPECIFIC_EXP": {"EQUICATGRY": "M", "READ_FLOC": eq["FunctionalLocation"]}, "RETURN": ret("", "", "000", "")}


# ---------------------------------------------------------------- PP

def _prodorder(w, number) -> tuple[str, dict]:
    v = _strict(number, 12, "CO", "Ordine")
    o = w.t("prodorder").get((v,))
    if not o:
        raise BusinessError("CO/404", f"Ordine {number} non esiste")
    return v, o


@fm("BAPI_PRODORD_GET_DETAIL")
def _prod_detail(w, s, p):
    objs = _p(p, "ORDER_OBJECTS", {}) or {}
    try:
        num, o = _prodorder(w, _p(p, "NUMBER", ""))
    except BusinessError as e:
        return {"RETURN": from_business(e), "HEADER": [], "OPERATION": [], "COMPONENT": []}
    status = conv.status_line(conv.order_status(w, o, "PP"))
    out = {"RETURN": ret("", "", "000", ""), "HEADER": [], "OPERATION": [], "COMPONENT": []}
    if _x(objs.get("HEADER")) or not objs:
        out["HEADER"] = [{"ORDER_NUMBER": alpha(num, 12), "ORDER_TYPE": o["ManufacturingOrderType"], "MATERIAL": o["Material"],
                          "PRODUCTION_PLANT": o["ProductionPlant"], "TARGET_QUANTITY": o["MfgOrderPlannedTotalQty"],
                          "CONFIRMED_QUANTITY": o["MfgOrderConfirmedYieldQty"], "SCRAP": o["MfgOrderConfirmedScrapQty"],
                          "UNIT": "ST", "BASIC_START_DATE": d8(o["MfgOrderPlannedStartDate"]), "BASIC_END_DATE": d8(o["MfgOrderPlannedEndDate"]),
                          "ACTUAL_RELEASE_DATE": d8(o.get("MfgOrderActualReleaseDate")), "SYSTEM_STATUS": status,
                          "ENTERED_BY": ecc_user(o["_by"]), "MRP_CONTROLLER": "001"}]
    if _x(objs.get("OPERATIONS")):
        for (oid, vornr), op in sorted(w.t("prodop").items()):
            if oid == num:
                out["OPERATION"].append({"ORDER_NUMBER": alpha(num, 12), "SEQUENCE_NO": "000000", "OPERATION_NUMBER": vornr,
                                         "WORK_CENTER": op["WorkCenter"], "PLANT": op["Plant"], "DESCRIPTION": op["OperationText"][:40],
                                         "QUANTITY": op["OpPlannedTotalQuantity"], "CONF_QUANTITY": op["OpTotalConfirmedYieldQty"],
                                         "EARL_SCHED_START_DATE_EXEC": d8(op.get("_start")), "EARL_SCHED_START_TIME_EXEC": t6(op.get("_start")),
                                         "EARL_SCHED_FIN_DATE_EXEC": d8(op.get("_end")), "EARL_SCHED_FIN_TIME_EXEC": t6(op.get("_end")),
                                         "SYSTEM_STATUS": op["OperationStatus"]})
    if _x(objs.get("COMPONENTS")):
        for (oid, item), c in sorted(w.t("prodcomp").items()):
            if oid == num:
                out["COMPONENT"].append({"RESERVATION_NUMBER": tables.rsnum_of_order(num), "RESERVATION_ITEM": item, "MATERIAL": c["Material"],
                                         "REQ_QUAN": c["RequiredQuantity"], "WITHDRAWN_QUANTITY": c["WithdrawnQuantity"],
                                         "BASE_UOM": unit(c["BaseUnit"]), "REQ_DATE": d8(c["RequirementDate"]), "STORAGE_LOCATION": c.get("StorageLocation", SLOC)})
    return out


@fm("BAPI_PRODORD_RELEASE")
def _prod_release(w, s, p):
    detail = []
    for r in _p(p, "ORDERS", []) or []:
        try:
            num, _ = _prodorder(w, r.get("ORDER_NUMBER", ""))
            lock(w, s, ("ORDER", alpha(num, 12)))
            pp.release_production_order(w, num, s.user)
            detail.append({"ORDER_NUMBER": alpha(num, 12), **ret("S", "CO", "093", f"Ordine {num} rilasciato")})
        except BusinessError as e:
            detail.append({"ORDER_NUMBER": str(r.get("ORDER_NUMBER", "")), **from_business(e)})
    return {"RETURN": ret("", "", "000", ""), "DETAIL_RETURN": detail}


@fm("BAPI_PRODORD_CREATE_FROM_PLORD")
def _from_plord(w, s, p):
    try:
        v = _strict(_p(p, "PLANNED_ORDER", ""), 10, "61", "Ordine pianificato")
        if (v,) not in w.t("plannedorder"):
            raise BusinessError("61/404", f"Ordine pianificato {v} non esiste")
        num = pp.convert_planned_order(w, v, s.user)
        lock(w, s, ("ORDER", alpha(num, 12)))
    except BusinessError as e:
        return {"PRODUCTION_ORDER": "", "RETURN": from_business(e)}
    return {"PRODUCTION_ORDER": alpha(num, 12), "RETURN": ret("", "", "000", "")}


@fm("BAPI_PRODORDCONF_CREATE_TT")
def _prod_conf(w, s, p):
    detail = []
    try:
        for tt in _p(p, "TIMETICKETS", []) or []:
            num, _ = _prodorder(w, tt.get("ORDERID", ""))
            lock(w, s, ("ORDER", alpha(num, 12)))
            for k in [k for k in w.t("prodcomp") if k[0] == num]:
                w.touch("prodcomp", k)
            w.touch("prodop", (num, str(tt.get("OPERATION", "0010"))))
            conf = pp.confirm_operation(w, {"OrderID": num, "OrderOperation": str(tt.get("OPERATION", "0010")),
                                            "ConfirmationYieldQuantity": _num(tt.get("YIELD")), "ConfirmationScrapQuantity": _num(tt.get("SCRAP")),
                                            "FinalConfirmationType": "X" if _x(tt.get("FIN_CONF")) else "",
                                            "ConfirmationText": str(tt.get("CONF_TEXT", ""))}, s.user)[0]
            detail.append({**ret("S", "RU", "100", f"Conferma {conf} registrata"), "CONF_NO": alpha(conf, 10), "CONF_CNT": "00000001"})
    except BusinessError as e:
        return {"RETURN": ret("", "", "000", ""), "DETAIL_RETURN": detail + [{**from_business(e), "CONF_NO": "", "CONF_CNT": ""}]}
    return {"RETURN": ret("", "", "000", ""), "DETAIL_RETURN": detail}


@fm("BAPI_PLANNEDORDER_GET_DETAIL")
def _plord_detail(w, s, p):
    v = str(_p(p, "PLANNEDORDER", ""))
    try:
        v = _strict(v, 10, "61", "Ordine pianificato")
        po = w.t("plannedorder").get((v,))
        if not po:
            raise BusinessError("61/404", f"Ordine pianificato {v} non esiste")
    except BusinessError as e:
        return {"HEADERDATA": {}, "RETURN": from_business(e)}
    return {"HEADERDATA": {"PLANNEDORDER_NUM": alpha(v, 10), "MATERIAL": po["Material"], "PLAN_PLANT": po["ProductionPlant"],
                           "PROD_PLANT": po["ProductionPlant"], "TOTAL_PLORD_QTY": po["PlannedTotalQtyInBaseUnit"], "BASE_UOM": "ST",
                           "ORDER_START_DATE": d8(po["PlndOrderPlannedStartDate"]), "ORDER_FIN_DATE": d8(po["PlndOrderPlannedEndDate"]),
                           "PLDORD_PROFILE": po["PlannedOrderType"], "FIRMING_IND": "X" if po.get("PlannedOrderIsFirm") else "",
                           "MRP_CONTROLLER": po["MRPController"]}, "RETURN": ret("", "", "000", "")}


def _mrp_elements(w, mat: str) -> list[dict]:
    out = []

    def add(ind, data, day, qty):
        out.append({"MRP_ELEMENT_IND": ind, "ELEMNT_DATA": data, "AVAIL_DATE": d8(day), "REC_REQD_QTY": round(qty, 3)})

    for p in w.t("pir").values():
        left = p["PlannedQuantity"] - p.get("_consumed", 0)
        if p["Product"] == mat and left > 0 and p["WorkingDayDate"] >= pp.monday(w.today):
            add("PP", "LSF", p["WorkingDayDate"], -left)
    for k, po in w.t("plannedorder").items():
        if po["Material"] == mat:
            add("PA", alpha(k[0], 10), po["PlndOrderPlannedEndDate"], po["PlannedTotalQtyInBaseUnit"])
        bom = w.t("bom").get((po["Material"],))
        for it in (bom or {}).get("Items", []):
            if it["BillOfMaterialComponent"] == mat:
                add("SB", alpha(k[0], 10), po["PlndOrderPlannedStartDate"], -po["PlannedTotalQtyInBaseUnit"] * it["BillOfMaterialItemQuantity"])
    for k, o in w.t("prodorder").items():
        left = o["MfgOrderPlannedTotalQty"] - o["_gr_qty"]
        if o["Material"] == mat and left > 0 and o["OrderSystemStatus"] != "TECO":
            add("FE", alpha(k[0], 12), o["MfgOrderPlannedEndDate"], left)
    for r in tables.r_resb(w):
        if r["MATNR"] == mat and r["XLOEK"] != "X" and r["BDMNG"] - r["ENMNG"] > 1e-9:
            add("AR" if r["AUFNR"] else "MR", r["AUFNR"] or r["RSNUM"], conv.parse_d8(r["BDTER"]) or w.today, -(r["BDMNG"] - r["ENMNG"]))
    for pr in w.t("preq").values():
        if pr.get("IsDeleted"):
            continue
        for it in pr["to_PurchaseReqnItem"]["results"]:
            if it["Material"] == mat and not it["_received"]:
                day = it["_delivery"] or it.get("DeliveryDate")
                add("BA", alpha(pr["PurchaseRequisition"], 10), day if isinstance(day, dt.date) else w.today, float(it["RequestedQuantity"]))
    out.sort(key=lambda r: (r["AVAIL_DATE"], r["REC_REQD_QTY"] < 0))
    return out


@fm("BAPI_MATERIAL_STOCK_REQ_LIST")
def _md04(w, s, p):
    mat = str(_p(p, "MATERIAL", "")).strip()
    prod = w.t("product").get((mat,))
    if not prod or prod["Plant"] != str(_p(p, "PLANT", "")):
        return {"MRP_LIST": {}, "MRP_ITEMS": [], "MRP_STOCK_DETAIL": {}, "RETURN": ret("E", "M3", "305", f"Materiale {mat} non esiste nella divisione")}
    stock = inv.stock_qty(w, mat)
    items = [{"MRP_ELEMENT_IND": "WB", "ELEMNT_DATA": "Giacenza", "AVAIL_DATE": d8(w.today), "REC_REQD_QTY": round(stock, 3)}]
    items += _mrp_elements(w, mat)
    running = 0.0
    for it in items:
        running += it["REC_REQD_QTY"]
        it["AVAIL_QTY"] = round(running, 3)
    return {"MRP_LIST": {"MATERIAL": mat, "PLANT": PLANT, "MRP_TYPE": prod.get("MRPType", ""), "MRP_CTRLER": "001",
                         "SAFETY_STOCK": prod.get("SafetyStockQuantity", 0) or 0, "BASE_UOM": unit(prod["BaseUnit"])},
            "MRP_STOCK_DETAIL": {"UNRESTRICTED_STCK": round(stock, 3)}, "MRP_ITEMS": items, "RETURN": ret("", "", "000", "")}


@fm("BAPI_MATERIAL_AVAILABILITY")
def _availability(w, s, p):
    mat = str(_p(p, "MATERIAL", "")).strip()
    prod = w.t("product").get((mat,))
    if not prod or prod["Plant"] != str(_p(p, "PLANT", "")):
        return {"AV_QTY_PLT": 0, "RETURN": ret("E", "M3", "305", f"Materiale {mat} non esiste nella divisione")}
    open_res = sum(r["BDMNG"] - r["ENMNG"] for r in tables.r_resb(w) if r["MATNR"] == mat and r["XLOEK"] != "X" and r["XWAOK"] == "X")
    return {"AV_QTY_PLT": round(max(0.0, inv.stock_qty(w, mat) - open_res), 3), "DIALOGFLAG": "", "ENDLEADTME": d8(
        w.today + dt.timedelta(days=int(prod.get("PlannedDeliveryDurationInDays") or 0))), "RETURN": ret("", "", "000", "")}


@fm("BAPI_MATERIAL_GET_DETAIL")
def _mat_detail(w, s, p):
    mat = str(_p(p, "MATERIAL", "")).strip()
    prod = w.t("product").get((mat,))
    if not prod:
        return {"MATERIAL_GENERAL_DATA": {}, "RETURN": ret("E", "M3", "305", f"Materiale {mat} non esiste")}
    return {"MATERIAL_GENERAL_DATA": {"MATL_DESC": prod["ProductDescription"], "MATL_TYPE": prod["ProductType"],
                                      "BASE_UOM": unit(prod["BaseUnit"]), "BASE_UOM_ISO": "PCE" if prod["BaseUnit"] == "PC" else prod["BaseUnit"],
                                      "MATL_GROUP": "GOMMA" if prod["ProductType"] in ("FERT", "ROH") else "RICAMBI"},
            "RETURN": ret("S", "M3", "800", "Dettagli materiale letti")}


# ---------------------------------------------------------------- MM: movimenti, prenotazioni, RdA

def _material(w, mat: str, plant: str) -> dict:
    prod = w.t("product").get((mat,))
    if not prod:
        raise BusinessError("M3/305", f"Materiale {mat} non esiste")
    if prod["Plant"] != plant:
        raise BusinessError("M3/351", f"Materiale {mat} non esteso alla divisione {plant}")
    return prod


@fm("BAPI_GOODSMVT_CREATE")
def _goodsmvt(w, s, p):
    items = _p(p, "GOODSMVT_ITEM", []) or []
    header = _p(p, "GOODSMVT_HEADER", {}) or {}
    docs = []
    try:
        if not items:
            raise BusinessError("M7/001", "Nessuna posizione da registrare")
        for it in items:
            mvt = str(it.get("MOVE_TYPE", ""))
            mat, qty = str(it.get("MATERIAL", "")).strip(), _num(it.get("ENTRY_QNT"))
            _material(w, mat, str(it.get("PLANT", "")))
            if qty <= 0:
                raise BusinessError("M7/003", "Quantità da registrare non valida")
            res_no = str(it.get("RESERV_NO", "") or "")
            if res_no:
                rid = _strict(res_no, 10, "M7", "Prenotazione")
                r = w.t("reservation").get((rid,))
                if not r:
                    raise BusinessError("M7/404", f"Prenotazione {res_no} non esiste")
                idx = next((i for i, x in enumerate(r["to_ReservationDocumentItem"]["results"])
                            if x["ReservationItem"] == str(it.get("RES_ITEM", "0001")).zfill(4)), None)
                if idx is None:
                    raise BusinessError("M7/405", f"Posizione {it.get('RES_ITEM')} non esiste nella prenotazione {res_no}")
                w.touch("reservation", (rid,))
                refs = {"MaintenanceOrder": unalpha(it.get("ORDERID", ""))} if it.get("ORDERID") else {}
                docs.append(inv.withdraw_reservation_item(w, rid, idx, qty, s.user, refs))
            elif mvt in ("261", "201"):
                refs = {}
                if it.get("ORDERID"):
                    num = _strict(it["ORDERID"], 12, "M7", "Ordine")
                    if (num,) in w.t("morder"):
                        refs = {"MaintenanceOrder": num}
                    elif (num,) in w.t("prodorder"):
                        refs = {"ManufacturingOrder": num}
                    else:
                        raise BusinessError("M7/404", f"Ordine {it['ORDERID']} non esiste")
                elif mvt == "201" and not it.get("COSTCENTER"):
                    raise BusinessError("M7/021", "Inserire un centro di costo per il movimento 201")
                docs.append(inv.goods_issue(w, mvt, mat, qty, s.user, refs, ("0030", "0010", "0020"),
                                            str(header.get("HEADER_TXT", ""))[:25] or inv.MOVEMENT_TEXT.get(mvt, "")))
            else:
                raise BusinessError("M7/010", f"Tipo movimento {mvt} non gestito in questa simulazione")
    except BusinessError as e:
        return {"GOODSMVT_HEADRET": {}, "MATERIALDOCUMENT": "", "MATDOCUMENTYEAR": "", "RETURN": [from_business(e)]}
    doc = docs[0] if docs else ""
    year = str(w.today.year)
    return {"GOODSMVT_HEADRET": {"MAT_DOC": doc, "DOC_YEAR": year}, "MATERIALDOCUMENT": doc, "MATDOCUMENTYEAR": year, "RETURN": []}


@fm("BAPI_RESERVATION_CREATE1")
def _res_create(w, s, p):
    h = _p(p, "RESERVATIONHEADER", {}) or {}
    items = _p(p, "RESERVATIONITEMS", []) or []
    try:
        mvt = str(h.get("MOVE_TYPE", "")).strip()
        if mvt not in ("201", "261"):
            raise BusinessError("M7/010", f"Tipo movimento {mvt or '(vuoto)'} non previsto per le prenotazioni")
        order = ""
        if mvt == "261":
            order = _strict(h.get("ORDERID", ""), 12, "M7", "Ordine")
            if (order,) not in w.t("morder"):
                raise BusinessError("M7/404", f"Ordine {h.get('ORDERID')} non esiste")
        elif not h.get("COSTCENTER"):
            raise BusinessError("M7/021", "Inserire un centro di costo per il movimento 201")
        body_items = []
        for it in items:
            mat, plant = str(it.get("MATERIAL", "")).strip(), str(it.get("PLANT", ""))
            _material(w, mat, plant)
            body_items.append({"Material": mat, "Plant": plant, "StorageLocation": str(it.get("STGE_LOC") or SLOC),
                               "ResvnItmRequiredQtyInBaseUnit": f"{_num(it.get('ENTRY_QNT')):g}",
                               "MatlCompRequirementDate": conv.parse_d8(it.get("REQ_DATE")) or conv.parse_d8(h.get("RES_DATE")) or w.today})
        res = inv.create_reservation(w, {"GoodsMovementType": mvt, "YY1_AtheryaRef": _ref_from_extension(_p(p, "EXTENSIONIN", [])),
                                         "to_ReservationDocumentItem": {"results": body_items}}, s.user)[0]
        w.update("reservation", (res,), {"_kostl": str(h.get("COSTCENTER", "")), "_aufnr": order}, s.user, quiet=True)
        lock(w, s, ("RKPF", alpha(res, 10)))
    except BusinessError as e:
        return {"RESERVATION": "", "RETURN": [from_business(e)]}
    return {"RESERVATION": alpha(res, 10), "RETURN": [ret("S", "M7", "060", f"Prenotazione {res} creata", alpha(res, 10))]}


@fm("BAPI_RESERVATION_DELETE")
def _res_delete(w, s, p):
    try:
        rid = _strict(_p(p, "RESERVATION", ""), 10, "M7", "Prenotazione")
        r = w.t("reservation").get((rid,))
        if not r:
            raise BusinessError("M7/404", f"Prenotazione {rid} non esiste")
        if any(_num(i["ResvnItmWithdrawnQtyInBaseUnit"]) > 0 for i in r["to_ReservationDocumentItem"]["results"]):
            raise BusinessError("M7/062", "Prenotazione già prelevata: non cancellabile")
        lock(w, s, ("RKPF", alpha(rid, 10)))
        w.delete("reservation", (rid,), s.user)
    except BusinessError as e:
        return {"RETURN": [from_business(e)]}
    return {"RETURN": [ret("S", "M7", "061", f"Prenotazione {rid} cancellata")]}


@fm("BAPI_PR_CREATE")
def _pr_create(w, s, p):
    h = _p(p, "PRHEADER", {}) or {}
    items = _p(p, "PRITEM", []) or []
    try:
        body_items = []
        for it in items:
            mat, plant = str(it.get("MATERIAL", "")).strip(), str(it.get("PLANT", ""))
            prod = _material(w, mat, plant)
            qty = _num(it.get("QUANTITY"))
            if qty <= 0:
                raise BusinessError("ME/040", "Inserire una quantità")
            body_items.append({"Material": mat, "Plant": plant, "RequestedQuantity": f"{qty:g}", "BaseUnit": prod["BaseUnit"],
                               "DeliveryDate": conv.parse_d8(it.get("DELIV_DATE")) or w.today, "PurReqnSource": "RFC"})
        pr = inv.create_purchase_requisition(w, {"PurchaseRequisitionType": str(h.get("PR_TYPE") or "NB"),
                                                 "YY1_AtheryaRef": _ref_from_extension(_p(p, "EXTENSIONIN", [])),
                                                 "to_PurchaseReqnItem": {"results": body_items}}, s.user)[0]
        lock(w, s, ("EBAN", alpha(pr, 10)))
    except BusinessError as e:
        return {"NUMBER": "", "RETURN": [from_business(e)]}
    return {"NUMBER": alpha(pr, 10), "RETURN": [ret("S", "06", "402", f"Richiesta d'acquisto {pr} creata", alpha(pr, 10))]}


@fm("BAPI_REQUISITION_DELETE")
def _pr_delete(w, s, p):
    num = str(_p(p, "NUMBER", ""))
    if num.isdigit() and len(num) != 10:
        return {"RETURN": [ret_old("E", "06 105", f"Richiesta d'acquisto {num} non esiste")]}
    pr = w.t("preq").get((unalpha(num),))
    if not pr:
        return {"RETURN": [ret_old("E", "06 105", f"Richiesta d'acquisto {num} non esiste")]}
    if any(it["ProcessingStatus"] != "N" for it in pr["to_PurchaseReqnItem"]["results"]):
        return {"RETURN": [ret_old("E", "06 120", "Richiesta già trasformata in ordine: non cancellabile")]}
    try:
        lock(w, s, ("EBAN", alpha(num, 10)))
    except BusinessError as e:
        return {"RETURN": [ret_old("E", "MC 601", e.message)]}
    w.update("preq", (unalpha(num),), {"IsDeleted": True}, s.user)
    return {"RETURN": [ret_old("S", "06 106", f"Richiesta d'acquisto {unalpha(num)} contrassegnata per la cancellazione")]}


# ---------------------------------------------------------------- WM (LE-WM)

@fm("L_TO_CREATE_SINGLE")
def _to_create(w, s, p):
    if str(_p(p, "I_LGNUM", "")) != conv.LGNUM:
        raise ABAPException("NO_TO_CREATED", f"Numero magazzino {_p(p, 'I_LGNUM', '')} non valido")
    mat, qty = str(_p(p, "I_MATNR", "")).strip(), _num(_p(p, "I_ANFME", 0))
    if (mat,) not in w.t("product"):
        raise ABAPException("MATERIAL_NOT_FOUND", f"Materiale {mat} non esiste")
    src, dst = str(_p(p, "I_VLPLA", "")).strip() or None, str(_p(p, "I_NLPLA", "")).strip()
    if dst and (dst,) not in w.t("bin"):
        raise ABAPException("NO_TO_CREATED", f"Ubicazione di destinazione {dst} non esiste")
    if not dst:
        dst = inv.free_bin_for(w, mat)
    created = inv.create_task(w, "TRASFERIMENTO", mat, qty, src, dst, str(_p(p, "I_BENUM", "")), s.user)
    if not created:
        raise ABAPException("NO_TO_CREATED", f"Giacenza insufficiente di {mat}: nessun ordine di trasferimento creato")
    if _x(_p(p, "I_COMMIT_WORK", "X")):
        commit(w, s)
    tanum = alpha(created[0], 10)
    return {"E_TANUM": tanum, "E_LTAP": next((r for r in tables.r_ltap(w) if r["TANUM"] == tanum), {})}


@fm("L_TO_CONFIRM")
def _to_confirm(w, s, p):
    num = str(_p(p, "I_TANUM", ""))
    t = w.t("whtask").get((unalpha(num),)) if not (num.isdigit() and len(num) != 10) else None
    if not t:
        raise ABAPException("TO_DOESNT_EXIST", f"Ordine di trasferimento {num} non esiste")
    if t["WarehouseTaskStatus"] == "C":
        raise ABAPException("TO_CONFIRMED", f"Ordine di trasferimento {num} già confermato")
    try:
        inv.confirm_task(w, unalpha(num), s.user)
    except BusinessError as e:
        raise ABAPException("NOT_CONFIRMED", e.message)
    if _x(_p(p, "I_COMMIT_WORK", "X")):
        commit(w, s)
    return {}


# ---------------------------------------------------------------- sviluppo del cliente per Atherya

@fm("Z_ATHERYA_PRODORD_OPR_CHANGE")
def _z_opr_change(w, s, p):
    """Modulo cliente (da trasportare): ECC non ha una BAPI rilasciata per cambiare il centro di lavoro di
    un'operazione dell'ordine di produzione. Controlla il blocco, il valore atteso (concorrenza ottimistica)
    e poi applica la modifica come farebbe CO02. Il commit resta al chiamante."""
    try:
        num, _ = _prodorder(w, _p(p, "IV_AUFNR", ""))
        vornr = str(_p(p, "IV_VORNR", "0010")).zfill(4)
        op = w.t("prodop").get((num, vornr))
        if not op:
            raise BusinessError("CO/031", f"Operazione {vornr} non esiste nell'ordine {num}")
        lock(w, s, ("ORDER", alpha(num, 12)))
        expected = str(_p(p, "IV_EXPECTED_ARBPL", "")).strip()
        if expected and op["WorkCenter"] != expected:
            raise BusinessError("ZATHERYA/001", f"Operazione modificata da altri: centro di lavoro attuale {op['WorkCenter']}, atteso {expected}")
        changes = {}
        if str(_p(p, "IV_ARBPL", "")).strip():
            changes["WorkCenter"] = str(p["IV_ARBPL"]).strip()
        start = conv.parse_d8(_p(p, "IV_START_DATE", ""))
        if start:
            changes["OpErlstSchedldExecStrtDte"] = start
            tm = conv.parse_t6(_p(p, "IV_START_TIME", "")) or dt.time(6)
            changes["OpErlstSchedldExecStrtTme"] = f"PT{tm.hour:02d}H{tm.minute:02d}M00S"
        if not changes:
            raise BusinessError("ZATHERYA/002", "Nessuna modifica richiesta")
        pp.update_operation(w, (num, vornr), op, changes, s.user)
    except BusinessError as e:
        return {"ET_RETURN": [from_business(e)]}
    return {"ET_RETURN": [ret("S", "ZATHERYA", "000", f"Ordine {num} operazione {vornr} aggiornato")]}


FUNCTION_NAMES = sorted(FUNCTIONS)
