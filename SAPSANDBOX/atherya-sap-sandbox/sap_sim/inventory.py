"""Magazzino (EWM semplificato) e movimenti merci.

- Struttura: tipi di magazzino e ubicazioni, quanti per ubicazione.
- Compiti di magazzino: stoccaggio, approvvigionamento della produzione, prelievo.
- Documenti materiale: 101 entrata, 261 uscita a ordine, 201 uscita per manutenzione, 701/702 differenze inventariali.
- Prenotazioni manuali e richieste d'acquisto, con consegne dei fornitori simulate (niente ordini d'acquisto né fatture).
- Inventario fisico: ogni quanto ha una quantità contabile e una reale (che diverge con i consumi reali).
"""

import datetime as dt

from .masterdata import PLANT, SLOC, WAREHOUSE
from .world import BusinessError

STORAGE_TYPES = {
    "0010": ("Materie prime", [f"RM-{i:02d}" for i in range(1, 13)]),
    "0020": ("Prodotti finiti", [f"FG-{i:02d}" for i in range(1, 25)]),
    "0030": ("Ricambi di manutenzione", [f"SP-{i:02d}" for i in range(1, 7)]),
    "0100": ("Approvvigionamento produzione", ["PSA-LA", "PSA-LB", "PSA-LC"]),
    "9010": ("Zona entrata merci", ["GR-ZONE"]),
    "9020": ("Zona uscita merci", ["GI-ZONE"]),
}
TYPE_FOR_PRODUCT = {"ROH": "0010", "FERT": "0020", "HALB": "0020", "ERSA": "0030"}
MOVEMENT_TEXT = {"101": "Entrata merci", "261": "Uscita merci a ordine", "201": "Uscita merci a centro di costo/manutenzione",
                 "701": "Differenza inventariale in eccesso", "702": "Differenza inventariale in difetto", "561": "Giacenza iniziale",
                 "601": "Uscita merci per consegna"}


# ---------------------------------------------------------------- struttura e giacenze iniziali

def seed_warehouse(w, initial: dict[str, float]) -> None:
    for stype, (desc, bins) in STORAGE_TYPES.items():
        w.insert("storagetype", (stype,), {"EWMWarehouse": WAREHOUSE, "EWMStorageType": stype, "EWMStorageTypeName": desc}, "SEED")
        for b in bins:
            w.insert("bin", (b,), {"EWMWarehouse": WAREHOUSE, "EWMStorageType": stype, "EWMStorageBin": b}, "SEED")
    items = []
    for product, qty in initial.items():
        bin_ = free_bin_for(w, product)
        add_quant(w, bin_, product, qty, "SEED")
        items.append({"Material": product, "QuantityInEntryUnit": qty, "EWMStorageBin": bin_})
    _material_document(w, "561", items, "SEED", "Giacenza iniziale")


def product(w, mat: str) -> dict:
    row = w.t("product").get((mat,))
    if not row:
        raise BusinessError("M3/305", f"Materiale {mat} inesistente")
    return row


def free_bin_for(w, mat: str) -> str:
    stype = TYPE_FOR_PRODUCT[product(w, mat)["ProductType"]]
    bins = STORAGE_TYPES[stype][1]
    for b in bins:  # prima un'ubicazione che contiene già il materiale
        if (b, mat) in w.t("quant"):
            return b
    used = {k[0] for k in w.t("quant")}
    return next((b for b in bins if b not in used), bins[0])


def add_quant(w, bin_: str, mat: str, qty: float, user: str, real: float | None = None) -> None:
    key = (bin_, mat)
    real = qty if real is None else real
    q = w.t("quant").get(key)
    if q:
        w.update("quant", key, {"EWMStockQuantityInBaseUnit": round(q["EWMStockQuantityInBaseUnit"] + qty, 3),
                                "_real": round(q["_real"] + real, 3)}, user, quiet=True)
    else:
        p = product(w, mat)
        w.insert("quant", key, {"EWMWarehouse": WAREHOUSE, "EWMStorageType": w.get("bin", (bin_,))["EWMStorageType"],
                                "EWMStorageBin": bin_, "Product": mat, "EWMStockType": "F2",
                                "EWMStockQuantityInBaseUnit": round(qty, 3), "EWMStockQuantityBaseUnit": p["BaseUnit"],
                                "_real": round(real, 3)}, user, quiet=True)


def remove_quant(w, bin_: str, mat: str, qty: float, user: str, real: float | None = None) -> None:
    key = (bin_, mat)
    q = w.t("quant").get(key)
    if not q or q["EWMStockQuantityInBaseUnit"] + 1e-9 < qty:
        have = q["EWMStockQuantityInBaseUnit"] if q else 0
        raise BusinessError("/SCWM/L3/012", f"Giacenza insufficiente di {mat} in {bin_}: disponibile {have:g}, richiesto {qty:g}")
    real = qty if real is None else real
    left, left_real = round(q["EWMStockQuantityInBaseUnit"] - qty, 3), round(q["_real"] - real, 3)
    if left <= 1e-9 and abs(left_real) <= 1e-9:
        w.delete("quant", key, user, quiet=True)
    else:
        w.update("quant", key, {"EWMStockQuantityInBaseUnit": left, "_real": left_real}, user, quiet=True)


def stock_qty(w, mat: str, exclude_types: tuple = ("9020",)) -> float:
    return round(sum(q["EWMStockQuantityInBaseUnit"] for (b, m), q in w.t("quant").items()
                     if m == mat and q["EWMStorageType"] not in exclude_types), 3)


def bins_with(w, mat: str, types: tuple) -> list[tuple[str, float]]:
    found = [(b, q["EWMStockQuantityInBaseUnit"]) for (b, m), q in w.t("quant").items()
             if m == mat and q["EWMStorageType"] in types and q["EWMStockQuantityInBaseUnit"] > 0]
    return sorted(found, key=lambda x: -x[1])


def plant_stock_rows(w) -> list[dict]:
    """Vista per divisione/magazzino come A_MatlStkInAcctMod: somma dei quanti EWM."""
    totals: dict[str, float] = {}
    for (b, m), q in w.t("quant").items():
        totals[m] = totals.get(m, 0) + q["EWMStockQuantityInBaseUnit"]
    rows = []
    for m in sorted(totals):
        p = product(w, m)
        rows.append({"Material": m, "Plant": PLANT, "StorageLocation": SLOC, "InventoryStockType": "01",
                     "MatlWrhsStkQtyInMatlBaseUnit": f"{round(totals[m], 3):g}", "MaterialBaseUnit": p["BaseUnit"],
                     "_etag": "", "_by": "SEED", "_at": w.start, "_changed_by": "SEED"})
    return rows


# ---------------------------------------------------------------- documenti materiale

def _material_document(w, mvt: str, items: list[dict], user: str, text: str = "") -> str:
    doc = w.number("matdoc", 4900000000)
    rows = []
    for i, it in enumerate(items, start=1):
        p = product(w, it["Material"])
        rows.append({"MaterialDocumentItem": f"{i:04d}", "Material": it["Material"], "Plant": PLANT, "StorageLocation": SLOC,
                     "GoodsMovementType": mvt, "QuantityInEntryUnit": round(it["QuantityInEntryUnit"], 3), "EntryUnit": p["BaseUnit"],
                     "ManufacturingOrder": it.get("ManufacturingOrder", ""), "MaintenanceOrder": it.get("MaintenanceOrder", ""),
                     "Reservation": it.get("Reservation", ""), "PurchaseRequisition": it.get("PurchaseRequisition", ""),
                     "EWMStorageBin": it.get("EWMStorageBin", "")})
    w.insert("matdoc", (doc,), {"MaterialDocument": doc, "MaterialDocumentYear": str(w.today.year),
                                "PostingDate": w.today, "DocumentDate": w.today, "GoodsMovementType": mvt,
                                "MaterialDocumentHeaderText": text or MOVEMENT_TEXT.get(mvt, ""), "Items": rows}, user)
    return doc


def goods_receipt(w, mat: str, qty: float, user: str, refs: dict, text: str) -> str:
    """101: la merce arriva in zona entrata e nasce il compito di stoccaggio."""
    add_quant(w, "GR-ZONE", mat, qty, user)
    doc = _material_document(w, "101", [{"Material": mat, "QuantityInEntryUnit": qty, "EWMStorageBin": "GR-ZONE", **refs}], user, text)
    create_task(w, "STOCCAGGIO", mat, qty, "GR-ZONE", free_bin_for(w, mat), doc, user)
    return doc


def goods_issue(w, mvt: str, mat: str, qty: float, user: str, refs: dict, preferred_types: tuple, text: str,
                real_factor: float = 1.0) -> str:
    """261/201: preleva dalle ubicazioni preferite, poi dal resto del magazzino."""
    if stock_qty(w, mat) + 1e-9 < qty:
        raise BusinessError("M7/021", f"Disponibilità insufficiente di {mat}: disponibile {stock_qty(w, mat):g}, richiesto {qty:g}")
    remaining, items = qty, []
    order = bins_with(w, mat, preferred_types) + [b for b in bins_with(w, mat, ("0010", "0020", "0030", "0100", "9010"))
                                                  if b[0] not in {x[0] for x in bins_with(w, mat, preferred_types)}]
    for bin_, have in order:
        take = min(have, remaining)
        remove_quant(w, bin_, mat, take, user, real=take * real_factor)
        items.append({"Material": mat, "QuantityInEntryUnit": take, "EWMStorageBin": bin_, **refs})
        remaining = round(remaining - take, 6)
        if remaining <= 1e-9:
            break
    return _material_document(w, mvt, items, user, text)


# ---------------------------------------------------------------- compiti di magazzino

def create_task(w, process: str, mat: str, qty: float, src: str | None, dst: str, ref: str, user: str) -> list[str]:
    """Se l'ubicazione di origine non è indicata, si sceglie (e si divide) tra quelle con giacenza."""
    sources = [(src, qty)] if src else []
    if not src:
        remaining = qty
        for bin_, have in bins_with(w, mat, ("0010", "0020", "0030")):
            have = round(have - committed(w, bin_, mat), 3)  # già impegnato da compiti aperti
            if have <= 0:
                continue
            take = min(have, remaining)
            sources.append((bin_, take))
            remaining -= take
            if remaining <= 1e-9:
                break
        if remaining > 1e-9:
            w.event("Magazzino", f"Giacenza insufficiente per {process.lower()} di {mat}: mancano {remaining:g}", ref)
            if not sources:
                return []
    created = []
    for bin_, q in sources:
        wt = w.number("whtask", 100000)
        w.insert("whtask", (wt,), {"EWMWarehouse": WAREHOUSE, "WarehouseTask": wt, "WarehouseProcessType": process,
                                   "Product": mat, "TargetQuantityInBaseUnit": round(q, 3), "SourceStorageBin": bin_,
                                   "DestinationStorageBin": dst, "WarehouseTaskStatus": "", "EWMReferenceDocument": ref,
                                   "WhseTaskCreationDateTime": w.now, "WhseTaskConfirmationDateTime": None}, user)
        created.append(wt)
    return created


def committed(w, bin_: str, mat: str) -> float:
    return sum(t["TargetQuantityInBaseUnit"] for t in w.t("whtask").values()
               if t["WarehouseTaskStatus"] == "" and t["SourceStorageBin"] == bin_ and t["Product"] == mat)


def confirm_task(w, wt: str, user: str) -> None:
    t = w.get("whtask", (wt,))
    if t["WarehouseTaskStatus"] == "C":
        raise BusinessError("/SCWM/L3/101", f"Compito {wt} già confermato")
    q = w.t("quant").get((t["SourceStorageBin"], t["Product"]))
    available = q["EWMStockQuantityInBaseUnit"] if q else 0
    qty = min(available, t["TargetQuantityInBaseUnit"])
    if qty <= 0:
        raise BusinessError("/SCWM/L3/012", f"Nessuna giacenza di {t['Product']} in {t['SourceStorageBin']}")
    real_share = (q["_real"] / q["EWMStockQuantityInBaseUnit"]) if q["EWMStockQuantityInBaseUnit"] else 1
    remove_quant(w, t["SourceStorageBin"], t["Product"], qty, user, real=qty * real_share)
    add_quant(w, t["DestinationStorageBin"], t["Product"], qty, user, real=qty * real_share)
    changes = {"WarehouseTaskStatus": "C", "WhseTaskConfirmationDateTime": w.now, "ActualQuantityInBaseUnit": round(qty, 3)}
    w.update("whtask", (wt,), changes, user)
    if qty + 1e-9 < t["TargetQuantityInBaseUnit"]:
        w.event("Magazzino", f"Compito {wt} confermato parzialmente: {qty:g} di {t['TargetQuantityInBaseUnit']:g} {t['Product']}", wt)


# ---------------------------------------------------------------- prenotazioni manuali

def create_reservation(w, body: dict, user: str) -> tuple:
    items = (body.get("to_ReservationDocumentItem") or {}).get("results") or []
    if not items:
        raise BusinessError("M7/001", "Nessuna posizione nella prenotazione")
    for it in items:
        p = w.t("product").get((it.get("Material", ""),))
        if not p or p["Plant"] != it.get("Plant"):
            raise BusinessError("M3/351", f"Materiale {it.get('Material')} non esteso alla divisione {it.get('Plant')}")
    res = w.number("resv", 5000)
    row = {k: v for k, v in body.items() if k != "to_ReservationDocumentItem"}
    row.update({"Reservation": res, "GoodsMovementType": body.get("GoodsMovementType", "201"), "ReservationDate": w.today,
                "to_ReservationDocumentItem": {"results": [
                    {**it, "Reservation": res, "ReservationItem": f"{i:04d}", "ResvnItmWithdrawnQtyInBaseUnit": "0",
                     "ReservationItemIsFinallyIssued": False}
                    for i, it in enumerate(items, start=1)]}})
    w.insert("reservation", (res,), row, user)
    return (res,)


def withdraw_reservation_item(w, res: str, idx: int, qty: float, user: str, refs: dict) -> str:
    row = w.get("reservation", (res,))
    item = row["to_ReservationDocumentItem"]["results"][idx]
    doc = goods_issue(w, row["GoodsMovementType"], item["Material"], qty, user, {"Reservation": res, **refs}, ("0030",),
                      f"Prelievo da prenotazione {res}")
    item["ResvnItmWithdrawnQtyInBaseUnit"] = f"{float(item['ResvnItmWithdrawnQtyInBaseUnit']) + qty:g}"
    item["ReservationItemIsFinallyIssued"] = float(item["ResvnItmWithdrawnQtyInBaseUnit"]) + 1e-9 >= float(item["ResvnItmRequiredQtyInBaseUnit"])
    w.update("reservation", (res,), {"to_ReservationDocumentItem": row["to_ReservationDocumentItem"]}, user)
    return doc


# ---------------------------------------------------------------- richieste d'acquisto e fornitori simulati

def create_purchase_requisition(w, body: dict, user: str) -> tuple:
    items = (body.get("to_PurchaseReqnItem") or {}).get("results") or []
    if not items:
        raise BusinessError("ME/083", "Nessuna posizione nella richiesta d'acquisto")
    for it in items:
        p = w.t("product").get((it.get("Material", ""),))
        if not p or p["Plant"] != it.get("Plant"):
            raise BusinessError("M3/351", f"Materiale {it.get('Material')} non esteso alla divisione {it.get('Plant')}")
    pr = w.number("pr", 10010000)
    row = {k: v for k, v in body.items() if k != "to_PurchaseReqnItem"}
    row.update({"PurchaseRequisition": pr, "PurchaseRequisitionType": body.get("PurchaseRequisitionType", "NB"),
                "to_PurchaseReqnItem": {"results": [
                    {**it, "PurchaseRequisition": pr, "PurchaseRequisitionItem": f"{i * 10:05d}", "ProcessingStatus": "N",
                     "_delivery": None, "_received": False}
                    for i, it in enumerate(items, start=1)]}})
    w.insert("preq", (pr,), row, user)
    return (pr,)


def procurement_cycle(w) -> None:
    """Ufficio acquisti e fornitori simulati: ordinano le RdA aperte e consegnano alla data prevista."""
    for key, pr in list(w.t("preq").items()):
        if pr.get("IsDeleted"):
            continue
        changed = False
        for it in pr["to_PurchaseReqnItem"]["results"]:
            if it["ProcessingStatus"] == "N":
                lead = product(w, it["Material"]).get("PlannedDeliveryDurationInDays", 5)
                wanted = it.get("DeliveryDate")
                wanted = wanted if isinstance(wanted, dt.date) else w.today
                it["_delivery"] = max(wanted, w.today + dt.timedelta(days=lead))
                it["ProcessingStatus"] = "B"  # ordinata (ordine d'acquisto fuori simulazione)
                changed = True
                w.event("Acquisti", f"RdA {pr['PurchaseRequisition']}: {it['Material']} ordinato, consegna prevista {it['_delivery']:%d/%m}", pr["PurchaseRequisition"])
            elif it["ProcessingStatus"] == "B" and not it["_received"] and it["_delivery"] <= w.today:
                goods_receipt(w, it["Material"], float(it["RequestedQuantity"]), "FORNITORE",
                              {"PurchaseRequisition": pr["PurchaseRequisition"]}, f"Consegna fornitore per RdA {pr['PurchaseRequisition']}")
                it["_received"] = True
                it["ProcessingStatus"] = "C"
                changed = True
                w.event("Acquisti", f"Consegnato {it['RequestedQuantity']} {it['Material']} (RdA {pr['PurchaseRequisition']})", pr["PurchaseRequisition"])
        if changed:
            w.update("preq", key, {"to_PurchaseReqnItem": pr["to_PurchaseReqnItem"]}, "ACQUISTI")


def open_supply(w, mat: str) -> list[tuple[dt.date, float]]:
    out = []
    for pr in w.t("preq").values():
        if pr.get("IsDeleted"):
            continue
        for it in pr["to_PurchaseReqnItem"]["results"]:
            if it["Material"] == mat and not it["_received"]:
                d = it["_delivery"] or (it["DeliveryDate"] if isinstance(it.get("DeliveryDate"), dt.date) else w.today)
                out.append((d, float(it["RequestedQuantity"])))
    return out


# ---------------------------------------------------------------- inventario fisico

def create_physical_inventory(w, bin_: str, user: str) -> list[str]:
    docs = []
    for (b, m), q in list(w.t("quant").items()):
        if b != bin_:
            continue
        doc = w.number("physinv", 7000000)
        w.insert("physinv", (doc,), {"PhysicalInventoryDocument": doc, "EWMWarehouse": WAREHOUSE, "EWMStorageBin": b,
                                     "Product": m, "BookQuantity": q["EWMStockQuantityInBaseUnit"], "CountedQuantity": None,
                                     "PhysInvtryStatus": "Aperto"}, user)
        docs.append(doc)
    return docs


def count_physical_inventory(w, doc: str, user: str, counted: float | None = None) -> None:
    pi = w.get("physinv", (doc,))
    q = w.t("quant").get((pi["EWMStorageBin"], pi["Product"]))
    value = counted if counted is not None else round(q["_real"] if q else 0, 3)
    w.update("physinv", (doc,), {"CountedQuantity": value, "PhysInvtryStatus": "Contato"}, user)


def post_physical_inventory(w, doc: str, user: str) -> str | None:
    pi = w.get("physinv", (doc,))
    if pi["PhysInvtryStatus"] != "Contato":
        raise BusinessError("/SCWM/PI/010", "Il documento non è ancora stato contato")
    q = w.t("quant").get((pi["EWMStorageBin"], pi["Product"]))
    book = q["EWMStockQuantityInBaseUnit"] if q else 0
    diff = round(pi["CountedQuantity"] - book, 3)
    mat_doc = None
    if diff > 0:
        add_quant(w, pi["EWMStorageBin"], pi["Product"], diff, user, real=0)
        mat_doc = _material_document(w, "701", [{"Material": pi["Product"], "QuantityInEntryUnit": diff, "EWMStorageBin": pi["EWMStorageBin"]}], user)
    elif diff < 0:
        remove_quant(w, pi["EWMStorageBin"], pi["Product"], -diff, user, real=0)
        mat_doc = _material_document(w, "702", [{"Material": pi["Product"], "QuantityInEntryUnit": -diff, "EWMStorageBin": pi["EWMStorageBin"]}], user)
    w.update("physinv", (doc,), {"PhysInvtryStatus": "Registrato", "DifferenceQuantity": diff}, user)
    return mat_doc
