# flowmetrics — report di flusso da export Status Time Free (Jira PS)

Fonti: due export CSV Status Time Free, stesso formato. `input_completed` = issue entrate in Done nel periodo;
`input_all` (facoltativo) = tutte le issue del periodo, aperte comprese. Unione per Key in `load.read_exports`
(in entrambi vale la riga di `input_all`; colonna `fonte`); `metrics.sources_check` segnala incongruenze fra i due.
Esempi in root: `sample-data-done.csv`, `sample-data-all.csv`.
Query Jira: `input_all` deve essere un SOVRAINSIEME di `input_completed` (stessa fine periodo, = `period_end`), altrimenti `sources_check` avvisa e il backlog storico e' sottostimato:
`project = PS AND created < "<fine+1>" AND (resolution = EMPTY OR resolved >= "<inizio>" OR status CHANGED TO "Done" DURING ("<inizio>","<fine+1>"))`.
Non usare "status CHANGED TO In Progress DURING": perde le issue gia' avviate prima del periodo e chiuse dentro (e il backlog mai toccato).

    pip install -r requirements.txt
    python -m flowmetrics --config config.yaml            # scrive out/report.html + CSV
    python -m flowmetrics --input-completed data/done.csv --input-all data/all.csv --today 2026-10-07
    pytest

Funziona uguale su Mac e Windows (Python 3.10+).

## Cosa risponde (sezioni del report)
1. Sintesi: ultimi `recent_days` (30/60) giorni contro lo storico (consegne da inizio periodo a 60 gg fa); verdetto
   migliora/peggiora/stabile su lead time P50/P85, cycle time P50, consegne/settimana (soglia `trend_tolerance`);
   messaggi per business e sviluppatori; quota del tempo per fase. Esportata anche in `out/summary.csv`.
2. Issue: tempo per fase (attesa = `queue_statuses`, attesa rilascio = `release_wait_statuses`, lavoro = altri stati non chiusi).
   Anomalia = fase oltre P85/P95 (`anomaly_percentiles`) degli item validi della stessa classe sull'intero periodo
   (classe con meno di `min_samples` item -> tutti gli item); si usano percentili e mediana, non la media (coda lunga).
   La key e' un link se `issue_url` e' impostato (`${issueKey}` sostituito; solo http/https).
3. Lead time / Cycle time: percentili P50/P85/P95 per classe e finestra, probabilita' di consegna entro N giorni.
4. Backlog/WIP a fine mese ricostruiti dalle transizioni; corretto solo con `input_all` (altrimenti avviso: sottostimato).
5. Deployments: item in produzione per mese e classe.

Tab 2-5: in testa KPI (card con delta e mini-grafico), in fondo "Info" con le spiegazioni. KPI = ultimi `kpi_months` (2)
mesi fino all'ultimo dato contro i `kpi_prev_months` (4) precedenti (`metrics.kpi_bounds`): lead/cycle = % entro N gg, un tab per N in `kpi_within_days` (7/14/30/60, aperto `kpi_default_days`), per
`kpi_classes` + Tutti, delta in punti (`kpi_tolerance_pp`), trend cumulato settimanale; backlog/WIP a fine dati vs 2 mesi
prima + consegne; deployments con andamento progressivo dei due periodi sovrapposti; issue = % con anomalie per fase.

Voce "Metodologia" (accanto al tema) = tab FAQ (`#faq`) per non addetti ai lavori, con valori presi da config.
Ogni modifica a regole o metriche va riportata anche li' (linguaggio semplice, percentili spiegati).

Cycle time = dal primo ingresso in `cycle_start_status` (In Progress) alla produzione; item mai passati da In Progress non hanno cycle time.
Tabelle del report: righe da `report_classes` (+ Tutti = tutti gli item validi).

## Struttura
- `config.yaml` tutte le regole di business (classi, filtri, soglie). Primo posto dove intervenire.
- `flowmetrics/load.py` parsing export; `metrics.py` calcoli (funzioni pure); `report.py` + `templates/report.html.j2` HTML; `cli.py`.
- `flowmetrics/vendor/` Tabler (CSS, MIT) ed Apache ECharts (JS) incorporati nel report: nessuna dipendenza esterna, si apre offline.
- `tests/` regole verificate su dati sintetici. Ogni nuova regola = un test.
- `out/` output (items.csv = una riga per item con motivo di esclusione: utile per audit).

## Convenzioni
Regola KPI: salvo requisito diverso (es. Sintesi = ultimi 30/60 gg vs storico), ogni KPI conta gli ultimi `kpi_months` (2)
mesi e, se serve un confronto, lo fa con i `kpi_prev_months` (4) precedenti (`kpi_bounds`): periodi di durata diversa, quindi i conteggi
(consegne, deployments) si confrontano come ritmo a settimana (`_rate_delta`). Badge dei tab compresi. Issue "degli ultimi
2 mesi" = aperte a fine dati o chiuse dopo il taglio (`recent_mask`).
Done = produzione; ultimo ingresso in Done; giorni con >300 ingressi Done = migrazione (2026-01-26) ignorati;
tipi Epic/Sub-task esclusi; Resolution valide: Done/Fixed/Resolved/Answered
(Unresolved/Open valgono solo se lo Status e' Done: Resolution mai impostata).
L'export `input_completed` contiene solo gli item entrati in Done nel periodo di estrazione: `period_start`/`period_end` in config
escludono le consegne fuori periodo ("fuori periodo" in items.csv) e limitano finestre (3/6 mesi) e trend ai mesi del periodo.

Export al minuto: transizioni nello stesso minuto non hanno ordine certo (colonne alfabetiche): `load.events` mette per ultimo lo Status attuale.
Tipi non previsti (es. Test Activity) finiscono in "Altro": `sources_check` li segnala, vanno in `exclude_issue_types` o in `classes`.

## Limiti noti / backlog evolutivo
- Dicembre 2025 non e' un dato reale: dipende dal filtro dell'export (vedi sopra). Dati prima di `period_start` ignorati.
- Senza `input_all` gli item aperti mancano: backlog "oggi" non misurabile. Il formato reale dell'export completo
  e' ancora ipotetico (`sample-data-all.csv`): verificarlo quando arriva il primo export vero.
- Expedite = label Jira: regola `labels` gia' in config, ma serve la colonna `Labels` nell'export (oggi assente: riga vuota nel report). Mancano anche Epic Link/Parent per distinguere le Story progettuali.
- Previsione per percentili storici; possibile Monte Carlo / "quando finisce un set di N item".
