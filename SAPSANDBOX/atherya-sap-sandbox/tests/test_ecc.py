"""Facciata ECC: RFC/BAPI, unità logica di lavoro, formato interno, blocchi, autorizzazioni, SOAP,
e il connettore di Atherya che porta lo stesso piano su ECC."""

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from atherya_sap.client import SAPConflictError, SAPPermissionError
from atherya_sap.ecc import ECCClient, SAPLockedError, alpha
from atherya_sap.executor import Approval, Executor
from atherya_sap.planner import Prediction, build_plan
from atherya_sap.policy import Policy
from pyrfc_sim import ABAPApplicationError, Connection, LogonError
from sap_sim.server import W, app, reset_state

P07 = "10000007"


@pytest.fixture
def http():
    reset_state()
    return TestClient(app)


def conn(http, user="ATHERYA_RFC"):
    return Connection(ashost="http://test", sysnr="00", client="100", user=user, passwd="demo", http=http)


@pytest.fixture
def ecc(http):
    return ECCClient(conn(http))


@pytest.fixture
def planner_ecc(http):
    return ECCClient(conn(http, "PLANNER"))


def advance(http, minutes):
    return http.post("/sim/advance", json={"minutes": minutes}).json()


def new_notification(c, text="Controllo guarnizione"):
    r = c.call("BAPI_ALM_NOTIF_CREATE", NOTIF_TYPE="M1", NOTIFHEADER={"EQUIPMENT": alpha(P07, 18), "SHORT_TEXT": text})
    return c.call("BAPI_ALM_NOTIF_SAVE", NUMBER=r["NOTIFHEADER_EXPORT"]["NOTIF_NO"])["NOTIFHEADER"]["NOTIF_NO"]


def p07_prediction():
    return Prediction(id="PRED-P07", equipment=P07, machine="P07", component="Guarnizione idraulica", window_min_days=10,
                      window_max_days=14, confidence=0.86, signature="instabilità di pressione in aumento",
                      known_context="stampo S-118", part_material="GUARN-HYD-250", part_qty=1, part_lead_time_days=5,
                      affected_order="1000471", affected_operation="0010", current_workcenter="PRESS07",
                      candidate_workcenters=("PRESS07", "PRESS12", "PRESS15"))


# ---------------------------------------------------------------- connessione

def test_logon_e_info_di_sistema(http):
    c = conn(http)
    assert c.call("RFC_SYSTEM_INFO")["RFCSI_EXPORT"]["RFCSYSID"] == "ECD"
    with pytest.raises(LogonError):
        Connection(user="ATHERYA_RFC", passwd="sbagliata", http=http)


# ---------------------------------------------------------------- unità logica di lavoro

def test_senza_commit_non_resta_nulla(http):
    c = conn(http)
    num = new_notification(c)
    assert num.startswith("0000100")
    c.close()  # chiusura senza BAPI_TRANSACTION_COMMIT
    other = ECCClient(conn(http))
    assert other.read_table("QMEL", ["QMNUM"], f"QMNUM = '{num}'") == []


def test_commit_rende_permanente_e_rilascia_i_blocchi(http):
    c = conn(http)
    num = new_notification(c)
    assert ("QMEL", num) in W().locks
    c.call("BAPI_TRANSACTION_COMMIT", WAIT="X")
    assert ("QMEL", num) not in W().locks
    c.close()
    rows = ECCClient(conn(http)).read_table("VIQMEL", ["QMNUM", "EQUNR", "QMTXT"], f"QMNUM = '{num}'")
    assert rows == [{"QMNUM": num, "EQUNR": alpha(P07, 18), "QMTXT": "Controllo guarnizione"}]


def test_rollback_annulla(http):
    c = conn(http)
    before = len(W().t("notif"))
    new_notification(c)
    c.call("BAPI_TRANSACTION_ROLLBACK")
    assert len(W().t("notif")) == before


def test_un_errore_nel_return_non_e_un_eccezione(http):
    c = conn(http)
    out = c.call("BAPI_ALM_NOTIF_CREATE", NOTIF_TYPE="M1", NOTIFHEADER={"EQUIPMENT": "99999", "SHORT_TEXT": "x"})
    assert out["RETURN"][0]["TYPE"] == "E"  # nessuna eccezione: chi non legge RETURN non se ne accorge


# ---------------------------------------------------------------- formato interno e tabelle

def test_chiavi_in_formato_interno(http, ecc):
    assert ecc.call("BAPI_PRODORD_GET_DETAIL", NUMBER="1000471")["RETURN"]["TYPE"] == "E"
    ok = ecc.bapi("BAPI_PRODORD_GET_DETAIL", NUMBER="000001000471", ORDER_OBJECTS={"HEADER": "X", "OPERATIONS": "X"})
    assert ok["HEADER"][0]["MATERIAL"] == "A-2210" and ok["OPERATION"][0]["WORK_CENTER"] == "PRESS07"


def test_rfc_read_table(ecc):
    rows = ecc.read_table("AFVC", ["AUFPL", "VORNR", "ARBID", "LTXA1"], "AUFPL = '0001000471'")
    assert rows[0]["VORNR"] == "0010" and rows[0]["ARBID"].isdigit()  # il centro di lavoro è un ID: serve CRHD
    crhd = ecc.read_table("CRHD", ["OBJID", "ARBPL"], f"OBJID = '{rows[0]['ARBID']}'")
    assert crhd[0]["ARBPL"] == "PRESS07"
    with pytest.raises(Exception) as e:
        ecc.call("RFC_READ_TABLE", QUERY_TABLE="AUFK")  # senza FIELDS: riga troppo larga
    assert e.value.code == "DATA_BUFFER_EXCEEDED"
    stock = ecc.read_table("MARD", ["MATNR", "LABST"], "MATNR LIKE 'GUARN%' AND LABST > 0")
    assert [r["MATNR"] for r in stock] == ["GUARN-HYD-250"]


# ---------------------------------------------------------------- autorizzazioni e blocchi

def test_autorizzazioni(http, ecc):
    with pytest.raises(SAPPermissionError):
        ecc.call("BAPI_PRODORD_RELEASE", ORDERS=[{"ORDER_NUMBER": "000001000471"}])  # S_RFC
    with ecc.luw():
        r = ecc.bapi("BAPI_ALM_ORDER_MAINTAIN",
                     IT_METHODS=[{"REFNUMBER": "000001", "OBJECTTYPE": "HEADER", "METHOD": "CREATE", "OBJECTKEY": "%00000000001"},
                                 {"METHOD": "SAVE"}],
                     IT_HEADER=[{"ORDER_TYPE": "PM02", "PLANPLANT": "1000", "MN_WK_CTR": "MAINT01", "EQUIPMENT": alpha(P07, 18),
                                 "SHORT_TEXT": "Controllo", "START_DATE": "20261005"}])
    order = r["ET_NUMBERS"][0]["AUFNR_NEW"]
    with pytest.raises(SAPPermissionError):  # controllo di business: il rilascio resta a una persona
        ecc.bapi("BAPI_ALM_ORDER_MAINTAIN", IT_METHODS=[{"REFNUMBER": "000001", "OBJECTTYPE": "HEADER", "METHOD": "RELEASE",
                                                         "OBJECTKEY": order}, {"METHOD": "SAVE"}])


def test_documento_bloccato_da_un_altro_utente(http, ecc):
    W().locks[("ORDER", "000001000471")] = {"user": "PIANIFICATORE", "tcode": "CO02", "since": W().now, "until": None}
    with pytest.raises(SAPLockedError) as e:
        with ecc.luw():
            ecc.bapi("Z_ATHERYA_PRODORD_OPR_CHANGE", IV_AUFNR="000001000471", IV_VORNR="0010", IV_ARBPL="PRESS12",
                     IV_EXPECTED_ARBPL="PRESS07")
    assert "PIANIF01" in e.value.message


def test_concorrenza_ottimistica_sul_modulo_cliente(ecc):
    with pytest.raises(SAPConflictError):
        with ecc.luw():
            ecc.bapi("Z_ATHERYA_PRODORD_OPR_CHANGE", IV_AUFNR="000001000471", IV_VORNR="0010", IV_ARBPL="PRESS12",
                     IV_EXPECTED_ARBPL="PRESS15")


# ---------------------------------------------------------------- SOAP

SOAP = """<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/"><soap-env:Body>
<urn:{fm} xmlns:urn="urn:sap-com:document:sap:rfc:functions">{body}</urn:{fm}></soap-env:Body></soap-env:Envelope>"""


def test_soap_con_sessione(http):
    def post(fm, body, cookie=None):
        return http.post("/sap/bc/soap/rfc?sap-client=100", content=SOAP.format(fm=fm, body=body), auth=("ATHERYA_RFC", "demo"),
                         headers={"cookie": f"sap-contextid={cookie}"} if cookie else {})
    r = post("BAPI_ALM_NOTIF_CREATE", f"<NOTIF_TYPE>M1</NOTIF_TYPE><NOTIFHEADER><EQUIPMENT>{alpha(P07, 18)}</EQUIPMENT>"
                                      "<SHORT_TEXT>Via SOAP</SHORT_TEXT></NOTIFHEADER>")
    cookie = r.cookies.get("sap-contextid")
    assert "%00000000001" in r.text
    r = post("BAPI_ALM_NOTIF_SAVE", "<NUMBER>%00000000001</NUMBER>", cookie)
    assert "<NOTIF_NO>0000100" in r.text
    post("BAPI_TRANSACTION_COMMIT", "<WAIT>X</WAIT>", cookie)
    assert any(n["NotificationText"] == "Via SOAP" for n in W().t("notif").values())
    fault = post("RFC_READ_TABLE", "<QUERY_TABLE>USR02</QUERY_TABLE>")
    assert fault.status_code == 500 and "TABLE_NOT_AVAILABLE" in fault.text


# ---------------------------------------------------------------- il connettore di Atherya su ECC

def test_piano_atherya_su_ecc_evita_il_guasto(http, ecc, planner_ecc):
    plan = build_plan(p07_prediction(), ecc, dt.date(2026, 10, 1), dt.date(2026, 10, 3))
    assert [type(a).__module__ for a in plan.actions] == ["atherya_sap.ecc"] * 4
    rec = Executor(ecc, Policy({"manutenzione": "con_approvazione", "scheduling": "con_approvazione"})).run(plan, Approval("capoturno"))
    order = alpha(rec["steps"][1]["result"]["key"], 12)
    with planner_ecc.luw():  # il rilascio lo fa una persona
        planner_ecc.bapi("BAPI_ALM_ORDER_MAINTAIN", IT_METHODS=[{"REFNUMBER": "000001", "OBJECTTYPE": "HEADER", "METHOD": "RELEASE",
                                                                 "OBJECTKEY": order}, {"METHOD": "SAVE"}])
    for _ in range(21):
        advance(http, 24 * 60)
    detail = ecc.bapi("BAPI_ALM_ORDER_GET_DETAIL", NUMBER=order)
    assert "TECO" in detail["ES_HEADER"]["SYS_STATUS"]
    breakdowns = ecc.read_table("VIQMEL", ["QMNUM"], f"EQUNR = '{alpha(P07, 18)}' AND MSAUS = 'X'")
    assert breakdowns == []
    resb = ecc.read_table("RESB", ["RSNUM", "KZEAR"], f"RSNUM = '{alpha(rec['steps'][2]['result']['key'], 10)}'")
    assert resb[0]["KZEAR"] == "X"


def test_annullamento_su_ecc(http, ecc):
    plan = build_plan(p07_prediction(), ecc, dt.date(2026, 10, 1), dt.date(2026, 10, 3))
    ex = Executor(ecc, Policy({"manutenzione": "con_approvazione", "scheduling": "con_approvazione"}))
    rec = ex.run(plan, Approval("capoturno"))
    keys = [s["result"]["key"] for s in rec["steps"]]
    assert W().get("prodop", ("1000471", "0010"))["WorkCenter"] == "PRESS12"
    done = ex.undo(plan.decision_id, "capoturno")
    assert len(done) == 4
    assert W().get("morder", (keys[1],))["_locked"]
    assert W().get("notif", (keys[0],))["MaintNotifProcessPhaseCode"] == "4"
    assert W().get("prodop", ("1000471", "0010"))["WorkCenter"] == "PRESS07"
    assert (keys[2],) not in W().t("reservation")


def test_risposta_persa_si_riconcilia_con_il_riferimento(http, ecc):
    http.post("/sim/admin/fault", json={"fault": "lose_response:BAPI_TRANSACTION_COMMIT"})
    plan = build_plan(p07_prediction(), ecc, dt.date(2026, 10, 1), dt.date(2026, 10, 3))
    rec = Executor(ecc, Policy({"manutenzione": "con_approvazione", "scheduling": "con_approvazione"})).run(plan, Approval("capoturno"))
    first = rec["steps"][0]["result"]["key"]
    same_ref = [n for n in W().t("notif").values() if n.get("YY1_AtheryaRef") == f"{plan.decision_id}:0"]
    assert len(same_ref) == 1 and same_ref[0]["MaintenanceNotification"] == first  # nessun duplicato


# ---------------------------------------------------------------- GUI

def test_tutte_le_transazioni_della_gui(http):
    advance(http, 3 * 24 * 60)
    for tx in http.get("/sim/ecc/gui/catalog").json():
        if tx["kind"] != "alv":
            continue
        sel = {f["name"]: f.get("default", "") for f in tx["sel"]}
        out = http.post("/sim/ecc/gui/run", json={"tcode": tx["tcode"], "sel": sel}).json()
        assert "result" in out, (tx["tcode"], out)
    r = http.post("/sim/ecc/gui/action", json={"action": "IW21", "values": {"QMART": "M1", "EQUNR": "10000007", "QMTXT": "Da GUI"}}).json()
    assert r["message"]["type"] == "S"
    detail = http.post("/sim/ecc/gui/run", json={"tcode": "CO03", "sel": {"KEY": "1000471"}}).json()["result"]
    assert detail["sections"][0]["fields"][2] == ["Materiale", "A-2210"]
