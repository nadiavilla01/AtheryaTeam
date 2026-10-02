"""Conversioni ECC: formato interno delle chiavi (ALPHA), date e ore, unità, utenti, stati di sistema."""

import datetime as dt

LGNUM = "100"          # numero magazzino WM (in ECC: LE-WM, non EWM)
CLIENT = "100"
SYSID = "ECD"
WM_TYPE = {"0010": "001", "0020": "002", "0030": "003", "0100": "100", "9010": "902", "9020": "916"}
WM_TYPE_IN = {v: k for k, v in WM_TYPE.items()}
UNIT = {"PC": "ST", "KG": "KG", "L": "L", "H": "STD"}
UNIT_IN = {**{v: k for k, v in UNIT.items()}, "PC": "PC", "KG": "KG", "L": "L", "H": "H", "PCE": "PC"}

# Nomi utente ECC: al massimo 12 caratteri.
ECC_USER = {"PIANIFICATORE": "PIANIF01", "MANUTENZIONE": "MANUT_CAPO", "MANUTENTORE": "MANUT01", "OPERATORE": "OPER_TURNO",
            "MAGAZZINO": "MAGAZ01", "SPEDIZIONI": "SPEDIZ01", "ACQUISTI": "ACQUISTI01", "FORNITORE": "WF-BATCH",
            "BATCH": "BATCH_PP", "SCADA": "RFC_SCADA", "SEED": "MIGRAZIONE", "SIM": "SIM", "ADMIN": "ADMIN"}


def user(u: str | None) -> str:
    return ECC_USER.get(u or "", (u or "")[:12])


def alpha(value, length: int) -> str:
    """CONVERSION_EXIT_ALPHA_INPUT: i valori solo numerici si completano con zeri a sinistra."""
    s = "" if value is None else str(value).strip()
    return s.zfill(length) if s.isdigit() else s


def unalpha(value) -> str:
    """CONVERSION_EXIT_ALPHA_OUTPUT."""
    s = "" if value is None else str(value)
    return (s.lstrip("0") or "0") if s.isdigit() else s


def d8(d) -> str:
    if isinstance(d, dt.datetime):
        d = d.date()
    return d.strftime("%Y%m%d") if isinstance(d, dt.date) else "00000000"


def t6(t) -> str:
    if isinstance(t, dt.datetime):
        t = t.time()
    if isinstance(t, dt.time):
        return t.strftime("%H%M%S")
    if isinstance(t, str) and t.startswith("PT"):
        return f"{t[2:4]}{t[5:7]}{t[8:10]}"
    return "000000"


def parse_d8(s) -> dt.date | None:
    if isinstance(s, dt.date):
        return s
    s = str(s or "").strip()
    if not s or s == "00000000":
        return None
    try:
        return dt.datetime.strptime(s.replace("-", ""), "%Y%m%d").date()
    except ValueError:
        return None


def parse_t6(s) -> dt.time | None:
    s = str(s or "").replace(":", "").strip()
    if len(s) != 6 or not s.isdigit():
        return None
    return dt.time(int(s[:2]), int(s[2:4]), int(s[4:]))


def unit(u: str) -> str:
    return UNIT.get(u, u)


def objnr_order(aufnr: str) -> str:
    return "OR" + alpha(aufnr, 12)


def objnr_notif(qmnum: str) -> str:
    return "QM" + alpha(qmnum, 12)


def objnr_equi(equnr: str) -> str:
    return "IE" + alpha(equnr, 18)


# Stati di sistema (tabella TJ02T): codici interni e testi brevi. Da verificare sul sistema del cliente.
TJ02T = {
    "I0001": ("CRTD", "Creato"), "I0002": ("REL", "Rilasciato"), "I0009": ("CNF", "Confermato"),
    "I0010": ("PCNF", "Parzialmente confermato"), "I0012": ("DLV", "Consegnato"), "I0043": ("LKD", "Bloccato"),
    "I0045": ("TECO", "Chiuso tecnicamente"), "I0068": ("OSNO", "Avviso in sospeso"), "I0070": ("NOPR", "Avviso in elaborazione"),
    "I0071": ("ORAS", "Ordine assegnato"), "I0072": ("NOCO", "Avviso completato"), "I0076": ("DLFL", "Contrassegno di cancellazione"),
}
BY_TXT04 = {v[0]: k for k, v in TJ02T.items()}


def order_status(w, row: dict, kind: str) -> list[str]:
    """Stati attivi di un ordine (kind 'PM' o 'PP') come lista di TXT04."""
    out = []
    if kind == "PM":
        st = row["MaintOrdSystemStatus"]
        out.append(st)
        if st == "REL" and any(op["ActualWorkQuantity"] > 0 for op in row["to_MaintenanceOrderOperation"]["results"]):
            out.append("PCNF")
        if row.get("_locked"):
            out.append("LKD")
    else:
        st = row["OrderSystemStatus"]
        if st == "PCNF":
            out += ["REL", "PCNF"]
        elif st == "TECO":
            out += ["REL", "CNF", "TECO"]
        else:
            out.append(st)
        if row.get("_gr_qty", 0) + 1e-9 >= row["MfgOrderPlannedTotalQty"] > 0:
            out.append("DLV")
    if row.get("IsDeleted"):
        out.append("DLFL")
    return out


def notif_status(row: dict) -> list[str]:
    phase = row.get("MaintNotifProcessPhaseCode", "1")
    out = {"1": ["OSNO"], "2": ["OSNO"], "3": ["NOPR", "ORAS"], "4": ["NOCO"]}[phase]
    if phase == "4" and row.get("MaintenanceOrder"):
        out = ["NOCO", "ORAS"]
    if row.get("IsDeleted"):
        out = out + ["DLFL"]
    return out


def status_line(codes: list[str]) -> str:
    return " ".join(codes)
