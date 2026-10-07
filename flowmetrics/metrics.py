"""Calcolo metriche. Funzioni pure: input DataFrame + config, output DataFrame."""
import re
import numpy as np
import pandas as pd
from .load import events, status_days


def classify(df, cfg):
    def one(row):
        for rule in cfg["classes"]:
            ok = True
            if "issue_types" in rule and row["Issue Type"] not in rule["issue_types"]:
                ok = False
            if ok and "summary_regex" in rule and not re.search(rule["summary_regex"], row["Summary"]):
                ok = False
            if ok and "filled_fields" in rule and not all(str(row.get(f, "")).strip() for f in rule["filled_fields"]):
                ok = False
            if ok and "empty_fields" in rule and any(str(row.get(f, "")).strip() for f in rule["empty_fields"]):
                ok = False
            if ok and "labels" in rule:
                have = {x.lower() for x in re.split(r"[,;\s]+", str(row.get("Labels", ""))) if x}
                if not have & {x.lower() for x in rule["labels"]}:
                    ok = False
            if ok:
                return rule["name"]
        return "Altro"
    return df.apply(one, axis=1)


def period_bounds(cfg):
    """(inizio, fine) del periodo di estrazione dalla config; None dove non configurato."""
    start, end = cfg.get("period_start"), cfg.get("period_end")
    start = pd.Timestamp(start) if start else None
    end = pd.Timestamp(end) + pd.Timedelta(days=1) - pd.Timedelta(minutes=1) if end else None
    return start, end


def prepare(df, ev, cfg):
    """Un record per item con data di produzione, lead time e flag di inclusione."""
    done = ev[ev.status == cfg["done_status"]].copy()
    per_day = done.groupby(done.ts.dt.date).size()
    bulk_days = set(per_day[per_day > cfg["bulk_done_threshold"]].index)
    done["bulk"] = done.ts.dt.date.isin(bulk_days)
    real = done[~done.bulk]
    agg = "last" if cfg["done_pick"] == "last" else "first"
    prod = real.groupby("Key").ts.agg(agg).rename("prod_date")
    only_bulk = set(done.Key) - set(real.Key)

    it = df.copy()
    it["class"] = classify(it, cfg)
    it = it.merge(prod, left_on="Key", right_index=True, how="left")
    it["lead_days"] = (it.prod_date - it.Created).dt.total_seconds() / 86400
    # pickup = primo ingresso in uno stato non-coda
    work = ev[~ev.status.isin(cfg["queue_statuses"] + cfg["closed_statuses"])]
    pick = work.groupby("Key").ts.min().rename("pickup_ts")
    it = it.merge(pick, left_on="Key", right_index=True, how="left")
    it["pickup_days"] = (it.pickup_ts - it.Created).dt.total_seconds() / 86400
    it.loc[it.pickup_days < 0, "pickup_days"] = 0
    # cycle time = dal primo ingresso in cycle_start_status (es. In Progress) alla produzione
    start = ev[ev.status == cfg.get("cycle_start_status", "In Progress")].groupby("Key").ts.min().rename("cycle_start_ts")
    it = it.merge(start, left_on="Key", right_index=True, how="left")
    it["cycle_days"] = (it.prod_date - it.cycle_start_ts).dt.total_seconds() / 86400
    it.loc[it.cycle_days < 0, "cycle_days"] = np.nan

    it["excl_reason"] = ""
    it.loc[it["Issue Type"].isin(cfg["exclude_issue_types"]), "excl_reason"] = "tipo escluso"
    ok_res = it.Resolution.isin(cfg["include_resolutions"]) | (
        it.Resolution.isin(cfg.get("default_resolutions", [])) & (it.Status == cfg["done_status"]))
    m = (it.excl_reason == "") & ~ok_res
    it.loc[m, "excl_reason"] = "resolution scartata"
    m = (it.excl_reason == "") & it.prod_date.isna()
    it.loc[m, "excl_reason"] = np.where(it.loc[m, "Key"].isin(only_bulk), "chiuso solo da bonifica", "mai in Done")
    m = (it.excl_reason == "") & (it.lead_days < 0)
    it.loc[m, "excl_reason"] = "date incoerenti"
    p_start, p_end = period_bounds(cfg)
    m = it.excl_reason == ""
    if p_start is not None:
        it.loc[m & (it.prod_date < p_start), "excl_reason"] = "fuori periodo"
    if p_end is not None:
        it.loc[m & (it.prod_date > p_end), "excl_reason"] = "fuori periodo"
    it["valid"] = it.excl_reason == ""
    it.attrs["bulk_days"] = sorted(str(d) for d in bulk_days)
    needed = [f for r in cfg["classes"] for f in (["Labels"] if "labels" in r else []) + r.get("filled_fields", []) + r.get("empty_fields", [])]
    it.attrs["missing_fields"] = sorted({f for f in needed if f not in df.columns})
    return it


def _pct(s, ps):
    return {f"P{p}": float(np.percentile(s, p)) for p in ps} if len(s) else {f"P{p}": np.nan for p in ps}


def forecast(it, cfg, today, col="lead_days"):
    """'Se entrasse oggi': percentili storici di `col` (lead_days o cycle_days) per classe e finestra
    (per data di produzione). Righe: cfg["report_classes"] se presente (anche senza dati), altrimenti le classi trovate."""
    rows = []
    v = it[it.valid]
    classes = list(cfg.get("report_classes") or sorted(v["class"].unique())) + ["TUTTI"]
    for w in cfg["windows_months"]:
        start = today - pd.DateOffset(months=w)
        p_start = period_bounds(cfg)[0]
        if p_start is not None:
            start = max(start, p_start)
        vw = v[v.prod_date >= start]
        for c in classes:
            s = vw if c == "TUTTI" else vw[vw["class"] == c]
            d = s[col].dropna()
            r = {"window": w, "class": c, "n": len(d), "reliable": len(d) >= cfg["min_samples"],
             "from": start.strftime("%d/%m/%Y")}
            r.update(_pct(d, cfg["percentiles"]))
            for n in cfg["within_days"]:
                r[f"<= {n}g"] = float((d <= n).mean()) if len(d) else np.nan
            if col == "lead_days":
                r["pickup_P50"] = float(s.pickup_days.dropna().median()) if s.pickup_days.notna().any() else np.nan
            rows.append(r)
    return pd.DataFrame(rows)


def _trend_months(cfg, today):
    """Tutti i mesi della finestra di trend, anche quelli senza consegne (altrimenti spariscono dal grafico)."""
    start = (today - pd.DateOffset(months=cfg["trend_months"])).to_period("M")
    p_start, p_end = period_bounds(cfg)
    if p_start is not None:
        start = max(start, p_start.to_period("M"))
    end = min(today, p_end) if p_end is not None else today
    return pd.period_range(start=start, end=end.to_period("M"), freq="M")


def monthly_throughput(it, cfg, today):
    months = _trend_months(cfg, today)
    v = it[it.valid].copy()
    v["month"] = v.prod_date.dt.to_period("M")
    v = v[v.month.isin(months)]
    t = v.pivot_table(index="month", columns="class", values="Key", aggfunc="count", fill_value=0)
    t = t.reindex(months, fill_value=0)
    t.index.name = "month"
    t["TOTALE"] = t.sum(axis=1)
    return t


def monthly_leadtime(it, cfg, today, col="lead_days"):
    months = _trend_months(cfg, today)
    v = it[it.valid].copy()
    v["month"] = v.prod_date.dt.to_period("M")
    v = v[v.month.isin(months)]
    g = v.groupby("month")[col]
    l = pd.DataFrame({"n": g.size(), "P50": g.median(), "P85": g.quantile(.85)}).reindex(months)
    l["n"] = l["n"].fillna(0).astype(int)
    return l


def snapshots(it, ev, cfg, today):
    """Ricostruisce backlog/WIP a fine mese dalle transizioni. Include TUTTI gli item (anche esclusi
    da lead time) tranne tipi esclusi; item chiusi con Resolution scartata escono dal backlog alla chiusura."""
    base = it[~it["Issue Type"].isin(cfg["exclude_issue_types"])][["Key", "class", "Created"]]
    e = ev[ev.Key.isin(base.Key)].sort_values("ts")
    months = _trend_months(cfg, today)
    q, closed = set(cfg["queue_statuses"]), set(cfg["closed_statuses"])
    rows = []
    for m in months:
        t = min(m.end_time, today)
        last = e[e.ts <= t].groupby("Key").tail(1).set_index("Key").status
        st = base.set_index("Key").join(last.rename("status"))
        st = st[st.Created <= t]
        st["status"] = st.status.fillna("To Do")
        st["fase"] = np.where(st.status.isin(closed), "chiuso", np.where(st.status.isin(q), "backlog", "wip"))
        g = st[st.fase != "chiuso"].groupby(["class", "fase"]).size().unstack(fill_value=0)
        for c, r in g.iterrows():
            rows.append({"month": m, "class": c, "backlog": int(r.get("backlog", 0)), "wip": int(r.get("wip", 0))})
    return pd.DataFrame(rows)


def backlog_now(it, ev, cfg, today):
    """Stato attuale dalla colonna Status dell'export (affidabile solo se l'export contiene gli item aperti)."""
    o = it[~it["Issue Type"].isin(cfg["exclude_issue_types"]) & ~it.Status.isin(cfg["closed_statuses"])]
    return o[["Key", "Issue Type", "class", "Status", "Created", "Summary"]].assign(
        age_days=(today - o.Created).dt.total_seconds() / 86400)
