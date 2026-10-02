"""Transazioni dell'interfaccia in stile SAP GUI (per HTML) sulla facciata ECC.

Ogni transazione è una schermata di selezione opzionale, poi un elenco ALV o un dettaglio.
Le azioni passano dagli stessi moduli funzione RFC (utente PLANNER), con commit immediato:
stesse regole di business, stessi blocchi, stesso registro.
"""

import datetime as dt

from .. import pp
from ..world import BusinessError
from . import conv, rfc, tables
from .conv import alpha, unalpha

# ---------------------------------------------------------------- formattazione


def fdate(v) -> str:
    v = str(v or "")
    return f"{v[6:8]}.{v[4:6]}.{v[:4]}" if len(v) == 8 and v != "00000000" else ""


def ftime(v) -> str:
    v = str(v or "")
    return f"{v[:2]}:{v[2:4]}:{v[4:6]}" if len(v) == 6 else ""


def fnum(v) -> str:
    try:
        x = float(v or 0)
    except (TypeError, ValueError):
        return str(v)
    s = f"{x:,.3f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return s.rstrip("0").rstrip(",") if "," in s else s


def col(field, text, kind="C", width=None):
    return {"field": field, "text": text, "kind": kind, "width": width}


def status_of(w, objnr: str) -> str:
    return " ".join(conv.TJ02T[r["STAT"]][0] for r in tables.r_jest(w) if r["OBJNR"] == objnr)


def _jest_index(w) -> dict:
    out = {}
    for r in tables.r_jest(w):
        out.setdefault(r["OBJNR"], []).append(conv.TJ02T[r["STAT"]][0])
    return {k: " ".join(v) for k, v in out.items()}


def _texts(w, table, key, field):
    return {r[key]: r[field] for r in tables.rows_of(w, table)}


# ---------------------------------------------------------------- elenchi

def iw28(w, sel):
    st, eqkt = _jest_index(w), _texts(w, "EQKT", "EQUNR", "EQKTX")
    rows = []
    for r in tables.r_viqmel(w):
        status = st.get(r["OBJNR"], "")
        if sel.get("EQUNR") and unalpha(r["EQUNR"]) != unalpha(sel["EQUNR"]):
            continue
        if sel.get("APERTI") == "X" and "NOCO" in status:
            continue
        rows.append({**r, "EQKTX": eqkt.get(r["EQUNR"], ""), "STATUS": status, "_key": r["QMNUM"]})
    rows.sort(key=lambda r: r["QMNUM"], reverse=True)
    return {"columns": [col("QMNUM", "Avviso", "K"), col("QMART", "Tp"), col("QMTXT", "Descrizione"), col("EQUNR", "Apparecchiatura", "K"),
                        col("EQKTX", "Denominazione"), col("MSAUS", "Guasto"), col("PRIOK", "P"), col("QMDAT", "Data", "D"),
                        col("MZEIT", "Ora", "T"), col("AUFNR", "Ordine", "K"), col("STATUS", "Stato sistema"), col("ERNAM", "Creato da"),
                        col("ZZ_ATHERYA_REF", "Rif. Atherya")],
            "rows": rows, "drill": "IW23"}


def iw38(w, sel):
    st, eqkt = _jest_index(w), _texts(w, "EQKT", "EQUNR", "EQKTX")
    afih = {r["AUFNR"]: r for r in tables.r_afih(w)}
    afko = {r["AUFNR"]: r for r in tables.r_afko(w)}
    rows = []
    for r in tables.r_aufk(w):
        if r["AUTYP"] != "30":
            continue
        h = afih[r["AUFNR"]]
        status = st.get(r["OBJNR"], "")
        if sel.get("EQUNR") and unalpha(h["EQUNR"]) != unalpha(sel["EQUNR"]):
            continue
        if sel.get("APERTI") == "X" and "TECO" in status:
            continue
        rows.append({**r, **h, "GSTRP": afko[r["AUFNR"]]["GSTRP"], "EQKTX": eqkt.get(h["EQUNR"], ""), "STATUS": status,
                     "ARBPL": tables.wc_name(w, h["GEWRK"]), "_key": r["AUFNR"]})
    rows.sort(key=lambda r: r["AUFNR"], reverse=True)
    return {"columns": [col("AUFNR", "Ordine", "K"), col("AUART", "Tipo"), col("KTEXT", "Descrizione"), col("EQUNR", "Apparecchiatura", "K"),
                        col("EQKTX", "Denominazione"), col("ARBPL", "CdL resp."), col("PRIOK", "P"), col("GSTRP", "Inizio cardine", "D"),
                        col("QMNUM", "Avviso", "K"), col("STATUS", "Stato sistema"), col("ERNAM", "Creato da"), col("AENAM", "Modificato da"),
                        col("ZZ_ATHERYA_REF", "Rif. Atherya")],
            "rows": rows, "drill": "IW33"}


def coois(w, sel):
    st = _jest_index(w)
    afko = {r["AUFNR"]: r for r in tables.r_afko(w)}
    ops = {}
    for (num, vornr), op in w.t("prodop").items():
        ops[alpha(num, 12)] = op["WorkCenter"]
    rows = []
    for r in tables.r_aufk(w):
        if r["AUTYP"] != "10":
            continue
        k = afko[r["AUFNR"]]
        status = st.get(r["OBJNR"], "")
        if sel.get("MATNR") and k["PLNBEZ"] != sel["MATNR"].upper():
            continue
        if sel.get("ARBPL") and ops.get(r["AUFNR"]) != sel["ARBPL"].upper():
            continue
        if sel.get("APERTI") == "X" and "TECO" in status:
            continue
        rows.append({**r, **k, "ARBPL": ops.get(r["AUFNR"], ""), "STATUS": status, "_key": r["AUFNR"]})
    rows.sort(key=lambda r: (r["GSTRS"] or "99999999", r["GSUZS"]))
    return {"columns": [col("AUFNR", "Ordine", "K"), col("AUART", "Tipo"), col("PLNBEZ", "Materiale"), col("GAMNG", "Qtà totale", "P"),
                        col("IGMNG", "Qtà confermata", "P"), col("GMEIN", "UM"), col("ARBPL", "Centro di lavoro"),
                        col("GSTRS", "Inizio schedulato", "D"), col("GSUZS", "Ora", "T"), col("GLTRS", "Fine schedulata", "D"),
                        col("GLTRP", "Fine cardine", "D"), col("STATUS", "Stato sistema"), col("AENAM", "Modificato da")],
            "rows": rows, "drill": "CO03"}


def md16(w, sel):
    makt = _texts(w, "MAKT", "MATNR", "MAKTX")
    rows = [{**r, "MAKTX": makt.get(r["MATNR"], ""), "_key": r["PLNUM"]} for r in tables.r_plaf(w)
            if not sel.get("MATNR") or r["MATNR"] == sel["MATNR"].upper()]
    rows.sort(key=lambda r: r["PSTTR"])
    return {"columns": [col("PLNUM", "Ordine pian.", "K"), col("MATNR", "Materiale"), col("MAKTX", "Descrizione"), col("GSMNG", "Quantità", "P"),
                        col("MEINS", "UM"), col("PSTTR", "Inizio", "D"), col("PEDTR", "Fine", "D"), col("PAART", "Tipo"), col("AUFFX", "Fissato")],
            "rows": rows, "actions": [{"id": "CO40", "label": "Converti in ordine di produzione (CO40)"}]}


def md04(w, sel):
    mat = (sel.get("MATNR") or "").upper().strip()
    if not mat:
        raise BusinessError("M3/001", "Inserire un materiale")
    s = rfc.open_session(w, "PLANNER", "GUI")
    try:
        out = rfc.call(w, s, "BAPI_MATERIAL_STOCK_REQ_LIST", {"MATERIAL": mat, "PLANT": sel.get("WERKS") or "1000"})
    finally:
        rfc.close_session(w, s.id)
    if rfc.has_error(out["RETURN"]):
        raise BusinessError("M3/305", out["RETURN"]["MESSAGE"])
    names = {"WB": "Gia.div.", "PP": "FabInd", "PA": "OrdPia", "SB": "FabSec", "FE": "OrdPro", "AR": "PrnOrd", "MR": "Prenot", "BA": "RdA"}
    rows = [{**r, "ELEMENT": names.get(r["MRP_ELEMENT_IND"], r["MRP_ELEMENT_IND"]),
             "RECEIPT": r["REC_REQD_QTY"] if r["REC_REQD_QTY"] > 0 else "", "REQ": -r["REC_REQD_QTY"] if r["REC_REQD_QTY"] < 0 else "",
             "_neg": r["AVAIL_QTY"] < 0} for r in out["MRP_ITEMS"]]
    head = out["MRP_LIST"]
    return {"title_suffix": f"{mat} · divisione {head['PLANT']} · tipo MRP {head['MRP_TYPE']} · scorta di sicurezza {fnum(head['SAFETY_STOCK'])}",
            "columns": [col("AVAIL_DATE", "Data", "D"), col("ELEMENT", "Elem. MRP"), col("ELEMNT_DATA", "Dati elemento MRP"),
                        col("RECEIPT", "Entrata/Fabbisogno +", "P"), col("REQ", "Fabbisogno −", "P"), col("AVAIL_QTY", "Quantità disponibile", "P")],
            "rows": rows}


def cm01(w, sel):
    rows = []
    for r in pp.capacity_rows(w):
        if sel.get("LINEA") and r["Line"] != sel["LINEA"].upper():
            continue
        rows.append({"ARBPL": r["WorkCenter"], "KTEXT": r["WorkCenterDesc"], "LINE": r["Line"],
                     **{f"D{d}": r[f"Day{d}"] for d in range(5)}, "_key": r["WorkCenter"]})
    days = [(w.today + dt.timedelta(days=d)) for d in range(5)]
    return {"columns": [col("ARBPL", "Centro di lavoro", "K"), col("KTEXT", "Descrizione"), col("LINE", "Linea")] +
                       [col(f"D{d}", days[d].strftime("%a %d.%m"), "%") for d in range(5)], "rows": rows}


def table_view(table, columns, key=None, where=None, sort=None, reverse=False, limit=None, drill=None, actions=None, extra=None):
    def view(w, sel):
        rows = tables.rows_of(w, table)
        if extra:
            rows = [extra(w, r) for r in rows]
        if where:
            rows = [r for r in rows if where(r, sel)]
        if sort:
            rows.sort(key=lambda r: tuple(r.get(s) or "" for s in sort), reverse=reverse)
        if limit:
            rows = rows[:limit]
        for r in rows:
            r["_key"] = "|".join(str(r[k]) for k in key) if key else ""
        return {"columns": columns, "rows": rows, "drill": drill, "actions": actions or []}
    return view


def _matdesc(w, r):
    if "_makt" not in w.__dict__ or w.__dict__["_makt_at"] != len(w.t("product")):
        w.__dict__["_makt"] = _texts(w, "MAKT", "MATNR", "MAKTX")
        w.__dict__["_makt_at"] = len(w.t("product"))
    return {**r, "MAKTX": w.__dict__["_makt"].get(r.get("MATNR"), "")}


def _sel_eq(field, sel_field=None):
    sel_field = sel_field or field

    def f(r, sel):
        v = (sel.get(sel_field) or "").strip().upper()
        return not v or unalpha(r.get(field)) == unalpha(v) or str(r.get(field)) == v
    return f


def _and(*fs):
    return lambda r, sel: all(f(r, sel) for f in fs)


def sm12(w, sel):
    rows = []
    for (obj, key), l in sorted(w.locks.items(), key=lambda kv: str(kv[1].get("since"))):
        rows.append({"MANDT": conv.CLIENT, "UNAME": conv.user(l["user"]), "TIME": l["since"].strftime("%H:%M:%S") if l.get("since") else "",
                     "SHR": "E", "GNAME": {"ORDER": "AUFK", "QMEL": "QMEL", "RKPF": "RKPF", "EBAN": "EBAN"}.get(obj, obj),
                     "GARG": f"{conv.CLIENT}{key}", "TCODE": l.get("tcode", ""),
                     "UNTIL": l["until"].strftime("%H:%M") if l.get("until") else "fino al commit", "_key": f"{obj}|{key}"})
    return {"columns": [col("MANDT", "Mand."), col("UNAME", "Utente"), col("TIME", "Ora"), col("SHR", "Modo"), col("GNAME", "Tabella"),
                        col("GARG", "Argomento di blocco"), col("TCODE", "Transazione"), col("UNTIL", "Rilascio previsto")], "rows": rows}


def sm04(w, sel):
    import time
    rows = []
    for s in rfc.sessions(w).values():
        rows.append({"UNAME": conv.user(s.user), "TYPE": s.kind, "OPENED": s.opened.strftime("%d.%m.%Y %H:%M"),
                     "CALLS": s.calls, "LAST_FM": s.last_fm, "IDLE": f"{int(time.time() - s.last_call)} s",
                     "LUW": "Modifiche non confermate" if s.dirty else "", "_key": s.id})
    return {"columns": [col("UNAME", "Utente"), col("TYPE", "Tipo"), col("OPENED", "Aperta (ora fabbrica)"), col("CALLS", "Chiamate", "P"),
                        col("LAST_FM", "Ultimo modulo funzione"), col("IDLE", "Inattiva da"), col("LUW", "Unità logica di lavoro")], "rows": rows}


def se16n(w, sel):
    name = (sel.get("TABLE") or "").upper().strip()
    if name not in tables.TABLES:
        raise BusinessError("DB/001", f"Tabella {name or '(vuota)'} non presente nel dizionario della simulazione")
    fields = tables.fields_of(name)
    rows = tables.rows_of(w, name)
    cond = (sel.get("WHERE") or "").strip()
    if cond:
        where = rfc._Where(cond, {f.name: f for f in fields})
        rows = [r for r in rows if where.match(r)]
    limit = int(sel.get("MAXROWS") or 500)
    total = len(rows)
    kinds = {"D": "D", "T": "T", "P": "P", "F": "P"}
    return {"title_suffix": f"{name} · {tables.TABLES[name][0]} · {total} voci trovate",
            "columns": [col(f.name, f"{f.name}\n{f.text}", kinds.get(f.type, "C")) for f in fields], "rows": rows[:limit]}


def cogi(w, sel):
    rows = [{"AUFNR": alpha(c["ManufacturingOrder"], 12), "MATNR": c["Material"], "ERFMG": c["Quantity"], "MSG": c["Message"],
             "DATUM": conv.d8(c["CreatedOn"]), "_key": "|".join(k)} for k, c in w.t("cogi").items()]
    return {"columns": [col("AUFNR", "Ordine", "K"), col("MATNR", "Materiale"), col("ERFMG", "Quantità", "P"),
                        col("MSG", "Messaggio d'errore"), col("DATUM", "Data", "D")], "rows": rows}


# ---------------------------------------------------------------- dettagli

def iw23(w, key):
    num = unalpha(key)
    s = rfc.open_session(w, "PLANNER", "GUI")
    try:
        d = rfc.call(w, s, "BAPI_ALM_NOTIF_GET_DETAIL", {"NUMBER": alpha(num, 12)})
    finally:
        rfc.close_session(w, s.id)
    if rfc.has_error(d["RETURN"]):
        raise BusinessError("IM/404", d["RETURN"][0]["MESSAGE"])
    h = d["NOTIFHEADER_EXPORT"]
    eq = w.t("equipment").get((unalpha(h["EQUIPMENT"]),), {})
    n = w.get("notif", (num,))
    status = d["SYSTEMSTATUS"]
    return {"title": f"Visualizzare avviso {num}: {h['NOTIF_TYPE']} · {h['SHORT_TEXT']}", "key": h["NOTIF_NO"], "status": status,
            "sections": [{"title": "Avviso", "fields": [["Avviso", h["NOTIF_NO"]], ["Tipo avviso", h["NOTIF_TYPE"]], ["Testo breve", h["SHORT_TEXT"]],
                                                         ["Stato sistema", status], ["Ordine", h["ORDERID"]], ["Rif. Atherya (ZZ)", n.get("YY1_AtheryaRef") or ""]]},
                         {"title": "Oggetto di riferimento", "fields": [["Apparecchiatura", h["EQUIPMENT"]], ["Denominazione", eq.get("EquipmentName", "")],
                                                                        ["Sede tecnica", eq.get("FunctionalLocation", "")], ["Div. pianificazione", h["PLANPLANT"]]]},
                         {"title": "Responsabilità e date", "fields": [["Priorità", h["PRIORITY"]], ["Guasto", h["BREAKDOWN"]],
                                                                       ["Data avviso", fdate(h["NOTIF_DATE"])], ["Ora", ftime(h["NOTIFTIME"])],
                                                                       ["Inizio malfunzionamento", fdate(h["STRMLFNDATE"])], ["Creato da", h["CREATED_BY"]],
                                                                       ["Completato il", fdate(h["COMPLDATE"])]]}],
            "text": "\n".join(line["TEXT_LINE"] for line in d["NOTLONGTXT"]),
            "actions": [{"id": "IW22_CLOSE", "label": "Completare avviso", "enabled": "NOCO" not in status},
                        {"id": "IW22_ORDER", "label": "Creare ordine", "enabled": not h["ORDERID"] and "NOCO" not in status}]}


def iw33(w, key):
    num = unalpha(key)
    s = rfc.open_session(w, "PLANNER", "GUI")
    try:
        d = rfc.call(w, s, "BAPI_ALM_ORDER_GET_DETAIL", {"NUMBER": alpha(num, 12)})
    finally:
        rfc.close_session(w, s.id)
    if rfc.has_error(d["RETURN"]):
        raise BusinessError("IW/404", d["RETURN"][0]["MESSAGE"])
    h = d["ES_HEADER"]
    o = w.get("morder", (num,))
    eq = w.t("equipment").get((unalpha(h["EQUIPMENT"]),), {})
    status = h["SYS_STATUS"]
    confs = [{"RUECK": r["RUECK"], "VORNR": r["VORNR"], "ISMNW": r["ISMNW"], "BUDAT": r["BUDAT"], "ERNAM": r["ERNAM"], "LTXA1": r["LTXA1"]}
             for r in tables.r_afru(w) if r["AUFNR"] == alpha(num, 12)]
    lock = w.locks.get(("ORDER", alpha(num, 12)))
    return {"title": f"Visualizzare ordine {h['ORDER_TYPE']} {num}: dati testata", "key": h["ORDERID"], "status": status,
            "lock": f"Ordine bloccato da {conv.user(lock['user'])} ({lock.get('tcode', '')})" if lock else "",
            "sections": [{"title": "Testata", "fields": [["Ordine", h["ORDERID"]], ["Tipo", h["ORDER_TYPE"]], ["Testo breve", h["SHORT_TEXT"]],
                                                         ["Stato sistema", status], ["Avviso", h["NOTIF_NO"]], ["Piano di manutenzione", h["MAINTPLAN"]],
                                                         ["Rif. Atherya (ZZ)", o.get("YY1_AtheryaRef") or ""]]},
                         {"title": "Responsabilità", "fields": [["Div. pianificazione", h["PLANPLANT"]], ["CdL responsabile", h["MN_WK_CTR"]],
                                                                ["Priorità", h["PRIORITY"]], ["Creato da", h["ENTERED_BY"]], ["Creato il", fdate(h["ENTER_DATE"])],
                                                                ["Modificato da", h["CHANGED_BY"]]]},
                         {"title": "Date cardine", "fields": [["Inizio cardine", fdate(h["START_DATE"])], ["Fine cardine", fdate(h["FINISH_DATE"])]]},
                         {"title": "Oggetto di riferimento", "fields": [["Apparecchiatura", h["EQUIPMENT"]], ["Denominazione", eq.get("EquipmentName", "")],
                                                                        ["Sede tecnica", eq.get("FunctionalLocation", "")]]}],
            "tabs": [{"title": "Operazioni", "columns": [col("ACTIVITY", "Op."), col("CONTROL_KEY", "Ch.ctr"), col("WORK_CNTR", "CdL"),
                                                          col("DESCRIPTION", "Testo breve operazione"), col("WORK_ACTIVITY", "Lavoro", "P"),
                                                          col("UN_WORK", "UM"), col("ACT_WORK", "Lavoro effettivo", "P")], "rows": d["ET_OPERATIONS"]},
                     {"title": "Componenti", "columns": [col("RES_ITEM", "Pos."), col("MATERIAL", "Componente"), col("REQUIREMENT_QUANTITY", "Qtà fabbisogno", "P"),
                                                          col("REQUIREMENT_QUANTITY_UNIT", "UM"), col("WITHDRAWN_QUANTITY", "Qtà prelevata", "P"),
                                                          col("RESERV_NO", "Prenotazione")], "rows": d["ET_COMPONENTS"]},
                     {"title": "Conferme", "columns": [col("RUECK", "Conferma"), col("VORNR", "Op."), col("ISMNW", "Lavoro effettivo", "P"),
                                                        col("BUDAT", "Data reg.", "D"), col("ERNAM", "Utente"), col("LTXA1", "Testo")], "rows": confs}],
            "actions": [{"id": "IW32_REL", "label": "Rilasciare", "enabled": status.startswith("CRTD") and "LKD" not in status},
                        {"id": "IW32_TECO", "label": "Chiusura tecnica", "enabled": status.startswith("REL")},
                        {"id": "IW32_LOCK", "label": "Bloccare", "enabled": "LKD" not in status and "TECO" not in status},
                        {"id": "IW32_UNLOCK", "label": "Sbloccare", "enabled": "LKD" in status}]}


def co03(w, key):
    num = unalpha(key)
    s = rfc.open_session(w, "PLANNER", "GUI")
    try:
        d = rfc.call(w, s, "BAPI_PRODORD_GET_DETAIL", {"NUMBER": alpha(num, 12),
                                                       "ORDER_OBJECTS": {"HEADER": "X", "OPERATIONS": "X", "COMPONENTS": "X"}})
    finally:
        rfc.close_session(w, s.id)
    if rfc.has_error(d["RETURN"]):
        raise BusinessError("CO/404", d["RETURN"]["MESSAGE"])
    h = d["HEADER"][0]
    confs = [{"RUECK": r["RUECK"], "VORNR": r["VORNR"], "LMNGA": r["LMNGA"], "XMNGA": r["XMNGA"], "BUDAT": r["BUDAT"], "ERNAM": r["ERNAM"],
              "AUERU": r["AUERU"]} for r in tables.r_afru(w) if r["AUFNR"] == alpha(num, 12)]
    mat = w.get("prodorder", (num,))["Material"]
    lock = w.locks.get(("ORDER", alpha(num, 12)))
    return {"title": f"Visualizzare ordine di produzione {num}: testata", "key": h["ORDER_NUMBER"], "status": h["SYSTEM_STATUS"],
            "lock": f"Ordine bloccato da {conv.user(lock['user'])} ({lock.get('tcode', '')})" if lock else "",
            "sections": [{"title": "Ordine", "fields": [["Ordine", h["ORDER_NUMBER"]], ["Tipo", h["ORDER_TYPE"]], ["Materiale", h["MATERIAL"]],
                                                        ["Divisione", h["PRODUCTION_PLANT"]], ["Stato sistema", h["SYSTEM_STATUS"]]]},
                         {"title": "Quantità", "fields": [["Quantità totale", fnum(h["TARGET_QUANTITY"]) + " ST"],
                                                          ["Quantità buona confermata", fnum(h["CONFIRMED_QUANTITY"])], ["Scarto", fnum(h["SCRAP"])]]},
                         {"title": "Date", "fields": [["Inizio cardine", fdate(h["BASIC_START_DATE"])], ["Fine cardine", fdate(h["BASIC_END_DATE"])],
                                                      ["Rilasciato il", fdate(h["ACTUAL_RELEASE_DATE"])], ["Responsabile MRP", h["MRP_CONTROLLER"]]]}],
            "tabs": [{"title": "Operazioni", "columns": [col("OPERATION_NUMBER", "Op."), col("WORK_CENTER", "CdL"), col("DESCRIPTION", "Testo breve operazione"),
                                                          col("QUANTITY", "Qtà", "P"), col("CONF_QUANTITY", "Confermata", "P"),
                                                          col("EARL_SCHED_START_DATE_EXEC", "Inizio esec.", "D"), col("EARL_SCHED_START_TIME_EXEC", "Ora", "T"),
                                                          col("EARL_SCHED_FIN_DATE_EXEC", "Fine esec.", "D"), col("EARL_SCHED_FIN_TIME_EXEC", "Ora", "T"),
                                                          col("SYSTEM_STATUS", "Stato")], "rows": d["OPERATION"]},
                     {"title": "Componenti", "columns": [col("RESERVATION_ITEM", "Pos."), col("MATERIAL", "Componente"), col("REQ_QUAN", "Qtà fabbisogno", "P"),
                                                          col("WITHDRAWN_QUANTITY", "Qtà prelevata", "P"), col("BASE_UOM", "UM"), col("REQ_DATE", "Data fabb.", "D"),
                                                          col("STORAGE_LOCATION", "Mag.")], "rows": d["COMPONENT"]},
                     {"title": "Conferme", "columns": [col("RUECK", "Conferma"), col("VORNR", "Op."), col("LMNGA", "Qtà buona", "P"), col("XMNGA", "Scarto", "P"),
                                                        col("BUDAT", "Data reg.", "D"), col("ERNAM", "Utente"), col("AUERU", "Finale")], "rows": confs}],
            "actions": [{"id": "CO02_REL", "label": "Rilasciare", "enabled": h["SYSTEM_STATUS"].startswith("CRTD")},
                        {"id": "CO02_WC", "label": "Cambiare centro di lavoro", "enabled": "TECO" not in h["SYSTEM_STATUS"],
                         "choices": pp.eligible_workcenters(w, mat), "current": d["OPERATION"][0]["WORK_CENTER"] if d["OPERATION"] else ""}]}


# ---------------------------------------------------------------- catalogo delle transazioni

S_EQ = [{"name": "EQUNR", "label": "Apparecchiatura"}, {"name": "APERTI", "label": "Solo documenti aperti", "type": "check", "default": "X"}]

TX = {
    "IW21": {"area": "Manutenzione", "title": "Creare avviso PM", "kind": "form",
             "fields": [{"name": "QMART", "label": "Tipo avviso", "default": "M2", "choices": ["M1", "M2", "M3"]},
                        {"name": "EQUNR", "label": "Apparecchiatura", "default": "10000007"},
                        {"name": "QMTXT", "label": "Testo breve", "default": ""},
                        {"name": "PRIOK", "label": "Priorità", "default": "2", "choices": ["1", "2", "3", "4"]},
                        {"name": "MSAUS", "label": "Guasto", "type": "check"},
                        {"name": "LTXT", "label": "Testo esteso", "type": "text"}]},
    "IW28": {"area": "Manutenzione", "title": "Modificare avvisi: elenco", "kind": "alv", "sel": S_EQ, "view": iw28},
    "IW23": {"area": "Manutenzione", "title": "Visualizzare avviso", "kind": "detail", "keylabel": "Avviso", "detail": iw23},
    "IW38": {"area": "Manutenzione", "title": "Modificare ordini PM: elenco", "kind": "alv", "sel": S_EQ, "view": iw38},
    "IW33": {"area": "Manutenzione", "title": "Visualizzare ordine PM", "kind": "detail", "keylabel": "Ordine", "detail": iw33},
    "IK17": {"area": "Manutenzione", "title": "Visualizzare documenti di misura", "kind": "alv",
             "sel": [{"name": "POINT", "label": "Punto di misura", "default": "072"}],
             "view": table_view("IMRG", [col("MDOCM", "Documento", "K"), col("POINT", "Punto misura", "K"), col("IDATE", "Data", "D"),
                                         col("ITIME", "Ora", "T"), col("READC", "Valore misurato"), col("RECDU", "UM"), col("ERNAM", "Utente")],
                                key=["MDOCM"], where=_sel_eq("POINT"), sort=["MDOCM"], reverse=True, limit=500)},
    "IH08": {"area": "Manutenzione", "title": "Visualizzare apparecchiature", "kind": "alv",
             "view": table_view("V_EQUI", [col("EQUNR", "Apparecchiatura", "K"), col("EQKTX", "Denominazione"), col("TPLNR", "Sede tecnica"),
                                           col("IWERK", "DivP"), col("GEWRK", "CdL resp. (ID)"), col("OBJNR", "Numero oggetto")],
                                key=["EQUNR"])},
    "IP16": {"area": "Manutenzione", "title": "Piani di manutenzione: elenco", "kind": "alv",
             "view": table_view("MHIS", [col("WARPL", "Piano", "K"), col("WPTXT", "Testo"), col("EQUNR", "Apparecchiatura", "K"), col("AUART", "Tipo ord."),
                                         col("ZYKZT", "Ciclo (gg)"), col("NPLDA", "Prossima data", "D"), col("LRMDT", "Ultima chiamata", "D"),
                                         col("AUFNR", "Ultimo ordine", "K")], key=["WARPL"],
                                extra=lambda w, r: {**r, **next(x for x in tables.r_mpos(w) if x["WARPL"] == r["WARPL"]),
                                                    "WPTXT": next(x["WPTXT"] for x in tables.r_mpla(w) if x["WARPL"] == r["WARPL"])},
                                sort=["NPLDA"])},
    "COOIS": {"area": "Produzione", "title": "Sistema informativo ordini di produzione", "kind": "alv",
              "sel": [{"name": "MATNR", "label": "Materiale"}, {"name": "ARBPL", "label": "Centro di lavoro"},
                      {"name": "APERTI", "label": "Solo ordini aperti", "type": "check", "default": "X"}], "view": coois},
    "CO03": {"area": "Produzione", "title": "Visualizzare ordine di produzione", "kind": "detail", "keylabel": "Ordine", "detail": co03},
    "MD16": {"area": "Produzione", "title": "Ordini pianificati: visualizzazione collettiva", "kind": "alv",
             "sel": [{"name": "MATNR", "label": "Materiale"}], "view": md16},
    "MD04": {"area": "Produzione", "title": "Lista fabbisogni/stock", "kind": "alv",
             "sel": [{"name": "MATNR", "label": "Materiale", "default": "EPDM-70"}, {"name": "WERKS", "label": "Divisione", "default": "1000"}],
             "view": md04},
    "CM01": {"area": "Produzione", "title": "Carico capacità: presse", "kind": "alv",
             "sel": [{"name": "LINEA", "label": "Linea (A, B, C)"}], "view": cm01},
    "COGI": {"area": "Produzione", "title": "Movimenti merci errati", "kind": "alv", "view": cogi},
    "MMBE": {"area": "Magazzino", "title": "Panoramica giacenze", "kind": "alv", "sel": [{"name": "MATNR", "label": "Materiale", "default": "GUARN-HYD-250"}],
             "view": table_view("LQUA", [col("MATNR", "Materiale"), col("MAKTX", "Descrizione"), col("LGNUM", "NMa"), col("LGTYP", "TMa"),
                                         col("LGPLA", "Ubicazione"), col("GESME", "Giac. totale", "P"), col("VERME", "Disponibile", "P"), col("MEINS", "UM")],
                                key=["LGPLA", "MATNR"], where=_sel_eq("MATNR"), extra=_matdesc, sort=["LGTYP", "LGPLA"])},
    "MB52": {"area": "Magazzino", "title": "Visualizzare giacenze di magazzino", "kind": "alv",
             "view": table_view("MARD", [col("MATNR", "Materiale"), col("MAKTX", "Descrizione"), col("WERKS", "Div."), col("LGORT", "Mag."),
                                         col("LABST", "Libera utilizzazione", "P"), col("INSME", "Contr. qualità", "P"), col("SPEME", "Bloccata", "P")],
                                key=["MATNR"], extra=_matdesc)},
    "MB51": {"area": "Magazzino", "title": "Elenco documenti materiale", "kind": "alv",
             "sel": [{"name": "MATNR", "label": "Materiale"}, {"name": "BWART", "label": "Tipo movimento"}],
             "view": table_view("MSEG", [col("MBLNR", "Doc. mat.", "K"), col("ZEILE", "Pos."), col("BWART", "TM"), col("MATNR", "Materiale"),
                                         col("MAKTX", "Descrizione"), col("MENGE", "Quantità", "P"), col("MEINS", "UM"), col("SHKZG", "D/A"),
                                         col("AUFNR", "Ordine", "K"), col("RSNUM", "Prenotazione", "K"), col("LGPLA", "Ubicazione")],
                                key=["MBLNR", "ZEILE"], where=_and(_sel_eq("MATNR"), _sel_eq("BWART")), extra=_matdesc,
                                sort=["MBLNR"], reverse=True, limit=500)},
    "MB25": {"area": "Magazzino", "title": "Prenotazioni: elenco", "kind": "alv",
             "sel": [{"name": "MATNR", "label": "Materiale"}],
             "view": table_view("RESB", [col("RSNUM", "Prenotazione", "K"), col("RSPOS", "Pos."), col("MATNR", "Materiale"), col("MAKTX", "Descrizione"),
                                         col("BDMNG", "Qtà fabbisogno", "P"), col("ENMNG", "Qtà prelevata", "P"), col("MEINS", "UM"),
                                         col("BDTER", "Data fabb.", "D"), col("BWART", "TM"), col("AUFNR", "Ordine", "K"), col("KZEAR", "Fin."),
                                         col("XLOEK", "Canc.")], key=["RSNUM", "RSPOS"], where=_sel_eq("MATNR"), extra=_matdesc,
                                sort=["BDTER"])},
    "ME5A": {"area": "Magazzino", "title": "Richieste d'acquisto: elenco", "kind": "alv",
             "sel": [{"name": "MATNR", "label": "Materiale"}],
             "view": table_view("EBAN", [col("BANFN", "Rich. acquisto", "K"), col("BNFPO", "Pos."), col("BSART", "Tp"), col("MATNR", "Materiale"),
                                         col("MAKTX", "Descrizione"), col("MENGE", "Quantità", "P"), col("MEINS", "UM"), col("LFDAT", "Data consegna", "D"),
                                         col("STATU", "St"), col("EBAKZ", "Chiusa"), col("LOEKZ", "Canc."), col("ESTKZ", "Orig."),
                                         col("ERNAM", "Creato da"), col("ZZ_ATHERYA_REF", "Rif. Atherya")],
                                key=["BANFN", "BNFPO"], where=_sel_eq("MATNR"), extra=_matdesc, sort=["BANFN"], reverse=True, limit=500)},
    "LX02": {"area": "Magazzino", "title": "Elenco giacenze WM (quanti)", "kind": "alv",
             "sel": [{"name": "LGTYP", "label": "Tipo magazzino"}],
             "view": table_view("LQUA", [col("LGNUM", "NMa"), col("LGTYP", "TMa"), col("LGPLA", "Ubicazione"), col("LQNUM", "Quanto"),
                                         col("MATNR", "Materiale"), col("MAKTX", "Descrizione"), col("GESME", "Giac. totale", "P"),
                                         col("VERME", "Disponibile", "P"), col("MEINS", "UM"), col("WDATU", "Data EM", "D")],
                                key=["LGPLA", "MATNR"], where=_sel_eq("LGTYP"), extra=_matdesc, sort=["LGTYP", "LGPLA"])},
    "LT23": {"area": "Magazzino", "title": "Ordini di trasferimento: elenco", "kind": "alv",
             "sel": [{"name": "APERTI", "label": "Solo non confermati", "type": "check", "default": "X"}],
             "view": table_view("LTAP", [col("TANUM", "OT", "K"), col("BWLVS", "TM WM"), col("MATNR", "Materiale"), col("VSOLM", "Qtà", "P"),
                                         col("MEINS", "UM"), col("VLTYP", "TMa orig."), col("VLPLA", "Ubic. origine"), col("NLTYP", "TMa dest."),
                                         col("NLPLA", "Ubic. destinazione"), col("BENUM", "Riferimento", "K"), col("BDATU", "Creato il", "D"),
                                         col("BZEIT", "Ora", "T"), col("PQUIT", "Conf."), col("QNAME", "Confermato da")],
                                key=["TANUM"], where=lambda r, sel: sel.get("APERTI") != "X" or r["PQUIT"] != "X",
                                extra=lambda w, r: {**r, **next(x for x in tables.r_ltak(w) if x["TANUM"] == r["TANUM"])},
                                sort=["TANUM"], reverse=True, limit=500,
                                actions=[{"id": "LT12", "label": "Confermare OT (LT12)"}])},
    "SE16N": {"area": "Sistema", "title": "Visualizzazione generale tabelle", "kind": "alv",
              "sel": [{"name": "TABLE", "label": "Tabella", "default": "AUFK", "choices": sorted(tables.TABLES)},
                      {"name": "WHERE", "label": "Condizione (Open SQL)", "default": ""},
                      {"name": "MAXROWS", "label": "Numero max. risultati", "default": "500"}], "view": se16n},
    "SM12": {"area": "Sistema", "title": "Voci di blocco", "kind": "alv", "view": sm12},
    "SM04": {"area": "Sistema", "title": "Sessioni utente e RFC", "kind": "alv", "view": sm04},
}
DETAIL_FOR = {"IW23": "IW23", "IW33": "IW33", "CO03": "CO03"}
ALIASES = {"IW22": "IW23", "IW32": "IW33", "CO02": "CO03", "IW29": "IW28", "IW39": "IW38", "CO40": "MD16", "LT12": "LT23", "SE16": "SE16N",
           "MB5B": "MB52", "SM50": "SM04"}


def catalog() -> list[dict]:
    return [{"tcode": k, "area": v["area"], "title": v["title"], "kind": v["kind"], "sel": v.get("sel", []), "fields": v.get("fields", []),
             "keylabel": v.get("keylabel", "")} for k, v in TX.items()]


def resolve(tcode: str) -> str:
    t = tcode.upper().lstrip("/").removeprefix("N").removeprefix("O") if tcode.upper().startswith(("/N", "/O")) else tcode.upper()
    return ALIASES.get(t, t)


def run(w, tcode: str, sel: dict) -> dict:
    t = resolve(tcode)
    if t not in TX:
        raise BusinessError("00/343", f"La transazione {tcode.upper()} non esiste")
    tx = TX[t]
    if tx["kind"] == "detail":
        key = (sel.get("KEY") or "").strip()
        if not key:
            raise BusinessError("00/055", f"Inserire {tx['keylabel'].lower()}")
        out = tx["detail"](w, key)
    elif tx["kind"] == "alv":
        out = tx["view"](w, sel)
    else:
        out = {}
    out.update({"tcode": t, "title": out.get("title") or tx["title"], "kind": tx["kind"]})
    return out


# ---------------------------------------------------------------- azioni (come le farebbe un utente in GUI)

def _gui_call(w, fm, params, commit=True):
    s = rfc.open_session(w, "PLANNER", "GUI")
    try:
        out = rfc.call(w, s, fm, params)
        msgs = []
        for name in ("RETURN", "ET_RETURN", "DETAIL_RETURN"):
            rows = out.get(name) or []
            msgs += [rows] if isinstance(rows, dict) else rows
        err = next((m for m in msgs if m.get("TYPE") in ("E", "A")), None)
        if err:
            rfc.rollback(w, s)
            raise BusinessError(f"{err.get('ID', '')}/{err.get('NUMBER', '')}", err["MESSAGE"])
        if commit:
            rfc.commit(w, s)
        return out
    finally:
        rfc.close_session(w, s.id)


def _maintain(w, key, method):
    _gui_call(w, "BAPI_ALM_ORDER_MAINTAIN", {"IT_METHODS": [{"REFNUMBER": "000001", "OBJECTTYPE": "HEADER", "METHOD": method, "OBJECTKEY": alpha(key, 12)},
                                                            {"METHOD": "SAVE"}]})


def action(w, p: dict) -> str:
    a, key, v = p.get("action"), str(p.get("key", "")), p.get("values") or {}
    if a == "IW21":
        s = rfc.open_session(w, "PLANNER", "GUI")  # creazione e salvataggio nella stessa sessione
        try:
            r = rfc.call(w, s, "BAPI_ALM_NOTIF_CREATE", {
                "NOTIF_TYPE": v.get("QMART", "M2"),
                "NOTIFHEADER": {"EQUIPMENT": alpha(v.get("EQUNR", ""), 18), "SHORT_TEXT": v.get("QMTXT", ""), "PRIORITY": v.get("PRIOK", "3"),
                                "BREAKDOWN": v.get("MSAUS", ""), "STRMLFNDATE": conv.d8(w.today), "STRMLFNTIME": conv.t6(w.now)},
                "LONGTEXTS": [{"TEXT_LINE": line} for line in str(v.get("LTXT", "")).splitlines()]})
            if rfc.has_error(r["RETURN"]):
                raise BusinessError("IM/000", r["RETURN"][0]["MESSAGE"])
            saved = rfc.call(w, s, "BAPI_ALM_NOTIF_SAVE", {"NUMBER": r["NOTIFHEADER_EXPORT"]["NOTIF_NO"]})
            if rfc.has_error(saved["RETURN"]):
                raise BusinessError("IM/000", saved["RETURN"][0]["MESSAGE"])
            rfc.commit(w, s)
        finally:
            rfc.close_session(w, s.id)
        return f"Avviso {unalpha(saved['NOTIFHEADER']['NOTIF_NO'])} memorizzato"
    if a == "IW22_CLOSE":
        _gui_call(w, "BAPI_ALM_NOTIF_CLOSE", {"NUMBER": alpha(key, 12), "SYSSTAT": {"REFDATE": conv.d8(w.today)}})
        return f"Avviso {unalpha(key)} completato"
    if a == "IW22_ORDER":
        n = w.get("notif", (unalpha(key),))
        r = _gui_call(w, "BAPI_ALM_ORDER_MAINTAIN", {
            "IT_METHODS": [{"REFNUMBER": "000001", "OBJECTTYPE": "HEADER", "METHOD": "CREATE", "OBJECTKEY": "%00000000001"}, {"METHOD": "SAVE"}],
            "IT_HEADER": [{"ORDER_TYPE": "PM01", "PLANPLANT": "1000", "MN_WK_CTR": "MAINT01", "EQUIPMENT": alpha(n["TechnicalObject"], 18),
                           "SHORT_TEXT": n["NotificationText"][:40], "START_DATE": conv.d8(w.today), "NOTIF_NO": alpha(key, 12),
                           "PRIORITY": n.get("MaintPriority", "3")}]})
        return f"Ordine {unalpha(r['ET_NUMBERS'][0]['AUFNR_NEW'])} memorizzato con avviso {unalpha(key)}"
    if a in ("IW32_REL", "IW32_TECO", "IW32_LOCK", "IW32_UNLOCK"):
        method = {"IW32_REL": "RELEASE", "IW32_TECO": "TECHNICALCOMPLETE", "IW32_LOCK": "LOCK", "IW32_UNLOCK": "UNLOCK"}[a]
        _maintain(w, key, method)
        return f"Ordine {unalpha(key)} memorizzato ({method.lower()})"
    if a == "CO02_REL":
        _gui_call(w, "BAPI_PRODORD_RELEASE", {"ORDERS": [{"ORDER_NUMBER": alpha(key, 12)}]})
        return f"Ordine {unalpha(key)} rilasciato"
    if a == "CO02_WC":
        _gui_call(w, "Z_ATHERYA_PRODORD_OPR_CHANGE", {"IV_AUFNR": alpha(key, 12), "IV_VORNR": "0010", "IV_ARBPL": v.get("ARBPL", "")})
        return f"Ordine {unalpha(key)}: operazione 0010 spostata su {v.get('ARBPL')}"
    if a == "CO40":
        r = _gui_call(w, "BAPI_PRODORD_CREATE_FROM_PLORD", {"PLANNED_ORDER": alpha(key, 10)})
        return f"Ordine di produzione {unalpha(r['PRODUCTION_ORDER'])} creato dall'ordine pianificato {unalpha(key)}"
    if a == "LT12":
        s = rfc.open_session(w, "PLANNER", "GUI")
        try:
            rfc.call(w, s, "L_TO_CONFIRM", {"I_LGNUM": conv.LGNUM, "I_TANUM": alpha(key, 10), "I_COMMIT_WORK": "X"})
        except rfc.ABAPException as e:
            raise BusinessError("L3/000", e.message)
        finally:
            rfc.close_session(w, s.id)
        return f"Ordine di trasferimento {unalpha(key)} confermato"
    raise BusinessError("00/001", "Funzione non prevista")
