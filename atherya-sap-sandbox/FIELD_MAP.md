# Nomi da verificare prima del SAP vero

La sandbox usa i nomi delle API pubblicate da SAP per S/4HANA on-premise, ricostruiti dalla
documentazione e non da un EDMX scaricato. Prima di collegarsi al sistema del cliente,
ogni riga va controllata sul `$metadata` della sua release (o sul SAP Business Accelerator Hub
per la stessa versione). Il connettore di Atherya isola questi nomi in `atherya_sap/actions.py`
e `atherya_sap/client.py`: le correzioni si fanno lì.

Legenda: **alta** = nome e struttura molto probabilmente corretti; **media** = servizio corretto,
qualche campo o navigazione da controllare; **da verificare** = nome plausibile ma incerto o
semplificato; **semplificazione** = comportamento diverso dal SAP vero, voluto.

## Servizi ed entità

| Servizio | Entità usate | Affidabilità | Note |
|---|---|---|---|
| `API_MAINTNOTIFICATION` | `MaintenanceNotification` | alta | Campi `IsBreakdown`, `MalfunctionStartDate/Time`, `MaintNotifProcessPhaseCode`: verificare. Completamento: nel SAP vero è un'azione dedicata, qui `CompleteMaintNotification`. |
| `API_MAINTENANCEORDER` | `MaintenanceOrder`, `MaintenanceOrderOperation`, `MaintenanceOrderComponent` | media | Navigazioni `to_MaintenanceOrderOperation` / `to_MaintenanceOrderComponent` e nomi delle funzioni di rilascio e chiusura tecnica da verificare. `MaintOrdSystemStatus` è un riassunto: nel SAP vero lo stato è una lista di stati di sistema. |
| `API_MAINTORDERCONFIRMATION` | `MaintOrderConfirmation` | media | Chiave `MaintOrderConf` e campi `ActualWorkQuantity` da verificare. |
| `API_MAINTENANCEPLAN` | `MaintenancePlan` | da verificare | Ciclo in giorni (`MaintPlanCycleDays`) è una semplificazione del ciclo a pacchetti/strategie. |
| `API_EQUIPMENT` | `Equipment` | alta | |
| `API_FUNCTIONALLOCATION` | `FunctionalLocation` | alta | |
| `API_MEASURINGPOINT` | `MeasuringPoint` | media | |
| `API_MEASUREMENTDOCUMENT_SRV` | `MeasurementDocument` | media | Data/ora lettura e unità da verificare. |
| `API_PRODUCT_SRV` | `A_Product` | alta | |
| `API_MATERIAL_STOCK_SRV` | `A_MatlStkInAcctMod` | alta | |
| `API_MATERIAL_DOCUMENT_SRV` | `A_MaterialDocumentHeader` (+ voci) | alta | Navigazione `to_MaterialDocumentItem`. Qui solo lettura. |
| `API_RESERVATION_DOCUMENT_SRV` | `A_ReservationDocumentHeader` (+ voci) | alta | Le prenotazioni dei componenti degli ordini di produzione **non** passano da qui (vedi sotto). |
| `API_PURCHASEREQ_PROCESS_SRV` | `A_PurchaseRequisitionHeader` (+ `to_PurchaseReqnItem`) | alta | `ProcessingStatus` N/B/C semplificato; consegna simulata senza ordine d'acquisto. |
| `API_BILL_OF_MATERIAL_SRV` | `MaterialBOM` (+ voci) | media | Chiave semplificata al solo `Material`: nel SAP vero include distinta, variante, utilizzo. |
| `API_PRODUCTION_ROUTING` | `ProductionRouting` (+ operazioni) | da verificare | Chiave semplificata al materiale; nel SAP vero gruppo e contatore del ciclo. |
| `API_WORK_CENTERS` | `A_WorkCenters` | media | La chiave vera include il tipo oggetto e l'ID interno. |
| `API_PLND_INDEP_RQMT_SRV` | `PlannedIndepRqmt` | media | Quantità per settimana in testata: nel SAP vero sono le voci (`PlannedIndepRqmtItem`). |
| `API_PLANNED_ORDERS` | `A_PlannedOrder` | alta | Conversione: in SAP non c'è una function import pubblica equivalente in tutte le release; `ConvertPlannedOrderToProdnOrd` è **da verificare**. |
| `API_PRODUCTION_ORDER_2_SRV` | `A_ProductionOrder_2`, `A_ProductionOrderOperation_2`, `A_ProductionOrderComponent_4` | media | Rilascio `ReleaseOrder` da verificare. Modificabilità dei campi dell'operazione (centro di lavoro, date) dipende dalla release: **da verificare per prima**, è l'azione chiave di Atherya. |
| `API_PROD_ORDER_CONFIRMATION_2_SRV` | `ProdnOrdConf2` | media | Prelievo a consuntivo e entrata merci automatici, come da customizing tipico. |
| `API_MRP_MATERIALS_SRV_01` | `SupplyDemandItems` | da verificare | Categorie elemento (`AR`, `BA`, `PA`, `FE`, `PP`) semplificate. |
| `API_WAREHOUSE_ORDER_TASK_2` | `WarehouseTask` | da verificare | Nelle release recenti molte API EWM sono **OData V4**: controllare protocollo, chiavi (numero magazzino + compito) e azione di conferma. |
| `API_WHSE_PHYSSTOCKPROD` | `WarehousePhysicalStockProducts` | da verificare | Come sopra (V4 probabile). |
| `API_WAREHOUSE_STORAGE_BIN` | `WarehouseStorageBin` | da verificare | Come sopra. |

## Campi e comportamenti semplificati

| Cosa | Nella sandbox | Nel SAP vero |
|---|---|---|
| `YY1_AtheryaRef` | campo su avvisi, ordini, prenotazioni, RdA | **campo personalizzato** da creare con l'app "Campi personalizzati" ed estendere alle API; il nome lo decide il cliente |
| Cancellazione | `IsDeleted` / DELETE semplice | contrassegno per cancellazione tramite stato o azione dedicata; spesso DELETE non è ammesso |
| Stati di sistema | un codice (`CRTD`, `REL`, `PCNF`, `TECO`) | lista di stati di sistema e utente, con profili di stato |
| Autorizzazioni | permessi per servizio e operazione | ruoli, cataloghi, oggetti di autorizzazione (es. `I_AUART`, `C_AFKO_AWK`) |
| `$metadata` | elenco JSON di entità e funzioni | EDMX XML completo |
| Codici di errore | classe di messaggio plausibile + numero inventato | classi e numeri reali (es. `IW`, `CO`, `M7`, `ME`, `RU`, `/SCWM/`): non fare logica sui numeri, solo sullo stato HTTP e sul testo |
| Componenti degli ordini di produzione | in `A_ProductionOrderComponent_4` | prenotazione legata all'ordine, non creabile da `API_RESERVATION_DOCUMENT_SRV` |
| Guasti | un solo modo di guasto (guarnizione idraulica) | qualsiasi |
| Acquisti | RdA → "ordinata" → consegna dopo il lead time | ordine d'acquisto, conferma fornitore, entrata merci, controllo fattura (fuori ambito) |
| Capacità | carico percentuale per giorno calcolato dalla sequenza | valutazione capacità con formule e calendari di fabbrica |

## Messaggi di errore usati dalla sandbox

Codici restituiti in `error.code`: `IW/001–031`, `IW/REL`, `IW/DEL` (manutenzione); `CO/120–142`,
`CR/001`, `CR/014` (ordini di produzione e centri di lavoro); `RU/001–003` (conferme);
`M3/305`, `M3/351` (materiali); `M7/001`, `M7/021`, `M7/062` (prenotazioni e disponibilità);
`ME/083`, `ME/120` (RdA); `IR/001` (misure); `/SCWM/L3/012`, `/SCWM/L3/101`, `/SCWM/PI/010` (magazzino);
`/IWFND/...` e `/IWBEP/...` per autenticazione, CSRF, permessi, risorse e precondizioni.
