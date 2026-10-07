import base64, io
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from jinja2 import Template

COL = {"Bug": "#d1495b", "Story": "#2e86ab", "Task": "#66a182", "Engine": "#edae49", "Speciali": "#8d6a9f", "TOTALE": "#333"}


def _png(fig):
    b = io.BytesIO(); fig.savefig(b, format="png", dpi=110, bbox_inches="tight"); plt.close(fig)
    return base64.b64encode(b.getvalue()).decode()


def chart_throughput(t):
    fig, ax = plt.subplots(figsize=(9, 3.6))
    cols = [c for c in t.columns if c != "TOTALE"]
    t[cols].plot.bar(stacked=True, ax=ax, color=[COL.get(c, "#999") for c in cols])
    ax.set_xticklabels([str(i) for i in t.index], rotation=45, ha="right"); ax.set_xlabel(""); ax.set_ylabel("item in produzione")
    ax.spines[["top", "right"]].set_visible(False); ax.legend(frameon=False, ncol=5)
    for i, v in enumerate(t["TOTALE"]): ax.text(i, v + 1, int(v), ha="center", fontsize=8)
    return _png(fig)


def chart_leadtime(l):
    fig, ax = plt.subplots(figsize=(9, 3.2))
    x = [str(i) for i in l.index]
    ax.plot(x, l.P50, marker="o", label="P50"); ax.plot(x, l.P85, marker="o", label="P85")
    ax.set_xticks(range(len(x))); ax.set_xticklabels(x, rotation=45, ha="right"); ax.set_ylabel("giorni")
    ax.spines[["top", "right"]].set_visible(False); ax.legend(frameon=False)
    return _png(fig)


def chart_backlog(s):
    fig, ax = plt.subplots(figsize=(9, 3.4))
    for fase, ls in (("backlog", "-"), ("wip", "--")):
        p = s.pivot_table(index="month", columns="class", values=fase, aggfunc="sum", fill_value=0)
        tot = p.sum(axis=1)
        ax.plot([str(i) for i in tot.index], tot.values, ls, marker="o", label=fase.upper() + " (totale)")
    ax.tick_params(axis="x", rotation=45); ax.set_ylabel("item")
    ax.spines[["top", "right"]].set_visible(False); ax.legend(frameon=False)
    return _png(fig)


TPL = Template("""<!doctype html><html lang="it"><meta charset="utf-8"><title>Flow report PS</title>
<style>body{font-family:Arial,sans-serif;max-width:1000px;margin:24px auto;padding:0 16px;color:#222}
h1{margin-bottom:0}h2{border-bottom:2px solid #eee;padding-bottom:4px;margin-top:36px}
table{border-collapse:collapse;font-size:13px;margin:8px 0}td,th{border:1px solid #ddd;padding:4px 9px;text-align:right}
th{background:#f5f5f5}td:first-child,th:first-child{text-align:left}.low{color:#999;font-style:italic}
.note{background:#fff8e1;border-left:4px solid #edae49;padding:8px 12px;font-size:13px;margin:10px 0}
.big{font-size:15px}img{max-width:100%}</style>
<h1>Flow report - progetto PS</h1><div>Dati al {{ today }} &middot; {{ n_valid }} item consegnati validi su {{ n_total }} nell'export</div>

<h2>1. Se entrasse oggi in board, quando arriva in produzione?</h2>
<p class="big">Stima dal lead time storico (creazione &rarr; ingresso in Done), in <b>giorni di calendario</b>. P85 = &laquo;nell'85% dei casi e' andata cosi' o meglio&raquo;.</p>
{% for w, tab in forecast %}<h3>Ultimi {{ w }} mesi</h3>
<table><tr><th>Classe</th><th>N</th>{% for p in pcols %}<th>{{ p }} (gg)</th>{% endfor %}<th>Data P85 se entra oggi</th>{% for c in wcols %}<th>{{ c }}</th>{% endfor %}<th>Pickup P50</th></tr>
{% for r in tab %}<tr {% if not r.reliable %}class="low"{% endif %}><td>{{ r["class"] }}{% if not r.reliable %} (poco affidabile){% endif %}</td><td>{{ r.n }}</td>
{% for p in pcols %}<td>{{ "%.1f"|format(r[p]) if r[p]==r[p] else "-" }}</td>{% endfor %}
<td>{{ r.d85 }}</td>{% for c in wcols %}<td>{{ "%.0f%%"|format(r[c]*100) if r[c]==r[c] else "-" }}</td>{% endfor %}<td>{{ "%.1f"|format(r.pickup_P50) if r.pickup_P50==r.pickup_P50 else "-" }}</td></tr>{% endfor %}</table>{% endfor %}
<img src="data:image/png;base64,{{ img_lead }}"><br><small>Andamento mensile del lead time (per mese di produzione)</small>

<h2>2. Come sta il backlog?</h2>
{% if open_n < 30 %}<div class="note"><b>Attenzione:</b> l'export contiene solo {{ open_n }} item non chiusi. Il backlog "oggi" non e' misurabile: serve un export (stessa query PS, stessi campi) che includa anche gli item aperti. Il grafico sotto e' <i>ricostruito dalle transizioni</i> e vale solo per il passato, sottostimando il backlog (survivorship bias): gli item mai chiusi o cancellati non compaiono.</div>{% endif %}
<img src="data:image/png;base64,{{ img_backlog }}">
<table><tr><th>Mese</th>{% for c in bcols %}<th>{{ c }} backlog</th>{% endfor %}<th>Backlog tot</th><th>WIP tot</th></tr>
{% for r in backlog_rows %}<tr><td>{{ r.m }}</td>{% for c in bcols %}<td>{{ r[c] }}</td>{% endfor %}<td>{{ r.tot }}</td><td>{{ r.wip }}</td></tr>{% endfor %}</table>
{% if bulk %}<div class="note">Bonifica di massa rilevata nei giorni: {{ bulk|join(", ") }}. Quegli ingressi in Done non sono considerati produzione per il lead time, ma chiudono gli item nel backlog ricostruito (si vede come un calo brusco).</div>{% endif %}

<h2>3. Quanti item in produzione, mese per mese</h2>
<img src="data:image/png;base64,{{ img_thr }}">
<table><tr><th>Mese</th>{% for c in tcols %}<th>{{ c }}</th>{% endfor %}</tr>
{% for r in thr_rows %}<tr><td>{{ r.m }}</td>{% for c in tcols %}<td>{{ r[c] }}</td>{% endfor %}</tr>{% endfor %}</table>

<h2>Note metodologiche</h2><ul>
<li>Produzione = ingresso in <b>{{ cfg.done_status }}</b> ({{ cfg.done_pick }}), escludendo giorni con piu' di {{ cfg.bulk_done_threshold }} ingressi.</li>
<li>Item scartati dal calcolo: {% for k, v in excl %}{{ k }}: {{ v }}; {% endfor %}</li>
<li>Percentili su giorni di calendario; con N &lt; {{ cfg.min_samples }} la stima e' indicativa.</li></ul></html>""")


def render(cfg, today, it, fc, thr, lead, snap, bnow, path):
    pcols = [f"P{p}" for p in cfg["percentiles"]]
    wcols = [f"<= {n}g" for n in cfg["within_days"]]
    fcs = []
    for w, g in fc.groupby("window"):
        g = g.copy()
        g["d85"] = [(today + pd.Timedelta(days=x)).strftime("%d/%m/%Y") if x == x else "-" for x in g["P85"]]
        fcs.append((w, g.to_dict("records")))
    tcols = list(thr.columns)
    thr_rows = [{"m": str(i), **{c: int(r[c]) for c in tcols}} for i, r in thr.iterrows()]
    p = snap.pivot_table(index="month", columns="class", values="backlog", aggfunc="sum", fill_value=0)
    w = snap.groupby("month").wip.sum()
    bcols = list(p.columns)
    brows = [{"m": str(i), **{c: int(r[c]) for c in bcols}, "tot": int(r.sum()), "wip": int(w.get(i, 0))} for i, r in p.iterrows()]
    excl = it[~it.valid].excl_reason.value_counts().items()
    html = TPL.render(today=today.strftime("%d/%m/%Y"), n_valid=int(it.valid.sum()), n_total=len(it), forecast=fcs,
                      pcols=pcols, wcols=wcols, img_lead=chart_leadtime(lead), img_backlog=chart_backlog(snap),
                      img_thr=chart_throughput(thr), open_n=len(bnow), bcols=bcols, backlog_rows=brows,
                      tcols=tcols, thr_rows=thr_rows, bulk=it.attrs.get("bulk_days", []), cfg=cfg, excl=list(excl))
    open(path, "w", encoding="utf-8").write(html)
