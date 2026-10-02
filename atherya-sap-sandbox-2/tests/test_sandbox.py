"""Test della sandbox: servizi, cicli di vita PM/PP/EWM, fabbrica viva, valore dell'intervento di Atherya."""

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from atherya_sap.client import SAPClient, SAPError, sap_date
from atherya_sap.executor import Approval, Executor
from atherya_sap.planner import Prediction, build_plan
from atherya_sap.policy import Policy
from sap_sim import factory
from sap_sim.server import app, reset_state

P07_EQ = "10000007"


@pytest.fixture
def http():
    reset_state()
    return TestClient(app)


@pytest.fixture
def planner(http):
    return SAPClient(http, "PLANNER", "demo")


@pytest.fixture
def atherya(http):
    return SAPClient(http, "ATHERYA_TECH", "demo")


def advance(http, minutes):
    return http.post("/sim/advance", json={"minutes": minutes}).json()


def call(client, service, function, **params):
    """Function import OData V2 (POST con parametri in query)."""
    q = "&".join(f"{k}='{v}'" for k, v in params.items())
    r = client.http.post(f"{client.base}/{service}/{function}?{q}",
                         headers=client._headers({"x-csrf-token": client._token(service)}))
    client._check(r)
    return r.json()["d"]


# ---------------------------------------------------------------- anagrafiche e servizi

def test_servizi_e_anagrafiche(planner):
    assert len(planner.query("API_EQUIPMENT", "Equipment")) == 43
    assert len(planner.query("API_MEASURINGPOINT", "MeasuringPoint")) == 129
    assert len(planner.query("API_WORK_CENTERS", "A_WorkCenters")) == 45
    bom = planner.query("API_BILL_OF_MATERIAL_SRV", "MaterialBOM", {"Material": "A-2210"})[0]
    assert {i["BillOfMaterialComponent"] for i in bom["to_BillOfMaterialItem"]["results"]} == {"EPDM-70", "INS-M8"}
    assert len(planner.query("API_PRODUCTION_ORDER_2_SRV", "A_ProductionOrder_2")) > 50


def test_metadata_elenca_entita_e_funzioni(http, planner):
    r = http.get("/sap/opu/odata/sap/API_MAINTENANCEORDER/$metadata", headers=planner._headers())
    d = r.json()["d"]
    assert "MaintenanceOrder" in d["EntitySets"] and "ReleaseMaintenanceOrder" in d["FunctionImports"]


# ---------------------------------------------------------------- manutenzione

def test_ciclo_di_vita_manutenzione(http, planner):
    notif = planner.create("API_MAINTNOTIFICATION", "MaintenanceNotification", {
        "NotificationType": "M1", "NotificationText": "Sostituire guarnizione P07", "TechnicalObject": P07_EQ,
        "MaintenancePlanningPlant": "1000"})["MaintenanceNotification"]
    order = planner.create("API_MAINTENANCEORDER", "MaintenanceOrder", {
        "MaintenanceOrderType": "PM01", "MaintenanceOrderDesc": "Sostituzione guarnizione idraulica P07", "Equipment": P07_EQ,
        "MaintenancePlanningPlant": "1000", "MainWorkCenter": "MAINT01", "MaintOrdBasicStartDate": sap_date(dt.date(2026, 10, 1)),
        "MaintenanceNotification": notif,
        "to_MaintenanceOrderComponent": {"results": [{"Material": "GUARN-HYD-250", "RequiredQuantity": 1}]}})["MaintenanceOrder"]
    n, _ = planner.read("API_MAINTNOTIFICATION", "MaintenanceNotification", notif)
    assert n["MaintNotifProcessPhaseCode"] == "3" and n["MaintenanceOrder"] == order
    call(planner, "API_MAINTENANCEORDER", "ReleaseMaintenanceOrder", MaintenanceOrder=order)
    advance(http, 8 * 60)
    o, _ = planner.read("API_MAINTENANCEORDER", "MaintenanceOrder", order)
    assert o["MaintOrdSystemStatus"] == "TECO"
    assert planner.query("API_MAINTORDERCONFIRMATION", "MaintOrderConfirmation", {"MaintenanceOrder": order})
    n, _ = planner.read("API_MAINTNOTIFICATION", "MaintenanceNotification", notif)
    assert n["MaintNotifProcessPhaseCode"] == "4"
    stock = planner.query("API_MATERIAL_STOCK_SRV", "A_MatlStkInAcctMod", {"Material": "GUARN-HYD-250"})
    assert float(stock[0]["MatlWrhsStkQtyInMatlBaseUnit"]) == 1
    assert http.get("/sim/truth").json()["PRESS07"]["wear"] < 10


def test_atherya_non_puo_rilasciare(atherya, planner):
    order = planner.create("API_MAINTENANCEORDER", "MaintenanceOrder", {
        "MaintenanceOrderType": "PM02", "MaintenanceOrderDesc": "Controllo", "Equipment": P07_EQ, "MaintenancePlanningPlant": "1000",
        "MainWorkCenter": "MAINT01", "MaintOrdBasicStartDate": sap_date(dt.date(2026, 10, 5))})["MaintenanceOrder"]
    with pytest.raises(SAPError) as e:
        call(atherya, "API_MAINTENANCEORDER", "ReleaseMaintenanceOrder", MaintenanceOrder=order)
    assert e.value.status == 403


# ---------------------------------------------------------------- il valore di Atherya

def _p07_breakdowns(client):
    return [n for n in client.query("API_MAINTNOTIFICATION", "MaintenanceNotification", {"TechnicalObject": P07_EQ}) if n["IsBreakdown"]]


def test_senza_atherya_p07_si_guasta(http, planner):
    for _ in range(21):
        advance(http, 24 * 60)
    assert _p07_breakdowns(planner), "Senza intervento P07 dovrebbe guastarsi entro tre settimane"


def test_con_atherya_p07_non_si_guasta(http, atherya, planner):
    pred = Prediction(id="PRED-P07", equipment=P07_EQ, machine="P07", component="Guarnizione idraulica", window_min_days=10,
                      window_max_days=14, confidence=0.86, signature="instabilità di pressione in aumento",
                      known_context="stampo S-118", part_material="GUARN-HYD-250", part_qty=1, part_lead_time_days=5,
                      affected_order="1000471", affected_operation="0010", current_workcenter="PRESS07",
                      candidate_workcenters=("PRESS07", "PRESS12", "PRESS15"))
    plan = build_plan(pred, atherya, dt.date(2026, 10, 1), dt.date(2026, 10, 3))
    rec = Executor(atherya, Policy({"manutenzione": "con_approvazione", "scheduling": "con_approvazione"})).run(plan, Approval("capoturno"))
    order = rec["steps"][1]["result"]["key"]
    call(planner, "API_MAINTENANCEORDER", "ReleaseMaintenanceOrder", MaintenanceOrder=order)  # l'ultima parola a una persona
    for _ in range(21):
        advance(http, 24 * 60)
    o, _ = planner.read("API_MAINTENANCEORDER", "MaintenanceOrder", order)
    assert o["MaintOrdSystemStatus"] == "TECO"
    assert not _p07_breakdowns(planner)
    res = planner.query("API_RESERVATION_DOCUMENT_SRV", "A_ReservationDocumentHeader")[0]
    assert res["to_ReservationDocumentItem"]["results"][0]["ReservationItemIsFinallyIssued"] is True


# ---------------------------------------------------------------- produzione

def test_da_ordine_pianificato_a_conferma(planner):
    po = sorted(planner.query("API_PLANNED_ORDERS", "A_PlannedOrder"), key=lambda p: p["PlndOrderPlannedStartDate"])[0]
    mo = call(planner, "API_PLANNED_ORDERS", "ConvertPlannedOrderToProdnOrd", PlannedOrder=po["PlannedOrder"])["ManufacturingOrder"]
    comps = planner.query("API_PRODUCTION_ORDER_2_SRV", "A_ProductionOrderComponent_4", {"ManufacturingOrder": mo})
    assert comps and all(c["WithdrawnQuantity"] == 0 for c in comps)
    call(planner, "API_PRODUCTION_ORDER_2_SRV", "ReleaseOrder", ManufacturingOrder=mo)
    tasks = planner.query("API_WAREHOUSE_ORDER_TASK_2", "WarehouseTask", {"EWMReferenceDocument": mo})
    assert tasks and all(t["DestinationStorageBin"].startswith("PSA") for t in tasks)
    for t in tasks:
        call(planner, "API_WAREHOUSE_ORDER_TASK_2", "ConfirmWarehouseTask", WarehouseTask=t["WarehouseTask"])
    planner.create("API_PROD_ORDER_CONFIRMATION_2_SRV", "ProdnOrdConf2", {
        "OrderID": mo, "OrderOperation": "0010", "ConfirmationYieldQuantity": 500, "ConfirmationScrapQuantity": 5})
    docs = planner.query("API_MATERIAL_DOCUMENT_SRV", "A_MaterialDocumentHeader")
    movements = {d["GoodsMovementType"] for d in docs}
    assert {"261", "101"} <= movements
    o, _ = planner.read("API_PRODUCTION_ORDER_2_SRV", "A_ProductionOrder_2", mo)
    assert o["OrderSystemStatus"] == "PCNF" and o["MfgOrderConfirmedYieldQty"] == 500


def test_pressa_non_idonea_rifiutata(planner):
    op, etag = planner.read("API_PRODUCTION_ORDER_2_SRV", "A_ProductionOrderOperation_2",
                            {"ManufacturingOrder": "1000471", "ManufacturingOrderOperation": "0010"})
    with pytest.raises(SAPError) as e:
        planner.update("API_PRODUCTION_ORDER_2_SRV", "A_ProductionOrderOperation_2",
                       {"ManufacturingOrder": "1000471", "ManufacturingOrderOperation": "0010"}, {"WorkCenter": "PRESS20"}, etag)
    assert "idoneo" in e.value.message


def test_mrp_e_fabbisogni(http, planner):
    advance(http, 24 * 60)  # passa la notte: gira l'MRP
    items = planner.query("API_MRP_MATERIALS_SRV_01", "SupplyDemandItems", {"Material": "EPDM-70"})
    cats = {i["MRPElementCategory"] for i in items}
    assert "AR" in cats and "BA" in cats  # fabbisogni dipendenti e RdA generate dall'MRP


# ---------------------------------------------------------------- magazzino

def test_giacenze_per_ubicazione_e_filtri(planner):
    rows = planner.query("API_WHSE_PHYSSTOCKPROD", "WarehousePhysicalStockProducts", {"Product": "GUARN-HYD-250"})
    assert rows and rows[0]["EWMStorageBin"].startswith("SP-")


# ---------------------------------------------------------------- fabbrica viva

def test_la_fabbrica_vive(http, planner):
    clock = advance(http, 7 * 24 * 60)
    assert clock["kpi"]["produced"] > 100000
    r = planner.http.get(f"{planner.base}/API_MEASUREMENTDOCUMENT_SRV/MeasurementDocument",
                         headers=planner._headers(), params={"$filter": "MeasuringPoint eq '072'", "$orderby": "MeasurementDocument desc",
                                                             "$top": "5", "$inlinecount": "allpages"})
    d = r.json()["d"]
    assert len(d["results"]) == 5 and int(d["__count"]) > 50
    confs = planner.query("API_PROD_ORDER_CONFIRMATION_2_SRV", "ProdnOrdConf2")
    assert confs


def test_deterministica():
    a, b = factory.build_world(seed=3), factory.build_world(seed=3)
    factory.advance(a, 5 * 24 * 60)
    factory.advance(b, 5 * 24 * 60)
    assert a.kpi == b.kpi
