import argparse, os
import pandas as pd, yaml
from . import load, metrics, report


def main(argv=None):
    ap = argparse.ArgumentParser(prog="flowmetrics", description="Report di flusso da export Status Time Free")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--input"); ap.add_argument("--today", help="YYYY-MM-DD (default: oggi)")
    a = ap.parse_args(argv)
    cfg = yaml.safe_load(open(a.config, encoding="utf-8"))
    path = a.input or cfg["input"]
    today = pd.Timestamp(a.today) if a.today else pd.Timestamp.now().normalize() + pd.Timedelta(days=1) - pd.Timedelta(minutes=1)
    df = load.read_export(path); ev = load.events(df)
    it = metrics.prepare(df, ev, cfg)
    fc = metrics.forecast(it, cfg, today); thr = metrics.monthly_throughput(it, cfg, today)
    lead = metrics.monthly_leadtime(it, cfg, today); snap = metrics.snapshots(it, ev, cfg, today)
    bnow = metrics.backlog_now(it, ev, cfg, today)
    out = cfg["output_dir"]; os.makedirs(out, exist_ok=True)
    it.drop(columns=[c for c in it.columns if c.startswith(("'->", "#"))]).to_csv(f"{out}/items.csv", index=False)
    fc.to_csv(f"{out}/forecast.csv", index=False); thr.to_csv(f"{out}/throughput.csv"); snap.to_csv(f"{out}/backlog_history.csv", index=False)
    report.render(cfg, today, it, fc, thr, lead, snap, bnow, f"{out}/report.html")
    print(f"OK: {out}/report.html  (item validi {int(it.valid.sum())}/{len(it)})")


if __name__ == "__main__":
    main()
