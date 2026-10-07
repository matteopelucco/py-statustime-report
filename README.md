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
Si usano due export Status Time Free, stesso formato e stessa query, impostati in `config.yaml`:
- `input`: le issue **entrate in Done nel periodo** (lead time, cycle time, deployments);
- `input_all` (facoltativo): **tutte** le issue del periodo, aperte comprese. Serve per un backlog corretto e per
  confrontare i due export; senza, il backlog è ricostruito solo dalle consegne ed è sottostimato.

```bash
# Usa input e regole da config.yaml (default: --config config.yaml)
python -m flowmetrics

# Config esplicito
python -m flowmetrics --config config.yaml

# Altri export, sovrascrivendo quelli del config
python -m flowmetrics --input data/done.csv --input-all data/all.csv

# Solo l'export delle consegne (ignora input_all del config)
python -m flowmetrics --input data/done.csv --input-all ""

# Data di riferimento diversa da oggi (YYYY-MM-DD)
python -m flowmetrics --input data/done.csv --today 2026-10-07

# Prova rapida con i dati di esempio inclusi
python -m flowmetrics --input sample-data-done.csv --input-all sample-data-all.csv
```

File di esempio in root: `sample-data-done.csv` (issue consegnate) e `sample-data-all.csv`
(le stesse più issue ipotetiche aperte e scartate).

Output in `out/`:
- `report.html` — report completo, si apre offline nel browser. Sezioni: 1. Sintesi (ultimi 30/60 giorni vs storico),
  2. Issue (tempo per fase con anomalie), 3. Lead time / Cycle time, 4. Backlog, 5. Deployments
- `summary.csv` — metriche della sintesi (storico, ultimi 30/60 giorni, variazione)
- `items.csv` — una riga per item con eventuale motivo di esclusione e `fonte` (entrambi / solo done / solo all) per l'audit
- `forecast.csv`, `cycle_forecast.csv`, `throughput.csv`, `backlog_history.csv`

## Test
```bash
pytest
```
