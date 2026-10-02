"""App Fiori della simulazione: cosa mostrare e quali azioni offrire, per area.

Le azioni sono quelle di un utente umano (PLANNER, o il ruolo indicato) e passano dalle stesse
regole di business delle API.
"""

import datetime as dt

from . import inventory as inv
from . import pm, pp
from .world import BusinessError

S_ORDER = {"CRTD": ["Creato", "Information"], "REL": ["Rilasciato", "Success"], "PCNF": ["Parzialmente confermato", "Warning"],
           "TECO": ["Chiuso tecnicamente", "None"]}
S_PHASE = {"1": ["Aperto", "Warning"], "2": ["Rinviato", "None"], "3": ["In lavorazione", "Information"], "4": ["Completato", "Success"]}
S_TASK = {"": ["Aperto", "Warning"], "C": ["Confermato", "Success"]}
S_MACHINE = {"RUN": ["In produzione", "Success"], "IDLE": ["Ferma, senza ordini", "None"], "DOWN": ["Guasto", "Error"],
             "MAINT": ["In manutenzione", "Warning"]}
S_PR = {"N": ["Aperta", "Warning"], "B": ["Ordinata", "Information"], "C": ["Consegnata", "Success"]}
S_PI = {"Aperto": ["Aperto", "Warning"], "Contato": ["Contato", "Information"], "Registrato": ["Registrato", "Success"]}
S_BOOL = {"true": ["Sì", "Error"], "false": ["No", "None"]}


def col(label, field, kind="text", sub=None, smap=None):
    c = {"label": label, "field": field, "kind": kind}
    if sub:
        c["sub"] = sub
    if smap:
        c["map"] = smap
    return c


def _items_first(field, qty, date):
    def flat(w, r):
        items = r.get(field, {}).get("results", []) if isinstance(r.get(field), dict) else r.get(field, [])
        first = items[0] if items else {}
        mat = first.get("Material", "")
        r = dict(r)
        r.update({"ItemMaterial": mat, "ItemMaterialDesc": w.t("product").get((mat,), {}).get("ProductDescription", ""),
                  "ItemQuantity": first.get(qty, ""), "ItemDate": first.get(date) or first.get("_delivery"),
                  "ItemStatus": first.get("ProcessingStatus", ""), "Items": items})
        return r
    return flat


def _eq_desc(field):
    def flat(w, r):
        r = dict(r)
        r["EquipmentName"] = w.t("equipment").get((r.get(field),), {}).get("EquipmentName", "")
        return r
    return flat


def _prodop(w, r):
    r = dict(r)
    o = w.get("prodorder", (r["ManufacturingOrder"],))
    r.update({"Material": o["Material"], "OrderStatus": o["OrderSystemStatus"], "Remaining": round(r["_remaining"]),
              "Start": r["_start"], "End": r["_end"], "Due": r["_due"], "LastChangedBy": r["_changed_by"]})
    return r


def _machines(w):
    rows = []
    for wc, m in sorted(w.machines.items()):
        op = next((o for o in w.t("prodop").values() if o["WorkCenter"] == wc and o["_status"] == "started"), None)
        rows.append({"WorkCenter": wc, "Equipment": m["equipment"], "Line": m["line"], "State": m["state"],
                     "Wear": round(m["wear"], 1), "FailAt": m["fail_at"], "Temperature": round(m["temp"], 1),
                     "Pressure": round(m["pressure"], 1), "Mold": m["mold"] or "", "CurrentOrder": op["ManufacturingOrder"] if op else "",
                     "Cycles": m["cycles"], "_by": "SIM", "_changed_by": "SIM", "_at": w.now})
    return rows


APPS = [
    # ---------------- manutenzione
    {"id": "notifications", "group": "Manutenzione", "title": "Avvisi di manutenzione", "sub": "Gestire avvisi", "icon": "sap-icon://alert",
     "table": "notif", "key": ["MaintenanceNotification"], "flatten": _eq_desc("TechnicalObject"),
     "cols": [col("Avviso", "MaintenanceNotification", "id", "NotificationText"), col("Tipo", "NotificationType"),
              col("Oggetto tecnico", "TechnicalObject", "id", "EquipmentName"), col("Guasto", "IsBreakdown", "status", smap=S_BOOL),
              col("Priorità", "MaintPriority"), col("Creato il", "NotificationCreationDate", "date"), col("Ordine", "MaintenanceOrder"),
              col("Creato da", "_by", "user"), col("Stato", "MaintNotifProcessPhaseCode", "status", smap=S_PHASE)],
     "actions": [{"id": "notif_order", "label": "Crea ordine", "when": {"MaintenanceOrder": [""], "MaintNotifProcessPhaseCode": ["1"]}},
                 {"id": "notif_complete", "label": "Completa", "when": {"MaintNotifProcessPhaseCode": ["1", "3"]}}]},
    {"id": "maintOrders", "group": "Manutenzione", "title": "Ordini di manutenzione", "sub": "Gestire ordini", "icon": "sap-icon://wrench",
     "table": "morder", "key": ["MaintenanceOrder"], "flatten": _eq_desc("Equipment"),
     "cols": [col("Ordine", "MaintenanceOrder", "id", "MaintenanceOrderDesc"), col("Tipo", "MaintenanceOrderType"),
              col("Apparecchiatura", "Equipment", "id", "EquipmentName"), col("Inizio", "MaintOrdBasicStartDate", "date"),
              col("Avviso", "MaintenanceNotification"), col("Creato da", "_by", "user"),
              col("Stato", "MaintOrdSystemStatus", "status", smap=S_ORDER)],
     "children": [{"field": "to_MaintenanceOrderOperation", "title": "Operazioni"}, {"field": "to_MaintenanceOrderComponent", "title": "Componenti"}],
     "actions": [{"id": "morder_release", "label": "Rilascia", "when": {"MaintOrdSystemStatus": ["CRTD"]}},
                 {"id": "morder_teco", "label": "Chiudi tecnicamente", "when": {"MaintOrdSystemStatus": ["REL"]}}]},
    {"id": "maintConfs", "group": "Manutenzione", "title": "Conferme di manutenzione", "sub": "Visualizzare", "icon": "sap-icon://accept",
     "table": "mconf", "key": ["MaintOrderConf"],
     "cols": [col("Conferma", "MaintOrderConf", "id"), col("Ordine", "MaintenanceOrder"), col("Operazione", "MaintenanceOrderOperation"),
              col("Ore effettive", "ActualWorkQuantity", "number"), col("Data", "PostingDate", "date"), col("Utente", "_by", "user")]},
    {"id": "maintPlans", "group": "Manutenzione", "title": "Piani di manutenzione", "sub": "Visualizzare piani", "icon": "sap-icon://appointment",
     "table": "maintplan", "key": ["MaintenancePlan"], "flatten": _eq_desc("Equipment"),
     "cols": [col("Piano", "MaintenancePlan", "id", "MaintenancePlanDesc"), col("Apparecchiatura", "Equipment", "id", "EquipmentName"),
              col("Ciclo (giorni)", "MaintPlanCycleDays", "number"), col("Prossima scadenza", "NextPlannedDate", "date"),
              col("Ultimo ordine", "LastCallOrder")]},
    {"id": "equipment", "group": "Manutenzione", "title": "Apparecchiature", "sub": "Visualizzare", "icon": "sap-icon://machine",
     "table": "equipment", "key": ["Equipment"],
     "cols": [col("Apparecchiatura", "Equipment", "id", "EquipmentName"), col("Sede tecnica", "FunctionalLocation"),
              col("Centro di lavoro", "_wc"), col("Centro di manutenzione", "MainWorkCenter")]},
    {"id": "measDocs", "group": "Manutenzione", "title": "Documenti di misura", "sub": "Ultime letture", "icon": "sap-icon://measuring-point",
     "table": "measdoc", "key": ["MeasurementDocument"], "newest": True, "inactive": True,
     "cols": [col("Documento", "MeasurementDocument", "id"), col("Punto di misura", "MeasuringPoint"), col("Oggetto tecnico", "TechnicalObject"),
              col("Lettura", "MeasurementReading", "number"), col("Unità", "MeasurementReadingUnit"),
              col("Data", "MeasurementReadingDate", "date"), col("Ora", "MeasurementReadingTime"), col("Utente", "_by", "user")]},
    # ---------------- produzione
    {"id": "pirs", "group": "Produzione", "title": "Fabbisogni indipendenti", "sub": "Domanda per settimana", "icon": "sap-icon://customer-order-entry",
     "table": "pir", "key": ["Product", "Plant", "WorkingDayDate"], "inactive": True,
     "cols": [col("Prodotto", "Product", "id"), col("Settimana", "WorkingDayDate", "date"), col("Quantità", "PlannedQuantity", "number"),
              col("Già spedito", "_consumed", "number")]},
    {"id": "plannedOrders", "group": "Produzione", "title": "Ordini pianificati", "sub": "Risultato MRP", "icon": "sap-icon://create-form",
     "table": "plannedorder", "key": ["PlannedOrder"],
     "cols": [col("Ordine pianificato", "PlannedOrder", "id"), col("Materiale", "Material"), col("Quantità", "PlannedTotalQtyInBaseUnit", "number"),
              col("Inizio", "PlndOrderPlannedStartDate", "date"), col("Fine", "PlndOrderPlannedEndDate", "date")],
     "actions": [{"id": "planned_convert", "label": "Converti in ordine di produzione", "when": {}}]},
    {"id": "prodOrders", "group": "Produzione", "title": "Ordini di produzione", "sub": "Gestire ordini", "icon": "sap-icon://factory",
     "table": "prodorder", "key": ["ManufacturingOrder"],
     "cols": [col("Ordine", "ManufacturingOrder", "id", "Material"), col("Quantità", "MfgOrderPlannedTotalQty", "number"),
              col("Confermata", "MfgOrderConfirmedYieldQty", "number"), col("Scarto", "MfgOrderConfirmedScrapQty", "number"),
              col("Inizio", "MfgOrderPlannedStartDate", "date"), col("Fine", "MfgOrderPlannedEndDate", "date"),
              col("Parti mancanti", "MissingPartsFlag", "status", smap=S_BOOL), col("Stato", "OrderSystemStatus", "status", smap=S_ORDER)],
     "actions": [{"id": "prod_release", "label": "Rilascia", "when": {"OrderSystemStatus": ["CRTD"]}}]},
    {"id": "prodOps", "group": "Produzione", "title": "Sequenza per pressa", "sub": "Schedulazione dettagliata", "icon": "sap-icon://gantt-bars",
     "table": "prodop", "key": ["ManufacturingOrder", "ManufacturingOrderOperation"], "flatten": _prodop, "sort": "Start",
     "where": lambda r: r["_status"] != "done",
     "cols": [col("Pressa", "WorkCenter", "id"), col("Ordine", "ManufacturingOrder", "id", "Material"), col("Stampo", "Mold"),
              col("Residuo (pz)", "Remaining", "number"), col("Setup (min)", "OpSetupDurationMin", "number"),
              col("Inizio previsto", "Start", "datetime"), col("Fine prevista", "End", "datetime"), col("Consegna", "Due", "date"),
              col("Ultima modifica da", "LastChangedBy", "user"), col("Stato", "OperationStatus")],
     "actions": [{"id": "op_move", "label": "Cambia pressa", "when": {}}]},
    {"id": "prodConfs", "group": "Produzione", "title": "Conferme di produzione", "sub": "Visualizzare", "icon": "sap-icon://complete",
     "table": "pconf", "key": ["ConfirmationGroup"], "newest": True, "inactive": True,
     "cols": [col("Conferma", "ConfirmationGroup", "id"), col("Ordine", "OrderID"), col("Pressa", "WorkCenter"),
              col("Quantità buona", "ConfirmationYieldQuantity", "number"), col("Scarto", "ConfirmationScrapQuantity", "number"),
              col("Finale", "FinalConfirmationType"), col("Data", "PostingDate", "date"), col("Errori di prelievo", "BackflushErrors")]},
    {"id": "capacity", "group": "Produzione", "title": "Carico delle presse", "sub": "Prossimi 5 giorni, %", "icon": "sap-icon://capacity",
     "rows": lambda w: pp.capacity_rows(w), "key": ["WorkCenter"], "inactive": True,
     "cols": [col("Pressa", "WorkCenter", "id", "WorkCenterDesc"), col("Linea", "Line")] + [col(f"Giorno {d + 1}", f"Day{d}", "percent") for d in range(5)]},
    {"id": "cogi", "group": "Produzione", "title": "Errori di prelievo", "sub": "Da rielaborare", "icon": "sap-icon://error",
     "table": "cogi", "key": ["ManufacturingOrder", "Material"], "inactive": True,
     "cols": [col("Ordine", "ManufacturingOrder", "id"), col("Materiale", "Material"), col("Quantità", "Quantity", "number"),
              col("Messaggio", "Message"), col("Data", "CreatedOn", "date")]},
    # ---------------- magazzino e materiali
    {"id": "stockBins", "group": "Magazzino e materiali", "title": "Giacenze per ubicazione", "sub": "Magazzino W100", "icon": "sap-icon://inventory",
     "table": "quant", "key": ["EWMStorageBin", "Product"], "inactive": True,
     "cols": [col("Ubicazione", "EWMStorageBin", "id", "EWMStorageType"), col("Prodotto", "Product"),
              col("Quantità", "EWMStockQuantityInBaseUnit", "number"), col("Unità", "EWMStockQuantityBaseUnit")]},
    {"id": "stock", "group": "Magazzino e materiali", "title": "Giacenze di materiale", "sub": "Per divisione", "icon": "sap-icon://product",
     "rows": lambda w: inv.plant_stock_rows(w), "key": ["Material"], "inactive": True,
     "cols": [col("Materiale", "Material", "id"), col("Magazzino", "StorageLocation"), col("Libera utilizzazione", "MatlWrhsStkQtyInMatlBaseUnit", "number"),
              col("Unità", "MaterialBaseUnit")]},
    {"id": "whTasks", "group": "Magazzino e materiali", "title": "Compiti di magazzino", "sub": "Eseguire", "icon": "sap-icon://shipping-status",
     "table": "whtask", "key": ["WarehouseTask"], "newest": True,
     "cols": [col("Compito", "WarehouseTask", "id", "WarehouseProcessType"), col("Prodotto", "Product"),
              col("Quantità", "TargetQuantityInBaseUnit", "number"), col("Da", "SourceStorageBin"), col("A", "DestinationStorageBin"),
              col("Riferimento", "EWMReferenceDocument"), col("Creato", "WhseTaskCreationDateTime", "datetime"),
              col("Stato", "WarehouseTaskStatus", "status", smap=S_TASK)],
     "actions": [{"id": "task_confirm", "label": "Conferma", "when": {"WarehouseTaskStatus": [""]}}]},
    {"id": "matDocs", "group": "Magazzino e materiali", "title": "Documenti materiale", "sub": "Movimenti merci", "icon": "sap-icon://documents",
     "table": "matdoc", "key": ["MaterialDocument"], "newest": True, "flatten": _items_first("Items", "QuantityInEntryUnit", "PostingDate"),
     "cols": [col("Documento", "MaterialDocument", "id", "MaterialDocumentHeaderText"), col("Movimento", "GoodsMovementType"),
              col("Materiale", "ItemMaterial", "id", "ItemMaterialDesc"), col("Quantità", "ItemQuantity", "number"),
              col("Data", "PostingDate", "date"), col("Utente", "_by", "user")],
     "children": [{"field": "Items", "title": "Posizioni"}]},
    {"id": "reservations", "group": "Magazzino e materiali", "title": "Prenotazioni", "sub": "Visualizzare", "icon": "sap-icon://bookmark",
     "table": "reservation", "key": ["Reservation"], "flatten": _items_first("to_ReservationDocumentItem", "ResvnItmRequiredQtyInBaseUnit", "MatlCompRequirementDate"),
     "cols": [col("Prenotazione", "Reservation", "id"), col("Movimento", "GoodsMovementType"), col("Materiale", "ItemMaterial", "id", "ItemMaterialDesc"),
              col("Quantità", "ItemQuantity", "number"), col("Data fabbisogno", "ItemDate", "date"), col("Creato da", "_by", "user")],
     "children": [{"field": "Items", "title": "Posizioni"}]},
    {"id": "purchaseReqs", "group": "Magazzino e materiali", "title": "Richieste d'acquisto", "sub": "Senza ordini né fatture", "icon": "sap-icon://cart",
     "table": "preq", "key": ["PurchaseRequisition"], "newest": True, "flatten": _items_first("to_PurchaseReqnItem", "RequestedQuantity", "DeliveryDate"),
     "cols": [col("Richiesta", "PurchaseRequisition", "id"), col("Materiale", "ItemMaterial", "id", "ItemMaterialDesc"),
              col("Quantità", "ItemQuantity", "number"), col("Consegna", "ItemDate", "date"), col("Creato da", "_by", "user"),
              col("Stato", "ItemStatus", "status", smap=S_PR)],
     "children": [{"field": "Items", "title": "Posizioni"}]},
    {"id": "physInv", "group": "Magazzino e materiali", "title": "Inventario fisico", "sub": "Conte a rotazione", "icon": "sap-icon://inspection",
     "table": "physinv", "key": ["PhysicalInventoryDocument"], "newest": True, "inactive": True,
     "cols": [col("Documento", "PhysicalInventoryDocument", "id"), col("Ubicazione", "EWMStorageBin"), col("Prodotto", "Product"),
              col("Contabile", "BookQuantity", "number"), col("Contato", "CountedQuantity", "number"),
              col("Differenza", "DifferenceQuantity", "number"), col("Stato", "PhysInvtryStatus", "status", smap=S_PI)]},
    # ---------------- controllo
    {"id": "log", "group": "Controllo", "title": "Registro modifiche", "sub": "Chi ha fatto cosa", "icon": "sap-icon://history",
     "rows": lambda w: list(reversed(w.log)), "key": ["Id"], "inactive": True,
     "cols": [col("Data e ora", "ts", "datetime"), col("Utente", "CreatedByUser", "user"), col("Operazione", "Operation"),
              col("Oggetto", "Table"), col("Documento", "Document"), col("Modifiche", "Changes")]},
    # ---------------- simulazione (non è SAP)
    {"id": "machines", "group": "Simulazione · non visibile in SAP", "title": "Stato reale delle presse", "sub": "Usura nascosta e soglia di guasto",
     "icon": "sap-icon://simulate", "rows": _machines, "key": ["WorkCenter"], "inactive": True,
     "cols": [col("Pressa", "WorkCenter", "id", "Equipment"), col("Linea", "Line"), col("Stato", "State", "status", smap=S_MACHINE),
              col("Usura", "Wear", "number"), col("Soglia di guasto", "FailAt", "number"), col("Temperatura olio", "Temperature", "number"),
              col("Pressione", "Pressure", "number"), col("Stampo", "Mold"), col("Ordine in corso", "CurrentOrder")]},
    {"id": "events", "group": "Simulazione · non visibile in SAP", "title": "Cronaca della fabbrica", "sub": "Cosa sta succedendo",
     "icon": "sap-icon://feed", "rows": lambda w: list(reversed(w.events)), "key": ["Id"], "inactive": True,
     "cols": [col("Data e ora", "ts", "datetime"), col("Area", "Area"), col("Evento", "Text"), col("Riferimento", "Ref")]},
]
BY_ID = {a["id"]: a for a in APPS}
for _a in APPS:
    _a["count"] = (lambda a: lambda w: len(_source(w, a)))(_a)


def _source(w, a) -> list[dict]:
    rows = a["rows"](w) if "rows" in a else list(w.t(a["table"]).values())
    if "where" in a:
        rows = [r for r in rows if a["where"](r)]
    return rows


def _jsonable(v):
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, dict):
        if set(v) == {"results"}:
            return [_jsonable(x) for x in v["results"]]
        return {k: _jsonable(x) for k, x in v.items() if not k.startswith("_") or k in ("_by", "_changed_by", "_wc", "_consumed")}
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    return v


def app_rows(w, app_id: str, top: int) -> list[dict]:
    a = BY_ID[app_id]
    rows = _source(w, a)
    if a.get("sort"):
        rows = sorted(rows, key=lambda r: (a["flatten"](w, r) if a.get("flatten") else r).get(a["sort"]) or dt.datetime.max)
    elif a.get("newest"):
        rows = sorted(rows, key=lambda r: r.get("_at") or dt.datetime.min, reverse=True)
    rows = rows[:top]
    out = []
    for r in rows:
        r2 = a["flatten"](w, r) if a.get("flatten") else r
        item = _jsonable(r2)
        item["_by"] = r.get("_by", "")
        item["__key"] = "|".join(str(_jsonable(r.get(k))) for k in a["key"])
        out.append(item)
    return out


def _key(payload) -> list[str]:
    return payload["key"].split("|")


def action(w, p: dict) -> str:
    user = p.get("user", "PLANNER")
    a, k = p["action"], _key(p)
    if a == "notif_order":
        n = w.get("notif", (k[0],))
        num = pm.create_order(w, {"MaintenanceOrderType": "PM01", "MaintenanceOrderDesc": n["NotificationText"][:40],
                                  "Equipment": n["TechnicalObject"], "MaintenancePlanningPlant": "1000", "MainWorkCenter": "MAINT01",
                                  "MaintOrdBasicStartDate": w.today, "MaintenanceNotification": k[0]}, user)[0]
        return f"Creato l'ordine {num}"
    if a == "notif_complete":
        pm.complete_notification(w, k[0], user)
        return f"Avviso {k[0]} completato"
    if a == "morder_release":
        pm.release_order(w, k[0], user)
        return f"Ordine {k[0]} rilasciato"
    if a == "morder_teco":
        pm.technically_complete(w, k[0], user)
        return f"Ordine {k[0]} chiuso tecnicamente"
    if a == "planned_convert":
        return f"Creato l'ordine di produzione {pp.convert_planned_order(w, k[0], user)}"
    if a == "prod_release":
        pp.release_production_order(w, k[0], user)
        return f"Ordine {k[0]} rilasciato"
    if a == "op_move":
        key = (k[0], k[1])
        row = w.get("prodop", key)
        pp.update_operation(w, key, row, {"WorkCenter": p["params"]["WorkCenter"]}, user)
        return f"Ordine {k[0]} spostato su {p['params']['WorkCenter']}"
    if a == "task_confirm":
        inv.confirm_task(w, k[0], user)
        return f"Compito {k[0]} confermato"
    if a == "op_options":
        mat = w.get("prodorder", (k[0],))["Material"]
        return ",".join(pp.eligible_workcenters(w, mat))
    raise BusinessError("SIM/001", "Azione non riconosciuta")
