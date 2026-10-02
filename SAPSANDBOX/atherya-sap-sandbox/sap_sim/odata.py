"""Esposizione OData V2: servizi ed entità con i nomi delle API S/4HANA on-premise.

Ogni entità dichiara tabella, chiavi, operazioni consentite e funzioni. Le regole di business
stanno nei moduli pm, pp, inventory: questo strato traduce soltanto.
Nomi di entità, campi e funzioni da verificare sugli EDMX ufficiali: vedi FIELD_MAP.md.
"""

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Callable

from . import inventory as inv
from . import pm, pp
from .world import BusinessError

EPOCH = dt.datetime(1970, 1, 1)


@dataclass
class Spec:
    service: str
    entity: str
    table: str | None
    keys: list[str]
    create: Callable | None = None
    update: Callable | None = None
    delete: Callable | None = None
    rows: Callable | None = None           # entità derivate
    nav: dict = field(default_factory=dict)  # campo interno → nome della navigazione OData


def _no_withdrawals(w, key, user):
    r = w.get("reservation", key)
    if any(float(i["ResvnItmWithdrawnQtyInBaseUnit"]) > 0 for i in r["to_ReservationDocumentItem"]["results"]):
        raise BusinessError("M7/062", "Prenotazione già prelevata: non cancellabile")
    w.delete("reservation", key, user)


def _pr_not_ordered(w, key, user):
    r = w.get("preq", key)
    if any(i["ProcessingStatus"] != "N" for i in r["to_PurchaseReqnItem"]["results"]):
        raise BusinessError("ME/120", "Richiesta già trasformata in ordine: non cancellabile")
    w.delete("preq", key, user)


def _children(table: str, nav: str):
    def rows(w):
        out = []
        for parent in w.t(table).values():
            for child in parent.get(nav, {}).get("results", []):
                out.append({**child, "_etag": parent["_etag"], "_by": parent["_by"], "_at": parent["_at"], "_changed_by": parent["_changed_by"]})
        return out
    return rows


def _supply_demand(w):
    rows = []
    def add(mat, cat, text, qty, day, ref):
        rows.append({"Material": mat, "MRPPlant": "1000", "MRPElementCategory": cat, "MRPElementCategoryName": text,
                     "MRPElement": ref, "MRPElementOpenQuantity": round(qty, 3), "MRPElementAvailyOrRqmtDate": day,
                     "_etag": "", "_by": "MRP", "_at": w.now, "_changed_by": "MRP"})
    for p in w.t("pir").values():
        left = p["PlannedQuantity"] - p.get("_consumed", 0)
        if left > 0 and p["WorkingDayDate"] >= pp.monday(w.today):
            add(p["Product"], "PP", "Fabbisogno indipendente", -left, p["WorkingDayDate"], p["Product"])
    for p in w.t("plannedorder").values():
        add(p["Material"], "PA", "Ordine pianificato", p["PlannedTotalQtyInBaseUnit"], p["PlndOrderPlannedEndDate"], p["PlannedOrder"])
    for o in w.t("prodorder").values():
        left = o["MfgOrderPlannedTotalQty"] - o["_gr_qty"]
        if left > 0 and o["OrderSystemStatus"] not in ("TECO",):
            add(o["Material"], "FE", "Ordine di produzione", left, o["MfgOrderPlannedEndDate"], o["ManufacturingOrder"])
    for c in w.t("prodcomp").values():
        left = c["RequiredQuantity"] - c["WithdrawnQuantity"]
        if left > 0:
            add(c["Material"], "AR", "Fabbisogno dipendente", -left, c["RequirementDate"], c["ManufacturingOrder"])
    for pr in w.t("preq").values():
        for it in pr["to_PurchaseReqnItem"]["results"]:
            if not it["_received"] and not pr.get("IsDeleted"):
                add(it["Material"], "BA", "Richiesta d'acquisto", float(it["RequestedQuantity"]),
                    it["_delivery"] or it.get("DeliveryDate"), pr["PurchaseRequisition"])
    return rows


SPECS = [
    # ---------- manutenzione ----------
    Spec("API_MAINTNOTIFICATION", "MaintenanceNotification", "notif", ["MaintenanceNotification"],
         create=pm.create_notification, update=pm.update_notification),
    Spec("API_MAINTENANCEORDER", "MaintenanceOrder", "morder", ["MaintenanceOrder"],
         create=pm.create_order, update=pm.update_order),
    Spec("API_MAINTENANCEORDER", "MaintenanceOrderOperation", None, ["MaintenanceOrder", "MaintenanceOrderOperation"],
         rows=_children("morder", "to_MaintenanceOrderOperation")),
    Spec("API_MAINTENANCEORDER", "MaintenanceOrderComponent", None, ["MaintenanceOrder", "MaintenanceOrderComponent"],
         rows=_children("morder", "to_MaintenanceOrderComponent")),
    Spec("API_MAINTORDERCONFIRMATION", "MaintOrderConfirmation", "mconf", ["MaintOrderConf"], create=pm.confirm_order),
    Spec("API_MAINTENANCEPLAN", "MaintenancePlan", "maintplan", ["MaintenancePlan"], nav={"Components": "to_MaintPlanComponent"}),
    Spec("API_EQUIPMENT", "Equipment", "equipment", ["Equipment"]),
    Spec("API_FUNCTIONALLOCATION", "FunctionalLocation", "funcloc", ["FunctionalLocation"]),
    Spec("API_MEASURINGPOINT", "MeasuringPoint", "measpoint", ["MeasuringPoint"]),
    Spec("API_MEASUREMENTDOCUMENT_SRV", "MeasurementDocument", "measdoc", ["MeasurementDocument"], create=pm.create_measurement),
    # ---------- materiali, magazzino, acquisti ----------
    Spec("API_PRODUCT_SRV", "A_Product", "product", ["Product"]),
    Spec("API_MATERIAL_STOCK_SRV", "A_MatlStkInAcctMod", None, ["Material", "Plant", "StorageLocation"], rows=inv.plant_stock_rows),
    Spec("API_MATERIAL_DOCUMENT_SRV", "A_MaterialDocumentHeader", "matdoc", ["MaterialDocument"], nav={"Items": "to_MaterialDocumentItem"}),
    Spec("API_RESERVATION_DOCUMENT_SRV", "A_ReservationDocumentHeader", "reservation", ["Reservation"],
         create=inv.create_reservation, delete=_no_withdrawals),
    Spec("API_PURCHASEREQ_PROCESS_SRV", "A_PurchaseRequisitionHeader", "preq", ["PurchaseRequisition"],
         create=inv.create_purchase_requisition, delete=_pr_not_ordered),
    Spec("API_WAREHOUSE_ORDER_TASK_2", "WarehouseTask", "whtask", ["WarehouseTask"]),
    Spec("API_WHSE_PHYSSTOCKPROD", "WarehousePhysicalStockProducts", "quant", ["EWMStorageBin", "Product"]),
    Spec("API_WAREHOUSE_STORAGE_BIN", "WarehouseStorageBin", "bin", ["EWMStorageBin"]),
    # ---------- produzione ----------
    Spec("API_BILL_OF_MATERIAL_SRV", "MaterialBOM", "bom", ["Material"], nav={"Items": "to_BillOfMaterialItem"}),
    Spec("API_PRODUCTION_ROUTING", "ProductionRouting", "routing", ["Material"], nav={"Operations": "to_ProductionRoutingOperation"}),
    Spec("API_WORK_CENTERS", "A_WorkCenters", "workcenter", ["WorkCenter"]),
    Spec("API_PLND_INDEP_RQMT_SRV", "PlannedIndepRqmt", "pir", ["Product", "Plant", "WorkingDayDate"]),
    Spec("API_PLANNED_ORDERS", "A_PlannedOrder", "plannedorder", ["PlannedOrder"]),
    Spec("API_PRODUCTION_ORDER_2_SRV", "A_ProductionOrder_2", "prodorder", ["ManufacturingOrder"]),
    Spec("API_PRODUCTION_ORDER_2_SRV", "A_ProductionOrderOperation_2", "prodop", ["ManufacturingOrder", "ManufacturingOrderOperation"],
         update=pp.update_operation),
    Spec("API_PRODUCTION_ORDER_2_SRV", "A_ProductionOrderComponent_4", "prodcomp", ["ManufacturingOrder", "ReservationItem"]),
    Spec("API_PROD_ORDER_CONFIRMATION_2_SRV", "ProdnOrdConf2", "pconf", ["ConfirmationGroup"], create=pp.confirm_operation),
    Spec("API_MRP_MATERIALS_SRV_01", "SupplyDemandItems", None, ["Material", "MRPElementCategory", "MRPElement"], rows=_supply_demand),
]
BY_NAME = {(s.service, s.entity): s for s in SPECS}

# Funzioni (function import): servizio, nome → (operazione richiesta, gestore, parametri)
FUNCTIONS = {
    ("API_MAINTNOTIFICATION", "CompleteMaintNotification"): ("update", lambda w, p, u: pm.complete_notification(w, p["MaintenanceNotification"], u)),
    ("API_MAINTENANCEORDER", "ReleaseMaintenanceOrder"): ("release", lambda w, p, u: pm.release_order(w, p["MaintenanceOrder"], u)),
    ("API_MAINTENANCEORDER", "TechnicallyCompleteMaintOrder"): ("update", lambda w, p, u: pm.technically_complete(w, p["MaintenanceOrder"], u)),
    ("API_PLANNED_ORDERS", "ConvertPlannedOrderToProdnOrd"): ("update", lambda w, p, u: {"ManufacturingOrder": pp.convert_planned_order(w, p["PlannedOrder"], u, p.get("WorkCenter") or None)}),
    ("API_PRODUCTION_ORDER_2_SRV", "ReleaseOrder"): ("release", lambda w, p, u: pp.release_production_order(w, p["ManufacturingOrder"], u)),
    ("API_WAREHOUSE_ORDER_TASK_2", "ConfirmWarehouseTask"): ("update", lambda w, p, u: inv.confirm_task(w, p["WarehouseTask"], u)),
}


# ---------------------------------------------------------------- serializzazione

def to_odata(value):
    if isinstance(value, dt.datetime):
        return f"/Date({int((value - EPOCH).total_seconds() * 1000)})/"
    if isinstance(value, dt.date):
        return f"/Date({int((dt.datetime.combine(value, dt.time()) - EPOCH).total_seconds() * 1000)})/"
    if isinstance(value, dt.time):
        return f"PT{value.hour:02d}H{value.minute:02d}M{value.second:02d}S"
    if isinstance(value, dict):
        return {k: to_odata(v) for k, v in value.items() if not k.startswith("_")}
    if isinstance(value, list):
        return [to_odata(v) for v in value]
    return value


def from_odata(value):
    if isinstance(value, str):
        m = re.fullmatch(r"/Date\((-?\d+)(?:[+-]\d+)?\)/", value)
        if m:
            t = EPOCH + dt.timedelta(milliseconds=int(m.group(1)))
            return t.date() if t.time() == dt.time() else t
        return value
    if isinstance(value, dict):
        return {k: from_odata(v) for k, v in value.items()}
    if isinstance(value, list):
        return [from_odata(v) for v in value]
    return value


def public(spec: Spec, row: dict, select: list[str] | None = None) -> dict:
    out = {}
    for k, v in row.items():
        if k.startswith("_"):
            continue
        name = spec.nav.get(k, k)
        if isinstance(v, list):
            v = {"results": v}
        out[name] = to_odata(v)
    if select:
        out = {k: v for k, v in out.items() if k in select}
    out["__metadata"] = {"type": f"{spec.service}.{spec.entity}Type", "etag": row.get("_etag", "")}
    return out


# ---------------------------------------------------------------- query

def parse_key(raw: str | None, keys: list[str]) -> tuple | None:
    if raw is None:
        return None
    raw = raw.strip()
    if "=" not in raw:
        return (raw.strip("'"),)
    parts = {k: (a or b) for k, a, b in re.findall(r"(\w+)=(?:'([^']*)'|datetime'([^']*)')", raw)}
    return tuple(parts.get(k) for k in keys)


def _cmp_value(v):
    if isinstance(v, dt.datetime):
        return v
    if isinstance(v, dt.date):
        return dt.datetime.combine(v, dt.time())
    return v


def _coerce(v, t):
    if isinstance(t, dt.datetime):
        return _cmp_value(v), t
    if isinstance(t, bool):
        return bool(v), t
    if isinstance(v, bool):
        return v, t in ("true", "X")
    if isinstance(v, (int, float)):
        try:
            return float(v), float(t)
        except (TypeError, ValueError):
            return str(v), t
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat(), t
    return ("" if v is None else str(v)), t


def apply_query(rows: list[dict], params) -> tuple[list[dict], int]:
    flt = params.get("$filter")
    if flt:
        for name, op, quoted, dtime, bare in re.findall(r"(\w+) (eq|ne|ge|le|gt|lt) (?:'([^']*)'|datetime'([^']*)'|([\w.\-]+))", flt):
            if dtime:
                target = dt.datetime.fromisoformat(dtime)
            elif bare in ("true", "false"):
                target = bare == "true"
            else:
                target = quoted if not bare else bare

            def ok(r, f=name, o=op, t=target):
                v, tt = _coerce(r.get(f), t)
                if v is None:
                    return False
                try:
                    return {"eq": v == tt, "ne": v != tt, "ge": v >= tt, "le": v <= tt, "gt": v > tt, "lt": v < tt}[o]
                except TypeError:
                    return False
            rows = [r for r in rows if ok(r)]
    count = len(rows)
    order = params.get("$orderby")
    if order:
        for part in reversed([p.strip() for p in order.split(",")]):
            name, *direction = part.split()
            present = [r for r in rows if r.get(name) is not None]
            missing = [r for r in rows if r.get(name) is None]
            present.sort(key=lambda r: _cmp_value(r.get(name)), reverse=bool(direction and direction[0] == "desc"))
            rows = present + missing
    skip, top = int(params.get("$skip", 0)), params.get("$top")
    rows = rows[skip:]
    if top is not None:
        rows = rows[:int(top)]
    return rows, count


def all_rows(w, spec: Spec) -> list[dict]:
    return spec.rows(w) if spec.rows else list(w.t(spec.table).values())


def find_row(w, spec: Spec, key: tuple) -> dict | None:
    if spec.table:
        row = w.t(spec.table).get(key)
        if row is None and spec.table == "pir":  # chiave con data
            row = next((r for k, r in w.t("pir").items() if (k[0], k[1], str(k[2])[:10]) == (key[0], key[1], str(key[2])[:10])), None)
        return row
    return next((r for r in spec.rows(w) if tuple(str(r.get(k)) for k in spec.keys) == key), None)
