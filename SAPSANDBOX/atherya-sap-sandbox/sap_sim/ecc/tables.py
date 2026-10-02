"""Tabelle del dizionario ECC, calcolate dal mondo simulato.

Ogni tabella ha i suoi campi (nome, tipo ABAP, lunghezza di uscita, testo) e un generatore di righe.
I valori sono già nel formato interno ECC: chiavi con zeri a sinistra, date AAAAMMGG, ore HHMMSS,
unità interne (ST per i pezzi). Sono le tabelle che un integratore legge con RFC_READ_TABLE e
che la GUI mostra in SE16N.

Nomi di tabelle e campi: quelli standard di ECC 6.0, ridotti ai campi utili. Alcune scelte sono
semplificate (vedi FIELD_MAP.md, sezione ECC).
"""

import datetime as dt
from dataclasses import dataclass

from .. import inventory as inv
from ..masterdata import PLANT, SLOC
from . import conv
from .conv import alpha, d8, t6, unit, user


@dataclass(frozen=True)
class F:
    name: str
    type: str      # C N D T P F
    length: int    # lunghezza di uscita in RFC_READ_TABLE
    text: str


def _c(name, length, text):
    return F(name, "C", length, text)


def _n(name, length, text):
    return F(name, "N", length, text)


def _d(name, text):
    return F(name, "D", 8, text)


def _t(name, text):
    return F(name, "T", 6, text)


def _p(name, text, length=17):
    return F(name, "P", length, text)


MANDT = _c("MANDT", 3, "Mandante")
MATNR = _c("MATNR", 18, "Materiale")
WERKS = _c("WERKS", 4, "Divisione")
LGORT = _c("LGORT", 4, "Magazzino")
MEINS = _c("MEINS", 3, "Unità di misura di base")
AUFNR = _c("AUFNR", 12, "Ordine")
EQUNR = _c("EQUNR", 18, "Apparecchiatura")
ERNAM = _c("ERNAM", 12, "Creato da")
ERDAT = _d("ERDAT", "Creato il")
ATHREF = _c("ZZ_ATHERYA_REF", 40, "Riferimento Atherya (campo cliente)")


# ---------------------------------------------------------------- cache di identificativi tecnici

def _cache(w) -> dict:
    return w.__dict__.setdefault("_ecc", {})


def wc_id(w, arbpl: str) -> str:
    """ID oggetto del centro di lavoro (CRHD-OBJID): numerico, diverso dal nome."""
    c = _cache(w).setdefault("wc", {})
    if not c:
        for i, k in enumerate(sorted(w.t("workcenter")), start=1):
            c[k[0]] = f"{10000000 + i:08d}"
    return c.get(arbpl, "00000000")


def wc_name(w, objid: str) -> str:
    wc_id(w, "")
    return next((k for k, v in _cache(w)["wc"].items() if v == objid), "")


def lqnum(w, bin_: str, mat: str) -> str:
    c = _cache(w).setdefault("lq", {})
    if (bin_, mat) not in c:
        c[(bin_, mat)] = f"{len(c) + 1:010d}"
    return c[(bin_, mat)]


def aufpl(aufnr: str) -> str:
    return f"{int(aufnr):010d}"


def rsnum_of_order(aufnr: str) -> str:
    return alpha(aufnr, 10)


def prod_unit(w, mat: str) -> str:
    p = w.t("product").get((mat,))
    return unit(p["BaseUnit"]) if p else ""


def _f(v) -> float:
    try:
        return round(float(v or 0), 3)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------- generatori di righe

def r_mara(w):
    return [{"MATNR": k[0], "MTART": p["ProductType"], "MATKL": "GOMMA" if p["ProductType"] in ("FERT", "ROH") else "RICAMBI",
             "MEINS": unit(p["BaseUnit"]), "ERSDA": d8(w.start), "ERNAM": user(p["_by"])}
            for k, p in sorted(w.t("product").items())]


def r_makt(w):
    return [{"MATNR": k[0], "SPRAS": "I", "MAKTX": p["ProductDescription"][:40]} for k, p in sorted(w.t("product").items())]


def r_marc(w):
    return [{"MATNR": k[0], "WERKS": p["Plant"], "DISPO": "001", "DISMM": p.get("MRPType", ""), "BESKZ": p.get("ProcurementType", ""),
             "PLIFZ": f"{int(p.get('PlannedDeliveryDurationInDays') or 0):03d}", "DZEIT": f"{int(p.get('InHouseProductionTime') or 0):03d}",
             "EISBE": _f(p.get("SafetyStockQuantity")), "BSTFE": _f(p.get("FixedLotSize")), "DISLS": p.get("LotSizingProcedure", "")}
            for k, p in sorted(w.t("product").items())]


def r_mard(w):
    totals = {}
    for (b, m), q in w.t("quant").items():
        if q["EWMStorageType"] != "9020":
            totals[m] = totals.get(m, 0) + q["EWMStockQuantityInBaseUnit"]
    return [{"MATNR": k[0], "WERKS": p["Plant"], "LGORT": SLOC, "LABST": round(totals.get(k[0], 0), 3), "INSME": 0.0, "SPEME": 0.0}
            for k, p in sorted(w.t("product").items()) if p["Plant"] == PLANT]


def r_t001w(w):
    return [{"WERKS": PLANT, "NAME1": "Stabilimento stampaggio gomma"}]


def r_t001l(w):
    return [{"WERKS": PLANT, "LGORT": SLOC, "LGOBE": "Magazzino centrale"}]


def r_crhd(w):
    return [{"OBJTY": "A", "OBJID": wc_id(w, k[0]), "ARBPL": k[0], "WERKS": wc["Plant"],
             "VERWE": "0001" if wc["WorkCenterCategoryCode"] == "0001" else "0005", "XSPRR": "X" if wc["_blocked"] else ""}
            for k, wc in sorted(w.t("workcenter").items())]


def r_crtx(w):
    return [{"OBJTY": "A", "OBJID": wc_id(w, k[0]), "SPRAS": "I", "KTEXT": wc["WorkCenterDesc"][:40]}
            for k, wc in sorted(w.t("workcenter").items())]


def r_equi(w):
    return [{"EQUNR": alpha(k[0], 18), "EQTYP": "M", "EQART": e.get("TechnicalObjectType", ""), "ERDAT": d8(w.start),
             "ERNAM": user(e["_by"]), "OBJNR": conv.objnr_equi(k[0])} for k, e in sorted(w.t("equipment").items())]


def r_eqkt(w):
    return [{"EQUNR": alpha(k[0], 18), "SPRAS": "I", "EQKTX": e["EquipmentName"][:40]} for k, e in sorted(w.t("equipment").items())]


def r_v_equi(w):
    return [{"EQUNR": alpha(k[0], 18), "DATBI": "99991231", "EQTYP": "M", "SPRAS": "I", "EQKTX": e["EquipmentName"][:40],
             "IWERK": e["MaintenancePlanningPlant"], "SWERK": e["MaintenancePlanningPlant"], "TPLNR": e["FunctionalLocation"],
             "GEWRK": wc_id(w, e["MainWorkCenter"]), "OBJNR": conv.objnr_equi(k[0])} for k, e in sorted(w.t("equipment").items())]


def r_iflot(w):
    return [{"TPLNR": k[0], "IWERK": PLANT, "TPLMA": f.get("SuperiorFunctionalLocation", "")} for k, f in sorted(w.t("funcloc").items())]


def r_iflotx(w):
    return [{"TPLNR": k[0], "SPRAS": "I", "PLTXT": f["FunctionalLocationName"][:40]} for k, f in sorted(w.t("funcloc").items())]


def r_imptt(w):
    return [{"POINT": alpha(k[0], 12), "MPOBJ": conv.objnr_equi(m["TechnicalObject"]), "PTTXT": m["MeasuringPointDescription"][:40],
             "MSEHI": m["MeasurementRangeUnit"][:3], "INDCT": "X" if m["MeasuringPointIsCounter"] else "",
             "MRMAX": _f(m.get("MeasurementRangeUpperLimit"))} for k, m in sorted(w.t("measpoint").items())]


def r_imrg(w):
    return [{"MDOCM": alpha(k[0], 20), "POINT": alpha(m["MeasuringPoint"], 12), "IDATE": d8(m["MeasurementReadingDate"]),
             "ITIME": t6(m["MeasurementReadingTime"]), "RECDV": float(m["MeasurementReading"]),
             "READC": f"{m['MeasurementReading']:g}", "RECDU": str(m.get("MeasurementReadingUnit", ""))[:3],
             "ERNAM": user(m["_by"]), "MDTXT": m.get("MeasurementDocumentText", "")[:40]}
            for k, m in w.t("measdoc").items()]


def _notif_rows(w):
    for k, n in sorted(w.t("notif").items()):
        yield k[0], n


def r_qmel(w):
    return [{"QMNUM": alpha(num, 12), "QMART": n["NotificationType"], "QMTXT": n["NotificationText"][:40], "ERNAM": user(n["_by"]),
             "ERDAT": d8(n["NotificationCreationDate"]), "MZEIT": t6(n["NotificationCreationTime"]), "QMDAT": d8(n["NotificationCreationDate"]),
             "PRIOK": n.get("MaintPriority", ""), "AUFNR": alpha(n.get("MaintenanceOrder"), 12), "OBJNR": conv.objnr_notif(num),
             "QMDAB": d8(n.get("NotificationCompletionDate")), "AENAM": user(n["_changed_by"]), "ZZ_ATHERYA_REF": n.get("YY1_AtheryaRef", "") or ""}
            for num, n in _notif_rows(w)]


def r_qmih(w):
    return [{"QMNUM": alpha(num, 12), "EQUNR": alpha(n["TechnicalObject"], 18), "IWERK": n["MaintenancePlanningPlant"],
             "MSAUS": "X" if n.get("IsBreakdown") else "", "AUSVN": d8(n.get("MalfunctionStartDate")), "AUZTV": t6(n.get("MalfunctionStartTime"))}
            for num, n in _notif_rows(w)]


def r_viqmel(w):
    qmih = {r["QMNUM"]: r for r in r_qmih(w)}
    return [{**r, **qmih[r["QMNUM"]]} for r in r_qmel(w)]


def _maint_orders(w):
    for k, o in sorted(w.t("morder").items()):
        yield k[0], o


def _prod_orders(w):
    for k, o in sorted(w.t("prodorder").items()):
        yield k[0], o


def r_aufk(w):
    rows = []
    for num, o in _maint_orders(w):
        st = conv.order_status(w, o, "PM")
        rows.append({"AUFNR": alpha(num, 12), "AUART": o["MaintenanceOrderType"], "AUTYP": "30", "KTEXT": o["MaintenanceOrderDesc"][:40],
                     "WERKS": o["MaintenancePlanningPlant"], "ERNAM": user(o["_by"]), "ERDAT": d8(o["_at"]), "AENAM": user(o["_changed_by"]),
                     "OBJNR": conv.objnr_order(num), "LOEKZ": "X" if o.get("IsDeleted") else "",
                     "PHAS0": "X" if st[0] == "CRTD" else "", "PHAS1": "X" if "REL" in st else "", "PHAS2": "X" if "TECO" in st else "",
                     "ZZ_ATHERYA_REF": o.get("YY1_AtheryaRef", "") or ""})
    for num, o in _prod_orders(w):
        st = conv.order_status(w, o, "PP")
        rows.append({"AUFNR": alpha(num, 12), "AUART": o["ManufacturingOrderType"], "AUTYP": "10", "KTEXT": o["Material"],
                     "WERKS": o["ProductionPlant"], "ERNAM": user(o["_by"]), "ERDAT": d8(o["_at"]), "AENAM": user(o["_changed_by"]),
                     "OBJNR": conv.objnr_order(num), "LOEKZ": "X" if o.get("IsDeleted") else "",
                     "PHAS0": "X" if st[0] == "CRTD" else "", "PHAS1": "X" if "REL" in st else "", "PHAS2": "X" if "TECO" in st else "",
                     "ZZ_ATHERYA_REF": ""})
    return rows


def r_afih(w):
    return [{"AUFNR": alpha(num, 12), "EQUNR": alpha(o["Equipment"], 18), "IWERK": o["MaintenancePlanningPlant"],
             "QMNUM": alpha(o.get("MaintenanceNotification"), 12), "PRIOK": o.get("MaintPriority", ""), "WARPL": alpha(o.get("MaintenancePlan"), 12),
             "GEWRK": wc_id(w, o["MainWorkCenter"]), "ILART": "002" if o["MaintenanceOrderType"] == "PM02" else "001"}
            for num, o in _maint_orders(w)]


def r_afko(w):
    rows = []
    for num, o in _maint_orders(w):
        rows.append({"AUFNR": alpha(num, 12), "AUFPL": aufpl(num), "GSTRP": d8(o["MaintOrdBasicStartDate"]), "GLTRP": d8(o.get("MaintOrdBasicEndDate")),
                     "GSTRS": d8(o["MaintOrdBasicStartDate"]), "GSUZS": "070000", "GLTRS": d8(o.get("MaintOrdBasicEndDate")), "GLUZS": "150000",
                     "FTRMI": d8(o.get("MaintOrdReleaseDate")), "PLNBEZ": "", "GAMNG": 0.0, "IGMNG": 0.0, "GMEIN": "", "DISPO": ""})
    for num, o in _prod_orders(w):
        op = w.t("prodop").get((num, "0010"), {})
        rows.append({"AUFNR": alpha(num, 12), "AUFPL": aufpl(num), "GSTRP": d8(o["MfgOrderPlannedStartDate"]), "GLTRP": d8(o["MfgOrderPlannedEndDate"]),
                     "GSTRS": d8(op.get("_start")), "GSUZS": t6(op.get("_start")), "GLTRS": d8(op.get("_end")), "GLUZS": t6(op.get("_end")),
                     "FTRMI": d8(o.get("MfgOrderActualReleaseDate")), "PLNBEZ": o["Material"], "GAMNG": _f(o["MfgOrderPlannedTotalQty"]),
                     "IGMNG": _f(o["MfgOrderConfirmedYieldQty"]), "GMEIN": "ST", "DISPO": "001"})
    return rows


def r_afpo(w):
    return [{"AUFNR": alpha(num, 12), "POSNR": "0001", "MATNR": o["Material"], "PSMNG": _f(o["MfgOrderPlannedTotalQty"]),
             "WEMNG": _f(o.get("_gr_qty")), "MEINS": "ST", "DWERK": o["ProductionPlant"], "LGORT": SLOC,
             "DGLTP": d8(o["MfgOrderPlannedEndDate"])} for num, o in _prod_orders(w)]


def _ops(w):
    for num, o in _maint_orders(w):
        for i, op in enumerate(o["to_MaintenanceOrderOperation"]["results"], start=1):
            yield num, i, op, "PM"
    for (num, vornr), op in sorted(w.t("prodop").items()):
        yield num, int(vornr) // 10, op, "PP"


def r_afvc(w):
    out = []
    for num, i, op, kind in _ops(w):
        out.append({"AUFPL": aufpl(num), "APLZL": f"{i:08d}", "VORNR": op.get("MaintenanceOrderOperation") or op["ManufacturingOrderOperation"],
                    "ARBID": wc_id(w, op["WorkCenter"]), "WERKS": PLANT, "STEUS": "PM01" if kind == "PM" else "PP01",
                    "LTXA1": (op.get("OperationDescription") or op.get("OperationText") or "")[:40], "OBJNR": f"OV{aufpl(num)}{i:08d}"})
    return out


def r_afvv(w):
    out = []
    for num, i, op, kind in _ops(w):
        if kind == "PM":
            out.append({"AUFPL": aufpl(num), "APLZL": f"{i:08d}", "MGVRG": 1.0, "LMNGA": 0.0, "XMNGA": 0.0,
                        "FSAVD": "00000000", "FSAVZ": "000000", "FSEDD": "00000000", "FSEDZ": "000000",
                        "ARBEI": _f(op["PlannedWorkQuantity"]), "ARBEH": "STD", "ISMNW": _f(op["ActualWorkQuantity"])})
        else:
            out.append({"AUFPL": aufpl(num), "APLZL": f"{i:08d}", "MGVRG": _f(op["OpPlannedTotalQuantity"]),
                        "LMNGA": _f(op["OpTotalConfirmedYieldQty"]), "XMNGA": _f(op["OpTotalConfirmedScrapQty"]),
                        "FSAVD": d8(op.get("_start")), "FSAVZ": t6(op.get("_start")), "FSEDD": d8(op.get("_end")), "FSEDZ": t6(op.get("_end")),
                        "ARBEI": 0.0, "ARBEH": "", "ISMNW": 0.0})
    return out


def r_afru(w):
    rows = []
    for k, c in w.t("mconf").items():
        rows.append({"RUECK": alpha(k[0], 10), "RMZHL": "00000001", "AUFNR": alpha(c["MaintenanceOrder"], 12), "VORNR": c["MaintenanceOrderOperation"],
                     "LMNGA": 0.0, "XMNGA": 0.0, "ISMNW": _f(c["ActualWorkQuantity"]), "ISMNE": "STD", "BUDAT": d8(c["PostingDate"]),
                     "ERNAM": user(c["_by"]), "ERSDA": d8(c["_at"]), "AUERU": "X" if c.get("IsFinalConfirmation") else "", "LTXA1": c.get("ConfirmationText", "")[:40]})
    for k, c in w.t("pconf").items():
        rows.append({"RUECK": alpha(k[0], 10), "RMZHL": "00000001", "AUFNR": alpha(c["OrderID"], 12), "VORNR": c["OrderOperation"],
                     "LMNGA": _f(c["ConfirmationYieldQuantity"]), "XMNGA": _f(c["ConfirmationScrapQuantity"]), "ISMNW": 0.0, "ISMNE": "",
                     "BUDAT": d8(c["PostingDate"]), "ERNAM": user(c["_by"]), "ERSDA": d8(c["_at"]), "AUERU": c.get("FinalConfirmationType", ""),
                     "LTXA1": c.get("ConfirmationText", "")[:40]})
    return rows


def r_resb(w):
    rows = []
    for (num, item), c in sorted(w.t("prodcomp").items()):
        rows.append({"RSNUM": rsnum_of_order(num), "RSPOS": item, "MATNR": c["Material"], "WERKS": PLANT, "LGORT": c.get("StorageLocation", SLOC),
                     "BDMNG": _f(c["RequiredQuantity"]), "ENMNG": _f(c["WithdrawnQuantity"]), "MEINS": unit(c["BaseUnit"]),
                     "BDTER": d8(c["RequirementDate"]), "AUFNR": alpha(num, 12), "BWART": "261",
                     "KZEAR": "X" if c["WithdrawnQuantity"] + 1e-9 >= c["RequiredQuantity"] else "", "XLOEK": "", "XWAOK": "X"})
    for num, o in _maint_orders(w):
        for c in o["to_MaintenanceOrderComponent"]["results"]:
            rows.append({"RSNUM": rsnum_of_order(num), "RSPOS": c["MaintenanceOrderComponent"], "MATNR": c["Material"], "WERKS": PLANT,
                         "LGORT": SLOC, "BDMNG": _f(c["RequiredQuantity"]), "ENMNG": _f(c["WithdrawnQuantity"]), "MEINS": prod_unit(w, c["Material"]),
                         "BDTER": d8(o["MaintOrdBasicStartDate"]), "AUFNR": alpha(num, 12), "BWART": "261",
                         "KZEAR": "X" if c["WithdrawnQuantity"] + 1e-9 >= c["RequiredQuantity"] else "", "XLOEK": "X" if o.get("IsDeleted") else "",
                         "XWAOK": "X" if o["MaintOrdSystemStatus"] in ("REL",) else ""})
    for k, r in sorted(w.t("reservation").items()):
        for it in r["to_ReservationDocumentItem"]["results"]:
            req, done = _f(it["ResvnItmRequiredQtyInBaseUnit"]), _f(it["ResvnItmWithdrawnQtyInBaseUnit"])
            rows.append({"RSNUM": alpha(k[0], 10), "RSPOS": it["ReservationItem"], "MATNR": it["Material"], "WERKS": it["Plant"],
                         "LGORT": it.get("StorageLocation", SLOC), "BDMNG": req, "ENMNG": done, "MEINS": prod_unit(w, it["Material"]),
                         "BDTER": d8(it.get("MatlCompRequirementDate")), "AUFNR": alpha(r.get("_aufnr", ""), 12), "BWART": r["GoodsMovementType"],
                         "KZEAR": "X" if it.get("ReservationItemIsFinallyIssued") else "", "XLOEK": "X" if r.get("IsDeleted") else "", "XWAOK": "X"})
    return rows


def r_rkpf(w):
    return [{"RSNUM": alpha(k[0], 10), "RSDAT": d8(r.get("ReservationDate")), "USNAM": user(r["_by"]), "BWART": r["GoodsMovementType"],
             "KOSTL": r.get("_kostl", ""), "AUFNR": alpha(r.get("_aufnr", ""), 12), "ZZ_ATHERYA_REF": r.get("YY1_AtheryaRef", "") or ""}
            for k, r in sorted(w.t("reservation").items())]


def r_eban(w):
    rows = []
    for k, pr in sorted(w.t("preq").items()):
        for it in pr["to_PurchaseReqnItem"]["results"]:
            st = it.get("ProcessingStatus", "N")
            lfdat = it.get("DeliveryDate")
            rows.append({"BANFN": alpha(k[0], 10), "BNFPO": it["PurchaseRequisitionItem"], "BSART": pr["PurchaseRequisitionType"],
                         "MATNR": it["Material"], "WERKS": it["Plant"], "LGORT": SLOC, "MENGE": _f(it["RequestedQuantity"]),
                         "MEINS": unit(it.get("BaseUnit", "")), "LFDAT": d8(lfdat if isinstance(lfdat, dt.date) else None),
                         "BADAT": d8(pr["_at"]), "ERNAM": user(pr["_by"]), "STATU": "N" if st == "N" else "B", "EBAKZ": "X" if st == "C" else "",
                         "LOEKZ": "X" if pr.get("IsDeleted") else "", "ESTKZ": "B" if it.get("PurReqnSource") == "MRP" else "R",
                         "EKGRP": "001", "ZZ_ATHERYA_REF": pr.get("YY1_AtheryaRef", "") or ""})
    return rows


RECEIPTS = {"101", "561", "701"}


def r_mkpf(w):
    return [{"MBLNR": k[0], "MJAHR": d["MaterialDocumentYear"], "BUDAT": d8(d["PostingDate"]), "BLDAT": d8(d["DocumentDate"]),
             "CPUDT": d8(d["_at"]), "CPUTM": t6(d["_at"]), "USNAM": user(d["_by"]), "BKTXT": d["MaterialDocumentHeaderText"][:25]}
            for k, d in w.t("matdoc").items()]


def r_mseg(w):
    rows = []
    for k, d in w.t("matdoc").items():
        for it in d["Items"]:
            bin_ = it.get("EWMStorageBin", "")
            stype = w.t("bin").get((bin_,), {}).get("EWMStorageType", "")
            rows.append({"MBLNR": k[0], "MJAHR": d["MaterialDocumentYear"], "ZEILE": it["MaterialDocumentItem"], "BWART": it["GoodsMovementType"],
                         "MATNR": it["Material"], "WERKS": it["Plant"], "LGORT": it["StorageLocation"], "MENGE": _f(it["QuantityInEntryUnit"]),
                         "MEINS": unit(it["EntryUnit"]), "SHKZG": "S" if it["GoodsMovementType"] in RECEIPTS else "H",
                         "AUFNR": alpha(it.get("ManufacturingOrder") or it.get("MaintenanceOrder"), 12),
                         "RSNUM": alpha(it.get("Reservation"), 10), "LGNUM": conv.LGNUM if bin_ else "",
                         "LGTYP": conv.WM_TYPE.get(stype, ""), "LGPLA": bin_})
    return rows


def r_plaf(w):
    return [{"PLNUM": alpha(k[0], 10), "MATNR": p["Material"], "PLWRK": p["ProductionPlant"], "PWWRK": p["ProductionPlant"],
             "GSMNG": _f(p["PlannedTotalQtyInBaseUnit"]), "PSTTR": d8(p["PlndOrderPlannedStartDate"]), "PEDTR": d8(p["PlndOrderPlannedEndDate"]),
             "PAART": p["PlannedOrderType"], "DISPO": p["MRPController"], "AUFFX": "X" if p.get("PlannedOrderIsFirm") else "", "MEINS": "ST"}
            for k, p in sorted(w.t("plannedorder").items())]


def _bdzei(w):
    mats = sorted({p["Product"] for p in w.t("pir").values()})
    return {m: f"{i:010d}" for i, m in enumerate(mats, start=1)}


def r_pbim(w):
    return [{"BDZEI": z, "MATNR": m, "WERKS": PLANT, "VERSB": "00", "BEDAE": "LSF", "VERVS": "X"} for m, z in _bdzei(w).items()]


def r_pbed(w):
    z = _bdzei(w)
    return [{"BDZEI": z[p["Product"]], "PDATU": d8(p["WorkingDayDate"]), "PLNMG": _f(p["PlannedQuantity"]), "ENTLU": "W",
             "MEINS": "ST", "ENMNG": _f(p.get("_consumed", 0))} for k, p in sorted(w.t("pir").items())]


def r_mast(w):
    return [{"MATNR": k[0], "WERKS": b["Plant"], "STLAN": "1", "STLNR": f"{i:08d}", "STLAL": "01"}
            for i, (k, b) in enumerate(sorted(w.t("bom").items()), start=1)]


def r_stpo(w):
    rows = []
    for i, (k, b) in enumerate(sorted(w.t("bom").items()), start=1):
        for j, it in enumerate(b["Items"], start=1):
            rows.append({"STLTY": "M", "STLNR": f"{i:08d}", "STLKN": f"{j:08d}", "POSNR": f"{j * 10:04d}", "POSTP": "L",
                         "IDNRK": it["BillOfMaterialComponent"], "MENGE": _f(it["BillOfMaterialItemQuantity"]), "MEINS": unit(it["BillOfMaterialItemUnit"])})
    return rows


def r_mpla(w):
    return [{"WARPL": alpha(k[0], 12), "WPTXT": p["MaintenancePlanDesc"][:40], "MPTYP": "PM", "ABRHO": "030"} for k, p in sorted(w.t("maintplan").items())]


def r_mpos(w):
    return [{"WARPL": alpha(k[0], 12), "WAPOS": alpha(k[0], 16), "EQUNR": alpha(p["Equipment"], 18), "AUART": p["MaintenanceOrderType"],
             "IWERK": p["MaintenancePlanningPlant"], "GEWRK": wc_id(w, p["MainWorkCenter"]), "PSTXT": p["MaintenancePlanDesc"][:40]}
            for k, p in sorted(w.t("maintplan").items())]


def r_mhis(w):
    return [{"WARPL": alpha(k[0], 12), "ABNUM": "000001", "NPLDA": d8(p["NextPlannedDate"]), "LRMDT": d8(p.get("LastCallDate")),
             "ZYKZT": f"{int(p['MaintPlanCycleDays']):07d}", "AUFNR": alpha(p.get("LastCallOrder"), 12)}
            for k, p in sorted(w.t("maintplan").items())]


def r_t301t(w):
    return [{"LGNUM": conv.LGNUM, "LGTYP": conv.WM_TYPE[k[0]], "SPRAS": "I", "LTYPT": s["EWMStorageTypeName"][:25]}
            for k, s in sorted(w.t("storagetype").items())]


def r_lagp(w):
    occupied = {k[0] for k in w.t("quant")}
    return [{"LGNUM": conv.LGNUM, "LGTYP": conv.WM_TYPE[b["EWMStorageType"]], "LGPLA": k[0], "KZLER": "" if k[0] in occupied else "X",
             "SKZUA": "", "SKZUE": ""} for k, b in sorted(w.t("bin").items())]


def r_lqua(w):
    rows = []
    for (bin_, mat), q in sorted(w.t("quant").items()):
        total = q["EWMStockQuantityInBaseUnit"]
        rows.append({"LGNUM": conv.LGNUM, "LQNUM": lqnum(w, bin_, mat), "MATNR": mat, "WERKS": PLANT, "LGORT": SLOC,
                     "LGTYP": conv.WM_TYPE[q["EWMStorageType"]], "LGPLA": bin_, "GESME": round(total, 3),
                     "VERME": round(max(0.0, total - inv.committed(w, bin_, mat)), 3), "MEINS": unit(q["EWMStockQuantityBaseUnit"]),
                     "WDATU": d8(q["_at"])})
    return rows


BWLVS = {"STOCCAGGIO": "101", "APPROVV_PRODUZIONE": "319", "PRELIEVO": "601"}


def _type_of_bin(w, bin_):
    return conv.WM_TYPE.get(w.t("bin").get((bin_,), {}).get("EWMStorageType", ""), "")


def r_ltak(w):
    return [{"LGNUM": conv.LGNUM, "TANUM": alpha(k[0], 10), "BWLVS": BWLVS.get(t["WarehouseProcessType"], "999"),
             "BENUM": alpha(t["EWMReferenceDocument"], 10), "BDATU": d8(t["WhseTaskCreationDateTime"]), "BZEIT": t6(t["WhseTaskCreationDateTime"]),
             "BNAME": user(t["_by"]), "KQUIT": "X" if t["WarehouseTaskStatus"] == "C" else ""} for k, t in w.t("whtask").items()]


def r_ltap(w):
    return [{"LGNUM": conv.LGNUM, "TANUM": alpha(k[0], 10), "TAPOS": "0001", "MATNR": t["Product"], "WERKS": PLANT,
             "VLTYP": _type_of_bin(w, t["SourceStorageBin"]), "VLPLA": t["SourceStorageBin"], "NLTYP": _type_of_bin(w, t["DestinationStorageBin"]),
             "NLPLA": t["DestinationStorageBin"], "VSOLM": _f(t["TargetQuantityInBaseUnit"]), "VISTM": _f(t.get("ActualQuantityInBaseUnit")),
             "MEINS": prod_unit(w, t["Product"]), "PQUIT": "X" if t["WarehouseTaskStatus"] == "C" else "",
             "QDATU": d8(t.get("WhseTaskConfirmationDateTime")), "QZEIT": t6(t.get("WhseTaskConfirmationDateTime")),
             "QNAME": user(t["_changed_by"]) if t["WarehouseTaskStatus"] == "C" else ""} for k, t in w.t("whtask").items()]


def r_jest(w):
    rows = []
    for num, o in _maint_orders(w):
        rows += [{"OBJNR": conv.objnr_order(num), "STAT": conv.BY_TXT04[s], "INACT": ""} for s in conv.order_status(w, o, "PM")]
    for num, o in _prod_orders(w):
        rows += [{"OBJNR": conv.objnr_order(num), "STAT": conv.BY_TXT04[s], "INACT": ""} for s in conv.order_status(w, o, "PP")]
    for num, n in _notif_rows(w):
        rows += [{"OBJNR": conv.objnr_notif(num), "STAT": conv.BY_TXT04[s], "INACT": ""} for s in conv.notif_status(n)]
    return rows


def r_tj02t(w):
    return [{"ISTAT": k, "SPRAS": "I", "TXT04": v[0], "TXT30": v[1]} for k, v in sorted(conv.TJ02T.items())]


# ---------------------------------------------------------------- dizionario

TABLES: dict[str, tuple[str, list[F], object]] = {
    "MARA": ("Dati generali del materiale", [MATNR, _c("MTART", 4, "Tipo materiale"), _c("MATKL", 9, "Gruppo merci"), MEINS,
                                            _d("ERSDA", "Creato il"), ERNAM], r_mara),
    "MAKT": ("Descrizioni materiale", [MATNR, _c("SPRAS", 1, "Lingua"), _c("MAKTX", 40, "Descrizione")], r_makt),
    "MARC": ("Dati materiale per divisione", [MATNR, WERKS, _c("DISPO", 3, "Responsabile MRP"), _c("DISMM", 2, "Caratteristica MRP"),
                                              _c("BESKZ", 1, "Tipo approvvigionamento"), _n("PLIFZ", 3, "Tempo consegna pianificato"),
                                              _n("DZEIT", 3, "Tempo di produzione interna"), _p("EISBE", "Scorta di sicurezza"),
                                              _p("BSTFE", "Lotto fisso"), _c("DISLS", 2, "Procedura lotto")], r_marc),
    "MARD": ("Giacenze per magazzino", [MATNR, WERKS, LGORT, _p("LABST", "Libera utilizzazione"), _p("INSME", "In controllo qualità"),
                                        _p("SPEME", "Bloccata")], r_mard),
    "T001W": ("Divisioni", [WERKS, _c("NAME1", 30, "Nome")], r_t001w),
    "T001L": ("Magazzini", [WERKS, LGORT, _c("LGOBE", 16, "Descrizione")], r_t001l),
    "CRHD": ("Centri di lavoro", [_c("OBJTY", 2, "Tipo oggetto"), _n("OBJID", 8, "ID oggetto"), _c("ARBPL", 8, "Centro di lavoro"), WERKS,
                                  _c("VERWE", 4, "Categoria"), _c("XSPRR", 1, "Bloccato per la pianificazione")], r_crhd),
    "CRTX": ("Testi centri di lavoro", [_c("OBJTY", 2, "Tipo oggetto"), _n("OBJID", 8, "ID oggetto"), _c("SPRAS", 1, "Lingua"),
                                        _c("KTEXT", 40, "Descrizione")], r_crtx),
    "EQUI": ("Apparecchiature", [EQUNR, _c("EQTYP", 1, "Categoria"), _c("EQART", 10, "Tipo oggetto tecnico"), ERDAT, ERNAM,
                                 _c("OBJNR", 22, "Numero oggetto")], r_equi),
    "EQKT": ("Testi apparecchiature", [EQUNR, _c("SPRAS", 1, "Lingua"), _c("EQKTX", 40, "Descrizione")], r_eqkt),
    "V_EQUI": ("Vista apparecchiature", [EQUNR, _d("DATBI", "Valido fino al"), _c("EQTYP", 1, "Categoria"), _c("SPRAS", 1, "Lingua"),
                                         _c("EQKTX", 40, "Descrizione"), _c("IWERK", 4, "Divisione di pianificazione"), _c("SWERK", 4, "Divisione di ubicazione"),
                                         _c("TPLNR", 30, "Sede tecnica"), _n("GEWRK", 8, "Centro di lavoro responsabile (ID)"),
                                         _c("OBJNR", 22, "Numero oggetto")], r_v_equi),
    "IFLOT": ("Sedi tecniche", [_c("TPLNR", 30, "Sede tecnica"), _c("IWERK", 4, "Divisione"), _c("TPLMA", 30, "Sede superiore")], r_iflot),
    "IFLOTX": ("Testi sedi tecniche", [_c("TPLNR", 30, "Sede tecnica"), _c("SPRAS", 1, "Lingua"), _c("PLTXT", 40, "Descrizione")], r_iflotx),
    "IMPTT": ("Punti di misura", [_c("POINT", 12, "Punto di misura"), _c("MPOBJ", 22, "Oggetto"), _c("PTTXT", 40, "Descrizione"),
                                  _c("MSEHI", 3, "Unità"), _c("INDCT", 1, "Contatore"), F("MRMAX", "F", 22, "Limite superiore")], r_imptt),
    "IMRG": ("Documenti di misura", [_c("MDOCM", 20, "Documento di misura"), _c("POINT", 12, "Punto di misura"), _d("IDATE", "Data"),
                                     _t("ITIME", "Ora"), F("RECDV", "F", 22, "Valore misurato"), _c("READC", 22, "Valore (carattere)"),
                                     _c("RECDU", 3, "Unità"), ERNAM, _c("MDTXT", 40, "Testo")], r_imrg),
    "QMEL": ("Avvisi: testata", [_c("QMNUM", 12, "Avviso"), _c("QMART", 2, "Tipo avviso"), _c("QMTXT", 40, "Testo breve"), ERNAM, ERDAT,
                                 _t("MZEIT", "Ora"), _d("QMDAT", "Data avviso"), _c("PRIOK", 1, "Priorità"), AUFNR, _c("OBJNR", 22, "Numero oggetto"),
                                 _d("QMDAB", "Completato il"), _c("AENAM", 12, "Modificato da"), ATHREF], r_qmel),
    "QMIH": ("Avvisi: dati di manutenzione", [_c("QMNUM", 12, "Avviso"), EQUNR, _c("IWERK", 4, "Divisione di pianificazione"),
                                              _c("MSAUS", 1, "Guasto"), _d("AUSVN", "Inizio malfunzionamento"), _t("AUZTV", "Ora inizio")], r_qmih),
    "VIQMEL": ("Vista avvisi", [_c("QMNUM", 12, "Avviso"), _c("QMART", 2, "Tipo avviso"), _c("QMTXT", 40, "Testo breve"), EQUNR,
                                _c("IWERK", 4, "Divisione di pianificazione"), _c("PRIOK", 1, "Priorità"), _d("QMDAT", "Data avviso"),
                                _t("MZEIT", "Ora"), AUFNR, _c("MSAUS", 1, "Guasto"), _d("AUSVN", "Inizio malfunzionamento"),
                                _t("AUZTV", "Ora inizio"), _d("QMDAB", "Completato il"), ERNAM, _c("OBJNR", 22, "Numero oggetto"), ATHREF], r_viqmel),
    "AUFK": ("Ordini: dati principali", [AUFNR, _c("AUART", 4, "Tipo ordine"), _n("AUTYP", 2, "Categoria ordine"), _c("KTEXT", 40, "Testo breve"),
                                         WERKS, ERNAM, ERDAT, _c("AENAM", 12, "Modificato da"), _c("OBJNR", 22, "Numero oggetto"),
                                         _c("LOEKZ", 1, "Contrassegno di cancellazione"), _c("PHAS0", 1, "Fase: creato"),
                                         _c("PHAS1", 1, "Fase: rilasciato"), _c("PHAS2", 1, "Fase: chiuso tecnicamente"), ATHREF], r_aufk),
    "AFIH": ("Ordini di manutenzione: testata", [AUFNR, EQUNR, _c("IWERK", 4, "Divisione di pianificazione"), _c("QMNUM", 12, "Avviso"),
                                                 _c("PRIOK", 1, "Priorità"), _c("WARPL", 12, "Piano di manutenzione"),
                                                 _n("GEWRK", 8, "Centro di lavoro responsabile (ID)"), _c("ILART", 3, "Tipo attività")], r_afih),
    "AFKO": ("Ordini: dati di schedulazione", [AUFNR, _n("AUFPL", 10, "Numero ciclo dell'ordine"), _d("GSTRP", "Inizio cardine"),
                                               _d("GLTRP", "Fine cardine"), _d("GSTRS", "Inizio schedulato"), _t("GSUZS", "Ora inizio schedulato"),
                                               _d("GLTRS", "Fine schedulata"), _t("GLUZS", "Ora fine schedulata"), _d("FTRMI", "Rilasciato il"),
                                               _c("PLNBEZ", 18, "Materiale"), _p("GAMNG", "Quantità totale"), _p("IGMNG", "Quantità confermata"),
                                               _c("GMEIN", 3, "Unità"), _c("DISPO", 3, "Responsabile MRP")], r_afko),
    "AFPO": ("Ordini: posizioni", [AUFNR, _n("POSNR", 4, "Posizione"), MATNR, _p("PSMNG", "Quantità ordine"), _p("WEMNG", "Quantità entrata"),
                                   MEINS, _c("DWERK", 4, "Divisione"), LGORT, _d("DGLTP", "Data fine pianificata")], r_afpo),
    "AFVC": ("Operazioni dell'ordine", [_n("AUFPL", 10, "Numero ciclo"), _n("APLZL", 8, "Contatore"), _c("VORNR", 4, "Operazione"),
                                        _n("ARBID", 8, "Centro di lavoro (ID)"), WERKS, _c("STEUS", 4, "Chiave di controllo"),
                                        _c("LTXA1", 40, "Testo operazione"), _c("OBJNR", 22, "Numero oggetto")], r_afvc),
    "AFVV": ("Quantità e date delle operazioni", [_n("AUFPL", 10, "Numero ciclo"), _n("APLZL", 8, "Contatore"), _p("MGVRG", "Quantità operazione"),
                                                  _p("LMNGA", "Quantità buona confermata"), _p("XMNGA", "Scarto confermato"),
                                                  _d("FSAVD", "Inizio schedulato"), _t("FSAVZ", "Ora inizio"), _d("FSEDD", "Fine schedulata"),
                                                  _t("FSEDZ", "Ora fine"), _p("ARBEI", "Lavoro pianificato"), _c("ARBEH", 3, "Unità lavoro"),
                                                  _p("ISMNW", "Lavoro effettivo")], r_afvv),
    "AFRU": ("Conferme degli ordini", [_n("RUECK", 10, "Conferma"), _n("RMZHL", 8, "Contatore"), AUFNR, _c("VORNR", 4, "Operazione"),
                                       _p("LMNGA", "Quantità buona"), _p("XMNGA", "Scarto"), _p("ISMNW", "Lavoro effettivo"),
                                       _c("ISMNE", 3, "Unità lavoro"), _d("BUDAT", "Data di registrazione"), ERNAM, _d("ERSDA", "Registrato il"),
                                       _c("AUERU", 1, "Conferma finale"), _c("LTXA1", 40, "Testo conferma")], r_afru),
    "RESB": ("Prenotazioni e fabbisogni dipendenti", [_c("RSNUM", 10, "Prenotazione"), _n("RSPOS", 4, "Posizione"), MATNR, WERKS, LGORT,
                                                      _p("BDMNG", "Quantità richiesta"), _p("ENMNG", "Quantità prelevata"), MEINS,
                                                      _d("BDTER", "Data fabbisogno"), AUFNR, _c("BWART", 3, "Tipo movimento"),
                                                      _c("KZEAR", 1, "Prelievo finale"), _c("XLOEK", 1, "Cancellata"),
                                                      _c("XWAOK", 1, "Movimento consentito")], r_resb),
    "RKPF": ("Prenotazioni: testata", [_c("RSNUM", 10, "Prenotazione"), _d("RSDAT", "Data base"), _c("USNAM", 12, "Utente"),
                                       _c("BWART", 3, "Tipo movimento"), _c("KOSTL", 10, "Centro di costo"), AUFNR, ATHREF], r_rkpf),
    "EBAN": ("Richieste d'acquisto", [_c("BANFN", 10, "Richiesta d'acquisto"), _n("BNFPO", 5, "Posizione"), _c("BSART", 4, "Tipo documento"),
                                      MATNR, WERKS, LGORT, _p("MENGE", "Quantità"), MEINS, _d("LFDAT", "Data di consegna"), _d("BADAT", "Data richiesta"),
                                      ERNAM, _c("STATU", 1, "Stato di elaborazione"), _c("EBAKZ", 1, "Chiusa"), _c("LOEKZ", 1, "Cancellata"),
                                      _c("ESTKZ", 1, "Origine"), _c("EKGRP", 3, "Gruppo acquisti"), ATHREF], r_eban),
    "MKPF": ("Documenti materiale: testata", [_c("MBLNR", 10, "Documento materiale"), _n("MJAHR", 4, "Esercizio"), _d("BUDAT", "Data di registrazione"),
                                              _d("BLDAT", "Data documento"), _d("CPUDT", "Registrato il"), _t("CPUTM", "Ora"), _c("USNAM", 12, "Utente"),
                                              _c("BKTXT", 25, "Testo testata")], r_mkpf),
    "MSEG": ("Documenti materiale: posizioni", [_c("MBLNR", 10, "Documento materiale"), _n("MJAHR", 4, "Esercizio"), _n("ZEILE", 4, "Posizione"),
                                                _c("BWART", 3, "Tipo movimento"), MATNR, WERKS, LGORT, _p("MENGE", "Quantità"), MEINS,
                                                _c("SHKZG", 1, "Dare/avere"), AUFNR, _c("RSNUM", 10, "Prenotazione"), _c("LGNUM", 3, "Numero magazzino"),
                                                _c("LGTYP", 3, "Tipo magazzino"), _c("LGPLA", 10, "Ubicazione")], r_mseg),
    "PLAF": ("Ordini pianificati", [_c("PLNUM", 10, "Ordine pianificato"), MATNR, _c("PLWRK", 4, "Divisione di pianificazione"),
                                    _c("PWWRK", 4, "Divisione di produzione"), _p("GSMNG", "Quantità totale"), _d("PSTTR", "Inizio"),
                                    _d("PEDTR", "Fine"), _c("PAART", 4, "Tipo ordine pianificato"), _c("DISPO", 3, "Responsabile MRP"),
                                    _c("AUFFX", 1, "Fissato"), MEINS], r_plaf),
    "PBIM": ("Fabbisogni indipendenti: testata", [_n("BDZEI", 10, "Puntatore"), MATNR, WERKS, _c("VERSB", 2, "Versione"),
                                                  _c("BEDAE", 4, "Tipo fabbisogno"), _c("VERVS", 1, "Attiva")], r_pbim),
    "PBED": ("Fabbisogni indipendenti: periodi", [_n("BDZEI", 10, "Puntatore"), _d("PDATU", "Data"), _p("PLNMG", "Quantità pianificata"),
                                                  _c("ENTLU", 1, "Periodo"), MEINS, _p("ENMNG", "Quantità consumata")], r_pbed),
    "MAST": ("Distinte per materiale", [MATNR, WERKS, _c("STLAN", 1, "Utilizzo"), _c("STLNR", 8, "Distinta"), _c("STLAL", 2, "Alternativa")], r_mast),
    "STPO": ("Posizioni di distinta", [_c("STLTY", 1, "Categoria"), _c("STLNR", 8, "Distinta"), _n("STLKN", 8, "Nodo"), _c("POSNR", 4, "Posizione"),
                                       _c("POSTP", 1, "Tipo posizione"), _c("IDNRK", 18, "Componente"), _p("MENGE", "Quantità"), MEINS], r_stpo),
    "MPLA": ("Piani di manutenzione", [_c("WARPL", 12, "Piano"), _c("WPTXT", 40, "Testo"), _c("MPTYP", 2, "Categoria"), _c("ABRHO", 3, "Orizzonte")], r_mpla),
    "MPOS": ("Posizioni di manutenzione", [_c("WARPL", 12, "Piano"), _c("WAPOS", 16, "Posizione"), EQUNR, _c("AUART", 4, "Tipo ordine"),
                                           _c("IWERK", 4, "Divisione"), _n("GEWRK", 8, "Centro di lavoro (ID)"), _c("PSTXT", 40, "Testo")], r_mpos),
    "MHIS": ("Storico schedulazione piani", [_c("WARPL", 12, "Piano"), _n("ABNUM", 6, "Chiamata"), _d("NPLDA", "Prossima data pianificata"),
                                             _d("LRMDT", "Ultima chiamata"), _n("ZYKZT", 7, "Ciclo (giorni)"), AUFNR], r_mhis),
    "T301T": ("Tipi magazzino WM", [_c("LGNUM", 3, "Numero magazzino"), _c("LGTYP", 3, "Tipo magazzino"), _c("SPRAS", 1, "Lingua"),
                                    _c("LTYPT", 25, "Descrizione")], r_t301t),
    "LAGP": ("Ubicazioni WM", [_c("LGNUM", 3, "Numero magazzino"), _c("LGTYP", 3, "Tipo magazzino"), _c("LGPLA", 10, "Ubicazione"),
                               _c("KZLER", 1, "Vuota"), _c("SKZUA", 1, "Blocco prelievo"), _c("SKZUE", 1, "Blocco stoccaggio")], r_lagp),
    "LQUA": ("Quanti WM", [_c("LGNUM", 3, "Numero magazzino"), _n("LQNUM", 10, "Quanto"), MATNR, WERKS, LGORT, _c("LGTYP", 3, "Tipo magazzino"),
                           _c("LGPLA", 10, "Ubicazione"), _p("GESME", "Giacenza totale"), _p("VERME", "Disponibile"), MEINS,
                           _d("WDATU", "Data entrata")], r_lqua),
    "LTAK": ("Ordini di trasferimento: testata", [_c("LGNUM", 3, "Numero magazzino"), _n("TANUM", 10, "Ordine di trasferimento"),
                                                  _n("BWLVS", 3, "Tipo movimento WM"), _c("BENUM", 10, "Riferimento"), _d("BDATU", "Creato il"),
                                                  _t("BZEIT", "Ora"), _c("BNAME", 12, "Utente"), _c("KQUIT", 1, "Confermato")], r_ltak),
    "LTAP": ("Ordini di trasferimento: posizioni", [_c("LGNUM", 3, "Numero magazzino"), _n("TANUM", 10, "Ordine di trasferimento"),
                                                    _n("TAPOS", 4, "Posizione"), MATNR, WERKS, _c("VLTYP", 3, "Tipo origine"), _c("VLPLA", 10, "Ubicazione origine"),
                                                    _c("NLTYP", 3, "Tipo destinazione"), _c("NLPLA", 10, "Ubicazione destinazione"),
                                                    _p("VSOLM", "Quantità richiesta"), _p("VISTM", "Quantità effettiva"), MEINS,
                                                    _c("PQUIT", 1, "Confermata"), _d("QDATU", "Confermata il"), _t("QZEIT", "Ora conferma"),
                                                    _c("QNAME", 12, "Confermata da")], r_ltap),
    "JEST": ("Stati degli oggetti", [_c("OBJNR", 22, "Numero oggetto"), _c("STAT", 5, "Stato"), _c("INACT", 1, "Inattivo")], r_jest),
    "TJ02T": ("Testi degli stati di sistema", [_c("ISTAT", 5, "Stato"), _c("SPRAS", 1, "Lingua"), _c("TXT04", 4, "Testo breve"),
                                               _c("TXT30", 30, "Testo")], r_tj02t),
}


def fields_of(table: str) -> list[F]:
    return TABLES[table][1]


def rows_of(w, table: str) -> list[dict]:
    return TABLES[table][2](w)
