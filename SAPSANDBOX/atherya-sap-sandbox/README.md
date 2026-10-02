# Atherya · sandbox SAP simulata (S/4HANA ed ECC 6.0)

Una fabbrica di articoli in gomma che vive da sola, con un ERP simulato davanti:
**manutenzione (PM), produzione (PP) e magazzino**. Due facciate sulla stessa fabbrica:

- **S/4HANA on-premise**: API OData V2 e app in stile Fiori (`/fiori/`);
- **ECC 6.0**: RFC/BAPI (client compatibile pyrfc e SOAP), tabelle del dizionario via RFC_READ_TABLE,
  interfaccia in stile SAP GUI con i codici transazione (`/sap/bc/gui/sap/its/webgui/`).

Un documento creato da una parte si vede dall'altra. Serve a sviluppare e provare gli agenti di
Atherya per settimane, sui due mondi SAP che troverà nei clienti, senza un SAP vero e senza
toccare i dati di un cliente.

Fatture, ordini d'acquisto completi, contabilità e vendite restano fuori: le richieste d'acquisto
"diventano" consegne dopo il lead time, le spedizioni sono uscite merci 601.

**Dichiarazione da fare sempre:** simulazione con le API SAP ufficiali; in produzione sul sistema
del cliente. Nessun logo o marchio SAP.

## Avvio

```bash
pip install -r requirements.txt
python -m pytest -q                         # 43 test: connettore OData, sandbox, facciata ECC
uvicorn sap_sim.server:app --port 8080
# http://localhost:8080/fiori/                        app Fiori (utente PLANNER) con l'orologio di fabbrica
# http://localhost:8080/sap/opu/odata/sap/            API OData V2 (utente ATHERYA_TECH / demo)
# http://localhost:8080/sap/bc/gui/sap/its/webgui/    GUI ECC: IW38, COOIS, MD04, SE16N, SM12… (utente PLANNER)
# RFC: pyrfc_sim.Connection(ashost="http://localhost:8080", client="100", user="ATHERYA_RFC", passwd="demo")
python demo.py --url http://localhost:8080  # il demo in tre atti, mentre guardi le app
```

Serve internet per l'interfaccia: OpenUI5 1.138.0 si carica da sdk.openui5.org.

## La fabbrica

| | |
|---|---|
| Divisione | 1000, magazzino 0001, magazzino EWM W100 |
| Presse | 43: linea A P01–P16 (250 t), linea B P17–P31 (320 t), linea C P32–P43 (vulcanizzazione) |
| Prodotti | 8 articoli finiti con stampo, mescola, inserto, tempo ciclo; alcuni condividono lo stampo (A-2210 e A-5120 su S-118) |
| Turni | 3 turni da 8 ore, lunedì–venerdì |
| Squadre | MAINT01 (3 manutentori), MAINT02 |
| Ricambi critici | GUARN-HYD-250 (2 a magazzino), GUARN-HYD-320 (0 a magazzino, consegna in 12 giorni) |

Ogni pressa ha un'**usura nascosta** che cresce con i cicli (più in fretta con temperatura e
pressione alte). Quando supera la sua soglia la pressa si guasta. SAP non vede l'usura: vede
solo i documenti di misura (temperatura olio, pressione, contacicli) che lo SCADA scrive ogni ora.
È esattamente il problema che Atherya deve risolvere.

## Chi lavora nella fabbrica (utenti simulati)

| Utente | Cosa fa |
|---|---|
| OPERATORE | conferma la produzione a fine turno; apre l'avviso M2 quando una pressa si ferma |
| SCADA | scrive le misure ogni ora |
| PIANIFICATORE | MRP notturno, conversione degli ordini pianificati, rilascio, sequenza sulle presse |
| BATCH | schedulazione dei piani di manutenzione (PM02 ogni 30 giorni) |
| MANUTENZIONE / MANUTENTORE | ordini correttivi dopo un guasto, rilascio, esecuzione, conferma, chiusura |
| MAGAZZINO | conferma i compiti di magazzino, conta a rotazione il sabato |
| ACQUISTI / FORNITORE | evade le richieste d'acquisto e consegna dopo il lead time |
| SPEDIZIONI | uscita merci giornaliera verso i clienti |
| PLANNER | l'utente umano delle app Fiori: può tutto, anche rilasciare |
| ATHERYA_TECH | l'utente tecnico di Atherya: legge tutto, crea e modifica il minimo, **non rilascia** |

## Il calendario di una giornata

| Quando | Cosa |
|---|---|
| ogni 15 minuti | le presse producono, si usurano, possono guastarsi; la squadra lavora |
| ogni ora | misure SCADA, compiti di magazzino confermati, ordini correttivi, commesse spostate dalle presse ferme |
| fine turno | conferme di produzione (prelievo a consuntivo 261, entrata merci 101) |
| 02:00 | piani di manutenzione, rielaborazione errori di prelievo, MRP, conversione, rilascio, sequenza |
| 10:00 | acquisti e consegne |
| 16:00 | spedizioni |
| sabato 08:00 | inventario a rotazione |

Sei settimane si simulano in circa 5 secondi. Con il seme 7, in sei settimane:
14 guasti, circa 2,85 milioni di pezzi buoni, 639 ordini di produzione, 3.588 conferme,
9.497 documenti materiale, 78.000 misure. P07 si rompe intorno al 20 ottobre se nessuno interviene.

## Comandare il tempo

Dalle app Fiori (barra dell'orologio) oppure via API:

```bash
curl -X POST localhost:8080/sim/advance -H 'content-type: application/json' -d '{"minutes": 1440}'
curl -X POST localhost:8080/sim/run -H 'content-type: application/json' -d '{"seconds_per_step": 1}'  # tempo continuo
curl -X POST localhost:8080/sim/stop
curl -X POST localhost:8080/sim/reset -H 'content-type: application/json' -d '{"seed": 7}'
curl localhost:8080/sim/truth   # usura reale e soglia di guasto: solo per valutare Atherya
```

La simulazione è deterministica: stesso seme, stessa storia. Così un test può dire
"senza Atherya P07 si rompe, con Atherya no" (`tests/test_sandbox.py`).

## App Fiori

Pagina iniziale con i numeri della fabbrica e 23 app in cinque gruppi:

- **Manutenzione:** avvisi, ordini (con operazioni e componenti), conferme, piani, apparecchiature, documenti di misura.
- **Produzione:** fabbisogni indipendenti, ordini pianificati, ordini di produzione, sequenza per pressa, conferme, carico delle presse, errori di prelievo.
- **Magazzino e materiali:** giacenze per ubicazione, giacenze di materiale, compiti di magazzino, documenti materiale, prenotazioni, richieste d'acquisto, inventario fisico.
- **Controllo:** registro modifiche (chi ha fatto cosa, utente per utente).
- **Simulazione · non visibile in SAP:** stato reale delle presse e cronaca della fabbrica.

Azioni da utente umano, con le stesse regole delle API: creare l'ordine da un avviso, completare
un avviso, rilasciare e chiudere ordini, convertire un ordine pianificato, rilasciare un ordine di
produzione, cambiare pressa (solo tra le presse idonee per lo stampo), confermare un compito di magazzino.

## Facciata ECC 6.0

Stessa fabbrica, dialetto ECC. Per le persone, la GUI con i codici transazione:

| Area | Transazioni |
|---|---|
| Manutenzione | IW21 crea avviso · IW28 avvisi · IW23 avviso · IW38 ordini PM · IW33 ordine (rilascio, chiusura, blocco) · IK17 misure · IH08 apparecchiature · IP16 piani |
| Produzione | COOIS ordini · CO03 ordine (rilascio, cambio pressa) · MD16 ordini pianificati (conversione) · MD04 fabbisogni/stock · CM01 carico presse · COGI |
| Magazzino | MMBE · MB52 · MB51 · MB25 prenotazioni · ME5A RdA · LX02 quanti WM · LT23 ordini di trasferimento (conferma) |
| Strumenti | SE16N qualsiasi tabella · SM12 blocchi · SM04 sessioni RFC e unità di lavoro aperte |

Come nella GUI vera, le chiavi si vedono senza zeri (4000101) mentre RFC e tabelle le hanno con gli
zeri (000004000101), e la schermata non si aggiorna da sola. Per Atherya: **CONNECT_ECC.md**.

## Struttura

```
sap_sim/
  world.py        stato, numerazione, registro modifiche, eventi
  calendar.py     turni e giorni lavorativi
  masterdata.py   divisione, presse, prodotti, distinte, cicli, piani, utenti
  inventory.py    giacenze, ubicazioni, movimenti, compiti, prenotazioni, RdA, inventario
  pm.py           avvisi, ordini, rilascio, esecuzione, conferme, misure, piani
  pp.py           domanda, MRP, ordini, rilascio, sequenza, conferme, spedizioni
  factory.py      la fabbrica che vive: macchine, usura, guasti, utenti simulati
  odata.py        28 entità OData V2 e 6 function import
  server.py       FastAPI: OData, comandi della simulazione, dati per le app
  fiori_apps.py   descrizione delle app (colonne, stati, azioni)
  fiori/          interfaccia OpenUI5, tema Horizon
  ecc/            facciata ECC 6.0: conv (ALPHA, date, stati), tables (45 tabelle), rfc (32 moduli funzione,
                  unità logica di lavoro, blocchi, S_RFC), http (SOAP, trasporto RFC), gui + webgui/ (SAP GUI)
pyrfc_sim/        client RFC con la stessa interfaccia di pyrfc
atherya_sap/      il connettore di Atherya (azioni tipizzate, policy, saga, audit); ecc.py = stesso connettore su BAPI
tests/            test del connettore e della sandbox
```

Per collegare Atherya: **CONNECT.md** (S/4HANA, OData) e **CONNECT_ECC.md** (ECC, RFC/BAPI). Per i nomi da verificare prima del SAP vero: **FIELD_MAP.md**.
