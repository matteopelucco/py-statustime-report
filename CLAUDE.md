# flowmetrics — report di flusso da export Status Time Free (Jira PS)

Unica fonte: export CSV "PS_All" (tutti gli item, tutti gli stati). Nessun altro input.

    pip install -r requirements.txt
    python -m flowmetrics --config config.yaml            # scrive out/report.html + CSV
    python -m flowmetrics --input data/nuovo.csv --today 2026-10-07
    pytest

Funziona uguale su Mac e Windows (Python 3.10+).

## Cosa risponde
1. Se entra oggi: percentili P50/P85/P95 del lead time (creazione -> Done) per classe e finestra (3/6/12/24 mesi), probabilita' di consegna entro N giorni.
2. Backlog/WIP a fine mese ricostruiti dalle transizioni (+ item aperti dall'export).
3. Item in produzione per mese e classe.

Cycle time = dal primo ingresso in `cycle_start_status` (In Progress) alla produzione; item mai passati da In Progress non hanno cycle time.
Tabelle del report: righe da `report_classes` (+ Tutti = tutti gli item validi).

## Struttura
- `config.yaml` tutte le regole di business (classi, filtri, soglie). Primo posto dove intervenire.
- `flowmetrics/load.py` parsing export; `metrics.py` calcoli (funzioni pure); `report.py` + `templates/report.html.j2` HTML; `cli.py`.
- `flowmetrics/vendor/` Tabler (CSS, MIT) ed Apache ECharts (JS) incorporati nel report: nessuna dipendenza esterna, si apre offline.
- `tests/` regole verificate su dati sintetici. Ogni nuova regola = un test.
- `out/` output (items.csv = una riga per item con motivo di esclusione: utile per audit).

## Convenzioni
Done = produzione; ultimo ingresso in Done; giorni con >300 ingressi Done = migrazione (2026-01-26) ignorati;
tipi Epic/Sub-task esclusi; Resolution valide: Done/Fixed/Resolved/Answered
(Unresolved/Open valgono solo se lo Status e' Done: Resolution mai impostata).
L'export contiene solo gli item entrati in Done nel periodo di estrazione: `period_start`/`period_end` in config
escludono le consegne fuori periodo ("fuori periodo" in items.csv) e limitano finestre (3/6 mesi) e trend ai mesi del periodo.

## Limiti noti / backlog evolutivo
- Dicembre 2025 non e' un dato reale: dipende dal filtro dell'export (vedi sopra). Dati prima di `period_start` ignorati.
- L'export non contiene gli item aperti: backlog "oggi" non misurabile -> serve export con aperti.
- Expedite = label Jira: regola `labels` gia' in config, ma serve la colonna `Labels` nell'export (oggi assente: riga vuota nel report). Mancano anche Epic Link/Parent per distinguere le Story progettuali.
- Previsione per percentili storici; possibile Monte Carlo / "quando finisce un set di N item".
