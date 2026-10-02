"""Esecuzione dei piani su SAP: approvazione, idempotenza, tutto-o-niente, audit, annullamento."""

import datetime as dt
import json
from dataclasses import dataclass

from .client import SAPClient, SAPError, SAPUnavailableError
from .policy import Policy


@dataclass
class Approval:
    by: str
    note: str = ""


class NeedsApproval(Exception):
    pass


class PlanFailed(Exception):
    def __init__(self, error: SAPError, compensations: list[str]):
        super().__init__(f"Piano interrotto da SAP: {error.message}")
        self.error, self.compensations = error, compensations


class Audit:
    def __init__(self, path: str | None = None):
        self.events: list[dict] = []
        self.path = path

    def log(self, **event) -> None:
        event["ts"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        self.events.append(event)
        if self.path:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    def trace(self, sap_key: str) -> list[dict]:
        """Dato un numero di documento SAP, tutta la storia della decisione che l'ha creato."""
        decisions = {e["decision"] for e in self.events if e.get("sap_key") == sap_key}
        return [e for e in self.events if e.get("decision") in decisions]


class Ledger:
    def __init__(self):
        self.decisions: dict[str, dict] = {}
        self.constraints: list[dict] = []


class Executor:
    def __init__(self, sap: SAPClient, policy: Policy, ledger: Ledger | None = None, audit: Audit | None = None):
        self.sap, self.policy = sap, policy
        self.ledger, self.audit = ledger or Ledger(), audit or Audit()

    def preview(self, plan) -> list[str]:
        return [f"{i + 1}. {a.preview()}" for i, a in enumerate(plan.actions)]

    def run(self, plan, approval: Approval | None = None) -> dict:
        self.policy.check(plan.actions)  # prima di qualsiasi scrittura
        mode = self.policy.mode(plan.actions)
        rec = self.ledger.decisions.setdefault(plan.decision_id, {"status": "proposta", "steps": [], "plan": plan})
        if rec["status"] == "eseguita":
            return rec  # idempotente: rieseguire non crea nulla

        if mode in ("solo_anteprima", "manuale"):
            rec["status"] = "anteprima"
            self.audit.log(decision=plan.decision_id, prediction=plan.prediction_id, event="anteprima", mode=mode,
                           preview=self.preview(plan))
            return rec
        if mode == "approvazione" and approval is None:
            raise NeedsApproval(f"La decisione {plan.decision_id} richiede un'approvazione")

        self.audit.log(decision=plan.decision_id, prediction=plan.prediction_id, event="avvio",
                       mode=mode, approved_by=approval.by if approval else None, evidence=plan.evidence)
        ctx = {"results": [s["result"] for s in rec["steps"]]}
        for i, action in enumerate(plan.actions):
            if i < len(rec["steps"]):
                continue  # passo già eseguito in un tentativo precedente
            ref = f"{plan.decision_id}:{i}"
            try:
                result = self._execute_once(action, ref, ctx, plan.decision_id)
            except SAPError as err:
                compensations = self._compensate(rec, plan)
                rec["status"] = "fallita"
                self.audit.log(decision=plan.decision_id, event="interrotta", step=i + 1,
                               sap_error=f"{err.code} {err.message}", compensations=compensations)
                raise PlanFailed(err, compensations) from err
            rec["steps"].append({"index": i, "result": result})
            ctx["results"].append(result)
            self.audit.log(decision=plan.decision_id, event="scritto", step=i + 1,
                           sap_object=result["label"], sap_key=result["key"])
        rec["status"] = "eseguita"
        self.audit.log(decision=plan.decision_id, event="completata")
        return rec

    def _execute_once(self, action, ref: str, ctx: dict, decision: str) -> dict:
        try:
            return action.execute(self.sap, ref, ctx)
        except SAPUnavailableError:
            # Esito incerto: il documento potrebbe esistere già. Mai riprovare alla cieca.
            existing = action.find_existing(self.sap, ref)
            if existing:
                self.audit.log(decision=decision, event="riconciliato", sap_key=existing["key"])
                return existing
            return action.execute(self.sap, ref, ctx)

    def _compensate(self, rec: dict, plan) -> list[str]:
        done = []
        for step in reversed(rec["steps"]):
            done.append(plan.actions[step["index"]].compensate(self.sap, step["result"]))
        rec["steps"] = []
        return done

    def reject(self, plan, by: str, reason: str, constraint: dict | None = None) -> None:
        rec = self.ledger.decisions.setdefault(plan.decision_id, {"status": "proposta", "steps": [], "plan": plan})
        rec["status"] = "rifiutata"
        if constraint:
            self.ledger.constraints.append(constraint)
        self.audit.log(decision=plan.decision_id, prediction=plan.prediction_id, event="rifiutata",
                       by=by, reason=reason, constraint=constraint)

    def undo(self, decision_id: str, by: str) -> list[str]:
        rec = self.ledger.decisions[decision_id]
        assert rec["status"] == "eseguita", "Si può annullare solo una decisione eseguita"
        compensations = self._compensate(rec, rec["plan"])
        rec["status"] = "annullata"
        self.audit.log(decision=decision_id, event="annullata", by=by, compensations=compensations)
        return compensations
