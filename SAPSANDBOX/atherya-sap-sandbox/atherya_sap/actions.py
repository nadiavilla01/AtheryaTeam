"""Le uniche azioni che Atherya può compiere su SAP: tipizzate, con anteprima e compensazione.

Nessun agente scrive in SAP con chiamate libere. Ogni azione:
- dichiara il suo dominio (serve al motore dei permessi);
- produce un'anteprima leggibile ("Cosa verrà scritto in SAP");
- porta un riferimento univoco (YY1_AtheryaRef) per non creare duplicati;
- sa come annullarsi (compensazione).

YY1_AtheryaRef: nel SAP vero è un campo personalizzato aggiunto con l'estensibilità
key user di S/4HANA. Va concordato con l'IT del cliente.
"""

from dataclasses import dataclass

from .client import SAPClient, sap_date

NOTIF = ("API_MAINTNOTIFICATION", "MaintenanceNotification")
ORDER = ("API_MAINTENANCEORDER", "MaintenanceOrder")
PREQ = ("API_PURCHASEREQ_PROCESS_SRV", "A_PurchaseRequisitionHeader")
RESV = ("API_RESERVATION_DOCUMENT_SRV", "A_ReservationDocumentHeader")
OPER = ("API_PRODUCTION_ORDER_2_SRV", "A_ProductionOrderOperation_2")


def _find_by_ref(sap: SAPClient, target: tuple, key_field: str, label: str, ref: str) -> dict | None:
    rows = sap.query(*target, filters={"YY1_AtheryaRef": ref})
    return {"label": label, "target": target, "key": rows[0][key_field]} if rows else None


def _flag_deleted(sap: SAPClient, target: tuple, key: str) -> None:
    _, etag = sap.read(*target, key)
    sap.update(*target, key, {"IsDeleted": True}, etag)


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
        return f"Avviso {self.notif_type} su apparecchiatura {self.equipment}: «{self.text}» (priorità {self.priority})"

    def execute(self, sap: SAPClient, ref: str, ctx: dict) -> dict:
        d = sap.create(*NOTIF, {
            "NotificationType": self.notif_type, "NotificationText": self.text,
            "MaintNotifLongText": self.long_text, "TechnicalObject": self.equipment,
            "TechObjIsEquipOrFuncnlLoc": "EAMS_EQUI", "MaintenancePlanningPlant": self.plant,
            "MaintPriority": self.priority, "YY1_AtheryaRef": ref,
        })
        return {"label": "Avviso", "target": NOTIF, "key": d["MaintenanceNotification"]}

    def find_existing(self, sap: SAPClient, ref: str):
        return _find_by_ref(sap, NOTIF, "MaintenanceNotification", "Avviso", ref)

    def compensate(self, sap: SAPClient, result: dict) -> str:
        _flag_deleted(sap, NOTIF, result["key"])
        return f"Avviso {result['key']} contrassegnato per la cancellazione"


@dataclass(frozen=True)
class CreateMaintenanceOrder:
    equipment: str
    plant: str
    work_center: str
    start: object  # datetime.date
    description: str
    notification_step: int | None = 0
    order_type: str = "PM01"
    domain = "manutenzione"

    def preview(self) -> str:
        return (f"Ordine {self.order_type} «{self.description}» su {self.equipment}, centro {self.work_center}, "
                f"inizio {self.start:%d/%m/%Y}, collegato all'avviso del passo {self.notification_step + 1}, stato CRTD")

    def execute(self, sap: SAPClient, ref: str, ctx: dict) -> dict:
        payload = {
            "MaintenanceOrderType": self.order_type, "MaintenanceOrderDesc": self.description,
            "Equipment": self.equipment, "MaintenancePlanningPlant": self.plant,
            "MainWorkCenter": self.work_center, "MaintOrdBasicStartDate": sap_date(self.start),
            "YY1_AtheryaRef": ref,
        }
        if self.notification_step is not None:
            payload["MaintenanceNotification"] = ctx["results"][self.notification_step]["key"]
        d = sap.create(*ORDER, payload)
        return {"label": "Ordine di manutenzione", "target": ORDER, "key": d["MaintenanceOrder"]}

    def find_existing(self, sap: SAPClient, ref: str):
        return _find_by_ref(sap, ORDER, "MaintenanceOrder", "Ordine di manutenzione", ref)

    def compensate(self, sap: SAPClient, result: dict) -> str:
        _flag_deleted(sap, ORDER, result["key"])
        return f"Ordine {result['key']} contrassegnato per la cancellazione"


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
                f"per il {self.requirement_date:%d/%m/%Y}")

    def execute(self, sap: SAPClient, ref: str, ctx: dict) -> dict:
        d = sap.create(*RESV, {
            "GoodsMovementType": "201", "YY1_AtheryaRef": ref,
            "to_ReservationDocumentItem": {"results": [{
                "Material": self.material, "Plant": self.plant, "StorageLocation": self.storage_location,
                "ResvnItmRequiredQtyInBaseUnit": str(self.quantity),
                "MatlCompRequirementDate": sap_date(self.requirement_date),
            }]},
        })
        return {"label": "Prenotazione", "target": RESV, "key": d["Reservation"]}

    def find_existing(self, sap: SAPClient, ref: str):
        return _find_by_ref(sap, RESV, "Reservation", "Prenotazione", ref)

    def compensate(self, sap: SAPClient, result: dict) -> str:
        sap.delete(*RESV, result["key"])
        return f"Prenotazione {result['key']} cancellata"


@dataclass(frozen=True)
class CreatePurchaseRequisition:
    material: str
    plant: str
    quantity: int
    delivery_date: object
    domain = "manutenzione"

    def preview(self) -> str:
        return f"Richiesta d'acquisto di {self.quantity} × {self.material}, consegna entro il {self.delivery_date:%d/%m/%Y}"

    def execute(self, sap: SAPClient, ref: str, ctx: dict) -> dict:
        d = sap.create(*PREQ, {
            "PurchaseRequisitionType": "NB", "YY1_AtheryaRef": ref,
            "to_PurchaseReqnItem": {"results": [{
                "Material": self.material, "Plant": self.plant, "RequestedQuantity": str(self.quantity),
                "BaseUnit": "PC", "DeliveryDate": sap_date(self.delivery_date),
            }]},
        })
        return {"label": "Richiesta d'acquisto", "target": PREQ, "key": d["PurchaseRequisition"]}

    def find_existing(self, sap: SAPClient, ref: str):
        return _find_by_ref(sap, PREQ, "PurchaseRequisition", "Richiesta d'acquisto", ref)

    def compensate(self, sap: SAPClient, result: dict) -> str:
        sap.delete(*PREQ, result["key"])
        return f"Richiesta d'acquisto {result['key']} cancellata"


@dataclass(frozen=True)
class RescheduleOperation:
    order: str
    operation: str
    from_work_center: str
    to_work_center: str
    expected_etag: str  # letto al momento della proposta: se qualcuno cambia l'ordine, SAP rifiuta
    domain = "scheduling"

    def _key(self) -> dict:
        return {"ManufacturingOrder": self.order, "ManufacturingOrderOperation": self.operation}

    def preview(self) -> str:
        return f"Ordine di produzione {self.order}, operazione {self.operation}: centro di lavoro {self.from_work_center} → {self.to_work_center}"

    def execute(self, sap: SAPClient, ref: str, ctx: dict) -> dict:
        sap.update(*OPER, self._key(), {"WorkCenter": self.to_work_center}, self.expected_etag)
        return {"label": "Riprogrammazione", "target": OPER, "key": f"{self.order}/{self.operation}"}

    def find_existing(self, sap: SAPClient, ref: str):
        row, _ = sap.read(*OPER, self._key())
        if row["WorkCenter"] == self.to_work_center:
            return {"label": "Riprogrammazione", "target": OPER, "key": f"{self.order}/{self.operation}"}
        return None

    def compensate(self, sap: SAPClient, result: dict) -> str:
        row, etag = sap.read(*OPER, self._key())
        if row["WorkCenter"] != self.to_work_center:
            return f"Operazione {self.order}/{self.operation} già modificata da altri: lasciata com'è"
        sap.update(*OPER, self._key(), {"WorkCenter": self.from_work_center}, etag)
        return f"Operazione {self.order}/{self.operation} riportata su {self.from_work_center}"


@dataclass(frozen=True)
class ReleaseMaintenanceOrder:
    """Esiste solo per dimostrare che il motore dei permessi la blocca: nessun livello la consente oggi."""
    order_step: int
    domain = "manutenzione"

    def preview(self) -> str:
        return f"Rilascio dell'ordine creato al passo {self.order_step + 1}"

    def execute(self, sap: SAPClient, ref: str, ctx: dict) -> dict:
        key = ctx["results"][self.order_step]["key"]
        _, etag = sap.read(*ORDER, key)
        sap.update(*ORDER, key, {"MaintOrdSystemStatus": "REL"}, etag)
        return {"label": "Rilascio", "target": ORDER, "key": key}

    def find_existing(self, sap, ref):
        return None

    def compensate(self, sap, result):
        return "Rilascio non compensabile automaticamente"
