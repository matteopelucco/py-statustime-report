"""Report HTML autonomo: Tabler (CSS) + Apache ECharts, entrambi incorporati nel file (nessuna dipendenza esterna)."""
import json
from pathlib import Path
from urllib.parse import quote
import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup
from . import metrics

HERE = Path(__file__).parent
COL = {"Bug": "#d1495b", "Story (non progettuale)": "#2e86ab", "Story progettuale": "#7fb6d3", "Story": "#2e86ab", "Task": "#66a182", "Engine": "#edae49", "Altro": "#8d6a9f", "Expedite": "#f76707", "TUTTI": "#6b7280"}
OTHER = "#9ca3af"
ENV = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape(["html", "j2"]))


MESI = ["Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno", "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"]


def month_name(p):
    """Periodo mensile -> 'Aprile 2026' (nomi italiani fissi, indipendenti dal locale di sistema)."""
    p = pd.Period(p, freq="M")
    return f"{MESI[p.month - 1]} {p.year}"


def _f(x, nd=1):
    return f"{x:.{nd}f}" if x == x else "-"


def _nan(x):
    return None if x != x else round(float(x), 1)


def _forecast(fc, today, pcols, wcols, with_pickup=True):
    out = []
    for w, g in fc.groupby("window"):
        rows = []
        for r in g.to_dict("records"):
            probs = [{"label": f"{r[c] * 100:.0f}%" if r[c] == r[c] else "-", "pct": round(r[c] * 100) if r[c] == r[c] else 0,
                      "cls": "bg-blue"} for c in wcols]
            rows.append({"name": "Tutti" if r["class"] == "TUTTI" else r["class"], "total": r["class"] == "TUTTI",
                         "color": COL.get(r["class"], OTHER), "n": r["n"], "reliable": r["reliable"],
                         "pcts": [_f(r[p]) for p in pcols],
                         "d85": (today + pd.Timedelta(days=r["P85"])).strftime("%d/%m/%Y") if r["P85"] == r["P85"] else "-",
                         "probs": probs, "pickup": _f(r["pickup_P50"]) if with_pickup else None})
        out.append({"window": w, "from": g["from"].iloc[0], "rows": rows})
    return out


PHASE_COL = {"wait": "#9ca3af", "work": "#2e86ab", "release": "#edae49"}
PHASE_LBL = {"wait": "Attesa in coda", "work": "Lavoro (dev + test)", "release": "Attesa rilascio (UAT + PROD)"}
VERDICT = {"migliora": ("Il flusso sta migliorando", "success"), "peggiora": ("Il flusso sta peggiorando", "danger"),
           "stabile": ("Il flusso è stabile", "secondary")}
TREND_CLS = {"meglio": "bg-green-lt", "peggio": "bg-red-lt", "stabile": "bg-secondary-lt", "n/d": "bg-secondary-lt"}


def _delta(x):
    return f"{x * 100:+.0f}%" if x == x else "-"


def _val(x, unit):
    if x != x:
        return "-"
    return f"{x * 100:.0f}%" if unit == "%" else _f(x)


def _summary(cfg, it, ev, cmp, end):
    """Sintesi: verdetto, KPI con variazione, tabella di confronto, messaggi per business e sviluppatori."""
    rec = sorted(cfg.get("recent_days", [30, 60]))
    d, r_ = rec[-1], f"r{rec[-1]}"
    c = cmp.set_index("metric")
    n = cmp.attrs["n"]
    verdict, _ = metrics.verdict(cmp, cfg)
    title, cls = VERDICT[verdict]
    lbl = lambda trend: [c.loc[k, "label"] for k in metrics.KEY_METRICS if c.loc[k, f"trend_{r_}"] == trend]
    why = "; ".join(filter(None, [("migliorano: " + ", ".join(lbl("meglio"))) if lbl("meglio") else "",
                                  ("peggiorano: " + ", ".join(lbl("peggio"))) if lbl("peggio") else ""]))
    sentence = (f"Ultimi {d} giorni a confronto con lo storico &mdash; "
                + (why or f"nessuna variazione oltre &plusmn;{cfg.get('trend_tolerance', .1) * 100:.0f}%") + ".")

    now, before = (metrics.state_at(it, ev, cfg, t) for t in (end, end - pd.Timedelta(days=d)))
    cnt = lambda st, f: int((st.fase == f).sum())
    bl, bl0, wip, wip0 = cnt(now, "backlog"), cnt(before, "backlog"), cnt(now, "wip"), cnt(before, "wip")

    def kpi(k, label, unit="giorni"):
        r = c.loc[k]
        return {"label": label, "value": _f(r[r_]), "unit": unit, "delta": _delta(r[f"delta_{r_}"]),
                "cls": TREND_CLS[r[f"trend_{r_}"]], "note": f"storico {_f(r['hist'])}"}
    kpis = [kpi("lead_p50", f"Lead time P50 · ultimi {d} gg"), kpi("lead_p85", f"Lead time P85 · ultimi {d} gg"),
            kpi("throughput", f"Consegne a settimana · ultimi {d} gg", "item"),
            {"label": "Item aperti (coda + lavorazione)", "value": bl + wip, "unit": "item",
             "delta": _delta((bl + wip) / (bl0 + wip0) - 1) if bl0 + wip0 else "-", "cls": "bg-secondary-lt",
             "note": f"{d} giorni fa: {bl0 + wip0}"}]

    rows = [{"label": r["label"], "hist": _val(r["hist"], r["unit"]),
             "rec": [{"v": _val(r[f"r{x}"], r["unit"]), "delta": _delta(r[f"delta_r{x}"]), "trend": r[f"trend_r{x}"],
                      "cls": TREND_CLS[r[f"trend_r{x}"]]} for x in rec]}
            for r in cmp[cmp.better.notna()].to_dict("records")]

    wins = ["hist"] + [f"r{x}" for x in rec]
    share = {w: {p: c.loc[f"{p}_share", w] for p in PHASE_COL} for w in wins}
    pct = lambda w: ", ".join(f"{PHASE_LBL[p][0].lower() + PHASE_LBL[p][1:]} {_val(v, '%')}" for p, v in share[w].items())
    pace = {"meglio": "in aumento", "peggio": "in calo"}.get(c.loc["throughput", f"trend_{r_}"], "stabile")
    biz = [f"Se un item entra oggi, nella metà dei casi arriva in produzione entro <b>{_f(c.loc['lead_p50', r_])} giorni</b> "
           f"e nell'85% entro <b>{_f(c.loc['lead_p85', r_])} giorni</b> (storico: {_f(c.loc['lead_p50', 'hist'])} e {_f(c.loc['lead_p85', 'hist'])}).",
           f"Negli ultimi {d} giorni sono andati in produzione <b>{n[r_]} item</b>, {_f(c.loc['throughput', r_])} a settimana "
           f"(storico {_f(c.loc['throughput', 'hist'])}): ritmo <b>{pace}</b>.",
           f"Un'urgenza presa subito in lavorazione arriva in produzione in circa <b>{_f(c.loc['cycle_p50', r_])} giorni</b> (cycle time P50).",
           f"Oggi ci sono <b>{bl} item in coda</b> e <b>{wip} in lavorazione</b> ({d} giorni fa: {bl0} e {wip0})."]

    deltas = {k: c.loc[k, f"delta_{r_}"] for k in ("wait_med", "work_med", "release_med")}
    deltas = {k: v for k, v in deltas.items() if v == v}
    worst = max(deltas, key=deltas.get) if deltas else None
    flags = [f"{p}_flag" for p in metrics.PHASES]
    recent = it[it.valid & (it.prod_date > end - pd.Timedelta(days=d))]
    open_ = it[~it["Issue Type"].isin(cfg["exclude_issue_types"]) & ~it.Status.isin(cfg["closed_statuses"])]
    n_alert, n_open_alert = (int((x[flags] == 2).any(axis=1).sum()) for x in (recent, open_))
    dev = [f"Dove va il tempo negli ultimi {d} giorni: {pct(r_)} (storico: {pct('hist')}).",
           (f"Fase che peggiora di pi&ugrave;: <b>{c.loc[worst, 'label'].lower()}</b>, da {_f(c.loc[worst, 'hist'])} "
            f"a {_f(c.loc[worst, r_])} giorni ({_delta(deltas[worst])}).") if worst and c.loc[worst, f"trend_{r_}"] == "peggio"
           else "Nessuna fase peggiora rispetto allo storico (mediane).",
           f"<b>{n_alert}</b> item consegnati negli ultimi {d} giorni hanno almeno una fase oltre il P{cfg.get('anomaly_percentiles', [85, 95])[1]} "
           f"della propria classe; <b>{n_open_alert}</b> item aperti ci sono gi&agrave; oltre (vedi <a href=\"#issue\">Issue</a>)."]

    chart = {"cats": ["Storico"] + [f"Ultimi {x} gg" for x in rec],
             "series": [{"name": PHASE_LBL[p], "color": PHASE_COL[p], "data": [_nan(share[w][p] * 100) for w in wins]}
                        for p in PHASE_COL]}
    a, b = cmp.attrs["windows"]["hist"]
    listed = it[~it["Issue Type"].isin(cfg["exclude_issue_types"])]
    return {"title": title, "cls": cls, "verdict_word": verdict, "sentence": Markup(sentence),
            "n_issues": len(listed), "n_anom": int((listed[flags] > 0).any(axis=1).sum()), "n_open": bl + wip, "kpis": kpis, "rows": rows, "rec": rec, "n": n,
            "low": n[r_] < cfg["min_samples"] or n["hist"] < cfg["min_samples"],
            "biz": [Markup(x) for x in biz], "dev": [Markup(x) for x in dev], "chart": chart,
            "hist_from": a.strftime("%d/%m/%Y"), "hist_to": b.strftime("%d/%m/%Y")}


def issue_url(cfg, key):
    """URL di dettaglio da cfg["issue_url"] con ${issueKey} sostituito; None se non configurato o non http(s)."""
    tpl = (cfg.get("issue_url") or "").strip()
    if not tpl.lower().startswith(("http://", "https://")):
        return None
    return tpl.replace("${issueKey}", quote(str(key), safe=""))


def _issues(it, cfg, recent):
    """Righe della tabella issue (JSON, disegnata lato browser con ricerca, filtri e ordinamento)."""
    v = it[~it["Issue Type"].isin(cfg["exclude_issue_types"])]
    v = v.assign(_recent=recent.reindex(v.index).fillna(False))
    return [{"k": r["Key"], "u": issue_url(cfg, r["Key"]), "r": bool(r["_recent"]), "s": r["Summary"], "t": r["Issue Type"], "c": r["class"], "st": r["Status"],
             "o": r["Status"] not in cfg["closed_statuses"], "b": r["base_class"],
             "v": [_nan(r[p]) for p in metrics.PHASES], "f": [int(r[p + "_flag"]) for p in metrics.PHASES],
             "m": [_nan(r[p + "_ref"]) for p in metrics.PHASES]} for r in v.to_dict("records")]


def _pct(x):
    return f"{x * 100:.0f}" if x == x else "-"


def _tab_kpis(cfg, it, ev, end):
    """KPI in testa ai tab (ultimi kpi_months mesi contro i precedenti) + dati dei mini-grafici."""
    start, cut, end = metrics.kpi_bounds(cfg, end)
    day = pd.Timedelta(days=1)
    win = {"last": f"{(cut + day):%d/%m}–{end:%d/%m/%Y}", "prev": f"{(start + day):%d/%m}–{cut:%d/%m/%Y}",
           "months": cfg.get("kpi_months", 3), "prev_months": cfg.get("kpi_prev_months", cfg.get("kpi_months", 3))}
    days = cfg.get("kpi_within_days", [7, 14, 30, 60])
    days = [days] if isinstance(days, int) else list(days)
    default = cfg.get("kpi_default_days", 14)
    default = default if default in days else days[0]
    sparks = {}

    def spark(sid, weeks, ys, color, pct=False):
        sparks[sid] = {"x": [f"{w:%d/%m}" for w in weeks], "y": [_nan(y * 100 if pct else y) if y == y else None for y in ys],
                       "color": color, "pct": pct}
        return sid

    def within(col, prefix):
        """Un gruppo di card per ogni soglia in kpi_within_days (un tab ciascuno)."""
        groups = []
        for n in days:
            cards = []
            for r in metrics.within_kpi(it, cfg, end, col, n):
                name = "Tutti" if r["class"] == "TUTTI" else r["class"]
                cards.append({"label": name, "color": COL.get(r["class"], OTHER), "value": _pct(r["pct_last"]), "unit": "%",
                              "delta": f"{r['delta_pp']:+.0f} pp" if r["delta_pp"] == r["delta_pp"] else "-",
                              "cls": TREND_CLS[r["trend"]], "low": r["n_last"] < cfg["min_samples"], "total": r["class"] == "TUTTI",
                              "note": f"{r['n_last']} item · prima {_pct(r['pct_prev'])}% su {r['n_prev']}",
                              "spark": spark(f"{prefix}-{n}-{len(cards)}", r["weeks"], r["cum"], COL.get(r["class"], OTHER), pct=True)})
            groups.append({"days": n, "default": n == default, "cards": cards})
        return groups

    b = metrics.backlog_kpi(it, ev, cfg, end)
    labels = {"backlog": ("In attesa (backlog)", "#9ca3af", f"{win['months']} mesi prima"),
              "wip": ("In lavorazione (WIP)", "#e5833b", f"{win['months']} mesi prima"),
              "delivered": (f"Consegnati negli ultimi {win['months']} mesi", "#66a182", f"nei {win['prev_months']} mesi precedenti")}
    backlog = [{"label": labels[k][0], "value": b[k]["now"], "unit": "item", "delta": _delta(b[k]["delta"]),
                "cls": TREND_CLS[b[k]["trend"]], "note": f"{labels[k][2]}: {b[k]['before']}" + (f" ({_f(b[k]['per_week_before'])}/sett. contro {_f(b[k]['per_week'])})" if k == "delivered" else ""),
                "spark": spark(f"bk-{k}", b[k]["weeks"], b[k]["series"], labels[k][1])} for k in ("backlog", "wip", "delivered")]

    dk = metrics.deploy_kpi(it, cfg, end)
    deploy = {"value": dk["now"], "delta": _delta(dk["delta"]), "cls": TREND_CLS[dk["trend"]], "before": dk["before"],
              "per_week": _f(dk["per_week"]), "per_week_before": _f(dk["per_week_before"])}

    recent = metrics.recent_mask(it, ev, cfg, end)
    ak = metrics.anomaly_kpi(it, cfg, recent)
    ap = cfg.get("anomaly_percentiles", [85, 95])
    issue = {"n": ak["n"], "open": ak["open"], "any": ak["any"], "any_pct": _pct(ak["any"] / ak["n"]) if ak["n"] else "-",
             "phases": [{"label": lbl, "pct": _pct(ak[p]["pct"]), "n": ak[p]["n"], "red": ak[p]["red"],
                         "red_w": round(ak[p]["red"] / ak["n"] * 100, 1) if ak["n"] else 0,
                         "yellow_w": round((ak[p]["n"] - ak[p]["red"]) / ak["n"] * 100, 1) if ak["n"] else 0}
                        for p, lbl in (("wait", "Attesa (presa in carico)"), ("work", "Lavoro (dev + test)"),
                                       ("release", "Attesa (rilascio)"))],
             "p_hi": ap[1]}
    return {"win": win, "lead": within("lead_days", "lt"), "cycle": within("cycle_days", "ct"), "backlog": backlog,
            "deploy": deploy, "issue": issue}, {"sparks": sparks, "deploy": {"last": dk["last"], "prev": dk["prev"],
                                                                              "last_label": f"Ultimi {win['months']} mesi ({win['last']})",
                                                                              "prev_label": f"{win['prev_months']} mesi precedenti ({win['prev']})"}}


def render(cfg, today, end, it, ev, fc, thr, lead, snap, bnow, cyc, cyl, cmp, src, path):
    pcols = [f"P{p}" for p in cfg["percentiles"]]
    wcols = [f"<= {n}g" for n in cfg["within_days"]]
    tcols = [c for c in thr.columns if c != "TOTALE"]
    thr_rows = [{"m": month_name(i), "vals": [int(r[c]) for c in tcols], "tot": int(r["TOTALE"])} for i, r in thr.iterrows()]

    p = snap.pivot_table(index="month", columns="class", values="backlog", aggfunc="sum", fill_value=0)
    w = snap.groupby("month").wip.sum()
    bcols = list(p.columns)
    brows = [{"m": month_name(i), "vals": [int(r[c]) for c in bcols], "tot": int(r.sum()), "wip": int(w.get(i, 0))} for i, r in p.iterrows()]

    def trend(l):
        return {"months": [month_name(i) for i in l.index], "p50": [_nan(v) for v in l.P50],
                "p85": [_nan(v) for v in l.P85], "n": [int(v) for v in l.n]}

    summ = _summary(cfg, it, ev, cmp, end)
    tk, tk_data = _tab_kpis(cfg, it, ev, end)
    data = {
        "phases": summ["chart"], "issues": _issues(it, cfg, metrics.recent_mask(it, ev, cfg, end)), **tk_data,
        "lead": trend(lead), "cycle": trend(cyl),
        "backlog": {"months": [month_name(i) for i in p.index],
                    "series": [{"name": c, "color": COL.get(c, OTHER), "data": [int(v) for v in p[c]]} for c in bcols],
                    "wip": [int(w.get(i, 0)) for i in p.index]},
        "throughput": {"months": [month_name(i) for i in thr.index],
                       "series": [{"name": c, "color": COL.get(c, OTHER), "data": [int(v) for v in thr[c]]} for c in tcols],
                       "total": [int(v) for v in thr["TOTALE"]]},
    }
    p_start, p_end = metrics.period_bounds(cfg)
    period = ""
    if p_start is not None:
        period = p_start.strftime("%d/%m/%Y") + (" al " + p_end.strftime("%d/%m/%Y") if p_end is not None else "")
    html = ENV.get_template("report.html.j2").render(
        today=today.strftime("%d/%m/%Y"), end=end.strftime("%d/%m/%Y"), period=period, n_valid=int(it.valid.sum()),
        n_total=len(it), summ=summ, tk=tk, ap=cfg.get("anomaly_percentiles", [85, 95]), forecast=_forecast(fc, today, pcols, wcols),
        cycle=_forecast(cyc, today, pcols, wcols, with_pickup=False), missing=it.attrs.get("missing_fields", []),
        pcols=pcols, wcols=[{"n": n} for n in cfg["within_days"]], open_n=len(bnow), src=src,
        bcols=[{"name": c} for c in bcols], backlog_rows=brows, tcols=[{"name": c} for c in tcols], thr_rows=thr_rows,
        bulk=it.attrs.get("bulk_days", []), cfg=cfg, excl=list(it[~it.valid].excl_reason.value_counts().items()),
        # "<" escapato: il JSON sta dentro un tag <script>
        data_json=Markup(json.dumps(data).replace("<", "\\u003c")),
        tabler_css=Markup((HERE / "vendor" / "tabler.min.css").read_text(encoding="utf-8")),
        echarts_js=Markup((HERE / "vendor" / "echarts.min.js").read_text(encoding="utf-8").replace("</script", "<\\/script")))
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
