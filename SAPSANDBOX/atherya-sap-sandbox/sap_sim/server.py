"""Server del simulatore: API OData V2, comandi della simulazione, dati per le app Fiori."""

import base64
import datetime as dt
import secrets
import threading
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import factory, inventory as inv, pm, pp
from .fiori_apps import APPS, action as fiori_action, app_rows
from .odata import BY_NAME, FUNCTIONS, all_rows, apply_query, find_row, from_odata, parse_key, public
from .world import BusinessError

app = FastAPI(title="Simulatore S/4HANA per Atherya")
LOCK = threading.RLock()
BASE = "/sap/opu/odata/sap"
OPS = {"GET": "read", "POST": "create", "PATCH": "update", "PUT": "update", "DELETE": "delete"}


class _Holder:
    world = None
    runner: threading.Thread | None = None
    speed = 1.0  # secondi reali per passo di 15 minuti


H = _Holder()


def reset_state(start: dt.datetime | None = None, seed: int = 7) -> None:
    with LOCK:
        H.world = factory.build_world(start, seed)


reset_state()


def W():
    return H.world


def sap_error(status: int, code: str, msg: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": {"lang": "it", "value": msg}}}, status_code=status, headers=headers or {})


def authenticate(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if not header.startswith("Basic "):
        return None
    try:
        user, password = base64.b64decode(header[6:]).decode().split(":", 1)
    except Exception:
        return None
    account = W().users.get(user)
    return user if account and account["password"] == password else None


def allowed(user: str, service: str, op: str) -> bool:
    return f"{service}:{op}" in W().users[user]["auths"]


# ---------------------------------------------------------------- OData V2

@app.api_route(BASE + "/{service}/{path:path}", methods=["GET", "POST", "PATCH", "PUT", "DELETE"])
async def odata(service: str, path: str, request: Request):
    with LOCK:
        w = W()
        user = authenticate(request)
        if not user:
            return sap_error(401, "/IWFND/CM_CONSUMER/401", "Autenticazione non riuscita")
        headers = {}
        if request.headers.get("x-csrf-token", "").lower() == "fetch":
            headers["x-csrf-token"] = w.csrf.setdefault(user, secrets.token_urlsafe(16))
        service = service.split(";")[0]
        if path in ("", "$metadata"):
            sets = [s.entity for s in BY_NAME.values() if s.service == service]
            funcs = [f for (s, f) in FUNCTIONS if s == service]
            if not sets and not funcs:
                return sap_error(404, "/IWFND/MED/170", f"Servizio {service} non trovato")
            return JSONResponse({"d": {"EntitySets": sets, "FunctionImports": funcs}}, headers=headers)
        if request.method != "GET" and request.headers.get("x-csrf-token") != w.csrf.get(user):
            return sap_error(403, "/IWFND/CM_BEC/076", "Convalida del token CSRF non riuscita", {"x-csrf-token": "Required"})
        try:
            return await _dispatch(w, service, path, request, user, headers)
        except BusinessError as e:
            return sap_error(e.status, e.code, e.message)


async def _dispatch(w, service, path, request, user, headers):
    name = path.split("(")[0].split("/")[0]
    if (service, name) in FUNCTIONS:
        op, handler = FUNCTIONS[(service, name)]
        if not allowed(user, service, op):
            return sap_error(403, "/IWBEP/CM_MGW_RT/004", f"Nessuna autorizzazione per '{op}' su {service}")
        params = {k: v.strip("'") for k, v in request.query_params.items()}
        result = handler(w, params, user) or {}
        return JSONResponse({"d": result}, headers=headers)

    import re
    m = re.match(r"^(\w+)(?:\((.*?)\))?(?:/(\w+))?$", path)
    spec = BY_NAME.get((service, m.group(1))) if m else None
    if not spec:
        return sap_error(404, "/IWBEP/CM_MGW_RT/020", f"Risorsa {path} non trovata nel servizio {service}")
    key, nav = parse_key(m.group(2), spec.keys), m.group(3)
    op = OPS[request.method]
    if not allowed(user, service, op):
        return sap_error(403, "/IWBEP/CM_MGW_RT/004", f"Nessuna autorizzazione per '{op}' su {service}")
    params = request.query_params

    if request.method == "GET":
        select = params.get("$select", "").split(",") if params.get("$select") else None
        if key is None:
            rows, count = apply_query(all_rows(w, spec), params)
            body = {"results": [public(spec, r, select) for r in rows]}
            if params.get("$inlinecount") == "allpages":
                body["__count"] = str(count)
            return JSONResponse({"d": body}, headers=headers)
        row = find_row(w, spec, key)
        if row is None:
            return sap_error(404, "/IWBEP/CM_MGW_RT/021", "Documento non trovato")
        out = public(spec, row, select)
        if nav:
            return JSONResponse({"d": out.get(nav, {"results": []})}, headers=headers)
        return JSONResponse({"d": out}, headers={**headers, "ETag": row.get("_etag", "")})

    if request.method == "POST":
        if not spec.create:
            return sap_error(405, "/IWBEP/CM_MGW_RT/005", "Creazione non supportata da questa entità")
        body = from_odata(await request.json())
        new_key = spec.create(w, body, user)
        row = w.get(spec.table, new_key)
        if w.faults.pop(f"lose_response:{service}", None):
            return sap_error(504, "/IWFND/CM_BEC/004", "Timeout del gateway")  # il documento esiste già
        return JSONResponse({"d": public(spec, row)}, status_code=201, headers={"ETag": row["_etag"]})

    if key is None:
        return sap_error(400, "/IWBEP/CM_MGW_RT/022", "Chiave mancante")
    row = find_row(w, spec, key)
    if row is None:
        return sap_error(404, "/IWBEP/CM_MGW_RT/021", "Documento non trovato")
    if request.method in ("PATCH", "PUT"):
        if not spec.update:
            return sap_error(405, "/IWBEP/CM_MGW_RT/005", "Modifica non supportata da questa entità")
        if_match = request.headers.get("if-match")
        if not if_match:
            return sap_error(428, "/IWBEP/CM_MGW_RT/129", "Precondizione richiesta: If-Match")
        if if_match != row["_etag"]:
            return sap_error(412, "/IWBEP/CM_MGW_RT/130", "Il documento è stato modificato da un altro utente")
        spec.update(w, key, row, from_odata(await request.json()), user)
        return Response(status_code=204, headers={"ETag": row["_etag"]})
    if not spec.delete:
        return sap_error(405, "/IWBEP/CM_MGW_RT/005", "Cancellazione non supportata da questa entità")
    spec.delete(w, key, user)
    return Response(status_code=204)


# ---------------------------------------------------------------- simulazione

def _clock() -> dict:
    w = W()
    from .calendar import shift_of
    return {"now": w.now.isoformat(), "weekday": ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"][w.now.weekday()],
            "shift": shift_of(w.now) if w.now.weekday() < 5 else "Fine settimana", "running": w.running, "speed": H.speed,
            "kpi": {k: round(v) for k, v in w.kpi.items()}}


def _runner():
    while True:
        with LOCK:
            if not W().running:
                break
            factory.advance(W(), factory.STEP_MIN)
        time.sleep(H.speed)


@app.post("/sim/reset")
def sim_reset(payload: dict | None = None):
    with LOCK:
        if H.world:
            H.world.running = False
        reset_state(seed=int((payload or {}).get("seed", 7)))
    return _clock()


@app.get("/sim/clock")
def sim_clock():
    with LOCK:
        return _clock()


@app.post("/sim/advance")
def sim_advance(payload: dict):
    with LOCK:
        factory.advance(W(), int(payload.get("minutes", 60)))
        return _clock()


@app.post("/sim/run")
def sim_run(payload: dict | None = None):
    with LOCK:
        H.speed = float((payload or {}).get("seconds_per_step", H.speed))
        if not W().running:
            W().running = True
            H.runner = threading.Thread(target=_runner, daemon=True)
            H.runner.start()
        return _clock()


@app.post("/sim/stop")
def sim_stop():
    with LOCK:
        W().running = False
        return _clock()


@app.post("/sim/mrp")
def sim_mrp():
    with LOCK:
        return pp.run_mrp(W(), "PLANNER")


@app.get("/sim/truth")
def sim_truth():
    """Verità simulata (usura nascosta, soglia di guasto): per valutare Atherya, invisibile a SAP."""
    with LOCK:
        return {wc: {k: (v.isoformat() if isinstance(v, dt.datetime) else v) for k, v in m.items() if k != "produced"}
                for wc, m in W().machines.items()}


@app.post("/sim/admin/revoke")
def sim_revoke(payload: dict):
    with LOCK:
        W().users[payload["user"]]["auths"].discard(payload["auth"])
    return {"ok": True}


@app.post("/sim/admin/workcenter")
def sim_workcenter(payload: dict):
    with LOCK:
        W().update("workcenter", (payload["workcenter"],), {"_blocked": bool(payload["blocked"])}, "ADMIN")
    return {"ok": True}


@app.post("/sim/admin/fault")
def sim_fault(payload: dict):
    with LOCK:
        W().faults[payload["fault"]] = True
    return {"ok": True}


# ---------------------------------------------------------------- app Fiori

@app.get("/fiori/api/config")
def fiori_config():
    return [{k: v for k, v in a.items() if k not in ("rows", "flatten")} for a in APPS]


@app.get("/fiori/api/summary")
def fiori_summary():
    with LOCK:
        return {"clock": _clock(), "counts": {a["id"]: a["count"](W()) for a in APPS}}


@app.get("/fiori/api/data/{app_id}")
def fiori_data(app_id: str, top: int = 400):
    with LOCK:
        return app_rows(W(), app_id, top)


@app.post("/fiori/api/action")
def fiori_act(payload: dict):
    with LOCK:
        try:
            msg = fiori_action(W(), payload)
        except BusinessError as e:
            return sap_error(e.status, e.code, e.message)
        return {"ok": True, "message": msg}


# ---------------------------------------------------------------- facciata ECC (RFC, SOAP, GUI)

from .ecc.http import make_router as _ecc_router  # noqa: E402

app.include_router(_ecc_router(W, LOCK))


@app.get("/")
def root():
    return RedirectResponse("/fiori/")


app.mount("/sap/bc/gui/sap/its/webgui", StaticFiles(directory=Path(__file__).parent / "ecc" / "webgui", html=True), name="webgui")
app.mount("/fiori", StaticFiles(directory=Path(__file__).parent / "fiori", html=True), name="fiori")
