# Collegare Atherya a SAP ECC 6.0 (istruzioni per Claude Code)

Questa facciata simula un ECC 6.0 (EhP7, NetWeaver 7.40, magazzino LE-WM) **sulla stessa fabbrica**
delle API S/4HANA. Un piano di Atherya scritto via ECC si vede nelle app Fiori, e viceversa.
Il connettore è `atherya_sap/ecc.py`: stesse azioni tipizzate, stesso planner, stesso executor,
stesso motore dei permessi. Cambia solo il dialetto.

## 1. Avvio

```bash
pip install -r requirements.txt
uvicorn sap_sim.server:app --port 8080
```

| Ingresso | Indirizzo | A cosa serve |
|---|---|---|
| RFC (client compatibile pyrfc) | `pyrfc_sim.Connection(ashost="http://localhost:8080", ...)` | il percorso di Atherya |
| RFC via SOAP | `POST /sap/bc/soap/rfc?sap-client=100` | integrazioni via HTTP, test con Postman/SoapUI |
| GUI in stile SAP GUI | `http://localhost:8080/sap/bc/gui/sap/its/webgui/` | per le persone: transazioni IW38, COOIS, MD04, SE16N… |

Utenti (mandante 100): `ATHERYA_RFC` / `demo` (utente tecnico, ruoli minimi) e `PLANNER` / `demo` (tutto, anche il rilascio).

## 2. Configurazione lato Atherya

```python
# produzione: SAP NW RFC SDK + pyrfc, verso il gateway del cliente
from pyrfc import Connection
conn = Connection(ashost="ecc.cliente.local", sysnr="00", client="100", user="ATHERYA_RFC", passwd=secret)

# sandbox: stesso codice
from pyrfc_sim import Connection
conn = Connection(ashost="http://localhost:8080", sysnr="00", client="100", user="ATHERYA_RFC", passwd="demo")

from atherya_sap.ecc import ECCClient
sap = ECCClient(conn)
plan = build_plan(prediction, sap, today, planned_stop)   # il planner riconosce ECCClient da solo
Executor(sap, policy).run(plan, Approval(by=user))
```

In produzione servono anche: NW RFC SDK installato sul server di Atherya, una destinazione con SNC
(cifratura) o almeno una rete privata verso il gateway, l'utente tecnico di tipo "Sistema" (non dialogo).

## 3. Le regole di ECC che il connettore rispetta (e che la sandbox fa rispettare)

1. **Unità logica di lavoro.** Le BAPI scrivono in un buffer. Senza `BAPI_TRANSACTION_COMMIT` non resta
   nulla: chiudere la connessione o chiamare `BAPI_TRANSACTION_ROLLBACK` annulla tutto. Il connettore
   fa un commit per ogni azione (`ECCClient.luw()`), così la saga sa esattamente cosa è stato scritto.
2. **Gli errori sono nella tabella RETURN.** Una BAPI che fallisce *risponde normalmente* con `TYPE = 'E'`.
   `ECCClient.bapi()` trasforma ogni E/A in eccezione. Mai chiamare una BAPI con `call()` e ignorare RETURN.
3. **Alcuni moduli usano eccezioni ABAP** invece di RETURN: `RFC_READ_TABLE`, `L_TO_CREATE_SINGLE`,
   `L_TO_CONFIRM`, `MEASUREM_DOCUM_RFC_SINGLE_001`. Arrivano come `ABAPApplicationError` con `key`.
4. **Formato interno.** Ordini `000004000101` (12), avvisi `000010000101` (12), apparecchiature
   `000000000010000007` (18), prenotazioni e RdA a 10 cifre. Con il formato esterno la BAPI risponde
   "non esiste". Date `AAAAMMGG`, ore `HHMMSS`, pezzi in unità interna `ST`. Usare `alpha()`/`unalpha()`.
5. **Niente ETag: blocchi.** Se qualcuno ha l'ordine aperto (CO02, IW32) la BAPI risponde `MC 601`
   → `SAPLockedError`: si riprova più tardi, non si forza. Gli utenti simulati tengono aperti ordini
   durante i turni (vedi SM12). Per lo spostamento delle operazioni il modulo cliente riceve anche il
   centro di lavoro atteso: se è cambiato → `SAPConflictError`, come il 412 di OData.
6. **Due barriere di autorizzazione.** `S_RFC` per modulo funzione (`RFC_NO_AUTHORITY`) e i controlli di
   business (il rilascio di un ordine). Entrambi diventano `SAPPermissionError`: Atherya si ferma.
7. **Esito incerto.** Se la risposta del commit si perde (`CommunicationError`), il documento può esistere.
   L'executor cerca prima per riferimento (`ZZ_ATHERYA_REF` via `RFC_READ_TABLE`) e solo dopo riprova.
8. **RFC_READ_TABLE ha limiti veri:** righe massimo 512 caratteri (le tabelle reali sono larghe: indicare
   sempre `FIELDS`), condizioni in righe da 72 caratteri, nessun join, spesso bloccata da `S_TABU_DIS`
   nei sistemi dei clienti. Serve per letture puntuali, non come canale dati principale.

## 4. Moduli funzione disponibili

| Area | Modulo | Note |
|---|---|---|
| Tecnici | `RFC_PING`, `RFC_SYSTEM_INFO`, `DDIF_FIELDINFO_GET`, `RFC_READ_TABLE` | |
| LUW | `BAPI_TRANSACTION_COMMIT` (WAIT), `BAPI_TRANSACTION_ROLLBACK` | |
| Avvisi | `BAPI_ALM_NOTIF_CREATE` → numero temporaneo `%00000000001`, `BAPI_ALM_NOTIF_SAVE` → numero definitivo, `BAPI_ALM_NOTIF_GET_DETAIL` (con testo lungo), `BAPI_ALM_NOTIF_CLOSE` | |
| Ordini PM | `BAPI_ALM_ORDER_MAINTAIN` (IT_METHODS: CREATE, CHANGE, RELEASE, TECHNICALCOMPLETE, LOCK, UNLOCK, sempre con SAVE), `BAPI_ALM_ORDER_GET_DETAIL`, `BAPI_ALM_ORDERHEAD_GET_LIST`, `BAPI_ALM_CONF_CREATE` | senza riga SAVE non si registra nulla |
| Misure | `MEASUREM_DOCUM_RFC_SINGLE_001` | eccezioni ABAP |
| Anagrafiche | `BAPI_EQUI_GETDETAIL`, `BAPI_MATERIAL_GET_DETAIL` | |
| Produzione | `BAPI_PRODORD_GET_DETAIL`, `BAPI_PRODORD_RELEASE`, `BAPI_PRODORD_CREATE_FROM_PLORD`, `BAPI_PRODORDCONF_CREATE_TT`, `BAPI_PLANNEDORDER_GET_DETAIL`, `BAPI_MATERIAL_STOCK_REQ_LIST` (MD04), `BAPI_MATERIAL_AVAILABILITY` | RETURN qui è una struttura, non una tabella |
| Materiali | `BAPI_GOODSMVT_CREATE` (261/201, anche da prenotazione), `BAPI_RESERVATION_CREATE1`, `BAPI_RESERVATION_DELETE`, `BAPI_PR_CREATE`, `BAPI_REQUISITION_DELETE` | `BAPI_REQUISITION_DELETE` usa il vecchio formato BAPIRETURN |
| WM | `L_TO_CREATE_SINGLE`, `L_TO_CONFIRM` | fanno commit da soli se `I_COMMIT_WORK = 'X'` |
| Cliente | `Z_ATHERYA_PRODORD_OPR_CHANGE` | **sviluppo da fare nel sistema del cliente**, vedi §6 |

Permessi di `ATHERYA_RFC`: tutte le letture; in scrittura solo avvisi, ordini PM (creazione e modifica,
non rilascio), prenotazioni, RdA, misure, il modulo Z. Niente rilascio di ordini, niente conferme,
niente movimenti merci, niente WM.

## 5. Tabelle leggibili (RFC_READ_TABLE, SE16N)

Manutenzione `QMEL QMIH VIQMEL AUFK AFIH AFKO AFVC AFVV AFRU EQUI EQKT V_EQUI IFLOT IFLOTX IMPTT IMRG MPLA MPOS MHIS JEST TJ02T` ·
Produzione `AFPO PLAF PBIM PBED MAST STPO CRHD CRTX` · Materiali `MARA MAKT MARC MARD T001W T001L RESB RKPF EBAN MKPF MSEG` ·
WM `T301T LAGP LQUA LTAK LTAP`.

Insidie volute, come nel sistema vero: il centro di lavoro nelle operazioni è un ID (`AFVC-ARBID`, da
risolvere con `CRHD`); l'apparecchiatura di un avviso è in `QMIH`, non in `QMEL` (oppure la vista `VIQMEL`);
gli stati sono in `JEST` con codici interni (`I0002` = REL) da decodificare con `TJ02T`.

## 6. Cosa chiedere all'IT del cliente (progetto ECC)

1. **Utente tecnico** `ATHERYA_RFC` tipo Sistema, ruolo con S_RFC sui gruppi funzione elencati in §4 e i
   permessi PM/MM per creare (non rilasciare).
2. **Campo cliente** `ZZ_ATHERYA_REF` (append su QMEL, AUFK/CI_AUFK, RKPF, EBAN/CI_EBANDB) e la logica
   EXTENSIONIN/EXTENSION_IN nelle BAdI delle BAPI usate. In alternativa: nessun campo e idempotenza con un
   registro lato Atherya (meno robusto se una risposta si perde).
3. **Modulo `Z_ATHERYA_PRODORD_OPR_CHANGE`**: ECC non ha una BAPI rilasciata per cambiare il centro di lavoro
   di un'operazione di un ordine di produzione. Il modulo (abilitato a RFC) blocca l'ordine, verifica il
   centro atteso, cambia l'operazione come CO02 e lascia il commit al chiamante. Un modulo piccolo,
   da trasportare con il normale ciclo DEV → QAS → PRD.
4. **Sistema di qualità** (QAS) per i test, con una copia recente dei dati.
5. Se `RFC_READ_TABLE` è bloccato: un modulo di lettura Z dedicato, o servizi Gateway (SEGW) se il cliente
   ha NetWeaver Gateway attivo su ECC.

## 7. Verifica

```bash
python -m pytest -q tests/test_ecc.py     # 15 test della facciata ECC, del connettore e della GUI
python -m pytest -q                       # tutto: 43 test
```

`test_piano_atherya_su_ecc_evita_il_guasto` è il modello: piano via BAPI, rilascio di una persona,
tre settimane di fabbrica, nessun guasto su P07, ricambio prelevato dalla prenotazione.
