"""Report HTML autonomo: Tabler (CSS) + Apache ECharts, entrambi incorporati nel file (nessuna dipendenza esterna)."""
import json
from pathlib import Path
import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup
from . import metrics

HERE = Path(__file__).parent
COL = {"Bug": "#d1495b", "Story (non progettuale)": "#2e86ab", "Story progettuale": "#7fb6d3", "Story": "#2e86ab", "Task": "#66a182", "Engine": "#edae49", "Speciali": "#8d6a9f", "Expedite": "#f76707", "TUTTI": "#6b7280"}
OTHER = "#9ca3af"
ENV = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape(["html", "j2"]))


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


def _kpis(fc, thr, today, n_valid):
    wmax = fc.window.max()
    t = fc[(fc.window == wmax) & (fc["class"] == "TUTTI")].iloc[0]
    complete = thr[thr.index < today.to_period("M")]["TOTALE"]
    per_month = f"{complete.mean():.0f}" if len(complete) else "-"
    return [
        {"label": "Item consegnati nel periodo", "value": n_valid, "unit": "", "note": "validi per le metriche"},
        {"label": f"Lead time P50 (ultimi {wmax} mesi)", "value": _f(t["P50"]), "unit": "giorni", "note": "metà degli item arriva prima"},
        {"label": f"Lead time P85 (ultimi {wmax} mesi)", "value": _f(t["P85"]), "unit": "giorni", "note": "85% degli item arriva prima"},
        {"label": "Consegne medie al mese", "value": per_month, "unit": "item", "note": "sui mesi completi del trend"},
    ]


def render(cfg, today, it, fc, thr, lead, snap, bnow, cyc, cyl, path):
    pcols = [f"P{p}" for p in cfg["percentiles"]]
    wcols = [f"<= {n}g" for n in cfg["within_days"]]
    tcols = [c for c in thr.columns if c != "TOTALE"]
    thr_rows = [{"m": str(i), "vals": [int(r[c]) for c in tcols], "tot": int(r["TOTALE"])} for i, r in thr.iterrows()]

    p = snap.pivot_table(index="month", columns="class", values="backlog", aggfunc="sum", fill_value=0)
    w = snap.groupby("month").wip.sum()
    bcols = list(p.columns)
    brows = [{"m": str(i), "vals": [int(r[c]) for c in bcols], "tot": int(r.sum()), "wip": int(w.get(i, 0))} for i, r in p.iterrows()]

    def trend(l):
        return {"months": [str(i) for i in l.index], "p50": [_nan(v) for v in l.P50],
                "p85": [_nan(v) for v in l.P85], "n": [int(v) for v in l.n]}

    data = {
        "lead": trend(lead), "cycle": trend(cyl),
        "backlog": {"months": [str(i) for i in p.index],
                    "series": [{"name": c, "color": COL.get(c, OTHER), "data": [int(v) for v in p[c]]} for c in bcols],
                    "wip": [int(w.get(i, 0)) for i in p.index]},
        "throughput": {"months": [str(i) for i in thr.index],
                       "series": [{"name": c, "color": COL.get(c, OTHER), "data": [int(v) for v in thr[c]]} for c in tcols],
                       "total": [int(v) for v in thr["TOTALE"]]},
    }
    p_start, p_end = metrics.period_bounds(cfg)
    period = ""
    if p_start is not None:
        period = p_start.strftime("%d/%m/%Y") + (" al " + p_end.strftime("%d/%m/%Y") if p_end is not None else "")
    html = ENV.get_template("report.html.j2").render(
        today=today.strftime("%d/%m/%Y"), period=period, n_valid=int(it.valid.sum()), n_total=len(it),
        kpis=_kpis(fc, thr, today, int(it.valid.sum())), forecast=_forecast(fc, today, pcols, wcols),
        cycle=_forecast(cyc, today, pcols, wcols, with_pickup=False), missing=it.attrs.get("missing_fields", []),
        pcols=pcols, wcols=[{"n": n} for n in cfg["within_days"]], open_n=len(bnow),
        bcols=[{"name": c} for c in bcols], backlog_rows=brows, tcols=[{"name": c} for c in tcols], thr_rows=thr_rows,
        bulk=it.attrs.get("bulk_days", []), cfg=cfg, excl=list(it[~it.valid].excl_reason.value_counts().items()),
        # "<" escapato: il JSON sta dentro un tag <script>
        data_json=Markup(json.dumps(data).replace("<", "\\u003c")),
        tabler_css=Markup((HERE / "vendor" / "tabler.min.css").read_text(encoding="utf-8")),
        echarts_js=Markup((HERE / "vendor" / "echarts.min.js").read_text(encoding="utf-8").replace("</script", "<\\/script")))
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
