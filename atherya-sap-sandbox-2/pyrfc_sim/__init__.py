"""Client RFC compatibile con pyrfc, per la sandbox.

In produzione Atherya usa pyrfc (SAP NW RFC SDK) verso il gateway del sistema ECC del cliente:

    from pyrfc import Connection
    conn = Connection(ashost="ecc.cliente.local", sysnr="00", client="100", user="ATHERYA_RFC", passwd="...")
    result = conn.call("BAPI_ALM_ORDER_GET_DETAIL", NUMBER="000004000101")

Nella sandbox lo stesso codice gira con:

    from pyrfc_sim import Connection
    conn = Connection(ashost="http://localhost:8080", sysnr="00", client="100", user="ATHERYA_RFC", passwd="demo")

Stessi metodi (call, close, ping, get_connection_attributes) e stesse classi di eccezione, con gli
stessi attributi principali (key, message). Differenza nota: pyrfc restituisce Decimal per i campi
quantità (tipo P) e qui arrivano float; il connettore deve convertire sempre con Decimal(str(x)).
"""

import httpx


class RFCError(Exception):
    def __init__(self, message="", code=None, key="", msg_class="", msg_type="", msg_number="", msg_v1="", msg_v2="", msg_v3="", msg_v4=""):
        super().__init__(message)
        self.message, self.code, self.key = message, code, key
        self.msg_class, self.msg_type, self.msg_number = msg_class, msg_type, msg_number
        self.msg_v1, self.msg_v2, self.msg_v3, self.msg_v4 = msg_v1, msg_v2, msg_v3, msg_v4

    def __str__(self):
        return f"{self.key}: {self.message}" if self.key else self.message


class RFCLibError(RFCError):
    pass


class CommunicationError(RFCLibError):
    pass


class LogonError(RFCLibError):
    pass


class ABAPApplicationError(RFCError):
    """Eccezione ABAP del modulo funzione (RAISING), o RFC_NO_AUTHORITY."""


class ABAPRuntimeError(RFCError):
    pass


class ExternalRuntimeError(RFCError):
    pass


_ERRORS = {"LogonError": LogonError, "CommunicationError": CommunicationError, "ABAPApplicationError": ABAPApplicationError,
           "ABAPRuntimeError": ABAPRuntimeError}


class Connection:
    """Una connessione = una sessione RFC = una unità logica di lavoro fino al commit."""

    def __init__(self, ashost="http://localhost:8080", sysnr="00", client="100", user="", passwd="", lang="IT", http=None, **kwargs):
        base = ashost if ashost.startswith("http") else f"http://{ashost}"
        self._http = http or httpx.Client(base_url=base, timeout=30)
        self._attrs = {"client": client, "user": user.upper(), "sysNumber": sysnr, "language": lang[:1]}
        try:
            r = self._http.post("/sim/ecc/rfc/open", json={"user": user, "passwd": passwd, "client": client})
        except httpx.HTTPError as e:
            raise CommunicationError(f"Gateway non raggiungibile: {e}", key="RFC_COMMUNICATION_FAILURE") from e
        data = r.json()
        if "error" in data:
            self._raise(data["error"])
        self._session = data["session"]
        self._attrs.update({"sysId": data["sysid"], "rfcRole": "C", "partnerHost": base})
        self.alive = True

    @staticmethod
    def _raise(err: dict):
        cls = _ERRORS.get(err.get("type"), RFCError)
        raise cls(err.get("message", ""), key=err.get("key", ""), msg_class=err.get("msg_class", ""), msg_number=err.get("msg_number", ""))

    def call(self, func_name: str, options: dict | None = None, **params):
        if not self.alive:
            raise CommunicationError("Connessione chiusa", key="RFC_INVALID_HANDLE")
        try:
            r = self._http.post("/sim/ecc/rfc/call", json={"session": self._session, "function": func_name, "params": params})
        except httpx.HTTPError as e:
            raise CommunicationError(f"Connessione interrotta: {e}", key="RFC_COMMUNICATION_FAILURE") from e
        if r.status_code >= 500:
            raise CommunicationError(f"Errore del gateway ({r.status_code})", key="RFC_COMMUNICATION_FAILURE")
        data = r.json()
        if "error" in data:
            if data["error"].get("key") == "RFC_INVALID_HANDLE":
                self.alive = False
            self._raise(data["error"])
        return data["result"]

    def ping(self):
        self.call("RFC_PING")

    def get_connection_attributes(self):
        return dict(self._attrs)

    def close(self):
        """Chiusura senza commit: il sistema annulla ciò che non è stato confermato."""
        if self.alive:
            try:
                self._http.post("/sim/ecc/rfc/close", json={"session": self._session})
            finally:
                self.alive = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
