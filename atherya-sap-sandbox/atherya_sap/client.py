"""Client OData V2 minimale verso SAP (simulato o reale): stesso codice per entrambi."""

import base64
import datetime as dt
import re


class SAPError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"[{status} {code}] {message}")
        self.status, self.code, self.message = status, code, message


class SAPPermissionError(SAPError):
    """403: l'utente tecnico non ha l'autorizzazione. Atherya si ferma, non cerca altre strade."""


class SAPConflictError(SAPError):
    """412: il documento è stato cambiato da qualcun altro. Atherya rilegge, non sovrascrive."""


class SAPUnavailableError(SAPError):
    """5xx o timeout: esito incerto. Prima di riprovare si verifica se il documento esiste già."""


def sap_date(d: dt.date) -> str:
    ms = int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp() * 1000)
    return f"/Date({ms})/"


def from_sap_date(value: str) -> dt.date:
    ms = int(re.search(r"-?\d+", value).group())
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).date()


def format_key(key) -> str:
    if isinstance(key, dict):
        return ",".join(f"{k}='{v}'" for k, v in key.items())
    return f"'{key}'"


class SAPClient:
    """`http` è un httpx.Client (con base_url) o il TestClient di FastAPI: stessa interfaccia."""

    def __init__(self, http, user: str, password: str, base: str = "/sap/opu/odata/sap"):
        self.http, self.base = http, base
        self._auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
        self._tokens: dict[str, str] = {}

    def _headers(self, extra: dict | None = None) -> dict:
        return {"Authorization": self._auth, "Accept": "application/json", **(extra or {})}

    def _check(self, r) -> None:
        if r.status_code < 400:
            return
        try:
            err = r.json()["error"]
            code, msg = err["code"], err["message"]["value"]
        except Exception:
            code, msg = "?", r.text
        cls = {403: SAPPermissionError, 412: SAPConflictError}.get(r.status_code)
        if cls is None:
            cls = SAPUnavailableError if r.status_code >= 500 else SAPError
        raise cls(r.status_code, code, msg)

    def _token(self, service: str) -> str:
        if service not in self._tokens:
            r = self.http.get(f"{self.base}/{service}/", headers=self._headers({"x-csrf-token": "Fetch"}))
            self._check(r)
            self._tokens[service] = r.headers["x-csrf-token"]
        return self._tokens[service]

    def _path(self, service: str, entity: str, key=None) -> str:
        suffix = f"({format_key(key)})" if key is not None else ""
        return f"{self.base}/{service}/{entity}{suffix}"

    def query(self, service: str, entity: str, filters: dict | None = None) -> list[dict]:
        params = {"$filter": " and ".join(f"{k} eq '{v}'" for k, v in filters.items())} if filters else None
        r = self.http.get(self._path(service, entity), headers=self._headers(), params=params)
        self._check(r)
        return r.json()["d"]["results"]

    def read(self, service: str, entity: str, key) -> tuple[dict, str]:
        r = self.http.get(self._path(service, entity, key), headers=self._headers())
        self._check(r)
        return r.json()["d"], r.headers.get("ETag")

    def create(self, service: str, entity: str, payload: dict) -> dict:
        r = self.http.post(self._path(service, entity), json=payload,
                           headers=self._headers({"x-csrf-token": self._token(service)}))
        self._check(r)
        return r.json()["d"]

    def update(self, service: str, entity: str, key, changes: dict, etag: str) -> str:
        r = self.http.patch(self._path(service, entity, key), json=changes,
                            headers=self._headers({"x-csrf-token": self._token(service), "If-Match": etag}))
        self._check(r)
        return r.headers.get("ETag")

    def delete(self, service: str, entity: str, key) -> None:
        r = self.http.delete(self._path(service, entity, key),
                             headers=self._headers({"x-csrf-token": self._token(service)}))
        self._check(r)
