"""Motore dei permessi: tutto è negato per impostazione predefinita.

Livelli per dominio:
- osserva: Atherya calcola e mostra l'anteprima, non scrive mai;
- suggerisce: Atherya prepara la proposta, una persona la esegue a mano in SAP;
- con_approvazione: Atherya scrive in SAP dopo l'approvazione esplicita della decisione;
- autonoma: Atherya scrive entro i vincoli e notifica.

Il motore sta fuori dagli agenti: nessun agente può modificarlo.
Sopra questo livello c'è comunque il modello autorizzativo SAP del cliente:
due barriere indipendenti.
"""

LEVELS = ("osserva", "suggerisce", "con_approvazione", "autonoma")

DEFAULT_ALLOWLIST = {
    "manutenzione": {"CreateNotification", "CreateMaintenanceOrder", "ReserveMaterial", "CreatePurchaseRequisition"},
    "scheduling": {"RescheduleOperation"},
}

MODES = {"osserva": "solo_anteprima", "suggerisce": "manuale",
         "con_approvazione": "approvazione", "autonoma": "automatica"}


class PolicyViolation(Exception):
    pass


class Policy:
    def __init__(self, levels: dict[str, str], allowlist: dict[str, set] | None = None):
        for level in levels.values():
            assert level in LEVELS, f"Livello sconosciuto: {level}"
        self.levels = levels
        self.allowlist = allowlist or DEFAULT_ALLOWLIST

    def check(self, actions) -> None:
        for action in actions:
            name, domain = type(action).__name__, action.domain
            if name not in self.allowlist.get(domain, set()):
                raise PolicyViolation(f"Azione {name} non consentita nel dominio {domain}")
            if domain not in self.levels:
                raise PolicyViolation(f"Nessun livello di autonomia definito per {domain}")

    def mode(self, actions) -> str:
        """Il piano segue il livello più restrittivo tra i domini che tocca."""
        strictest = min((self.levels[a.domain] for a in actions), key=LEVELS.index)
        return MODES[strictest]
