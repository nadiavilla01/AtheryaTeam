"""Ingressi HTTP della facciata ECC.

- /sap/bc/soap/rfc            RFC via SOAP (servizio ICF standard di ECC), sessione con il cookie sap-contextid
- /sim/ecc/rfc/{open,call,close}  trasporto del client compatibile pyrfc (solo sandbox: in produzione pyrfc
                                  parla il protocollo RFC binario con il gateway SAP)
"""

import base64
import xml.etree.ElementTree as ET

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from ..world import BusinessError
from . import conv, gui, rfc

NS_ENV = "http://schemas.xmlsoap.org/soap/envelope/"
NS_RFC = "urn:sap-com:document:sap:rfc:functions"


def _basic(request: Request) -> tuple[str, str]:
    h = request.headers.get("authorization", "")
    if not h.startswith("Basic "):
        return "", ""
    try:
        u, pw = base64.b64decode(h[6:]).decode().split(":", 1)
        return u.upper(), pw
    except Exception:
        return "", ""


def _error(kind: str, key: str, message: str, status: int = 200, **extra) -> JSONResponse:
    return JSONResponse({"error": {"type": kind, "key": key, "message": message, **extra}}, status_code=status)


# ---------------------------------------------------------------- XML ↔ parametri

def _local(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _from_xml(el):
    children = list(el)
    if not children:
        return el.text or ""
    if all(_local(c.tag) == "item" for c in children):
        return [_from_xml(c) if list(c) else (c.text or "") for c in children]
    return {_local(c.tag): _from_xml(c) for c in children}


def _to_xml(parent, name, value):
    el = ET.SubElement(parent, name)
    if isinstance(value, list):
        for item in value:
            _to_xml(el, "item", item)
    elif isinstance(value, dict):
        for k, v in value.items():
            _to_xml(el, k, v)
    elif isinstance(value, float):
        el.text = f"{value:.3f}"
    else:
        el.text = "" if value is None else str(value)
    return el


def _soap(body_el) -> Response:
    env = ET.Element(f"{{{NS_ENV}}}Envelope")
    ET.SubElement(env, f"{{{NS_ENV}}}Header")
    b = ET.SubElement(env, f"{{{NS_ENV}}}Body")
    b.append(body_el)
    return env


def _fault(code: str, key: str, text: str, fm: str = "") -> Response:
    ET.register_namespace("soap-env", NS_ENV)
    ET.register_namespace("urn", NS_RFC)
    f = ET.Element(f"{{{NS_ENV}}}Fault")
    ET.SubElement(f, "faultcode").text = f"soap-env:{code}"
    ET.SubElement(f, "faultstring").text = key
    detail = ET.SubElement(f, "detail")
    ex = ET.SubElement(detail, f"{{{NS_RFC}}}{fm or 'RFC'}.Exception")
    ET.SubElement(ex, "Name").text = key
    ET.SubElement(ex, "Text").text = text
    return f


def make_router(get_world, lock) -> APIRouter:
    r = APIRouter()

    # ------------------------------------------------ SOAP RFC
    @r.post("/sap/bc/soap/rfc")
    async def soap_rfc(request: Request):
        ET.register_namespace("soap-env", NS_ENV)
        ET.register_namespace("urn", NS_RFC)
        with lock:
            w = get_world()
            u, pw = _basic(request)
            client = request.query_params.get("sap-client", conv.CLIENT)
            try:
                rfc.logon(w, u, pw, client)
            except rfc.LogonFailed as e:
                return Response(str(e), status_code=401, headers={"WWW-Authenticate": 'Basic realm="SAP NetWeaver Application Server [ECD/100]"'})
            sid = request.cookies.get("sap-contextid")
            s = rfc.sessions(w).get(sid) if sid else None
            if s is None or s.user != u:
                s = rfc.open_session(w, u, "SOAP")
            try:
                root = ET.fromstring(await request.body())
                body = next(c for c in root if _local(c.tag) == "Body")
                call_el = list(body)[0]
            except Exception:
                return Response("Richiesta SOAP non valida", status_code=400)
            fm = _local(call_el.tag)
            params = {_local(c.tag): _from_xml(c) for c in call_el}
            status = 200
            try:
                out = rfc.call(w, s, fm, params)
                resp = ET.Element(f"{{{NS_RFC}}}{fm}.Response")
                for k, v in out.items():
                    _to_xml(resp, k, v)
            except rfc.NoAuthority as e:
                resp, status = _fault("Client", "RFC_NO_AUTHORITY", str(e), fm), 500
            except rfc.ABAPException as e:
                resp, status = _fault("Client", e.key, e.message, fm), 500
            env = _soap(resp)
            xml = b'<?xml version="1.0" encoding="utf-8"?>' + ET.tostring(env, encoding="utf-8")
            return Response(xml, status_code=status, media_type="text/xml; charset=utf-8",
                            headers={"set-cookie": f"sap-contextid={s.id}; path=/sap/bc/soap/rfc"})

    # ------------------------------------------------ trasporto del client compatibile pyrfc
    @r.post("/sim/ecc/rfc/open")
    def rfc_open(payload: dict):
        with lock:
            w = get_world()
            rfc.expire_sessions(w)
            u = str(payload.get("user", "")).upper()
            try:
                rfc.logon(w, u, str(payload.get("passwd", "")), str(payload.get("client", conv.CLIENT)))
            except rfc.LogonFailed as e:
                return _error("LogonError", "RFC_LOGON_FAILURE", str(e), 401)
            return {"session": rfc.open_session(w, u).id, "sysid": conv.SYSID, "client": conv.CLIENT}

    @r.post("/sim/ecc/rfc/call")
    def rfc_call(payload: dict):
        with lock:
            w = get_world()
            s = rfc.sessions(w).get(payload.get("session", ""))
            if s is None:
                return _error("CommunicationError", "RFC_INVALID_HANDLE", "Connessione RFC non valida o chiusa", 410)
            fm = str(payload.get("function", "")).upper()
            try:
                result = rfc.call(w, s, fm, payload.get("params") or {})
                if w.faults.pop(f"lose_response:{fm}", None):
                    # la chiamata è arrivata ed è stata eseguita, la risposta si perde: esito incerto per il client
                    return _error("CommunicationError", "RFC_COMMUNICATION_FAILURE", "Connessione interrotta durante la risposta", 502)
                return {"result": result}
            except rfc.NoAuthority as e:
                return _error("ABAPApplicationError", "RFC_NO_AUTHORITY", str(e))
            except rfc.ABAPException as e:
                return _error("ABAPApplicationError", e.key, e.message, msg_class=e.msg_class, msg_number=e.msg_number)

    @r.post("/sim/ecc/rfc/close")
    def rfc_close(payload: dict):
        with lock:
            rfc.close_session(get_world(), payload.get("session", ""))
        return {"ok": True}

    # ------------------------------------------------ interfaccia in stile SAP GUI (utente PLANNER)
    @r.get("/sim/ecc/gui/catalog")
    def gui_catalog():
        return gui.catalog()

    @r.post("/sim/ecc/gui/run")
    def gui_run(payload: dict):
        with lock:
            try:
                return {"result": gui.run(get_world(), str(payload.get("tcode", "")), payload.get("sel") or {})}
            except BusinessError as e:
                return {"error": {"type": "E", "code": e.code, "text": e.message}}

    @r.post("/sim/ecc/gui/action")
    def gui_action(payload: dict):
        with lock:
            try:
                return {"message": {"type": "S", "text": gui.action(get_world(), payload)}}
            except BusinessError as e:
                return {"message": {"type": "E", "code": e.code, "text": e.message}}

    return r
