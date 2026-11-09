import argparse, os, re
import pandas as pd, yaml
from . import analysis, load, metrics, report


def report_filename(cfg, first, last, kind="flow_report", ext=".html"):
    """<project_name>_<kind>_<inizio>_<fine><ext>: periodo da config; se non impostato, prima/ultima data dei dati.
    Senza project_name il prefisso sparisce; i caratteri non validi nei nomi file diventano "-"."""
    start, end = metrics.period_bounds(cfg)
    name = re.sub(r"[^\w.-]+", "-", str(cfg.get("project_name") or "").strip()).strip("-")
    parts = [name, kind, f"{start or first:%Y%m%d}", f"{end or last:%Y%m%d}"]
    return "_".join(p for p in parts if p) + ext


def main(argv=None):
    ap = argparse.ArgumentParser(prog="flowmetrics", description="Report di flusso da export Status Time Free")
    ap.add_argument("--config", default="config/config.yaml",
                    help="file di configurazione (default: config/config.yaml; modello: config.example.yaml)")
    ap.add_argument("--input-completed", "--input", dest="input_completed",
                    help="export delle issue entrate in Done nel periodo (default: input_completed in config)")
    ap.add_argument("--input-all", help="export di TUTTE le issue del periodo, aperte comprese (default: input_all in config; "
                                        "'' per non usarlo)")
    ap.add_argument("--today", help="YYYY-MM-DD (default: oggi)")
    a = ap.parse_args(argv)
    if not os.path.exists(a.config):
        ap.error(f"config non trovata: {a.config} (copiare config.example.yaml in config/config.yaml)")
    with open(a.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    path = a.input_completed or cfg.get("input_completed") or cfg["input"]   # "input": nome storico
    path_all = a.input_all if a.input_all is not None else cfg.get("input_all")
    # "oggi" = fine giornata (anche con --today): le transizioni di quel giorno fanno parte dei dati
    today = (pd.Timestamp(a.today) if a.today else pd.Timestamp.now()).normalize() + pd.Timedelta(days=1) - pd.Timedelta(minutes=1)
    df = load.read_exports(path, path_all); ev = load.events(df)
    it = metrics.prepare(df, ev, cfg)
    src = metrics.sources_check(it, cfg, bool(path_all))
    # i trend mensili finiscono con i dati (non con --today): niente mesi vuoti dopo l'export
    end = metrics.data_end(ev, cfg, today)
    fc = metrics.forecast(it, cfg, end); thr = metrics.monthly_throughput(it, cfg, end)
    lead = metrics.monthly_leadtime(it, cfg, end); snap = metrics.snapshots(it, ev, cfg, end)
    bnow = metrics.backlog_now(it, ev, cfg, today)
    it = metrics.flag_phases(metrics.phases(it, ev, cfg, end), cfg)
    cmp = metrics.compare_periods(it, cfg, end)
    cyc = metrics.forecast(it, cfg, end, col="cycle_days"); cyl = metrics.monthly_leadtime(it, cfg, end, col="cycle_days")
    out = cfg["output_dir"]; os.makedirs(out, exist_ok=True)
    it.drop(columns=[c for c in it.columns if c.startswith(("'->", "#"))]).to_csv(f"{out}/items.csv", index=False)
    fc.to_csv(f"{out}/forecast.csv", index=False); cyc.to_csv(f"{out}/cycle_forecast.csv", index=False); thr.to_csv(f"{out}/throughput.csv"); snap.to_csv(f"{out}/backlog_history.csv", index=False)
    cmp.to_csv(f"{out}/summary.csv", index=False)
    first = ev.ts.min() if len(ev) else end
    html_name = report_filename(cfg, first, end)
    report.render(cfg, today, end, it, ev, fc, thr, lead, snap, bnow, cyc, cyl, cmp, src, f"{out}/{html_name}")
    print(f"OK: {out}/{html_name}  (item validi {int(it.valid.sum())}/{len(it)})")
    # analisi testuale a parte (Markdown), mai dentro il report
    md_path = f"{out}/{report_filename(cfg, first, end, kind='flow_analysis', ext='.md')}"
    analysis.render(cfg, today, end, df, it, ev, fc, cyc, snap, cmp, src, html_name, md_path)
    print(f"OK: {md_path}")
    for w in src["warnings"]:
        print(f"ATTENZIONE: {w}")


if __name__ == "__main__":
    main()
