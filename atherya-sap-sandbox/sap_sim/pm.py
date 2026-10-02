"""Manutenzione (PM): avvisi, ordini con operazioni e componenti, rilascio, esecuzione, conferme,
chiusura tecnica, piani di manutenzione, punti e documenti di misura."""

import datetime as dt

from . import inventory as inv
from .masterdata import PLANT
from .world import BusinessError

PHASE = {"1": "Aperto", "2": "Rinviato", "3": "In lavorazione", "4": "Completato"}


def _required(body: dict, fields: list[str]) -> None:
    gone = [f for f in fields if body.get(f) in (None, "")]
    if gone:
        raise BusinessError("IW/001", f"Campi obbligatori mancanti: {', '.join(gone)}")


def _technical_object(w, obj: str, plant: str) -> dict:
    eq = w.t("equipment").get((obj,))
    if eq and eq["MaintenancePlanningPlant"] == plant:
        return eq
    fl = w.t("funcloc").get((obj,))
    if fl and plant == PLANT:
        return fl
    raise BusinessError("IW/002", f"Oggetto tecnico {obj} non esiste nella divisione {plant}")


# ---------------------------------------------------------------- avvisi

def create_notification(w, body: dict, user: str) -> tuple:
    _required(body, ["NotificationText", "NotificationType", "TechnicalObject", "MaintenancePlanningPlant"])
    _technical_object(w, body["TechnicalObject"], body["MaintenancePlanningPlant"])
    if body["NotificationType"] not in ("M1", "M2", "M3"):
        raise BusinessError("IW/005", f"Tipo avviso {body['NotificationType']} non previsto")
    num = w.number("notif", 10000100)
    row = dict(body)
    row.update({"MaintenanceNotification": num, "MaintNotifProcessPhaseCode": "1",
                "NotificationCreationDate": w.today, "NotificationCreationTime": w.now.time(),
                "MaintPriority": body.get("MaintPriority", "3"), "IsBreakdown": bool(body.get("IsBreakdown", False)),
                "MaintenanceOrder": ""})
    w.insert("notif", (num,), row, user)
    return (num,)


NOTIF_EDITABLE = {"NotificationText", "MaintNotifLongText", "MaintPriority", "IsDeleted", "MaintNotifProcessPhaseCode", "YY1_AtheryaRef"}


def update_notification(w, key: tuple, row: dict, changes: dict, user: str) -> None:
    bad = set(changes) - NOTIF_EDITABLE
    if bad:
        raise BusinessError("IW/010", f"Campi non modificabili: {', '.join(sorted(bad))}")
    if changes.get("MaintNotifProcessPhaseCode") and changes["MaintNotifProcessPhaseCode"] not in PHASE:
        raise BusinessError("IW/011", "Fase dell'avviso non valida")
    w.update("notif", key, changes, user)


def complete_notification(w, num: str, user: str) -> None:
    n = w.get("notif", (num,))
    if n.get("MaintNotifProcessPhaseCode") == "4":
        raise BusinessError("IW/012", f"Avviso {num} già completato")
    w.update("notif", (num,), {"MaintNotifProcessPhaseCode": "4", "NotificationCompletionDate": w.today}, user)


# ---------------------------------------------------------------- ordini

def create_order(w, body: dict, user: str) -> tuple:
    _required(body, ["MaintenanceOrderType", "MaintenanceOrderDesc", "Equipment", "MaintenancePlanningPlant",
                     "MainWorkCenter", "MaintOrdBasicStartDate"])
    if body["MaintenanceOrderType"] not in ("PM01", "PM02", "PM03"):
        raise BusinessError("IW/020", f"Tipo ordine {body['MaintenanceOrderType']} non previsto")
    _technical_object(w, body["Equipment"], body["MaintenancePlanningPlant"])
    wc = w.t("workcenter").get((body["MainWorkCenter"],))
    if not wc:
        raise BusinessError("IW/003", f"Centro di lavoro {body['MainWorkCenter']} inesistente")
    notif = body.get("MaintenanceNotification")
    if notif:
        n = w.t("notif").get((notif,))
        if not n:
            raise BusinessError("IW/004", f"Avviso {notif} inesistente")
        if n.get("MaintenanceOrder"):
            raise BusinessError("IW/006", f"L'avviso {notif} ha già l'ordine {n['MaintenanceOrder']}")
    if body.get("MaintOrdSystemStatus") == "REL" and not has_release(w, user):
        raise BusinessError("IW/REL", "Nessuna autorizzazione al rilascio dell'ordine", 403)
    ops = (body.get("to_MaintenanceOrderOperation") or {}).get("results") or [
        {"OperationDescription": body["MaintenanceOrderDesc"], "WorkCenter": body["MainWorkCenter"],
         "PlannedWorkQuantity": 3.0 if body["MaintenanceOrderType"] == "PM01" else 2.0, "WorkQuantityUnit": "H"}]
    comps = (body.get("to_MaintenanceOrderComponent") or {}).get("results") or []
    for c in comps:
        p = w.t("product").get((c.get("Material", ""),))
        if not p or p["Plant"] != body["MaintenancePlanningPlant"]:
            raise BusinessError("M3/351", f"Materiale {c.get('Material')} non esteso alla divisione {body['MaintenancePlanningPlant']}")
    num = w.number("order", 4000100)
    start = body["MaintOrdBasicStartDate"]
    hours = sum(float(o.get("PlannedWorkQuantity", 2)) for o in ops)
    row = {k: v for k, v in body.items() if k not in ("to_MaintenanceOrderOperation", "to_MaintenanceOrderComponent")}
    row.update({
        "MaintenanceOrder": num, "MaintOrdSystemStatus": body.get("MaintOrdSystemStatus", "CRTD"),
        "MaintOrdBasicEndDate": start + dt.timedelta(days=max(0, int(hours // 16))),
        "MaintPriority": body.get("MaintPriority", "3"),
        "to_MaintenanceOrderOperation": {"results": [
            {"MaintenanceOrder": num, "MaintenanceOrderOperation": f"{(i + 1) * 10:04d}",
             "OperationDescription": o.get("OperationDescription", ""), "WorkCenter": o.get("WorkCenter", body["MainWorkCenter"]),
             "PlannedWorkQuantity": float(o.get("PlannedWorkQuantity", 2)), "WorkQuantityUnit": "H", "ActualWorkQuantity": 0.0}
            for i, o in enumerate(ops)]},
        "to_MaintenanceOrderComponent": {"results": [
            {"MaintenanceOrder": num, "MaintenanceOrderComponent": f"{(i + 1) * 10:04d}", "Material": c["Material"],
             "RequiredQuantity": float(c.get("RequiredQuantity", 1)), "WithdrawnQuantity": 0.0}
            for i, c in enumerate(comps)]},
        "_exec": None,
    })
    w.insert("morder", (num,), row, user)
    if notif:
        w.update("notif", (notif,), {"MaintenanceOrder": num, "MaintNotifProcessPhaseCode": "3"}, user)
    return (num,)


ORDER_EDITABLE = {"MaintenanceOrderDesc", "MaintOrdBasicStartDate", "MaintPriority", "MainWorkCenter", "IsDeleted",
                  "MaintOrdSystemStatus", "YY1_AtheryaRef"}


def has_release(w, user: str) -> bool:
    return user in ("MANUTENZIONE",) or "API_MAINTENANCEORDER:release" in w.users.get(user, {}).get("auths", set())


def update_order(w, key: tuple, row: dict, changes: dict, user: str) -> None:
    bad = set(changes) - ORDER_EDITABLE
    if bad:
        raise BusinessError("IW/021", f"Campi non modificabili: {', '.join(sorted(bad))}")
    if row["MaintOrdSystemStatus"] in ("TECO",) and set(changes) - {"IsDeleted"}:
        raise BusinessError("IW/022", "Ordine chiuso tecnicamente: non modificabile")
    status = changes.get("MaintOrdSystemStatus")
    if status:
        if status == "REL":
            release_order(w, key[0], user)
            changes = {k: v for k, v in changes.items() if k != "MaintOrdSystemStatus"}
            if not changes:
                return
        elif status != row["MaintOrdSystemStatus"]:
            raise BusinessError("IW/023", "Cambio di stato consentito solo tramite le funzioni previste")
    if changes.get("IsDeleted") and row.get("_exec"):
        raise BusinessError("IW/024", "Ordine in esecuzione: non può essere cancellato")
    w.update("morder", key, changes, user)


def release_order(w, num: str, user: str) -> None:
    if not has_release(w, user):
        raise BusinessError("IW/REL", "Nessuna autorizzazione al rilascio dell'ordine", 403)
    o = w.get("morder", (num,))
    if o.get("IsDeleted"):
        raise BusinessError("IW/DEL", "Ordine contrassegnato per la cancellazione: rilascio impossibile")
    if o.get("_locked"):
        raise BusinessError("IW/LKD", f"Ordine {num} bloccato (LKD): rilascio impossibile")
    if o["MaintOrdSystemStatus"] != "CRTD":
        raise BusinessError("IW/025", f"Ordine {num} non in stato Creato")
    w.update("morder", (num,), {"MaintOrdSystemStatus": "REL", "MaintOrdReleaseDate": w.today}, user)
    w.event("Manutenzione", f"Ordine {num} rilasciato da {user}", num)


def technically_complete(w, num: str, user: str) -> None:
    o = w.get("morder", (num,))
    if o["MaintOrdSystemStatus"] not in ("REL",):
        raise BusinessError("IW/026", "Solo un ordine rilasciato può essere chiuso tecnicamente")
    w.update("morder", (num,), {"MaintOrdSystemStatus": "TECO", "MaintOrdTechCompletionDate": w.today}, user)
    if o.get("MaintenanceNotification"):
        n = w.t("notif").get((o["MaintenanceNotification"],))
        if n and n.get("MaintNotifProcessPhaseCode") != "4":
            complete_notification(w, o["MaintenanceNotification"], user)


def confirm_order(w, body: dict, user: str) -> tuple:
    _required(body, ["MaintenanceOrder", "MaintenanceOrderOperation", "ActualWorkQuantity"])
    o = w.get("morder", (body["MaintenanceOrder"],))
    if o["MaintOrdSystemStatus"] != "REL":
        raise BusinessError("IW/030", "Si può confermare solo un ordine rilasciato")
    ops = o["to_MaintenanceOrderOperation"]["results"]
    op = next((x for x in ops if x["MaintenanceOrderOperation"] == body["MaintenanceOrderOperation"]), None)
    if not op:
        raise BusinessError("IW/031", "Operazione inesistente")
    op["ActualWorkQuantity"] = round(op["ActualWorkQuantity"] + float(body["ActualWorkQuantity"]), 2)
    conf = w.number("mconf", 60000)
    w.insert("mconf", (conf,), {"MaintOrderConf": conf, "MaintenanceOrder": o["MaintenanceOrder"],
                                "MaintenanceOrderOperation": op["MaintenanceOrderOperation"],
                                "ActualWorkQuantity": float(body["ActualWorkQuantity"]), "ActualWorkQuantityUnit": "H",
                                "IsFinalConfirmation": bool(body.get("IsFinalConfirmation", False)),
                                "ConfirmationText": body.get("ConfirmationText", ""), "PostingDate": w.today}, user)
    w.update("morder", (o["MaintenanceOrder"],), {"to_MaintenanceOrderOperation": o["to_MaintenanceOrderOperation"]}, user)
    if body.get("IsFinalConfirmation") and all(x["ActualWorkQuantity"] > 0 for x in ops):
        technically_complete(w, o["MaintenanceOrder"], user)
    return (conf,)


# ---------------------------------------------------------------- punti e documenti di misura

def create_measurement(w, body: dict, user: str) -> tuple:
    _required(body, ["MeasuringPoint", "MeasurementReading"])
    mp = w.t("measpoint").get((body["MeasuringPoint"],))
    if not mp:
        raise BusinessError("IR/001", f"Punto di misura {body['MeasuringPoint']} inesistente")
    doc = w.number("measdoc", 900000)
    w.insert("measdoc", (doc,), {"MeasurementDocument": doc, "MeasuringPoint": body["MeasuringPoint"],
                                 "TechnicalObject": mp["TechnicalObject"], "MeasurementReading": float(body["MeasurementReading"]),
                                 "MeasurementReadingUnit": mp["MeasurementRangeUnit"],
                                 "MeasurementReadingDate": w.today, "MeasurementReadingTime": w.now.time(),
                                 "MeasurementDocumentText": body.get("MeasurementDocumentText", "")}, user, quiet=True)
    return (doc,)


# ---------------------------------------------------------------- piani di manutenzione

def schedule_plans(w, horizon_days: int = 7) -> None:
    """Schedulazione dei piani: crea l'ordine preventivo quando la scadenza entra nell'orizzonte."""
    for key, plan in w.t("maintplan").items():
        due = plan["NextPlannedDate"]
        if due > w.today + dt.timedelta(days=horizon_days):
            continue
        num = create_order(w, {
            "MaintenanceOrderType": plan["MaintenanceOrderType"], "MaintenanceOrderDesc": plan["MaintenancePlanDesc"],
            "Equipment": plan["Equipment"], "MaintenancePlanningPlant": plan["MaintenancePlanningPlant"],
            "MainWorkCenter": plan["MainWorkCenter"], "MaintOrdBasicStartDate": due, "MaintenancePlan": plan["MaintenancePlan"],
            "to_MaintenanceOrderOperation": {"results": [{"OperationDescription": plan["MaintenancePlanDesc"],
                                                          "WorkCenter": plan["MainWorkCenter"], "PlannedWorkQuantity": plan["PlannedWorkHours"]}]},
            "to_MaintenanceOrderComponent": {"results": [{"Material": c["Material"], "RequiredQuantity": c["Quantity"]}
                                                         for c in plan["Components"]]},
        }, "BATCH")[0]
        w.update("maintplan", key, {"NextPlannedDate": due + dt.timedelta(days=plan["MaintPlanCycleDays"]),
                                    "LastCallDate": w.today, "LastCallOrder": num}, "BATCH")
        w.event("Manutenzione", f"Piano {plan['MaintenancePlan']}: creato ordine preventivo {num} per il {due:%d/%m}", num)


# ---------------------------------------------------------------- esecuzione (squadra di manutenzione simulata)

def required_spare(w, order: dict) -> tuple[str, float] | None:
    """Ricambio necessario per un intervento correttivo o di sostituzione su una pressa."""
    eq = w.t("equipment").get((order["Equipment"],))
    if not eq or order["MaintenanceOrderType"] == "PM02":
        return None
    comps = order["to_MaintenanceOrderComponent"]["results"]
    seal = f"GUARN-HYD-{eq['_tonnage']}"
    if any(c["Material"] == seal for c in comps):
        return None  # già previsto come componente
    text = (order.get("MaintenanceOrderDesc", "") + " " + str(order.get("MaintenanceNotification", ""))).lower()
    if order["MaintenanceOrderType"] == "PM01" and ("guarniz" in text or order.get("_breakdown")):
        return seal, 1.0
    return None


def _withdraw_spare(w, order: dict, mat: str, qty: float, user: str) -> bool:
    for key, res in w.t("reservation").items():  # prima una prenotazione aperta per quel ricambio
        for idx, it in enumerate(res["to_ReservationDocumentItem"]["results"]):
            open_qty = float(it["ResvnItmRequiredQtyInBaseUnit"]) - float(it["ResvnItmWithdrawnQtyInBaseUnit"])
            if it["Material"] == mat and open_qty >= qty and inv.stock_qty(w, mat) >= qty:
                inv.withdraw_reservation_item(w, key[0], idx, qty, user, {"MaintenanceOrder": order["MaintenanceOrder"]})
                return True
    if inv.stock_qty(w, mat) >= qty:
        inv.goods_issue(w, "261", mat, qty, user, {"MaintenanceOrder": order["MaintenanceOrder"]}, ("0030",),
                        f"Prelievo per ordine {order['MaintenanceOrder']}")
        return True
    return False


def try_start(w, num: str, user: str = "MANUTENTORE") -> bool:
    o = w.get("morder", (num,))
    if o["MaintOrdSystemStatus"] != "REL" or o.get("_exec") or o.get("IsDeleted") or o.get("_locked"):
        return False
    spare = required_spare(w, o)
    needs = [(c["Material"], c["RequiredQuantity"] - c["WithdrawnQuantity"]) for c in o["to_MaintenanceOrderComponent"]["results"]
             if c["RequiredQuantity"] > c["WithdrawnQuantity"]]
    if spare:
        needs.append(spare)
    missing = [m for m, q in needs if inv.stock_qty(w, m) < q]
    if missing:
        if not o.get("_waiting"):
            w.update("morder", (num,), {"_waiting": True}, user)
            w.event("Manutenzione", f"Ordine {num} fermo: manca il ricambio {', '.join(missing)}", num)
        return False
    for c in o["to_MaintenanceOrderComponent"]["results"]:
        q = c["RequiredQuantity"] - c["WithdrawnQuantity"]
        if q > 0:
            _withdraw_spare(w, o, c["Material"], q, user)
            c["WithdrawnQuantity"] = c["RequiredQuantity"]
    if spare:
        _withdraw_spare(w, o, spare[0], spare[1], user)
    minutes = sum(op["PlannedWorkQuantity"] for op in o["to_MaintenanceOrderOperation"]["results"]) * 60
    w.update("morder", (num,), {"_exec": {"started": w.now, "left": minutes, "spare": spare[0] if spare else None},
                                "_waiting": False, "to_MaintenanceOrderComponent": o["to_MaintenanceOrderComponent"]}, user)
    w.event("Manutenzione", f"Iniziato l'intervento {num} su {o['Equipment']}", num)
    return True


def finish(w, num: str, user: str = "MANUTENTORE") -> None:
    o = w.get("morder", (num,))
    for op in o["to_MaintenanceOrderOperation"]["results"]:
        confirm_order(w, {"MaintenanceOrder": num, "MaintenanceOrderOperation": op["MaintenanceOrderOperation"],
                          "ActualWorkQuantity": op["PlannedWorkQuantity"], "IsFinalConfirmation": False,
                          "ConfirmationText": "Intervento eseguito"}, user)
    w.update("morder", (num,), {"_exec": None}, user)
    technically_complete(w, num, user)
    w.event("Manutenzione", f"Chiuso l'intervento {num} su {o['Equipment']}", num)
