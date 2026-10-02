"""La fabbrica che vive: un passo ogni 15 minuti simulati.

Chi fa cosa (utenti simulati, tutto tracciato nel registro modifiche):
- le presse producono, si usurano, si fermano per guasto (verità nascosta: SAP vede solo le misure);
- OPERATORE: avvisi di guasto, conferme di fine turno;
- SCADA: documenti di misura ogni ora (temperatura olio, pressione), contatore cicli a fine turno;
- MAGAZZINO: conferma i compiti di magazzino, inventari a rotazione il sabato;
- MANUTENZIONE: ordini correttivi sui guasti, rilascio dei preventivi; MANUTENTORE: esegue gli interventi;
- BATCH: MRP notturno, schedulazione dei piani di manutenzione;
- PIANIFICATORE: converte e rilascia gli ordini, sposta il lavoro dalle presse ferme;
- ACQUISTI / FORNITORE: ordinano e consegnano le RdA; SPEDIZIONI: consegne ai clienti.
"""

import datetime as dt

from . import inventory as inv
from . import pm, pp
from .calendar import is_working, working_days_before
from .masterdata import SPARES
from .world import BusinessError

STEP_MIN = 15
MOLD_WEAR = {"S-118": 1.15}
LINE_WEAR = {"A": 1.0, "B": 0.9, "C": 0.8}
WEAR_PER_PIECE = 0.0007
CREW_CAPACITY = {"MAINT01": 3, "MAINT02": 1}
BREAKDOWN_REPAIR_H = 8.0


def advance(w, minutes: int) -> None:
    for _ in range(max(1, minutes // STEP_MIN)):
        _step(w)


def _step(w) -> None:
    before = w.now
    w.now = w.now + dt.timedelta(minutes=STEP_MIN)
    if is_working(before):
        _machines(w)
    else:
        for m in w.machines.values():
            m["temp"] += (32 - m["temp"]) * 0.05
    _maintenance(w)
    if w.now.minute == 0:
        _hourly(w)
        h = w.now.hour
        if h in (6, 14, 22):
            _shift_end(w)
        if h == 2:
            _nightly(w)
        if h == 10:
            inv.procurement_cycle(w)
        if h == 16:
            pp.ship_daily(w)
        if h == 8 and w.today.weekday() == 5:
            _cycle_count(w)


# ---------------------------------------------------------------- presse

def _current_op(w, wc: str):
    ops = [op for op in w.t("prodop").values()
           if op["WorkCenter"] == wc and op["_status"] != "done"
           and w.get("prodorder", (op["ManufacturingOrder"],))["OrderSystemStatus"] in ("REL", "PCNF")]
    started = [op for op in ops if op["_status"] == "started"]
    if started:
        return started[0]
    ready = [op for op in ops if op["_start"] is not None]
    return min(ready, key=lambda op: op["_start"]) if ready else None


def _machines(w) -> None:
    for wc, m in w.machines.items():
        if m["state"] in ("DOWN", "MAINT"):
            w.kpi["downtime_min"] = w.kpi.get("downtime_min", 0) + (STEP_MIN if m["state"] == "DOWN" else 0)
            m["temp"] += (35 - m["temp"]) * 0.1
            continue
        op = _current_op(w, wc)
        if not op:
            m["state"] = "IDLE"
            m["temp"] += (38 - m["temp"]) * 0.1
            continue
        m["state"] = "RUN"
        minutes = float(STEP_MIN)
        if op["_status"] == "open":
            m["setup_left"] = pp.SETUP_SAME_MOLD_MIN if m["mold"] == op["Mold"] else pp.SETUP_MOLD_CHANGE_MIN
            m["mold"] = op["Mold"]
            w.update("prodop", (op["ManufacturingOrder"], op["ManufacturingOrderOperation"]),
                     {"_status": "started", "OperationStatus": "In corso", "OpActualExecutionStartDate": w.today}, "OPERATORE", quiet=True)
            w.event("Produzione", f"{wc}: avvio ordine {op['ManufacturingOrder']} (stampo {op['Mold']})", op["ManufacturingOrder"])
        if m["setup_left"] > 0:
            used = min(m["setup_left"], minutes)
            m["setup_left"] -= used
            minutes -= used
        if minutes <= 0:
            continue
        slow = 1 + max(0.0, m["wear"] - 60) / 200  # l'usura allunga il ciclo
        perf = w.rng.uniform(0.86, 0.96)
        pieces = int(minutes * 60 / (op["_cycle_s"] * slow) * perf)
        pieces = int(min(pieces, op["_remaining"]))
        scrap = round(pieces * (0.012 + max(0.0, m["wear"] - 70) / 900))
        op["_remaining"] = max(0.0, op["_remaining"] - pieces)
        op["_unconfirmed_yield"] += pieces - scrap
        op["_unconfirmed_scrap"] += scrap
        m["cycles"] += pieces
        w.kpi["produced"] = w.kpi.get("produced", 0) + pieces - scrap
        w.kpi["scrap"] = w.kpi.get("scrap", 0) + scrap
        m["wear"] = round(m["wear"] + pieces * WEAR_PER_PIECE * MOLD_WEAR.get(op["Mold"], 1.0) * LINE_WEAR[m["line"]], 4)
        m["temp"] = 45 + m["wear"] * 0.18 + w.rng.gauss(0, 0.8)
        m["pressure"] = 150 - m["wear"] * 0.05 + w.rng.gauss(0, 1.5 * (1 + max(0.0, m["wear"] - 50) / 12))
        if m["wear"] >= m["fail_at"]:
            _breakdown(w, wc, m)


def _breakdown(w, wc: str, m: dict) -> None:
    m["state"] = "DOWN"
    m["down_since"] = w.now
    w.kpi["breakdowns"] = w.kpi.get("breakdowns", 0) + 1
    num = pm.create_notification(w, {
        "NotificationType": "M2", "NotificationText": "Perdita d'olio, pressione instabile: macchina ferma",
        "MaintNotifLongText": f"Guasto rilevato dall'operatore alle {w.now:%H:%M}. Pressa ferma.",
        "TechnicalObject": m["equipment"], "TechObjIsEquipOrFuncnlLoc": "EAMS_EQUI", "MaintenancePlanningPlant": "1000",
        "MaintPriority": "1", "IsBreakdown": True, "MalfunctionStartDate": w.today, "MalfunctionStartTime": w.now.time()}, "OPERATORE")[0]
    w.event("Produzione", f"GUASTO su {wc}: pressa ferma, avviso {num}", num)


# ---------------------------------------------------------------- manutenzione

def _maintenance(w) -> None:
    busy = {k: 0 for k in CREW_CAPACITY}
    for key, o in w.t("morder").items():
        ex = o.get("_exec")
        if not ex:
            continue
        busy[o["MainWorkCenter"]] = busy.get(o["MainWorkCenter"], 0) + 1
        ex["left"] -= STEP_MIN
        if ex["left"] <= 0:
            _complete_intervention(w, o)
    if not (6 <= w.now.hour < 22):
        return
    due = sorted([o for o in w.t("morder").values()
                  if o["MaintOrdSystemStatus"] == "REL" and not o.get("_exec") and not o.get("IsDeleted")
                  and o["MaintOrdBasicStartDate"] <= w.today],
                 key=lambda o: (o["MaintPriority"], o["MaintOrdBasicStartDate"], o["MaintenanceOrder"]))
    for o in due:
        crew = o["MainWorkCenter"]
        if busy.get(crew, 0) >= CREW_CAPACITY.get(crew, 1):
            continue
        if pm.try_start(w, o["MaintenanceOrder"]):
            busy[crew] = busy.get(crew, 0) + 1
            wc = w.get("equipment", (o["Equipment"],)).get("_wc")
            if wc in w.machines and w.machines[wc]["state"] != "DOWN":
                w.machines[wc]["state"] = "MAINT"
        elif o.get("_waiting"):
            _request_missing_spare(w, o)


def _complete_intervention(w, o: dict) -> None:
    spare = o["_exec"].get("spare")
    comps = {c["Material"] for c in o["to_MaintenanceOrderComponent"]["results"]}
    pm.finish(w, o["MaintenanceOrder"])
    eq = w.get("equipment", (o["Equipment"],))
    wc = eq.get("_wc")
    if wc not in w.machines:
        return
    m = w.machines[wc]
    replaced = spare or any(c.startswith("GUARN-HYD") for c in comps)
    if replaced:
        m["wear"] = round(w.rng.uniform(2, 6), 1)
        m["fail_at"] = round(w.rng.uniform(96, 108), 1)
        if o.get("_breakdown"):
            w.kpi["corrective_replacements"] = w.kpi.get("corrective_replacements", 0) + 1
        else:
            w.kpi["preventive_replacements"] = w.kpi.get("preventive_replacements", 0) + 1
    elif o["MaintenanceOrderType"] == "PM02":
        m["wear"] = max(0.0, round(m["wear"] - 3, 1))
    if m["state"] == "DOWN" and not replaced:
        return  # un intervento che non sostituisce la guarnizione non ripara il guasto
    m["state"] = "RUN"
    m["down_since"] = None
    for n_key, n in w.t("notif").items():
        if n.get("IsBreakdown") and n["TechnicalObject"] == eq["Equipment"] and n["MaintNotifProcessPhaseCode"] != "4" and replaced:
            pm.complete_notification(w, n_key[0], "MANUTENZIONE")


def _request_missing_spare(w, o: dict) -> None:
    spare = pm.required_spare(w, o)
    mats = [spare[0]] if spare else [c["Material"] for c in o["to_MaintenanceOrderComponent"]["results"]]
    for mat in mats:
        if inv.stock_qty(w, mat) >= 1 or inv.open_supply(w, mat):
            continue
        p = inv.product(w, mat)
        inv.create_purchase_requisition(w, {"PurchaseRequisitionType": "NB", "to_PurchaseReqnItem": {"results": [
            {"Material": mat, "Plant": "1000", "RequestedQuantity": "2", "BaseUnit": p["BaseUnit"],
             "DeliveryDate": w.today + dt.timedelta(days=p["PlannedDeliveryDurationInDays"]), "PurReqnSource": "Urgente"}]}}, "MANUTENZIONE")
        w.event("Manutenzione", f"Richiesta d'acquisto urgente per {mat} (ordine {o['MaintenanceOrder']})", o["MaintenanceOrder"])


def _corrective_orders(w) -> None:
    for key, n in w.t("notif").items():
        if not n.get("IsBreakdown") or n.get("MaintenanceOrder") or n["MaintNotifProcessPhaseCode"] == "4":
            continue
        if (w.now - dt.datetime.combine(n["NotificationCreationDate"], n["NotificationCreationTime"])).total_seconds() < 1800:
            continue
        num = pm.create_order(w, {
            "MaintenanceOrderType": "PM01", "MaintenanceOrderDesc": "Riparazione guasto: sostituzione guarnizione idraulica",
            "Equipment": n["TechnicalObject"], "MaintenancePlanningPlant": "1000", "MainWorkCenter": "MAINT01",
            "MaintOrdBasicStartDate": w.today, "MaintenanceNotification": key[0], "MaintPriority": "1",
            "to_MaintenanceOrderOperation": {"results": [{"OperationDescription": "Smontaggio, sostituzione e collaudo",
                                                          "WorkCenter": "MAINT01", "PlannedWorkQuantity": BREAKDOWN_REPAIR_H}]}},
            "MANUTENZIONE")[0]
        w.update("morder", (num,), {"_breakdown": True}, "MANUTENZIONE", quiet=True)
        pm.release_order(w, num, "MANUTENZIONE")


# ---------------------------------------------------------------- orari

def _hourly(w) -> None:
    for wc, m in w.machines.items():
        n = int(wc[-2:])
        if is_working(w.now) or m["state"] != "IDLE":
            pm.create_measurement(w, {"MeasuringPoint": f"{n:02d}1", "MeasurementReading": round(m["temp"], 1)}, "SCADA")
            pm.create_measurement(w, {"MeasuringPoint": f"{n:02d}2", "MeasurementReading": round(m["pressure"], 1)}, "SCADA")
    for key, t in list(w.t("whtask").items()):
        if t["WarehouseTaskStatus"] == "" and (w.now - t["WhseTaskCreationDateTime"]).total_seconds() >= 1800:
            try:
                inv.confirm_task(w, key[0], "MAGAZZINO")
            except BusinessError as e:
                if not t.get("_warned"):
                    t["_warned"] = True
                    w.event("Magazzino", f"Compito {key[0]} non eseguibile: {e.message}", key[0])
    _corrective_orders(w)
    _move_work_from_stopped_presses(w)
    _user_locks(w)


def _user_locks(w) -> None:
    """Persone che hanno un documento aperto in modifica (IW32, CO02): blocchi enqueue visibili in SM12.

    Contano solo per la facciata ECC (RFC): un BAPI su un oggetto bloccato fallisce come nel sistema vero.
    Usa un generatore casuale separato, così la storia della fabbrica non cambia.
    """
    for key in [k for k, l in w.locks.items() if l.get("until") and l["until"] <= w.now]:
        del w.locks[key]
    if not is_working(w.now):
        return
    r = w.lock_rng
    prod = sorted(k[0] for k, o in w.t("prodorder").items() if o["OrderSystemStatus"] in ("CRTD", "REL", "PCNF"))
    for order in r.sample(prod, min(2, len(prod))):
        if ("ORDER", order.zfill(12)) not in w.locks:
            w.locks[("ORDER", order.zfill(12))] = {"user": "PIANIFICATORE", "tcode": "CO02", "since": w.now,
                                         "until": w.now + dt.timedelta(minutes=r.choice((15, 30, 45)))}
    maint = sorted(k[0] for k, o in w.t("morder").items() if o["MaintOrdSystemStatus"] in ("CRTD", "REL"))
    if maint and r.random() < 0.5:
        order = r.choice(maint)
        if ("ORDER", order.zfill(12)) not in w.locks:
            w.locks[("ORDER", order.zfill(12))] = {"user": "MANUTENZIONE", "tcode": "IW32", "since": w.now,
                                         "until": w.now + dt.timedelta(minutes=r.choice((15, 30)))}


def _shift_end(w) -> None:
    for (order, opnum), op in list(w.t("prodop").items()):
        y, s = round(op["_unconfirmed_yield"]), round(op["_unconfirmed_scrap"])
        if y + s <= 0:
            continue
        op["_unconfirmed_yield"] = 0.0
        op["_unconfirmed_scrap"] = 0.0
        try:
            pp.confirm_operation(w, {"OrderID": order, "OrderOperation": opnum, "ConfirmationYieldQuantity": y,
                                     "ConfirmationScrapQuantity": s, "_from_machine": True,
                                     "_real_factor": w.rng.uniform(0.97, 1.04),
                                     "ConfirmationText": "Conferma di fine turno"}, "OPERATORE")
        except BusinessError as e:
            w.event("Produzione", f"Conferma non registrata su {order}: {e.message}", order)
    for wc, m in w.machines.items():
        n = int(wc[-2:])
        pm.create_measurement(w, {"MeasuringPoint": f"{n:02d}3", "MeasurementReading": m["cycles"]}, "SCADA")


def _move_work_from_stopped_presses(w) -> None:
    """Il pianificatore sposta le operazioni non iniziate dalle presse ferme per guasto."""
    for wc, m in w.machines.items():
        if m["state"] != "DOWN":
            continue
        for key, op in list(w.t("prodop").items()):
            if op["WorkCenter"] != wc or op["_status"] != "open":
                continue
            mat = w.get("prodorder", (op["ManufacturingOrder"],))["Material"]
            options = [c for c in pp.eligible_workcenters(w, mat) if c != wc and w.machines[c]["state"] != "DOWN" and pp._available(w, c)]
            if not options:
                continue
            target = min(options, key=lambda c: pp._open_minutes(w, c))
            try:
                pp.update_operation(w, key, op, {"WorkCenter": target}, "PIANIFICATORE")
            except BusinessError:
                pass


def _nightly(w) -> None:
    pm.schedule_plans(w)
    pp.retry_backflush_errors(w)
    for key, o in w.t("morder").items():
        if (o["MaintOrdSystemStatus"] == "CRTD" and o["MaintenanceOrderType"] == "PM02" and not o.get("IsDeleted") and not o.get("_locked")
                and o["MaintOrdBasicStartDate"] <= w.today + dt.timedelta(days=2)):
            pm.release_order(w, key[0], "MANUTENZIONE")
    pp.run_mrp(w, "BATCH")
    horizon = w.today + dt.timedelta(days=4)
    for key, p in sorted(w.t("plannedorder").items(), key=lambda kv: kv[1]["PlndOrderPlannedStartDate"]):
        if p["PlndOrderPlannedStartDate"] <= horizon:
            try:
                pp.convert_planned_order(w, key[0], "PIANIFICATORE")
            except BusinessError as e:
                w.event("Pianificazione", f"Ordine pianificato {key[0]} non convertito: {e.message}", key[0])
    for key, o in w.t("prodorder").items():
        if o["OrderSystemStatus"] == "CRTD" and o["MfgOrderPlannedStartDate"] <= w.today + dt.timedelta(days=2):
            pp.release_production_order(w, key[0], "PIANIFICATORE")
    pp.schedule_all(w)


def _cycle_count(w) -> None:
    occupied = sorted({k[0] for k in w.t("quant") if not k[0].startswith(("GR", "GI", "PSA"))})
    for bin_ in w.rng.sample(occupied, min(3, len(occupied))):
        for doc in inv.create_physical_inventory(w, bin_, "MAGAZZINO"):
            inv.count_physical_inventory(w, doc, "MAGAZZINO")
            md = inv.post_physical_inventory(w, doc, "MAGAZZINO")
            if md:
                pi = w.get("physinv", (doc,))
                w.event("Magazzino", f"Inventario {bin_}: differenza {pi['DifferenceQuantity']:g} {pi['Product']}", doc)


# ---------------------------------------------------------------- costruzione del mondo

def build_world(start: dt.datetime | None = None, seed: int = 7):
    from .masterdata import FINISHED, RAW, seed as seed_master
    from .world import World
    w = World(start or dt.datetime(2026, 10, 1, 6, 0), seed)
    seed_master(w)
    pp.seed_demand(w)
    weekly = pp.weekly_requirements(w)
    initial = {m: round(weekly[m] * 1.6, -1) for m in RAW}           # circa otto giorni lavorativi di materie prime
    initial.update({m: round(weekly[m] * 0.6, -2) for m in FINISHED})  # tre giorni di spedizioni
    initial.update({m: v[3] for m, v in SPARES.items() if v[3] > 0})
    inv.seed_warehouse(w, initial)
    # L'ordine della storia: 4.800 × A-2210 su P07, già rilasciato.
    pp.create_production_order(w, "A-2210", 4800, w.today, w.today + dt.timedelta(days=2), "PRESS07", "SEED", number="1000471")
    pp.release_production_order(w, "1000471", "SEED")
    pp.run_mrp(w, "BATCH", raw=False)
    for key, p in sorted(w.t("plannedorder").items(), key=lambda kv: kv[1]["PlndOrderPlannedStartDate"]):
        if p["PlndOrderPlannedStartDate"] <= w.today + dt.timedelta(days=3):
            pp.convert_planned_order(w, key[0], "PIANIFICATORE")
    for key, o in list(w.t("prodorder").items()):
        if o["OrderSystemStatus"] == "CRTD" and o["MfgOrderPlannedStartDate"] <= w.today + dt.timedelta(days=1):
            pp.release_production_order(w, key[0], "PIANIFICATORE")
    for key, t in list(w.t("whtask").items()):  # il magazzino ha già approvvigionato le linee
        if t["WarehouseTaskStatus"] == "":
            inv.confirm_task(w, key[0], "MAGAZZINO")
    pp.schedule_all(w)
    from .ecc.rfc import seed_users as seed_ecc_users
    seed_ecc_users(w)
    w.log.clear()
    w.events.clear()
    w.event("Simulazione", f"Fabbrica avviata: {len(w.t('prodorder'))} ordini di produzione, {len(w.t('plannedorder'))} ordini pianificati")
    return w
