/* Simulatore S/4HANA: app in stile Fiori costruite con OpenUI5 (Apache 2.0) e tema Horizon.
   Nessun logo o marchio SAP: è una simulazione dichiarata. Le app sono descritte dal server
   (/fiori/api/config): colonne, stati, azioni. I dati sono lo stesso stato delle API OData. */
sap.ui.define([
  "sap/m/App", "sap/m/Page", "sap/m/MessageStrip", "sap/f/ShellBar", "sap/m/Avatar", "sap/m/Button",
  "sap/m/GenericTile", "sap/m/TileContent", "sap/m/NumericContent", "sap/m/FlexBox", "sap/m/VBox", "sap/m/HBox",
  "sap/m/Table", "sap/m/Column", "sap/m/ColumnListItem", "sap/m/Text", "sap/m/ObjectIdentifier", "sap/m/ObjectStatus",
  "sap/m/ObjectHeader", "sap/m/ObjectAttribute", "sap/m/IconTabBar", "sap/m/IconTabFilter",
  "sap/ui/layout/form/SimpleForm", "sap/m/Label", "sap/m/Toolbar", "sap/m/ToolbarSpacer", "sap/m/Title",
  "sap/m/SearchField", "sap/m/OverflowToolbar", "sap/m/MessageToast", "sap/m/MessageBox", "sap/m/Dialog",
  "sap/m/Select", "sap/ui/core/Item", "sap/ui/model/json/JSONModel", "sap/ui/model/Filter", "sap/ui/model/FilterOperator",
  "sap/m/ToolbarSeparator"
], function (App, Page, MessageStrip, ShellBar, Avatar, Button, GenericTile, TileContent, NumericContent, FlexBox, VBox, HBox,
  Table, Column, ColumnListItem, Text, ObjectIdentifier, ObjectStatus, ObjectHeader, ObjectAttribute, IconTabBar, IconTabFilter,
  SimpleForm, Label, Toolbar, ToolbarSpacer, Title, SearchField, OverflowToolbar, MessageToast, MessageBox, Dialog,
  Select, Item, JSONModel, Filter, FilterOperator, ToolbarSeparator) {
  "use strict";

  const model = new JSONModel({ counts: {}, clock: {}, rows: {} });
  let CONFIG = [];
  let appCtl;
  let current = null; // { app, key, json } quando è aperto un dettaglio
  let currentList = null; // id dell'app il cui elenco è aperto

  // etichette italiane dei campi; il nome tecnico OData resta visibile come suggerimento (come F1 in SAP)
  const LABELS = { "NotificationText": "Descrizione", "MaintNotifLongText": "Testo esteso", "NotificationType": "Tipo avviso", "TechnicalObject": "Oggetto tecnico", "TechObjIsEquipOrFuncnlLoc": "Tipo oggetto tecnico", "TechnicalObjectType": "Tipo oggetto tecnico", "MaintenancePlanningPlant": "Divisione di pianificazione", "MaintPriority": "Priorità", "IsBreakdown": "Guasto", "MalfunctionStartDate": "Inizio malfunzionamento", "MalfunctionStartTime": "Ora inizio malfunzionamento", "NotificationCreationDate": "Creato il", "NotificationCreationTime": "Ora creazione", "NotificationCompletionDate": "Completato il", "MaintNotifProcessPhaseCode": "Fase", "MaintenanceNotification": "Avviso", "YY1_AtheryaRef": "Riferimento Atherya", "MaintenanceOrder": "Ordine di manutenzione", "MaintenanceOrderType": "Tipo ordine", "MaintenanceOrderDesc": "Descrizione", "Equipment": "Apparecchiatura", "EquipmentName": "Descrizione apparecchiatura", "MainWorkCenter": "Centro di lavoro responsabile", "MaintOrdBasicStartDate": "Data inizio cardine", "MaintOrdBasicEndDate": "Data fine cardine", "MaintOrdReleaseDate": "Rilasciato il", "MaintOrdTechCompletionDate": "Chiuso tecnicamente il", "MaintOrdSystemStatus": "Stato di sistema", "MaintenancePlan": "Piano di manutenzione", "MaintenancePlanDesc": "Descrizione piano", "MaintPlanCycleDays": "Ciclo (giorni)", "NextPlannedDate": "Prossima scadenza", "LastCallDate": "Ultima chiamata", "LastCallOrder": "Ultimo ordine", "MaintenanceOrderOperation": "Operazione", "MaintenanceOrderComponent": "Componente", "OperationDescription": "Testo operazione", "PlannedWorkQuantity": "Lavoro pianificato", "PlannedWorkHours": "Ore pianificate", "ActualWorkQuantity": "Lavoro effettivo", "ActualWorkQuantityUnit": "Unità lavoro", "WorkQuantityUnit": "Unità lavoro", "RequiredQuantity": "Quantità richiesta", "WithdrawnQuantity": "Quantità prelevata", "MaintOrderConf": "Conferma", "ConfirmationText": "Testo conferma", "IsFinalConfirmation": "Conferma finale", "FunctionalLocation": "Sede tecnica", "MeasurementDocument": "Documento di misura", "MeasuringPoint": "Punto di misura", "MeasurementReading": "Lettura", "MeasurementReadingUnit": "Unità", "MeasurementReadingDate": "Data lettura", "MeasurementReadingTime": "Ora lettura", "MeasurementDocumentText": "Testo", "Product": "Prodotto", "Material": "Materiale", "Plant": "Divisione", "ProductionPlant": "Divisione di produzione", "StorageLocation": "Magazzino", "WorkingDayDate": "Settimana", "PlannedQuantity": "Quantità pianificata", "PlndIndepRqmtType": "Tipo fabbisogno", "PlndIndepRqmtVersion": "Versione", "PlannedOrder": "Ordine pianificato", "PlannedOrderType": "Tipo ordine pianificato", "PlannedOrderIsFirm": "Fissato", "PlannedTotalQtyInBaseUnit": "Quantità", "PlndOrderPlannedStartDate": "Inizio pianificato", "PlndOrderPlannedEndDate": "Fine pianificata", "MRPController": "Responsabile MRP", "ManufacturingOrder": "Ordine di produzione", "ManufacturingOrderType": "Tipo ordine", "ManufacturingOrderOperation": "Operazione", "ManufacturingOrderSequence": "Sequenza", "MfgOrderPlannedTotalQty": "Quantità totale", "MfgOrderConfirmedYieldQty": "Quantità buona confermata", "MfgOrderConfirmedScrapQty": "Scarto confermato", "MfgOrderPlannedStartDate": "Inizio pianificato", "MfgOrderPlannedEndDate": "Fine pianificata", "MfgOrderActualReleaseDate": "Rilasciato il", "MissingPartsFlag": "Parti mancanti", "OrderSystemStatus": "Stato di sistema", "OrderStatus": "Stato ordine", "ProductionUnit": "Unità di misura", "TotalQuantity": "Quantità totale", "WorkCenter": "Centro di lavoro", "WorkCenterDesc": "Descrizione", "OperationText": "Testo operazione", "OpPlannedTotalQuantity": "Quantità operazione", "OpTotalConfirmedYieldQty": "Quantità buona confermata", "OpTotalConfirmedScrapQty": "Scarto confermato", "Mold": "Stampo", "OpErlstSchedldExecStrtDte": "Inizio schedulato", "OpErlstSchedldExecStrtTme": "Ora inizio schedulato", "OpErlstSchedldExecEndDte": "Fine schedulata", "OpErlstSchedldExecEndTme": "Ora fine schedulata", "OpSetupDurationMin": "Attrezzaggio (min)", "OperationStatus": "Stato operazione", "OpActualExecutionStartDate": "Inizio effettivo", "Remaining": "Residuo (pz)", "Start": "Inizio previsto", "End": "Fine prevista", "Due": "Consegna", "ConfirmationGroup": "Conferma", "OrderID": "Ordine", "OrderOperation": "Operazione", "ConfirmationYieldQuantity": "Quantità buona", "ConfirmationScrapQuantity": "Scarto", "FinalConfirmationType": "Tipo conferma", "PostingDate": "Data di registrazione", "DocumentDate": "Data documento", "BackflushErrors": "Errori di prelievo", "Quantity": "Quantità", "EWMWarehouse": "Numero magazzino", "EWMStorageBin": "Ubicazione", "EWMStorageType": "Tipo magazzino", "EWMStockType": "Tipo stock", "EWMStockQuantityInBaseUnit": "Quantità", "EWMStockQuantityBaseUnit": "Unità", "MatlWrhsStkQtyInMatlBaseUnit": "Libera utilizzazione", "MaterialBaseUnit": "Unità", "InventoryStockType": "Tipo stock", "WarehouseTask": "Compito di magazzino", "WarehouseProcessType": "Tipo processo", "TargetQuantityInBaseUnit": "Quantità", "ActualQuantityInBaseUnit": "Quantità effettiva", "SourceStorageBin": "Ubicazione di origine", "DestinationStorageBin": "Ubicazione di destinazione", "EWMReferenceDocument": "Documento di riferimento", "WhseTaskCreationDateTime": "Creato", "WhseTaskConfirmationDateTime": "Confermato", "WarehouseTaskStatus": "Stato", "MaterialDocument": "Documento materiale", "MaterialDocumentYear": "Esercizio", "MaterialDocumentItem": "Posizione", "MaterialDocumentHeaderText": "Testo testata", "GoodsMovementType": "Tipo movimento", "QuantityInEntryUnit": "Quantità", "EntryUnit": "Unità", "Reservation": "Prenotazione", "PurchaseRequisition": "Richiesta d'acquisto", "PurchaseRequisitionItem": "Posizione", "PurchaseRequisitionType": "Tipo documento", "RequestedQuantity": "Quantità richiesta", "DeliveryDate": "Data di consegna", "ProcessingStatus": "Stato elaborazione", "PurReqnSource": "Origine", "BaseUnit": "Unità", "UnitOfMeasure": "Unità", "PhysicalInventoryDocument": "Documento inventario", "BookQuantity": "Quantità contabile", "CountedQuantity": "Quantità contata", "DifferenceQuantity": "Differenza", "PhysInvtryStatus": "Stato", "CreatedByUser": "Utente", "LastChangedBy": "Ultima modifica da", "ItemMaterial": "Materiale", "ItemMaterialDesc": "Descrizione materiale", "ItemQuantity": "Quantità", "ItemDate": "Data", "ItemStatus": "Stato posizione", "ResvnItmRequiredQtyInBaseUnit": "Quantità richiesta", "MatlCompRequirementDate": "Data fabbisogno", "ReservationItem": "Posizione", "ReservationItemIsFinallyIssued": "Prelievo concluso", "ResvnItmWithdrawnQtyInBaseUnit": "Quantità prelevata", "ts": "Data e ora", "Operation": "Operazione", "Table": "Oggetto", "Document": "Documento", "Changes": "Modifiche", "Line": "Linea" };
  const labelOf = (k) => LABELS[k] || k;

  // ---------------------------------------------------------------- formattazione
  function parseDate(v) {
    if (!v) { return null; }
    const s = String(v);
    const m = /\/Date\((-?\d+)\)\//.exec(s);
    if (m) { return new Date(parseInt(m[1], 10)); }
    const d = new Date(s.length === 10 ? s + "T00:00:00" : s);
    return isNaN(d) ? null : d;
  }
  const fmtDate = (v) => { const d = parseDate(v); return d ? d.toLocaleDateString("it-IT") : (v || ""); };
  const fmtDateTime = (v) => {
    const d = parseDate(v);
    return d ? d.toLocaleDateString("it-IT") + " " + d.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" }) : (v || "");
  };
  const fmtNumber = (v) => (v === "" || v === null || v === undefined ? "" : Number(v).toLocaleString("it-IT", { maximumFractionDigits: 2 }));
  const SYSTEM_USERS = { SEED: "Dati iniziali", SIM: "Simulazione" };
  const userText = (u) => (u === "ATHERYA_TECH" ? "ATHERYA_TECH · utente tecnico" : (SYSTEM_USERS[u] || u || ""));
  const userState = (u) => (u === "ATHERYA_TECH" ? "Information" : "None");
  const statusOf = (c, v) => (c.map && (c.map[String(v)] || c.map[String(v).charAt(0).toUpperCase() + String(v).slice(1)])) || [v === undefined || v === null ? "" : String(v), "None"];
  const percentState = (v) => (v > 100 ? "Error" : v >= 85 ? "Warning" : v > 0 ? "Success" : "None");
  const short = (s, n) => { s = s === undefined || s === null ? "" : String(s); return s.length > n ? s.slice(0, n - 1) + "…" : s; };

  function formatValue(kind, v) {
    if (kind === "date") { return fmtDate(v); }
    if (kind === "datetime") { return fmtDateTime(v); }
    if (kind === "number") { return fmtNumber(v); }
    if (kind === "percent") { return v === "" || v === undefined ? "" : Math.round(v) + " %"; }
    if (kind === "user") { return userText(v); }
    if (typeof v === "boolean") { return v ? "Sì" : "No"; }
    const dur = /^PT(\d+)H(\d+)M(\d+)S$/.exec(String(v));
    if (dur) { return dur[1].padStart(2, "0") + ":" + dur[2].padStart(2, "0"); }
    return v === undefined || v === null ? "" : String(v);
  }

  // ---------------------------------------------------------------- celle
  function cell(c) {
    const p = "d>" + c.field;
    switch (c.kind) {
      case "id":
        return new ObjectIdentifier({ title: "{" + p + "}", text: c.sub ? "{d>" + c.sub + "}" : "" });
      case "status":
        return new ObjectStatus({ text: { path: p, formatter: (v) => statusOf(c, v)[0] }, state: { path: p, formatter: (v) => statusOf(c, v)[1] } });
      case "user":
        return new ObjectStatus({ text: { path: p, formatter: userText }, state: { path: p, formatter: userState } });
      case "percent":
        return new ObjectStatus({ text: { path: p, formatter: (v) => formatValue("percent", v) }, state: { path: p, formatter: percentState } });
      case "number":
        return new Text({ text: { path: p, formatter: fmtNumber } }).addStyleClass("simClock");
      case "date":
      case "datetime":
        return new Text({ text: { path: p, formatter: (v) => formatValue(c.kind, v) } });
      default:
        return new Text({ text: { path: p, formatter: (v) => short(v, 90) }, wrapping: true });
    }
  }

  // ---------------------------------------------------------------- dati
  const getJSON = (url) => fetch(url).then((r) => r.json());

  const lastRows = {};
  function loadRows(id, top) {
    return getJSON("/fiori/api/data/" + id + "?top=" + (top || 400)).then((rows) => {
      const json = JSON.stringify(rows);
      if (lastRows[id] !== json) { lastRows[id] = json; model.setProperty("/rows/" + id, rows); } // niente ridisegno se non cambia nulla
      return rows;
    });
  }

  function post(url, body) {
    return fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) })
      .then((r) => r.json().then((j) => ({ ok: r.ok, j: j })));
  }

  function act(a, row, action, params) {
    return post("/fiori/api/action", { action: action.id, key: row.__key, params: params || {}, user: "PLANNER" }).then((res) => {
      if (!res.ok) { MessageBox.error(res.j.error.message.value, { title: "Messaggio di errore · " + res.j.error.code }); return; }
      MessageToast.show(res.j.message);
      refresh();
    });
  }

  // ---------------------------------------------------------------- pagina iniziale
  function homePage() {
    const groups = [];
    CONFIG.forEach((a) => { if (groups.indexOf(a.group) < 0) { groups.push(a.group); } });
    const content = [];
    const kpi = (label, key, unit) => new GenericTile({
      header: label, frameType: "OneByHalf", press: () => openList(CONFIG.find((a) => a.id === "events")),
      tileContent: [new TileContent({ unit: unit || "", content: new NumericContent({ withMargin: false, truncateValueTo: 12,
        value: { path: "d>/clock/kpi/" + key, formatter: (v) => fmtNumber(v || 0) } }) })]
    });
    content.push(new Title({ text: "La fabbrica dall'inizio della simulazione", level: "H3" })
      .addStyleClass("sapUiMediumMarginBegin sapUiMediumMarginTop sapUiSmallMarginBottom"));
    content.push(new FlexBox({ items: [kpi("Pezzi buoni", "produced", "pz"), kpi("Scarti", "scrap", "pz"), kpi("Spediti", "shipped", "pz"),
      kpi("Guasti", "breakdowns"), kpi("Fermo macchina", "downtime_min", "min"),
      kpi("Ricambi preventivi", "preventive_replacements"), kpi("Ricambi dopo guasto", "corrective_replacements")] })
      .addStyleClass("simTiles sapUiMediumMarginBeginEnd"));
    groups.forEach((g) => {
      content.push(new Title({ text: g, level: "H3" }).addStyleClass("sapUiMediumMarginBegin sapUiMediumMarginTop sapUiSmallMarginBottom"));
      content.push(new FlexBox({ items: CONFIG.filter((a) => a.group === g).map((a) => new GenericTile({
        header: a.title,
        subheader: a.sub,
        press: () => openList(a),
        tileContent: [new TileContent({
          footer: a.group.indexOf("Simulazione") === 0 ? "Solo simulazione" : "Documenti",
          content: new NumericContent({ value: { path: "d>/counts/" + a.id, formatter: (n) => String(n || 0) }, icon: a.icon, withMargin: false })
        })]
      })) }).addStyleClass("simTiles sapUiMediumMarginBeginEnd"));
    });
    content.push(new VBox({ height: "2rem" }));
    return new Page("home", { showHeader: false, content: content });
  }

  // ---------------------------------------------------------------- elenco
  const listPages = {};

  function listPage(a) {
    const searchable = a.cols.map((c) => c.field).concat(a.cols.filter((c) => c.sub).map((c) => c.sub));
    const table = new Table({
      growing: true, growingThreshold: 50, sticky: ["ColumnHeaders", "HeaderToolbar"], noDataText: "Nessun documento",
      headerToolbar: new Toolbar({ content: [
        new Title({ text: { path: "d>/rows/" + a.id, formatter: (arr) => a.title + " (" + (arr || []).length + ")" } }),
        new ToolbarSpacer(),
        new SearchField({ width: "18rem", placeholder: "Cerca", liveChange: (e) => {
          const v = e.getParameter("newValue");
          table.getBinding("items").filter(v ? [new Filter({
            filters: searchable.map((f) => new Filter({ path: f, test: (x) => x !== null && x !== undefined && String(x).toLowerCase().indexOf(v.toLowerCase()) >= 0 })),
            and: false })] : []);
        } }),
        new Button({ icon: "sap-icon://refresh", tooltip: "Aggiorna", type: "Transparent", press: () => loadRows(a.id) })
      ] }),
      columns: a.cols.map((c, i) => new Column({
        header: new Label({ wrapping: true, text: c.kind === "percent"
          ? { path: "d>/rows/" + a.id + "/0/" + c.field + "Date", formatter: (v) => (v ? parseDate(v).toLocaleDateString("it-IT", { weekday: "short", day: "2-digit", month: "2-digit" }) : c.label) }
          : c.label }),
        hAlign: c.kind === "number" || c.kind === "percent" ? "End" : "Begin",
        minScreenWidth: i < 3 ? "" : "Tablet", demandPopin: true
      }))
    });
    table.bindItems({
      path: "d>/rows/" + a.id,
      templateShareable: false,
      template: new ColumnListItem({
        type: a.inactive ? "Inactive" : "Navigation",
        press: (e) => openDetail(a, e.getSource().getBindingContext("d").getObject()),
        cells: a.cols.map(cell)
      })
    });
    return new Page(a.id + "Page", { title: a.title, showNavButton: true, navButtonPress: () => { currentList = null; appCtl.back(); },
      content: [table] }).addStyleClass("sapUiResponsivePadding--header sapUiResponsivePadding--content");
  }

  function openList(a) {
    if (!listPages[a.id]) { listPages[a.id] = listPage(a); appCtl.addPage(listPages[a.id]); }
    currentList = a.id;
    loadRows(a.id);
    appCtl.to(listPages[a.id]);
  }

  // ---------------------------------------------------------------- dettaglio
  const detail = new Page("detail", { showNavButton: true, navButtonPress: () => { current = null; appCtl.back(); } })
    .addStyleClass("sapUiResponsivePadding--header");

  function fieldsForm(a, obj, children) {
    const kinds = {};
    a.cols.forEach((c) => { kinds[c.field] = c; });
    const labels = {};
    a.cols.forEach((c) => { labels[c.field] = c.label; });
    const form = new SimpleForm({ editable: false, layout: "ResponsiveGridLayout", labelSpanL: 4, labelSpanM: 4, columnsL: 2, columnsXL: 2 });
    Object.keys(obj).filter((k) => k !== "__key" && k !== "Items" && children.indexOf(k) < 0 && (obj[k] === null || typeof obj[k] !== "object"))
      .forEach((k) => {
        const c = kinds[k];
        let kind = c ? c.kind : (/(Date|Dte)$/.test(k) ? "date" : /DateTime$|^ts$/.test(k) ? "datetime" : k === "_by" || k === "_changed_by" ? "user" : "text");
        let v = c && c.kind === "status" ? statusOf(c, obj[k])[0] : formatValue(kind, obj[k]);
        const label = k === "_by" ? "Creato da" : k === "_changed_by" ? "Ultima modifica da" : k === "_wc" ? "Centro di lavoro" : (labels[k] || labelOf(k));
        form.addContent(new Label({ text: label, tooltip: k }));
        form.addContent(new Text({ text: v }));
      });
    return form;
  }

  function childTable(items) {
    const keys = Object.keys(items[0] || {}).filter((k) => k[0] !== "_");
    return new Table({
      columns: keys.map((k) => new Column({ header: new Label({ text: labelOf(k), tooltip: k, wrapping: true }), minScreenWidth: "Tablet", demandPopin: true })),
      items: items.map((it) => new ColumnListItem({ cells: keys.map((k) => new Text({
        text: /(Date|Dte)$/.test(k) ? fmtDate(it[k]) : typeof it[k] === "number" ? fmtNumber(it[k]) : formatValue("text", it[k])
      })) }))
    });
  }

  function changesTable(doc, log) {
    const rows = log.filter((l) => String(l.Document) === doc);
    return new Table({
      noDataText: "Nessuna modifica registrata",
      columns: ["Data e ora", "Utente", "Operazione", "Modifiche"].map((h) => new Column({ header: new Label({ text: h }) })),
      items: rows.map((l) => new ColumnListItem({ cells: [
        new Text({ text: fmtDateTime(l.ts) }),
        new ObjectStatus({ text: userText(l.CreatedByUser), state: userState(l.CreatedByUser) }),
        new Text({ text: l.Operation }), new Text({ text: short(l.Changes, 220), wrapping: true })
      ] }))
    });
  }

  function actionAllowed(action, obj) {
    return Object.keys(action.when || {}).every((f) => action.when[f].indexOf(obj[f] === undefined || obj[f] === null ? "" : String(obj[f])) >= 0);
  }

  function moveDialog(a, obj, action) {
    post("/fiori/api/action", { action: "op_options", key: obj.__key }).then((res) => {
      if (!res.ok) { MessageBox.error(res.j.error.message.value); return; }
      const options = res.j.message ? res.j.message.split(",") : [];
      const select = new Select({ width: "100%", selectedKey: obj.WorkCenter, items: options.map((o) => new Item({ key: o, text: o })) });
      const dialog = new Dialog({
        title: "Cambia pressa · ordine " + obj.ManufacturingOrder,
        contentWidth: "26rem",
        content: [new VBox({ items: [
          new Text({ text: "Presse idonee per lo stampo " + (obj.Mold || "") + ". L'operazione viene rischedulata sulla nuova pressa." }).addStyleClass("sapUiSmallMarginBottom"),
          new Label({ text: "Centro di lavoro", labelFor: select }), select
        ] }).addStyleClass("sapUiSmallMargin")],
        beginButton: new Button({ text: "Salva", type: "Emphasized", press: () => {
          act(a, obj, action, { WorkCenter: select.getSelectedKey() });
          dialog.close();
        } }),
        endButton: new Button({ text: "Annulla", press: () => dialog.close() }),
        afterClose: () => dialog.destroy()
      });
      dialog.open();
    });
  }

  function renderDetail(a, obj, log) {
    current.json = JSON.stringify(obj);
    detail.destroyContent();
    detail.destroyFooter();
    detail.setTitle(a.title);
    const idCol = a.cols.find((c) => c.kind === "id") || a.cols[0];
    const statuses = a.cols.filter((c) => c.kind === "status").map((c) => {
      const s = statusOf(c, obj[c.field]);
      return new ObjectStatus({ title: c.label, text: s[0], state: s[1] });
    });
    const attributes = [];
    if (obj._by) { attributes.push(new ObjectAttribute({ title: "Creato da", text: userText(obj._by) })); }
    if (obj._changed_by && obj._changed_by !== obj._by) { attributes.push(new ObjectAttribute({ title: "Ultima modifica da", text: userText(obj._changed_by) })); }
    if (obj.YY1_AtheryaRef) { attributes.push(new ObjectAttribute({ title: "Riferimento Atherya", text: obj.YY1_AtheryaRef })); }
    detail.addContent(new ObjectHeader({
      title: (idCol.sub && obj[idCol.sub] && obj[idCol.sub] !== obj.Material ? obj[idCol.sub] : null) || obj.OperationText || obj.NotificationText ||
        obj.MaintenanceOrderDesc || obj.MaterialDocumentHeaderText || obj.ItemMaterialDesc || obj.WorkCenterDesc || (idCol.sub && obj[idCol.sub]) || a.title,
      number: String(obj[idCol.field] === undefined ? "" : obj[idCol.field]),
      responsive: true, backgroundDesign: "Translucent", attributes: attributes, statuses: statuses
    }));
    const children = (a.children || []).map((c) => c.field);
    const tabs = [new IconTabFilter({ text: "Dati generali", content: [fieldsForm(a, obj, children)] })];
    (a.children || []).forEach((c) => {
      const items = obj[c.field] || [];
      tabs.push(new IconTabFilter({ text: c.title, count: String(items.length),
        content: [items.length ? childTable(items) : new Text({ text: "Nessuna posizione" }).addStyleClass("sapUiSmallMargin")] }));
    });
    tabs.push(new IconTabFilter({ text: "Registro modifiche", content: [changesTable(String(obj[idCol.field]), log)] }));
    detail.addContent(new IconTabBar({ expandable: false, items: tabs }).addStyleClass("sapUiResponsiveContentPadding"));

    const buttons = (a.actions || []).filter((x) => x.id !== "op_options").map((x, i) => new Button({
      text: x.label, type: i === 0 ? "Emphasized" : "Default", enabled: actionAllowed(x, obj),
      press: () => (x.id === "op_move" ? moveDialog(a, obj, x) : act(a, obj, x))
    }));
    detail.setShowFooter(buttons.length > 0);
    if (buttons.length) { detail.setFooter(new OverflowToolbar({ content: [new ToolbarSpacer()].concat(buttons) })); }
  }

  function openDetail(a, obj) {
    current = { app: a, key: obj.__key, json: "" };
    getJSON("/fiori/api/data/log?top=3000").then((log) => {
      renderDetail(a, obj, log);
      appCtl.to(detail);
    });
  }

  // ---------------------------------------------------------------- orologio simulato
  function clockBar() {
    const clockText = new Title({ level: "H5", text: {
      path: "d>/clock", formatter: (c) => (c && c.now ? c.weekday + " " + fmtDateTime(c.now) + " · " + c.shift : "") } }).addStyleClass("simClock");
    const advance = (minutes) => post("/sim/advance", { minutes: minutes }).then(refresh);
    const runBtn = new Button({
      text: { path: "d>/clock/running", formatter: (r) => (r ? "Ferma" : "Avvia tempo") },
      icon: { path: "d>/clock/running", formatter: (r) => (r ? "sap-icon://media-pause" : "sap-icon://media-play") },
      type: { path: "d>/clock/running", formatter: (r) => (r ? "Reject" : "Accept") },
      tooltip: "In tempo continuo ogni secondo reale corrisponde a 15 minuti di fabbrica",
      press: () => post(model.getProperty("/clock/running") ? "/sim/stop" : "/sim/run", { seconds_per_step: 1 }).then(refresh)
    });
    return new OverflowToolbar({ style: "Clear", content: [
      new Button({ icon: "sap-icon://home", type: "Transparent", tooltip: "Pagina iniziale",
        press: () => { current = null; currentList = null; appCtl.backToTop(); } }),
      new Label({ text: "Orologio di fabbrica" }), clockText,
      new ToolbarSpacer(),
      runBtn,
      new Button({ text: "+1 ora", press: () => advance(60) }),
      new Button({ text: "+1 giorno", press: () => advance(24 * 60) }),
      new Button({ text: "+1 settimana", press: () => advance(7 * 24 * 60) }),
      new Button({ text: "Esegui MRP", icon: "sap-icon://process", press: () => post("/sim/mrp").then((r) => {
        MessageToast.show("MRP eseguito"); refresh(); return r;
      }) }),
      new Button({ text: "Ricomincia", icon: "sap-icon://reset", type: "Transparent", press: () => MessageBox.confirm(
        "La fabbrica torna a giovedì 1 ottobre 2026, ore 06:00. Tutti i documenti creati vanno persi.", {
          title: "Ricominciare la simulazione?",
          onClose: (r) => { if (r === MessageBox.Action.OK) { post("/sim/reset").then(() => { appCtl.backToTop(); current = null; currentList = null; refresh(); }); } }
        }) })
    ] }).addStyleClass("sapUiTinyMarginBeginEnd");
  }

  // ---------------------------------------------------------------- aggiornamento
  let busy = false;
  function refresh() {
    if (busy) { return Promise.resolve(); }
    busy = true;
    const jobs = [getJSON("/fiori/api/summary").then((s) => { model.setProperty("/clock", s.clock); model.setProperty("/counts", s.counts); })];
    const page = appCtl.getCurrentPage();
    if (current && page === detail) {
      jobs.push(Promise.all([loadRows(current.app.id, 2000), getJSON("/fiori/api/data/log?top=3000")]).then((res) => {
        const obj = res[0].find((o) => o.__key === current.key);
        if (obj && JSON.stringify(obj) !== current.json) { renderDetail(current.app, obj, res[1]); }
      }));
    } else if (currentList && page === listPages[currentList]) {
      jobs.push(loadRows(currentList));
    }
    return Promise.all(jobs).finally(() => { busy = false; });
  }

  // ---------------------------------------------------------------- avvio
  getJSON("/fiori/api/config").then((config) => {
    CONFIG = config;
    new ShellBar({
      title: "Simulatore S/4HANA",
      secondTitle: "Stabilimento 1000 · utente PLANNER",
      showCopilot: false,
      profile: new Avatar({ initials: "PL" })
    }).placeAt("shell");

    new MessageStrip({
      text: "Simulazione con le API SAP ufficiali · in produzione sul sistema del cliente",
      type: "Warning", showIcon: true
    }).addStyleClass("sapUiTinyMargin").placeAt("strip");

    const bar = clockBar();
    bar.setModel(model, "d");
    bar.placeAt("clock");

    appCtl = new App({ pages: [homePage(), detail] });
    appCtl.setModel(model, "d");
    appCtl.placeAt("app");

    refresh();
    setInterval(refresh, 3000);
  });
});
