# py-statustime-report
Jira Status Time Free reporting tool

## Requisiti
Python 3.10+ (Mac e Windows).

## Installazione
Consigliato un ambiente virtuale:

```bash
# Mac / Linux
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

```powershell
# Windows (PowerShell)
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Utilizzo
Le configurazioni stanno in `config/` (ignorata da git): prima di iniziare creare la cartella e copiare
`config.example.yaml` in `config/config.yaml`.

Si usano due export Status Time Free, stesso formato e stessa query, impostati nel file di configurazione:
- `input_completed`: le issue **entrate in Done nel periodo** (lead time, cycle time, deployments);
- `input_all` (facoltativo): **tutte** le issue del periodo, aperte comprese. Serve per un backlog corretto e per
  confrontare i due export; senza, il backlog è ricostruito solo dalle consegne ed è sottostimato.

`input_all` deve contenere **almeno** tutte le issue di `input_completed` (stesso intervallo di date, uguale a `period_end` in config).
Query consigliata (periodo 01/01–30/06, estremo finale escluso):

```
project = PROJ AND created < "2026-07-01" AND (resolution = EMPTY OR resolved >= "2026-01-01" OR status CHANGED TO "Done" DURING ("2026-01-01", "2026-07-01"))
```

```bash
# Usa input_completed, input_all e regole da config/config.yaml (default)
python -m flowmetrics

# Altra configurazione (es. una per progetto)
python -m flowmetrics --config config/config.local.yaml

# Altri export, sovrascrivendo quelli del config
python -m flowmetrics --input-completed data/done.csv --input-all data/all.csv

# Solo l'export delle consegne (ignora input_all del config)
python -m flowmetrics --input-completed data/done.csv --input-all ""

# Data di riferimento diversa da oggi (YYYY-MM-DD)
python -m flowmetrics --input-completed data/done.csv --today 2026-07-07

# Prova rapida con i dati di esempio inclusi
python -m flowmetrics --config config.example.yaml --input-completed sample-data-done.csv --input-all sample-data-all.csv
```

File di esempio in root: `sample-data-done.csv` (issue consegnate) e `sample-data-all.csv`
(le stesse più issue ipotetiche aperte e scartate).

Output in `out/`:
- `<project_name>_flow_analysis_<period_start>_<period_end>.md` — analisi testuale separata dal report (sintesi, le 5 domande,
  dati significativi, attività proposte, limiti), generata a ogni esecuzione con regole e soglie del blocco `analysis` in config
- `<project_name>_flow_report_<period_start>_<period_end>.html` (es. `PS_flow_report_20260401_20260930.html`) — report completo, si apre offline nel browser. Sezioni: 1. Sintesi (ultimi 30/60 giorni vs storico),
  2. Issue (tempo per fase con anomalie), 3. Lead time / Cycle time, 4. Backlog, 5. Deployments
- `summary.csv` — metriche della sintesi (storico, ultimi 30/60 giorni, variazione)
- `items.csv` — una riga per item con eventuale motivo di esclusione e `fonte` (entrambi / solo done / solo all) per l'audit
- `forecast.csv`, `cycle_forecast.csv`, `throughput.csv`, `backlog_history.csv`

## Titoli riservati (uso con un assistente AI)
I titoli di issue ed epiche non devono arrivare a un LLM. Gli export reali stanno in `private/raw/` (cartella ignorata da
git e vietata in lettura all'AI); il resto del progetto lavora su copie anonime, in cui il titolo è sostituito dal
segnaposto `[[T:CHIAVE]]`. La chiave della issue resta in chiaro.

```bash
python -m flowmetrics.privacy anonymize      # private/raw/**.csv -> data/**.csv + dizionario private/titles.csv
python -m flowmetrics.privacy check          # nessun titolo nei file leggibili dall'AI? (esce con 0 se pulito)
python -m flowmetrics --config config/x.yaml             # report e analisi di lavoro, con segnaposto, in out/
python -m flowmetrics --config config/x.yaml --reveal    # report e analisi finali, con i titoli, in private/out/
python -m flowmetrics.privacy reveal analisi.md          # testi scritti a mano o dall'AI -> private/out/
```

- `require_anonymized: true` nella config fa rifiutare gli export con titoli in chiaro.
- I valori riservati della config (es. `issue_url` con l'indirizzo interno di Jira) vanno in `private/config/<stesso nome della config>`:
  sono usati solo con `--reveal`.
- Gli script stampano solo conteggi, nomi di file e chiavi: mai un titolo.

## Test
```bash
pytest
```
