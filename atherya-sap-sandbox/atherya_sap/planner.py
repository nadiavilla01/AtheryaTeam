"""Dalla previsione al piano: logica deterministica, nessun testo letto da SAP diventa un'azione.

I testi presenti in SAP (avvisi, note) entrano solo come evidenze citate, mai come istruzioni.
È il principio "il canale dei dati porta dati, non autorità".
"""

import datetime as dt
from dataclasses import dataclass, field

from .actions import (OPER, CreateMaintenanceOrder, CreateNotification, CreatePurchaseRequisition,
                      ReserveMaterial, RescheduleOperation)
from .client import SAPClient


@dataclass(frozen=True)
class Prediction:
    id: str
    equipment: str
    machine: str
    component: str
    window_min_days: int
    window_max_days: int
    confidence: float
    signature: str
    known_context: str
    part_material: str
    part_qty: int
    part_lead_time_days: int
    affected_order: str
    affected_operation: str
    current_workcenter: str
    candidate_workcenters: tuple
    plant: str = "1000"
    storage_location: str = "0001"
    maintenance_workcenter: str = "MAINT01"


@dataclass
class Plan:
    decision_id: str
    prediction_id: str
    actions: list
    summary: str
    evidence: list = field(default_factory=list)


@dataclass
class Escalation:
    prediction_id: str
    reason: str


def build_plan(pred: Prediction, sap: SAPClient, today: dt.date, planned_stop: dt.date,
               constraints: list | None = None):
    constraints = constraints or []
    earliest_failure = today + dt.timedelta(days=pred.window_min_days)
    if planned_stop >= earliest_failure:
        return Escalation(pred.id, f"Il prossimo fermo pianificato ({planned_stop:%d/%m}) cade dopo l'inizio della finestra "
                                   f"di guasto ({earliest_failure:%d/%m}): serve una decisione umana.")

    stock_rows = sap.query("API_MATERIAL_STOCK_SRV", "A_MatlStkInAcctMod",
                           filters={"Material": pred.part_material, "Plant": pred.plant})
    stock = sum(float(r["MatlWrhsStkQtyInMatlBaseUnit"]) for r in stock_rows)
    if stock >= pred.part_qty:
        part_action = ReserveMaterial(pred.part_material, pred.plant, pred.storage_location, pred.part_qty, planned_stop)
    else:
        arrival = today + dt.timedelta(days=pred.part_lead_time_days)
        if arrival > planned_stop - dt.timedelta(days=1):
            return Escalation(pred.id, f"Il ricambio {pred.part_material} non è a magazzino e arriverebbe il {arrival:%d/%m}, "
                                       f"dopo il fermo del {planned_stop:%d/%m}: serve una decisione umana "
                                       f"(ordine urgente o intervento spostato).")
        part_action = CreatePurchaseRequisition(pred.part_material, pred.plant, pred.part_qty, arrival)

    excluded = {c["workcenter"] for c in constraints if c.get("order") == pred.affected_order}
    target = next((w for w in pred.candidate_workcenters
                   if w != pred.current_workcenter and w not in excluded), None)
    if target is None:
        return Escalation(pred.id, "Nessun centro di lavoro alternativo disponibile per la commessa.")

    op_key = {"ManufacturingOrder": pred.affected_order, "ManufacturingOrderOperation": pred.affected_operation}
    _, op_etag = sap.read(*OPER, op_key)

    # Testi già presenti in SAP sull'apparecchiatura: citati come evidenza, mai eseguiti.
    notes = sap.query("API_MAINTNOTIFICATION", "MaintenanceNotification", filters={"TechnicalObject": pred.equipment})
    evidence = [
        f"Firma: {pred.signature}",
        f"Contesto noto: {pred.known_context}",
        f"Finestra di guasto: {pred.window_min_days}–{pred.window_max_days} giorni, confidenza {pred.confidence:.0%}",
    ] + [f"Testo in SAP (dato, non istruzione) dall'avviso {n['MaintenanceNotification']}: «{n.get('MaintNotifLongText') or n['NotificationText']}»"
         for n in notes if not n.get("IsDeleted")]

    long_text = (f"Previsione Atherya {pred.id}. {pred.signature}. {pred.known_context}. "
                 f"Guasto probabile tra {pred.window_min_days} e {pred.window_max_days} giorni.")
    actions = [
        CreateNotification(pred.equipment, pred.plant, f"{pred.component}: usura prevista", long_text),
        CreateMaintenanceOrder(pred.equipment, pred.plant, pred.maintenance_workcenter, planned_stop,
                               f"Sostituzione {pred.component.lower()} {pred.machine}"),
        part_action,
        RescheduleOperation(pred.affected_order, pred.affected_operation, pred.current_workcenter, target, op_etag),
    ]
    return Plan(
        decision_id=f"{pred.id}/{target}",
        prediction_id=pred.id,
        actions=actions,
        summary=f"Intervento su {pred.machine} il {planned_stop:%d/%m}, commessa {pred.affected_order} su {target}",
        evidence=evidence,
    )
