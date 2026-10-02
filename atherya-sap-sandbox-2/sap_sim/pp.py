"""Produzione (PP) con schedulazione dettagliata in stile PP/DS.

Fabbisogni indipendenti → MRP → ordini pianificati (e RdA per i materiali d'acquisto) →
conversione in ordini di produzione → rilascio con verifica dei componenti e approvvigionamento
della linea → schedulazione per pressa con matrice dei setup → conferme con prelievo a consuntivo
ed entrata del prodotto finito → spedizioni giornaliere.
"""

import datetime as dt
import math

from . import inventory as inv
from .calendar import add_working_minutes, next_working, working_days_before
from .masterdata import LINES, PLANT, SETUP_MOLD_CHANGE_MIN, SETUP_SAME_MOLD_MIN, press_wc
from .world import BusinessError

PLANNING_EFFICIENCY = 0.85
HORIZON_DAYS = 56


def fmt_time(t: dt.datetime | None) -> str:
    return f"PT{t.hour:02d}H{t.minute:02d}M{t.second:02d}S" if t else ""


def monday(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())


def max_lot(w, mat: str) -> float:
    """Un lotto non supera due giorni di lavoro di una pressa."""
    cycle = w.get("product", (mat,))["_cycle_s"]
    return math.floor(2 * 24 * 3600 / cycle * PLANNING_EFFICIENCY / 100) * 100


# ---------------------------------------------------------------- fabbisogni indipendenti

def seed_demand(w) -> None:
    from .masterdata import FINISHED
    for mat, (line, mold, _c, _i, cycle, _d) in FINISHED.items():
        presses = len(LINES[line]["presses"])
        same_line = sum(1 for v in FINISHED.values() if v[0] == line)
        weekly = presses / same_line * 5 * 24 * 3600 / cycle * PLANNING_EFFICIENCY * 0.72
        start = monday(w.today)
        for week in range(0, 10):
            day = start + dt.timedelta(weeks=week)
            qty = round(weekly * w.rng.uniform(0.9, 1.1), -2)
            key = (mat, PLANT, day.isoformat())
            w.insert("pir", key, {"Product": mat, "Plant": PLANT, "PlndIndepRqmtType": "LSF", "PlndIndepRqmtVersion": "00",
                                  "WorkingDayDate": day, "PlannedQuantity": qty, "UnitOfMeasure": "PC"}, "SEED")


# ---------------------------------------------------------------- MRP

def _receipts(w, mat: str) -> list[tuple[dt.date, float]]:
    out = []
    for o in w.t("prodorder").values():
        if o["Material"] == mat and o["OrderSystemStatus"] not in ("TECO", "DLV") and not o.get("IsDeleted"):
            open_qty = o["MfgOrderPlannedTotalQty"] - o["_gr_qty"]
            if open_qty > 0:
                out.append((o["MfgOrderPlannedEndDate"], open_qty))
    return out


def _dependent(w, mat: str) -> list[tuple[dt.date, float]]:
    out = []
    for c in w.t("prodcomp").values():
        if c["Material"] == mat:
            left = c["RequiredQuantity"] - c["WithdrawnQuantity"]
            if left > 1e-9:
                out.append((c["RequirementDate"], left))
    for p in w.t("plannedorder").values():
        bom = w.t("bom").get((p["Material"],))
        for it in (bom or {}).get("Items", []):
            if it["BillOfMaterialComponent"] == mat:
                out.append((p["PlndOrderPlannedStartDate"], p["PlannedTotalQtyInBaseUnit"] * it["BillOfMaterialItemQuantity"]))
    return out


def run_mrp(w, user: str = "BATCH", materials: list[str] | None = None, raw: bool = True) -> dict:
    """MRP a rigenerazione: cancella gli ordini pianificati non convertiti e li ricrea."""
    stats = {"planned_orders": 0, "purchase_requisitions": 0}
    fins = [k[0] for k, p in w.t("product").items() if p["ProductType"] == "FERT" and p["Plant"] == PLANT]
    for mat in materials or fins:
        for key in [k for k, p in w.t("plannedorder").items() if p["Material"] == mat]:
            w.delete("plannedorder", key, user, quiet=True)
        prod = w.get("product", (mat,))
        reqs = [(p["WorkingDayDate"], p["PlannedQuantity"] - p.get("_consumed", 0))
                for p in w.t("pir").values() if p["Product"] == mat and p["WorkingDayDate"] >= monday(w.today)]
        events = sorted([(d, -q) for d, q in reqs if q > 0] + [(d, q) for d, q in _receipts(w, mat)], key=lambda x: (x[0], x[1] < 0))
        projected = inv.stock_qty(w, mat) - prod["SafetyStockQuantity"]
        lot = max_lot(w, mat)
        for day, qty in events:
            projected += qty
            while projected < -1e-9:
                q = min(lot, math.ceil(-projected / 100) * 100)
                end = max(working_days_before(day, 1), w.today)
                start = max(working_days_before(end, prod["InHouseProductionTime"]), w.today)
                num = w.number("plannedorder", 20000000)
                w.insert("plannedorder", (num,), {"PlannedOrder": num, "Material": mat, "ProductionPlant": PLANT,
                                                  "PlannedOrderType": "LA", "PlannedTotalQtyInBaseUnit": q,
                                                  "PlndOrderPlannedStartDate": start, "PlndOrderPlannedEndDate": end,
                                                  "MRPController": "001", "PlannedOrderIsFirm": False}, user, quiet=True)
                projected += q
                stats["planned_orders"] += 1
    if raw:
        for key, p in w.t("product").items():
            if p["ProductType"] != "ROH" or p["Plant"] != PLANT:
                continue
            mat = key[0]
            # Fabbisogni dentro il tempo di consegna: coperti alla prima data possibile, non ripetuti a ogni MRP.
            earliest = w.today + dt.timedelta(days=p["PlannedDeliveryDurationInDays"])
            demand = sorted((max(d, earliest), q) for d, q in _dependent(w, mat))
            supply = sorted((max(d, earliest) if d < w.today else d, q) for d, q in inv.open_supply(w, mat))
            projected = inv.stock_qty(w, mat) - p["SafetyStockQuantity"]
            timeline = sorted([(d, -q) for d, q in demand] + supply, key=lambda x: (x[0], x[1] < 0))
            for day, qty in timeline:
                projected += qty
                if projected < 0:
                    lots = math.ceil(-projected / p["FixedLotSize"])
                    q = lots * p["FixedLotSize"]
                    delivery = max(day - dt.timedelta(days=1), earliest)
                    inv.create_purchase_requisition(w, {"PurchaseRequisitionType": "NB", "to_PurchaseReqnItem": {"results": [
                        {"Material": mat, "Plant": PLANT, "RequestedQuantity": f"{q:g}", "BaseUnit": p["BaseUnit"],
                         "DeliveryDate": delivery, "PurReqnSource": "MRP"}]}}, user)
                    projected += q
                    stats["purchase_requisitions"] += 1
    w.event("Pianificazione", f"MRP eseguito: {stats['planned_orders']} ordini pianificati, {stats['purchase_requisitions']} RdA")
    return stats


# ---------------------------------------------------------------- conversione e ordini di produzione

def _open_minutes(w, wc: str) -> float:
    return sum(op["_remaining"] * op["_cycle_s"] / 60 / PLANNING_EFFICIENCY
               for op in w.t("prodop").values() if op["WorkCenter"] == wc and op["_status"] != "done")


def eligible_workcenters(w, mat: str) -> list[str]:
    line = w.get("product", (mat,))["_line"]
    return [press_wc(n) for n in LINES[line]["presses"]]


def _available(w, wc: str) -> bool:
    if w.get("workcenter", (wc,))["_blocked"]:
        return False
    eq = w.get("workcenter", (wc,))["Equipment"]
    return not any(n.get("IsBreakdown") and n["TechnicalObject"] == eq and n["MaintNotifProcessPhaseCode"] != "4"
                   for n in w.t("notif").values())


def convert_planned_order(w, num: str, user: str, workcenter: str | None = None) -> str:
    p = w.get("plannedorder", (num,))
    mat = p["Material"]
    candidates = [wc for wc in eligible_workcenters(w, mat) if _available(w, wc)]
    if workcenter:
        if workcenter not in eligible_workcenters(w, mat):
            raise BusinessError("CO/120", f"Il centro di lavoro {workcenter} non è idoneo per lo stampo di {mat}")
        wc = workcenter
    else:
        if not candidates:
            raise BusinessError("CO/121", f"Nessuna pressa disponibile per {mat}")
        wc = min(candidates, key=lambda c: _open_minutes(w, c))
    order = create_production_order(w, mat, p["PlannedTotalQtyInBaseUnit"], p["PlndOrderPlannedStartDate"],
                                    p["PlndOrderPlannedEndDate"], wc, user, planned_order=num)
    w.delete("plannedorder", (num,), user)
    return order


def create_production_order(w, mat: str, qty: float, start: dt.date, end: dt.date, wc: str, user: str,
                            planned_order: str = "", number: str | None = None) -> str:
    routing = w.get("routing", (mat,))["Operations"][0]
    order = number or w.number("prodorder", 1000500)
    w.insert("prodorder", (order,), {
        "ManufacturingOrder": order, "ManufacturingOrderType": "PP01", "Material": mat, "ProductionPlant": PLANT,
        "MfgOrderPlannedTotalQty": qty, "TotalQuantity": f"{qty:g}", "ProductionUnit": "PC",
        "MfgOrderPlannedStartDate": start, "MfgOrderPlannedEndDate": end, "OrderSystemStatus": "CRTD",
        "MfgOrderConfirmedYieldQty": 0.0, "MfgOrderConfirmedScrapQty": 0.0, "PlannedOrder": planned_order,
        "MissingPartsFlag": False, "_gr_qty": 0.0}, user)
    w.insert("prodop", (order, "0010"), {
        "ManufacturingOrder": order, "ManufacturingOrderOperation": "0010", "ManufacturingOrderSequence": "0",
        "WorkCenter": wc, "Plant": PLANT, "OperationText": routing["OperationText"], "OpPlannedTotalQuantity": qty,
        "OpTotalConfirmedYieldQty": 0.0, "OpTotalConfirmedScrapQty": 0.0, "Mold": routing["Mold"],
        "OpErlstSchedldExecStrtDte": None, "OpErlstSchedldExecStrtTme": "", "OpErlstSchedldExecEndDte": None,
        "OpErlstSchedldExecEndTme": "", "OpSetupDurationMin": 0, "OperationStatus": "Pianificata",
        "_cycle_s": routing["MachineTimeSecPerPiece"], "_remaining": qty, "_unconfirmed_yield": 0.0, "_unconfirmed_scrap": 0.0,
        "_status": "open", "_requested_start": None, "_start": None, "_end": None, "_due": end}, user)
    bom = w.get("bom", (mat,))
    for i, it in enumerate(bom["Items"], start=1):
        w.insert("prodcomp", (order, f"{i * 10:04d}"), {
            "ManufacturingOrder": order, "ReservationItem": f"{i * 10:04d}", "Material": it["BillOfMaterialComponent"],
            "RequiredQuantity": round(qty * it["BillOfMaterialItemQuantity"], 3), "WithdrawnQuantity": 0.0,
            "BaseUnit": it["BillOfMaterialItemUnit"], "RequirementDate": start, "StorageLocation": "0001",
            "_per_piece": it["BillOfMaterialItemQuantity"], "_staged": 0.0}, user, quiet=True)
    schedule_workcenter(w, wc)
    w.event("Pianificazione", f"Ordine di produzione {order}: {qty:g} × {mat} su {wc}", order)
    return order


def release_production_order(w, order: str, user: str) -> None:
    o = w.get("prodorder", (order,))
    if o["OrderSystemStatus"] != "CRTD":
        raise BusinessError("CO/130", f"Ordine {order} non in stato Creato")
    comps = [c for k, c in w.t("prodcomp").items() if k[0] == order]
    missing = [c["Material"] for c in comps if inv.stock_qty(w, c["Material"]) + 1e-9 < c["RequiredQuantity"] - c["WithdrawnQuantity"]]
    w.update("prodorder", (order,), {"OrderSystemStatus": "REL", "MfgOrderActualReleaseDate": w.today,
                                     "MissingPartsFlag": bool(missing)}, user)
    op = w.get("prodop", (order, "0010"))
    w.update("prodop", (order, "0010"), {"OperationStatus": "Rilasciata"}, user, quiet=True)
    line = w.get("workcenter", (op["WorkCenter"],))["_line"]
    for c in comps:  # approvvigionamento della linea
        qty = round(c["RequiredQuantity"] - c["_staged"], 3)
        if qty > 0 and inv.create_task(w, "APPROVV_PRODUZIONE", c["Material"], qty, None, f"PSA-L{line}", order, "BATCH"):
            c["_staged"] = c["RequiredQuantity"]
    if missing:
        w.event("Pianificazione", f"Ordine {order} rilasciato con parti mancanti: {', '.join(missing)}", order)


# ---------------------------------------------------------------- schedulazione per pressa

def schedule_workcenter(w, wc: str) -> None:
    ops = [op for op in w.t("prodop").values() if op["WorkCenter"] == wc and op["_status"] != "done"]
    machine = w.machines.get(wc, {})
    mold = machine.get("mold")
    started = [op for op in ops if op["_status"] == "started"]
    rest = sorted([op for op in ops if op["_status"] != "started"],
                  key=lambda op: (op["_requested_start"] or dt.datetime.combine(op["_due"], dt.time(0)) - dt.timedelta(days=2),
                                  op["ManufacturingOrder"]))
    t = next_working(w.now)
    for op in started + rest:
        if op["_status"] == "started":
            setup = 0
        else:
            setup = SETUP_SAME_MOLD_MIN if op["Mold"] == mold else SETUP_MOLD_CHANGE_MIN
        if op["_status"] != "started" and op["_requested_start"] and op["_requested_start"] > t:
            t = next_working(op["_requested_start"])
        start = t
        run = op["_remaining"] * op["_cycle_s"] / 60 / PLANNING_EFFICIENCY
        end = add_working_minutes(start, setup + run)
        op.update({"_start": start, "_end": end, "OpSetupDurationMin": setup,
                   "OpErlstSchedldExecStrtDte": start.date(), "OpErlstSchedldExecStrtTme": fmt_time(start),
                   "OpErlstSchedldExecEndDte": end.date(), "OpErlstSchedldExecEndTme": fmt_time(end)})
        t, mold = end, op["Mold"]


def schedule_all(w) -> None:
    for wc in {op["WorkCenter"] for op in w.t("prodop").values()}:
        schedule_workcenter(w, wc)


OP_EDITABLE = {"WorkCenter", "OpErlstSchedldExecStrtDte", "OpErlstSchedldExecStrtTme"}


def update_operation(w, key: tuple, row: dict, changes: dict, user: str) -> None:
    bad = set(changes) - OP_EDITABLE
    if bad:
        raise BusinessError("CO/140", f"Campi non modificabili: {', '.join(sorted(bad))}")
    if row["_status"] == "done":
        raise BusinessError("CO/141", "Operazione già completata")
    old_wc = row["WorkCenter"]
    if "WorkCenter" in changes:
        new = changes["WorkCenter"]
        wc = w.t("workcenter").get((new,))
        if not wc or wc["Plant"] != row["Plant"]:
            raise BusinessError("CR/001", f"Centro di lavoro {new} inesistente nella divisione {row['Plant']}")
        if wc["_blocked"]:
            raise BusinessError("CR/014", f"Centro di lavoro {new} bloccato per la pianificazione")
        mat = w.get("prodorder", (row["ManufacturingOrder"],))["Material"]
        if new not in eligible_workcenters(w, mat):
            raise BusinessError("CO/120", f"Il centro di lavoro {new} non è idoneo per lo stampo {row['Mold']}")
        if row["_status"] == "started" and new != old_wc:
            raise BusinessError("CO/142", "Operazione in corso: confermare la quantità prodotta prima di spostarla")
    if "OpErlstSchedldExecStrtDte" in changes:
        d = changes["OpErlstSchedldExecStrtDte"]
        tm = changes.get("OpErlstSchedldExecStrtTme") or "PT06H00M00S"
        h, m = int(tm[2:4]), int(tm[5:7])
        changes = {**changes, "_requested_start": dt.datetime.combine(d, dt.time(h, m))}
    w.update("prodop", key, changes, user)
    schedule_workcenter(w, row["WorkCenter"])
    if old_wc != row["WorkCenter"]:
        schedule_workcenter(w, old_wc)
        w.event("Pianificazione", f"Ordine {row['ManufacturingOrder']} spostato da {old_wc} a {row['WorkCenter']} da {user}", row["ManufacturingOrder"])


# ---------------------------------------------------------------- conferme

def confirm_operation(w, body: dict, user: str) -> tuple:
    order, opnum = body.get("OrderID") or body.get("ManufacturingOrder"), body.get("OrderOperation") or "0010"
    if not order:
        raise BusinessError("RU/001", "Ordine mancante")
    o = w.get("prodorder", (order,))
    op = w.get("prodop", (order, opnum))
    if o["OrderSystemStatus"] not in ("REL", "PCNF"):
        raise BusinessError("RU/002", f"Ordine {order} non rilasciato")
    yield_q = float(body.get("ConfirmationYieldQuantity", 0))
    scrap_q = float(body.get("ConfirmationScrapQuantity", 0))
    if yield_q < 0 or scrap_q < 0 or yield_q + scrap_q == 0:
        raise BusinessError("RU/003", "Quantità confermate non valide")
    conf = w.number("pconf", 80000)
    real_factor = body.get("_real_factor", 1.0)
    errors = []
    for (oid, item), c in w.t("prodcomp").items():  # prelievo a consuntivo (backflush)
        if oid != order:
            continue
        qty = round((yield_q + scrap_q) * c["_per_piece"], 3)
        line = w.get("workcenter", (op["WorkCenter"],))["_line"]
        try:
            inv.goods_issue(w, "261", c["Material"], qty, user, {"ManufacturingOrder": order}, ("0100",),
                            f"Prelievo a consuntivo ordine {order}", real_factor=real_factor)
            c["WithdrawnQuantity"] = round(c["WithdrawnQuantity"] + qty, 3)
        except BusinessError as e:
            errors.append(c["Material"])
            w.insert("cogi", (w.number("cogi", 1),), {"ManufacturingOrder": order, "Material": c["Material"], "Quantity": qty,
                                                      "Message": e.message, "CreatedOn": w.today}, user, quiet=True)
            w.event("Produzione", f"Errore di prelievo a consuntivo su {order}: {e.message}", order)
    if yield_q > 0:
        inv.goods_receipt(w, o["Material"], yield_q, user, {"ManufacturingOrder": order}, f"Entrata merci ordine {order}")
    # La pressa simulata scala da sola i pezzi residui; una conferma manuale li scala qui.
    remaining = op["_remaining"] if body.get("_from_machine") else max(0.0, op["_remaining"] - yield_q - scrap_q)
    final = body.get("FinalConfirmationType") == "X" or remaining <= 0
    w.update("prodop", (order, opnum), {
        "OpTotalConfirmedYieldQty": round(op["OpTotalConfirmedYieldQty"] + yield_q, 3),
        "OpTotalConfirmedScrapQty": round(op["OpTotalConfirmedScrapQty"] + scrap_q, 3),
        "OperationStatus": "Confermata" if final else "Parzialmente confermata",
        "_status": "done" if final else op["_status"], "_remaining": 0 if final else remaining}, user, quiet=True)
    gr = round(o["_gr_qty"] + yield_q, 3)
    status = "TECO" if final else "PCNF"
    w.update("prodorder", (order,), {"MfgOrderConfirmedYieldQty": round(o["MfgOrderConfirmedYieldQty"] + yield_q, 3),
                                     "MfgOrderConfirmedScrapQty": round(o["MfgOrderConfirmedScrapQty"] + scrap_q, 3),
                                     "_gr_qty": gr, "OrderSystemStatus": status}, user)
    w.insert("pconf", (conf,), {"ConfirmationGroup": conf, "OrderID": order, "OrderOperation": opnum,
                                "ConfirmationYieldQuantity": yield_q, "ConfirmationScrapQuantity": scrap_q,
                                "FinalConfirmationType": "X" if final else "", "PostingDate": w.today,
                                "WorkCenter": op["WorkCenter"], "ConfirmationText": body.get("ConfirmationText", ""),
                                "BackflushErrors": ", ".join(errors)}, user)
    if final:
        schedule_workcenter(w, op["WorkCenter"])
    return (conf,)


# ---------------------------------------------------------------- spedizioni e capacità

def ship_daily(w, user: str = "SPEDIZIONI") -> None:
    """Spedisce ogni giorno lavorativo un quinto del fabbisogno settimanale (movimento 601)."""
    if w.today.weekday() >= 5:
        return
    for key, pir in w.t("pir").items():
        if pir["WorkingDayDate"] != monday(w.today):
            continue
        qty = round(pir["PlannedQuantity"] / 5, -1)
        have = inv.stock_qty(w, pir["Product"], exclude_types=("9020", "9010"))
        shipped = min(qty, have)
        if shipped > 0:
            inv.goods_issue(w, "601", pir["Product"], shipped, user, {}, ("0020",), "Uscita merci per consegna al cliente")
        w.update("pir", key, {"_consumed": pir.get("_consumed", 0) + shipped}, user, quiet=True)
        w.kpi["shipped"] = w.kpi.get("shipped", 0) + shipped
        if shipped + 1e-9 < qty:
            w.kpi["late_pieces"] = w.kpi.get("late_pieces", 0) + (qty - shipped)
            w.event("Spedizioni", f"Consegna incompleta di {pir['Product']}: spediti {shipped:g} su {qty:g}", pir["Product"])


def capacity_rows(w, days: int = 5) -> list[dict]:
    rows = []
    day0 = w.today
    for wc_key, wc in sorted(w.t("workcenter").items()):
        if wc["WorkCenterCategoryCode"] != "0001":
            continue
        loads = []
        for d in range(days):
            day = day0 + dt.timedelta(days=d)
            lo, hi = dt.datetime.combine(day, dt.time(0)), dt.datetime.combine(day + dt.timedelta(days=1), dt.time(0))
            mins = 0.0
            for op in w.t("prodop").values():
                if op["WorkCenter"] == wc_key[0] and op["_status"] != "done" and op["_start"]:
                    overlap = (min(op["_end"], hi) - max(op["_start"], lo)).total_seconds() / 60
                    mins += max(0.0, overlap)
            avail = 24 * 60 if day.weekday() < 5 else 0
            loads.append(round(100 * mins / avail) if avail else 0)
        rows.append({"WorkCenter": wc_key[0], "WorkCenterDesc": wc["WorkCenterDesc"], "Line": wc["_line"],
                     **{f"Day{d}": loads[d] for d in range(days)},
                     **{f"Day{d}Date": (day0 + dt.timedelta(days=d)) for d in range(days)},
                     "_etag": "", "_by": "SEED", "_at": w.start, "_changed_by": "SEED"})
    return rows


def retry_backflush_errors(w, user: str = "PIANIFICATORE") -> None:
    """Rielabora gli errori di prelievo a consuntivo (come la transazione COGI)."""
    for key, c in list(w.t("cogi").items()):
        try:
            inv.goods_issue(w, "261", c["Material"], c["Quantity"], user, {"ManufacturingOrder": c["ManufacturingOrder"]},
                            ("0100",), f"Rielaborazione prelievo ordine {c['ManufacturingOrder']}")
        except BusinessError:
            continue
        for (oid, item), comp in w.t("prodcomp").items():
            if oid == c["ManufacturingOrder"] and comp["Material"] == c["Material"]:
                comp["WithdrawnQuantity"] = round(comp["WithdrawnQuantity"] + c["Quantity"], 3)
                break
        w.delete("cogi", key, user, quiet=True)


def weekly_requirements(w) -> dict[str, float]:
    """Fabbisogno settimanale medio di ogni materiale (prodotti finiti e componenti)."""
    out: dict[str, float] = {}
    weeks = {}
    for p in w.t("pir").values():
        weeks.setdefault(p["Product"], []).append(p["PlannedQuantity"])
    for mat, qs in weeks.items():
        avg = sum(qs) / len(qs)
        out[mat] = out.get(mat, 0) + avg
        for it in w.get("bom", (mat,))["Items"]:
            out[it["BillOfMaterialComponent"]] = out.get(it["BillOfMaterialComponent"], 0) + avg * it["BillOfMaterialItemQuantity"]
    return out
