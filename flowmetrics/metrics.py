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


def state_at(it, ev, cfg, t):
    """Fase (backlog/wip/chiuso) di ogni item all'istante t, ricostruita dalle transizioni (tipi esclusi esclusi)."""
    base = it[~it["Issue Type"].isin(cfg["exclude_issue_types"])][["Key", "class", "Created"]]
    e = ev[ev.Key.isin(base.Key) & (ev.ts <= t)].sort_values("ts")
    last = e.groupby("Key").tail(1).set_index("Key").status
    st = base.set_index("Key").join(last.rename("status"))
    st = st[st.Created <= t]
    st["status"] = st.status.fillna("To Do")
    st["fase"] = np.where(st.status.isin(cfg["closed_statuses"]), "chiuso",
                          np.where(st.status.isin(cfg["queue_statuses"]), "backlog", "wip"))
    return st


def snapshots(it, ev, cfg, today):
    """Ricostruisce backlog/WIP a fine mese dalle transizioni. Include TUTTI gli item (anche esclusi
    da lead time) tranne tipi esclusi; item chiusi con Resolution scartata escono dal backlog alla chiusura."""
    rows = []
    for m in _trend_months(cfg, today):
        st = state_at(it, ev, cfg, min(m.end_time, today))
        g = st[st.fase != "chiuso"].groupby(["class", "fase"]).size().unstack(fill_value=0)
        for c, r in g.iterrows():
            rows.append({"month": m, "class": c, "backlog": int(r.get("backlog", 0)), "wip": int(r.get("wip", 0))})
    return pd.DataFrame(rows, columns=["month", "class", "backlog", "wip"])


def backlog_now(it, ev, cfg, today):
    """Stato attuale dalla colonna Status dell'export (affidabile solo se l'export contiene gli item aperti)."""
    o = it[~it["Issue Type"].isin(cfg["exclude_issue_types"]) & ~it.Status.isin(cfg["closed_statuses"])]
    return o[["Key", "Issue Type", "class", "Status", "Created", "Summary"]].assign(
        age_days=(today - o.Created).dt.total_seconds() / 86400)


PHASES = ["wait_days", "work_days", "release_days", "flow_days"]


def data_end(ev, cfg, today):
    """Fine dei dati: oggi, ma non oltre period_end ne' oltre l'ultima transizione dell'export."""
    end = today
    p_end = period_bounds(cfg)[1]
    if p_end is not None:
        end = min(end, p_end)
    if len(ev):
        end = min(end, ev.ts.max())
    return end


def phases(it, ev, cfg, end):
    """Tempo di ogni item diviso in fasi (giorni di calendario): attesa = stati coda (queue_statuses),
    attesa rilascio = release_wait_statuses, lavoro = tutti gli altri stati non chiusi (dev + test).
    Il tempo negli stati chiusi non conta; per gli item aperti lo stato attuale conta fino a `end`."""
    e = ev[ev.Key.isin(it.Key)].sort_values(["Key", "ts"]).copy()
    e["next"] = e.groupby("Key").ts.shift(-1).fillna(end)
    e["dur"] = ((e.next - e.ts).dt.total_seconds() / 86400).clip(lower=0)
    e.loc[e.status.isin(cfg["closed_statuses"]), "dur"] = 0
    e["phase"] = np.select([e.status.isin(cfg["queue_statuses"]), e.status.isin(cfg.get("release_wait_statuses", []))],
                           ["wait_days", "release_days"], "work_days")
    p = e.pivot_table(index="Key", columns="phase", values="dur", aggfunc="sum").reindex(columns=PHASES[:3])
    out = it.drop(columns=[c for c in it.columns if c in PHASES], errors="ignore").merge(p, left_on="Key", right_index=True, how="left")
    out[PHASES[:3]] = out[PHASES[:3]].fillna(0)
    # prima della prima transizione l'item e' in coda; senza transizioni e' in coda fino a end se ancora aperto
    first = out.Key.map(e.groupby("Key").ts.min())
    open_ = ~out.Status.isin(cfg["closed_statuses"])
    gap = (first.fillna(end) - out.Created).dt.total_seconds() / 86400
    gap = gap.where(first.notna() | open_, 0).clip(lower=0).fillna(0)
    out["wait_days"] += gap
    out["flow_days"] = out[PHASES[:3]].sum(axis=1)
    return out


def flag_phases(it, cfg):
    """Segnala le fasi anomale confrontando ogni item con la distribuzione degli item validi della sua classe
    (tutto il periodo; se la classe ha meno di min_samples item, con tutti gli item validi).
    <fase>_flag: 2 = oltre il percentile alto, 1 = oltre quello basso, 0 = nella norma; <fase>_ref = mediana di riferimento."""
    warn, alert = cfg.get("anomaly_percentiles", [85, 95])
    min_days = cfg.get("anomaly_min_days", 1)
    v = it[it.valid]
    counts = v["class"].value_counts()
    out = it.copy()
    out["base_class"] = np.where(out["class"].map(counts).fillna(0) >= cfg["min_samples"], out["class"], "TUTTI")
    groups = {"TUTTI": v, **{c: g for c, g in v.groupby("class")}}
    for ph in PHASES:
        st = {c: (g[ph].median(), g[ph].quantile(warn / 100), g[ph].quantile(alert / 100)) if len(g) else (np.nan,) * 3
              for c, g in groups.items()}
        ref = out.base_class.map(lambda c: st.get(c, (np.nan,) * 3))
        med, p_w, p_a = (ref.map(lambda r, i=i: r[i]).astype(float) for i in range(3))
        x = out[ph]
        big = x >= min_days
        out[ph + "_ref"] = med
        out[ph + "_flag"] = np.where(big & (x > p_a), 2, np.where(big & (x > p_w), 1, 0))
    return out


# metrica -> (etichetta, unita', cosa e' meglio)
COMPARE = {
    "throughput": ("Consegne a settimana", "item", "high"),
    "lead_p50": ("Lead time P50", "gg", "low"),
    "lead_p85": ("Lead time P85", "gg", "low"),
    "cycle_p50": ("Cycle time P50", "gg", "low"),
    "wait_med": ("Attesa in coda (mediana)", "gg", "low"),
    "work_med": ("Lavoro dev + test (mediana)", "gg", "low"),
    "release_med": ("Attesa rilascio (mediana)", "gg", "low"),
}
KEY_METRICS = ["throughput", "lead_p50", "lead_p85", "cycle_p50"]


def _window_stats(v, days):
    if not len(v):
        return {k: np.nan for k in COMPARE} | {"n": 0, "wait_share": np.nan, "work_share": np.nan, "release_share": np.nan}
    flow = v.flow_days.sum()
    return {"n": len(v), "throughput": len(v) / days * 7 if days > 0 else np.nan,
            "lead_p50": v.lead_days.median(), "lead_p85": v.lead_days.quantile(.85), "cycle_p50": v.cycle_days.median(),
            "wait_med": v.wait_days.median(), "work_med": v.work_days.median(), "release_med": v.release_days.median(),
            **{f"{p}_share": v[f"{p}_days"].sum() / flow if flow else np.nan for p in ("wait", "work", "release")}}


def compare_periods(it, cfg, end):
    """Ultimi N giorni (recent_days) contro lo storico (consegne dall'inizio periodo fino a max(recent_days) giorni fa).
    Una riga per metrica; colonne hist, r<N>, delta_r<N> (variazione relativa), trend_r<N> (meglio/peggio/stabile)."""
    v = it[it.valid & it.prod_date.notna()]
    rec = sorted(cfg.get("recent_days", [30, 60]))
    tol = cfg.get("trend_tolerance", 0.10)
    cut = end - pd.Timedelta(days=rec[-1])
    start = period_bounds(cfg)[0]
    if start is None:
        start = v.prod_date.min() if len(v) else cut
    wins = {"hist": (start, cut), **{f"r{d}": (end - pd.Timedelta(days=d), end) for d in rec}}
    stats = {w: _window_stats(v[(v.prod_date >= a) & (v.prod_date < b if w == "hist" else v.prod_date <= b)],
                              (b - a).total_seconds() / 86400) for w, (a, b) in wins.items()}
    rows = []
    for k in list(COMPARE) + ["wait_share", "work_share", "release_share"]:
        label, unit, better = COMPARE.get(k, (k, "%", None))
        r = {"metric": k, "label": label, "unit": unit, "better": better, "hist": stats["hist"][k]}
        for d in rec:
            x, h = stats[f"r{d}"][k], stats["hist"][k]
            delta = (x - h) / h if h and h == h and x == x else np.nan
            trend = "n/d"
            if better and delta == delta:
                good = -delta if better == "low" else delta
                trend = "meglio" if good > tol else "peggio" if good < -tol else "stabile"
            r |= {f"r{d}": x, f"delta_r{d}": delta, f"trend_r{d}": trend}
        rows.append(r)
    out = pd.DataFrame(rows)
    out.attrs["n"] = {w: s["n"] for w, s in stats.items()}
    out.attrs["windows"] = wins
    return out


def verdict(cmp, cfg):
    """Giudizio complessivo sull'ultima finestra piu' lunga: 'migliora', 'peggiora' o 'stabile' (+ punteggio)."""
    d = max(cfg.get("recent_days", [30, 60]))
    t = cmp.set_index("metric").loc[KEY_METRICS, f"trend_r{d}"]
    score = int((t == "meglio").sum() - (t == "peggio").sum())
    return ("migliora" if score > 0 else "peggiora" if score < 0 else "stabile"), score


def sources_check(it, cfg, has_all):
    """Confronto fra export 'done' e export 'all': quante issue per fonte e incongruenze fra i due perimetri.
    Senza export 'all' il backlog e' ricostruito solo dagli item consegnati (sottostimato)."""
    n = it.fonte.value_counts()
    excl = it["Issue Type"].isin(cfg["exclude_issue_types"])
    open_ = ~excl & ~it.Status.isin(cfg["closed_statuses"])
    out = {"has_all": has_all, "both": int(n.get("entrambi", 0)), "only_done": int(n.get("solo done", 0)),
           "only_all": int(n.get("solo all", 0)), "open": int(open_.sum()), "warnings": []}
    p_start = period_bounds(cfg)[0]
    if has_all:
        ex = lambda s: ", ".join(s.Key.head(3)) + ("..." if len(s) > 3 else "")
        miss = it[(it.fonte == "solo all") & it.valid]
        if len(miss):
            out["warnings"].append(f"{len(miss)} issue consegnate nel periodo sono solo nell'export completo ({ex(miss)}): "
                                   "l'export delle consegne potrebbe essere incompleto o estratto in un'altra data.")
        extra = it[it.fonte == "solo done"]
        if len(extra):
            old = int((extra.Created < p_start).sum()) if p_start is not None else 0
            hint = (f" {old} sono nate prima di {p_start:%d/%m/%Y}: la query dell'export completo deve includere anche le issue "
                    "gia' avviate prima del periodo e chiuse al suo interno (non solo quelle passate da In Progress nel periodo), "
                    "altrimenti il backlog storico e' sottostimato.") if old else ""
            out["warnings"].append(f"{len(extra)} issue dell'export delle consegne mancano nell'export completo ({ex(extra)}): "
                                   f"i due export non coprono lo stesso perimetro.{hint}")
    # tipi senza regola di classe ne' esclusione: finiscono in "Altro" e contano come lavoro vero (es. record di test)
    ruled = {t for r in cfg["classes"] for t in r.get("issue_types", [])} | set(cfg["exclude_issue_types"])
    unk = it[~it["Issue Type"].isin(ruled)]["Issue Type"].value_counts()
    if len(unk):
        out["warnings"].append("tipi di issue senza regola in `classes` ne' in `exclude_issue_types` (finiscono in 'Altro'): "
                               + ", ".join(f"{t} ({n})" for t, n in unk.items()) + ".")
    return out


# --- KPI dei tab: ultimi kpi_months mesi contro i kpi_prev_months precedenti ---

def kpi_bounds(cfg, end):
    """(inizio precedente, taglio, fine): ultimi kpi_months mesi = (taglio, fine], precedenti = (inizio, taglio]
    di kpi_prev_months mesi (default = kpi_months). I due periodi possono durare diverso: i conteggi si confrontano
    come ritmi (_rate_delta). L'inizio non va prima di period_start (fuori periodo l'export non ha dati)."""
    m = cfg.get("kpi_months", 3)
    cut = end - pd.DateOffset(months=m)
    start = cut - pd.DateOffset(months=cfg.get("kpi_prev_months", m))
    p_start = period_bounds(cfg)[0]
    if p_start is not None:
        start = max(start, p_start - pd.Timedelta(minutes=1))
    return start, cut, end


def _weeks(start, end):
    """Fine di ogni settimana da start a end (end compreso)."""
    w = [t for t in pd.date_range(start, end, freq="7D")[1:] if t < end]
    return w + [end]


def _trend(delta, better, tol):
    if delta != delta:
        return "n/d"
    good = delta if better == "high" else -delta
    return "meglio" if good > tol else "peggio" if good < -tol else "stabile"


def _rel(a, b):
    return (a - b) / b if b else np.nan


def _rate_delta(n_last, n_prev, bounds):
    """Variazione relativa del ritmo (item al giorno) fra ultimo periodo e precedente: durano diverso, i totali non sono confrontabili."""
    start, cut, end = bounds
    return _rel(n_last / max((end - cut).days, 1), n_prev / max((cut - start).days, 1))


def within_kpi(it, cfg, end, col="lead_days", n_days=14):
    """Per classe (kpi_classes + TUTTI): quota di item consegnati entro n_days giorni (col = lead_days
    o cycle_days) negli ultimi kpi_months mesi contro i precedenti (delta in punti percentuali) e andamento
    cumulato settimanale: a ogni settimana, la quota su tutte le consegne dall'inizio del confronto."""
    start, cut, end = kpi_bounds(cfg, end)
    tol = cfg.get("kpi_tolerance_pp", 5)
    v = it[it.valid & it.prod_date.notna() & it[col].notna()]
    v = v[(v.prod_date > start) & (v.prod_date <= end)]
    weeks = _weeks(start, end)
    share = lambda x: float((x[col] <= n_days).mean()) if len(x) else np.nan
    rows = []
    for c in list(cfg.get("kpi_classes", [])) + ["TUTTI"]:
        s = v if c == "TUTTI" else v[v["class"] == c]
        last, prev = s[s.prod_date > cut], s[s.prod_date <= cut]
        pl, pp = share(last), share(prev)
        d = (pl - pp) * 100
        rows.append({"class": c, "n_last": len(last), "n_prev": len(prev), "pct_last": pl, "pct_prev": pp,
                     "delta_pp": d, "trend": _trend(d, "high", tol), "weeks": weeks,
                     "cum": [share(s[s.prod_date <= w]) for w in weeks]})
    return rows


def backlog_kpi(it, ev, cfg, end):
    """Item in attesa (backlog) e in lavorazione (WIP) a fine dati contro kpi_months mesi prima, consegne degli
    ultimi kpi_months mesi contro i precedenti (confronto fra ritmi); serie settimanali (stato per backlog/WIP, cumulato per le consegne)."""
    start, cut, end = kpi_bounds(cfg, end)
    tol = cfg.get("trend_tolerance", 0.10)
    weeks = _weeks(start, end)
    cnt = {w: state_at(it, ev, cfg, w).fase.value_counts() for w in [cut] + weeks}
    out = {}
    for f in ("backlog", "wip"):
        now, before = int(cnt[end].get(f, 0)), int(cnt[cut].get(f, 0))
        out[f] = {"now": now, "before": before, "delta": _rel(now, before), "trend": _trend(_rel(now, before), "low", tol),
                  "weeks": weeks, "series": [int(cnt[w].get(f, 0)) for w in weeks]}
    d = it[it.valid & it.prod_date.notna()].prod_date
    last, prev = int(((d > cut) & (d <= end)).sum()), int(((d > start) & (d <= cut)).sum())
    delta = _rate_delta(last, prev, (start, cut, end))
    out["delivered"] = {"now": last, "before": prev, "delta": delta, "trend": _trend(delta, "high", tol),
                        "per_week": last / max((end - cut).days, 1) * 7, "per_week_before": prev / max((cut - start).days, 1) * 7,
                        "weeks": weeks, "series": [int(((d > start) & (d <= w)).sum()) for w in weeks]}
    return out


def deploy_kpi(it, cfg, end):
    """Consegne in produzione negli ultimi kpi_months mesi contro i precedenti e andamento progressivo giorno per
    giorno dei due periodi sovrapposti (giorno 1 = primo giorno del periodo)."""
    start, cut, end = kpi_bounds(cfg, end)
    tol = cfg.get("trend_tolerance", 0.10)
    d = it[it.valid & it.prod_date.notna()].prod_date

    def progressive(a, b):
        days = int(np.ceil((b - a).total_seconds() / 86400))
        return [int(((d > a) & (d <= min(a + pd.Timedelta(days=i), b))).sum()) for i in range(1, days + 1)]
    last, prev = progressive(cut, end), progressive(start, cut)
    n_last, n_prev = (last[-1] if last else 0), (prev[-1] if prev else 0)
    delta = _rate_delta(n_last, n_prev, (start, cut, end))
    return {"now": n_last, "before": n_prev, "delta": delta, "trend": _trend(delta, "high", tol),
            "per_week": n_last / max((end - cut).days, 1) * 7, "per_week_before": n_prev / max((cut - start).days, 1) * 7, "last": last, "prev": prev,
            "bounds": (start, cut, end)}


def recent_mask(it, ev, cfg, end):
    """Issue 'degli ultimi kpi_months mesi': aperte a fine dati oppure chiuse (consegnate o scartate) dopo il taglio.
    La chiusura e' l'ultima transizione dell'item."""
    _, cut, _ = kpi_bounds(cfg, end)
    last = it.Key.map(ev.groupby("Key").ts.max())
    open_ = ~it.Status.isin(cfg["closed_statuses"])
    return open_ | ((last > cut) & (last <= end))


def anomaly_kpi(it, cfg, mask=None):
    """Issue del tab Issue (tipi esclusi a parte; solo `mask` se dato, es. recent_mask) e quante hanno anomalie
    per fase (giallo o rosso)."""
    v = it[~it["Issue Type"].isin(cfg["exclude_issue_types"]) & (True if mask is None else mask)]
    n = len(v)
    out = {"n": n, "open": int((~v.Status.isin(cfg["closed_statuses"])).sum()),
           "any": int((v[[f"{p}_flag" for p in PHASES]] > 0).any(axis=1).sum())}
    for p in ("wait", "work", "release"):
        f = v[f"{p}_days_flag"]
        out[p] = {"n": int((f > 0).sum()), "red": int((f == 2).sum()), "pct": (f > 0).mean() if n else np.nan}
    return out
