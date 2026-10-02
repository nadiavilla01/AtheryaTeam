"""Anagrafiche dello stabilimento simulato: uno stampaggio gomma con 43 presse.

Pino: qui si allineano codici, divisioni, centri di lavoro, materiali e tempi a quelli reali.
Le tabelle usano i nomi dei campi delle API SAP dove noti (vedi FIELD_MAP.md).
"""

import datetime as dt

PLANT = "1000"
SLOC = "0001"
WAREHOUSE = "W100"

LINES = {
    "A": {"name": "Linea A", "desc": "Presse a iniezione", "presses": range(1, 17), "tonnage": 250},
    "B": {"name": "Linea B", "desc": "Presse a compressione", "presses": range(17, 32), "tonnage": 320},
    "C": {"name": "Linea C", "desc": "Vulcanizzazione e finitura", "presses": range(32, 44), "tonnage": 250},
}

# Prodotto finito: linea, stampo, mescola (kg/pezzo), inserto, tempo ciclo per pezzo (s), descrizione
FINISHED = {
    "A-2210": ("A", "S-118", ("EPDM-70", 0.12), ("INS-M8", 1), 24, "Guarnizione portiera"),
    "A-5120": ("A", "S-118", ("EPDM-70", 0.10), ("INS-M8", 1), 22, "Guarnizione portiera corta"),
    "A-1874": ("A", "S-131", ("NBR-60", 0.09), ("INS-M10", 1), 30, "Soffietto sterzo"),
    "A-6031": ("A", "S-152", ("EPDM-70", 0.08), None, 18, "Tappo passacavo"),
    "A-3302": ("B", "S-104", ("EPDM-70", 0.15), ("INS-M10", 2), 40, "Supporto motore"),
    "A-4410": ("B", "S-140", ("NBR-60", 0.20), None, 45, "Tampone antivibrante"),
    "A-0955": ("C", "S-090", ("FKM-75", 0.05), None, 20, "O-ring turbo"),
    "A-7088": ("C", "S-090", ("FKM-75", 0.06), None, 21, "O-ring collettore"),
}

RAW = {
    "EPDM-70": ("Mescola EPDM 70 Shore", "KG", 5, 2000),
    "NBR-60": ("Mescola NBR 60 Shore", "KG", 5, 1500),
    "FKM-75": ("Mescola FKM 75 Shore", "KG", 8, 300),
    "INS-M8": ("Inserto metallico M8", "PC", 7, 20000),
    "INS-M10": ("Inserto metallico M10", "PC", 7, 15000),
}

SPARES = {
    "GUARN-HYD-250": ("Guarnizione idraulica 250 t", "PC", 5, 2),
    "GUARN-HYD-320": ("Guarnizione idraulica 320 t", "PC", 12, 0),
    "RES-HEAT-10": ("Resistenza riscaldante 10 kW", "PC", 6, 3),
    "OLIO-HYD-46": ("Olio idraulico ISO VG 46", "L", 3, 400),
    "FILTRO-HYD": ("Filtro olio idraulico", "PC", 4, 12),
}

SETUP_MOLD_CHANGE_MIN = 90
SETUP_SAME_MOLD_MIN = 10


def press_wc(n: int) -> str:
    return f"PRESS{n:02d}"


def press_equipment(n: int) -> str:
    return f"100000{n:02d}"


def line_of(n: int) -> str:
    return next(k for k, v in LINES.items() if n in v["presses"])


def seed(w) -> None:
    U = "SEED"

    # ---------- divisione, magazzino, centri di lavoro ----------
    w.insert("plant", (PLANT,), {"Plant": PLANT, "PlantName": "Stabilimento stampaggio gomma"}, U)
    w.insert("workcenter", ("MAINT01",), {"WorkCenter": "MAINT01", "Plant": PLANT, "WorkCenterCategoryCode": "0005",
                                          "WorkCenterDesc": "Manutenzione meccanica", "CapacityPersons": 3,
                                          "_blocked": False, "_line": None}, U)
    w.insert("workcenter", ("MAINT02",), {"WorkCenter": "MAINT02", "Plant": PLANT, "WorkCenterCategoryCode": "0005",
                                          "WorkCenterDesc": "Manutenzione elettrica", "CapacityPersons": 1,
                                          "_blocked": False, "_line": None}, U)

    # ---------- sedi tecniche, apparecchiature, punti di misura, centri di lavoro presse ----------
    w.insert("funcloc", (PLANT,), {"FunctionalLocation": PLANT, "FunctionalLocationName": "Stabilimento", "SuperiorFunctionalLocation": ""}, U)
    for code, line in LINES.items():
        fl = f"{PLANT}-L{code}"
        w.insert("funcloc", (fl,), {"FunctionalLocation": fl, "FunctionalLocationName": f"{line['name']} · {line['desc']}",
                                    "SuperiorFunctionalLocation": PLANT}, U)
        for n in line["presses"]:
            wc, eq = press_wc(n), press_equipment(n)
            kind = "Pressa a iniezione" if code == "A" else ("Pressa a compressione" if code == "B" else "Pressa di vulcanizzazione")
            w.insert("funcloc", (f"{fl}-P{n:02d}",), {"FunctionalLocation": f"{fl}-P{n:02d}", "FunctionalLocationName": f"Postazione P{n:02d}",
                                                      "SuperiorFunctionalLocation": fl}, U)
            w.insert("equipment", (eq,), {"Equipment": eq, "EquipmentName": f"{kind} P{n:02d}", "FunctionalLocation": f"{fl}-P{n:02d}",
                                          "MaintenancePlanningPlant": PLANT, "MainWorkCenter": "MAINT01", "TechnicalObjectType": "PRESSA",
                                          "_wc": wc, "_tonnage": line["tonnage"], "_line": code}, U)
            w.insert("workcenter", (wc,), {"WorkCenter": wc, "Plant": PLANT, "WorkCenterCategoryCode": "0001",
                                           "WorkCenterDesc": f"Pressa P{n:02d}", "CapacityPersons": 1, "Equipment": eq,
                                           "_blocked": False, "_line": code}, U)
            for i, (char, unit, upper, counter) in enumerate((("Temperatura olio", "°C", 65.0, False),
                                                             ("Pressione di mantenimento", "bar", 175.0, False),
                                                             ("Contatore cicli", "CYC", None, True))):
                mp = f"{n:02d}{i + 1}"
                w.insert("measpoint", (mp,), {"MeasuringPoint": mp, "MeasuringPointDescription": f"{char} P{n:02d}",
                                              "TechnicalObject": eq, "MeasurementRangeUnit": unit,
                                              "MeasuringPointIsCounter": counter, "MeasurementRangeUpperLimit": upper}, U)
            # Verità simulata: usura nascosta. P07 è la pressa della storia del deck.
            wear = 78.0 if n == 7 else round(w.rng.uniform(10, 86), 1)
            w.machines[wc] = {"equipment": eq, "line": code, "state": "RUN", "wear": wear,
                              "fail_at": round(w.rng.uniform(96, 108), 1), "cycles": w.rng.randint(400_000, 2_000_000),
                              "temp": 48.0, "pressure": 150.0, "setup_left": 0.0, "mold": None, "down_since": None,
                              "maint_left": 0.0, "maint_order": None, "produced": {}}

    # ---------- materiali ----------
    for mat, (line, mold, compound, insert, cycle, desc) in FINISHED.items():
        w.insert("product", (mat,), {"Product": mat, "ProductType": "FERT", "ProductDescription": desc, "BaseUnit": "PC",
                                     "Plant": PLANT, "MRPType": "PD", "LotSizingProcedure": "EX", "ProcurementType": "E",
                                     "InHouseProductionTime": 2, "PlannedDeliveryDurationInDays": 0, "SafetyStockQuantity": 2000,
                                     "_mold": mold, "_line": line, "_cycle_s": cycle}, U)
        items = [{"BillOfMaterialComponent": compound[0], "BillOfMaterialItemQuantity": compound[1], "BillOfMaterialItemUnit": "KG"}]
        if insert:
            items.append({"BillOfMaterialComponent": insert[0], "BillOfMaterialItemQuantity": insert[1], "BillOfMaterialItemUnit": "PC"})
        w.insert("bom", (mat,), {"Material": mat, "Plant": PLANT, "BillOfMaterialVariant": "1", "Items": items}, U)
        w.insert("routing", (mat,), {"Material": mat, "Plant": PLANT, "Operations": [{
            "Operation": "0010", "OperationText": f"Stampaggio {mat} · stampo {mold}",
            "WorkCenterGroup": line, "Mold": mold, "MachineTimeSecPerPiece": cycle,
            "SetupMoldChangeMin": SETUP_MOLD_CHANGE_MIN, "SetupSameMoldMin": SETUP_SAME_MOLD_MIN}]}, U)
    for mat, (desc, unit, lead, lot) in RAW.items():
        w.insert("product", (mat,), {"Product": mat, "ProductType": "ROH", "ProductDescription": desc, "BaseUnit": unit,
                                     "Plant": PLANT, "MRPType": "PD", "LotSizingProcedure": "FX", "FixedLotSize": lot,
                                     "ProcurementType": "F", "PlannedDeliveryDurationInDays": lead, "SafetyStockQuantity": lot / 2}, U)
    for mat, (desc, unit, lead, _) in SPARES.items():
        w.insert("product", (mat,), {"Product": mat, "ProductType": "ERSA", "ProductDescription": desc, "BaseUnit": unit,
                                     "Plant": PLANT, "MRPType": "ND", "ProcurementType": "F", "PlannedDeliveryDurationInDays": lead}, U)
    # materiale volutamente non esteso alla divisione 1000 (per i casi d'errore)
    w.insert("product", ("RES-HEAT-08",), {"Product": "RES-HEAT-08", "ProductType": "ERSA", "ProductDescription": "Resistenza 8 kW",
                                           "BaseUnit": "PC", "Plant": "2000", "MRPType": "ND", "ProcurementType": "F",
                                           "PlannedDeliveryDurationInDays": 10}, U)

    # ---------- piani di manutenzione: lubrificazione ogni 30 giorni, sfalsati ----------
    for n in range(1, 44):
        eq = press_equipment(n)
        plan = f"3000{n:02d}"
        w.insert("maintplan", (plan,), {
            "MaintenancePlan": plan, "MaintenancePlanDesc": f"Lubrificazione e controllo P{n:02d}", "Equipment": eq,
            "MaintenancePlanningPlant": PLANT, "MaintPlanCycleDays": 30, "MaintenanceOrderType": "PM02",
            "MainWorkCenter": "MAINT01", "PlannedWorkHours": 2.0, "Components": [{"Material": "OLIO-HYD-46", "Quantity": 5}],
            "NextPlannedDate": (w.today + dt.timedelta(days=(n * 7) % 30 + 1)),
        }, U)

    # ---------- utenti ----------
    services_all = ("API_MAINTNOTIFICATION", "API_MAINTENANCEORDER", "API_MAINTORDERCONFIRMATION", "API_MAINTENANCEPLAN",
                    "API_EQUIPMENT", "API_FUNCTIONALLOCATION", "API_MEASURINGPOINT", "API_MEASUREMENTDOCUMENT_SRV",
                    "API_PURCHASEREQ_PROCESS_SRV", "API_RESERVATION_DOCUMENT_SRV", "API_MATERIAL_DOCUMENT_SRV",
                    "API_MATERIAL_STOCK_SRV", "API_PRODUCT_SRV", "API_BILL_OF_MATERIAL_SRV", "API_PRODUCTION_ROUTING",
                    "API_WORK_CENTERS", "API_PLND_INDEP_RQMT_SRV", "API_PLANNED_ORDERS", "API_PRODUCTION_ORDER_2_SRV",
                    "API_PROD_ORDER_CONFIRMATION_2_SRV", "API_MRP_MATERIALS_SRV_01", "API_WAREHOUSE_ORDER_TASK_2",
                    "API_WHSE_PHYSSTOCKPROD", "API_WAREHOUSE_STORAGE_BIN")
    read_all = {f"{s}:read" for s in services_all}
    w.users["PLANNER"] = {"password": "demo", "auths": {f"{s}:{op}" for s in services_all
                                                        for op in ("read", "create", "update", "delete", "release")}}
    w.users["ATHERYA_TECH"] = {"password": "demo", "auths": read_all | {
        "API_MAINTNOTIFICATION:create", "API_MAINTNOTIFICATION:update",
        "API_MAINTENANCEORDER:create", "API_MAINTENANCEORDER:update",
        "API_PURCHASEREQ_PROCESS_SRV:create", "API_PURCHASEREQ_PROCESS_SRV:delete",
        "API_RESERVATION_DOCUMENT_SRV:create", "API_RESERVATION_DOCUMENT_SRV:delete",
        "API_PRODUCTION_ORDER_2_SRV:update", "API_MEASUREMENTDOCUMENT_SRV:create",
    }}
