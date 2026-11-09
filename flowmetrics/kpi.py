"""KPI standard: 8 indicatori a colpo d'occhio, gli stessi nella Sintesi del report e nell'analisi Markdown.

Finestre: regola KPI del progetto (metrics.kpi_bounds): ultimi kpi_months mesi contro i kpi_prev_months precedenti.
Per ogni KPI: valore, riferimento (periodo precedente), variazione, trend (meglio/peggio/stabile) e semaforo
(verde/giallo/rosso) con le soglie di config `standard_kpis`. Il semaforo dice se il valore e' buono in assoluto,
il trend se sta migliorando: sono due informazioni diverse.
"""
import numpy as np
import pandas as pd
from . import metrics

DEFAULTS = {
    "throughput_delta": {"green": 0.0, "red": -0.25},
    "lead_p50": {"green": 25, "red": 40},
    "cycle_p50": {"green": 15, "red": 30},
    "work_wait": {"green": 0.75, "red": 0.50},
    "release_share": {"green": 0.15, "red": 0.25},
    "balance": {"green": 1.0, "red": 1.2},
    "wip_delta": {"green": 0.05, "red": 0.20},
    "backlog_delta": {"green": 0.05, "red": 0.20},
}


def _opts(cfg):
    o = {**DEFAULTS}
    for k, x in (cfg.get("standard_kpis") or {}).items():
        o[k] = {**o[k], **x} if isinstance(o.get(k), dict) else x
    return o


def status(x, th, better):
    """Semaforo: verde se x e' dalla parte buona di th['green'], rosso se oltre th['red'], giallo in mezzo."""
    if x is None or x != x:
        return "n/d"
    if better == "high":
        return "verde" if x >= th["green"] else "rosso" if x < th["red"] else "giallo"
    return "verde" if x <= th["green"] else "rosso" if x > th["red"] else "giallo"


def _rule(th, better, fmt):
    g, r = fmt(th["green"]), fmt(th["red"])
    return f"verde ≥ {g}, rosso < {r}" if better == "high" else f"verde ≤ {g}, rosso > {r}"


def _trend_txt(delta, delta_txt, trend, better, mode, tol, tol_pp):
    """Perche' il trend e' meglio/peggio/stabile: variazione, tolleranza e verso buono del KPI."""
    if delta != delta:
        return "Trend non calcolabile: manca il valore o il riferimento."
    lim = f"±{tol_pp * 100:.0f} punti" if mode == "pp" else f"±{tol * 100:.0f}%"
    out = abs(delta) > (tol_pp if mode == "pp" else tol)
    verso = "più alto è meglio" if better == "high" else "più basso è meglio"
    return (f"Trend {trend}: variazione {delta_txt} rispetto al riferimento, {'oltre' if out else 'entro'} la tolleranza di "
            f"{lim} ({verso}).")


def _window(v, a, b):
    return v[(v.prod_date > a) & (v.prod_date <= b)]


def _ratio(g):
    wait = g.wait_days.sum() + g.release_days.sum()
    return g.work_days.sum() / wait if wait else np.nan


def _share(g):
    flow = g.flow_days.sum()
    return g.release_days.sum() / flow if flow else np.nan


def standard_kpis(it, ev, cfg, end):
    """Lista degli 8 KPI standard (dict). `it` deve avere le fasi (metrics.phases)."""
    o = _opts(cfg)
    start, cut, end = metrics.kpi_bounds(cfg, end)
    tol, tol_pp = cfg.get("trend_tolerance", 0.10), cfg.get("kpi_tolerance_pp", 5) / 100
    v = it[it.valid & it.prod_date.notna()]
    last, prev = _window(v, cut, end), _window(v, start, cut)
    days_l, days_p = max((end - cut).total_seconds() / 86400, 1), max((cut - start).total_seconds() / 86400, 1)
    sc = it[metrics.in_scope(it, cfg)]
    created = lambda a, b: int(((sc.Created > a) & (sc.Created <= b)).sum())
    st_now, st_before = (metrics.state_at(it, ev, cfg, t).fase for t in (end, cut))
    bl_now, bl_before = int((st_now == "backlog").sum()), int((st_before == "backlog").sum())
    wip_now, wip_before = int((st_now == "wip").sum()), int((st_before == "wip").sum())
    days = lambda x: f"{x:.1f}"
    pct = lambda x: f"{x * 100:.0f}%"
    gg = lambda x: f"{x:.0f} giorni"
    num = lambda x: f"{x:.2f}"
    rel = lambda x: f"{x * 100:+.0f}%"
    tp_l, tp_p = len(last) / days_l * 7, len(prev) / days_p * 7
    nl, np_ = len(last), len(prev)
    c_l, c_p = int(last.cycle_days.notna().sum()), int(prev.cycle_days.notna().sum())
    sums = lambda g: {k: g[f"{k}_days"].sum() for k in ("wait", "work", "release", "flow")}
    s_l, s_p = sums(last), sums(prev)
    cr_l, cr_p = created(cut, end), created(start, cut)
    d_end, d_cut = f"{end:%d/%m}", f"{cut:%d/%m}"
    # come si calcola ogni valore, con i numeri del periodo (ultimo; riferimento)
    calc = {
        "throughput": f"{nl} consegne in {days_l:.0f} giorni = {tp_l:.1f} a settimana; riferimento {np_} in {days_p:.0f} giorni "
                      f"= {tp_p:.1f}." + (" Si confrontano i ritmi perché i due periodi durano diverso."
                                          if round(days_l) != round(days_p) else ""),
        "lead_p50": f"Mediana del lead time (giorni di calendario dalla creazione all'ingresso in {cfg['done_status']}) "
                    f"di {nl} consegne; riferimento su {np_}. Confrontato con il cycle time P50 mostra quanto pesa l'attesa "
                    f"prima dell'avvio ({last.lead_days.median() - last.cycle_days.median():.1f} giorni ora, "
                    f"{prev.lead_days.median() - prev.cycle_days.median():.1f} nel riferimento)." if nl and np_ else
                    f"Mediana del lead time di {nl} consegne; riferimento su {np_}.",
        "wip": f"{wip_now} attività in lavorazione (avviate, non ancora in produzione) il {d_end}, {wip_before} il {d_cut}"
               + (f"; al ritmo attuale servono {wip_now / tp_l:.1f} settimane per completarle." if tp_l else "."),
        "cycle_p50": f"Mediana del cycle time di {c_l} consegne passate da {cfg.get('cycle_start_status', 'In Progress')} "
                     f"(su {nl}); riferimento {c_p} su {np_}.",
        "work_wait": f"{s_l['work']:.0f} giorni di lavoro diviso {s_l['wait'] + s_l['release']:.0f} di attesa ({s_l['wait']:.0f} in coda "
                     f"+ {s_l['release']:.0f} in attesa di rilascio), sommati sulle consegne; riferimento {s_p['work']:.0f} / "
                     f"{s_p['wait'] + s_p['release']:.0f}.",
        "release_share": f"{s_l['release']:.0f} giorni in attesa di rilascio su {s_l['flow']:.0f} giorni totali delle consegne; "
                         f"riferimento {s_p['release']:.0f} su {s_p['flow']:.0f}.",
        "balance": f"{cr_l} attività create diviso {nl} consegnate; riferimento {cr_p} / {np_}.",
        "backlog": f"{bl_now} attività in coda il {d_end}, {bl_before} il {d_cut}"
                   + (f"; la coda di oggi equivale a {bl_now / tp_l:.1f} settimane di consegne." if tp_l else "."),
    }
    bal_l = created(cut, end) / len(last) if len(last) else np.nan
    bal_p = created(start, cut) / len(prev) if len(prev) else np.nan

    # key, etichetta, valore, riferimento, unita', formato, meglio, modo del confronto, soglie (+ su cosa), definizione
    spec = [
        ("throughput", "Consegne a settimana", tp_l, tp_p, "item", days, "high", "rel", ("throughput_delta", "delta", rel),
         "Attività arrivate in produzione, in media a settimana. Si confronta solo con la storia del team: "
         "il semaforo guarda la variazione rispetto al periodo di riferimento."),
        ("lead_p50", "Lead time P50", last.lead_days.median(), prev.lead_days.median(), "giorni", days, "low", "rel",
         ("lead_p50", "value", gg),
         "Giorni dalla creazione alla produzione per l'attività tipica: metà ci mette meno, metà di più. "
         "È il tempo vissuto da chi ha fatto la richiesta."),
        ("cycle_p50", "Cycle time P50", last.cycle_days.median(), prev.cycle_days.median(), "giorni", days, "low", "rel",
         ("cycle_p50", "value", gg),
         f"Giorni dall'avvio dei lavori (primo ingresso in {cfg.get('cycle_start_status', 'In Progress')}) alla produzione "
         "per l'attività tipica: metà ci mette meno, metà di più. È il tempo di lavorazione, senza l'attesa iniziale."),
        ("wip", "Work in progress", float(wip_now), float(wip_before), "item", lambda x: f"{x:.0f}", "low", "rel",
         ("wip_delta", "delta", rel),
         "Attività avviate e non ancora in produzione alla fine dei dati; il riferimento è il WIP all'inizio degli ultimi "
         f"{cfg.get('kpi_months', 3)} mesi. Più lavoro aperto insieme allunga i tempi: il semaforo guarda quanto è cresciuto."),
        ("work_wait", "Rapporto lavoro / attesa", _ratio(last), _ratio(prev), "", num, "high", "rel", ("work_wait", "value", num),
         "Giorni di lavoro (sviluppo e test) per ogni giorno di attesa (in coda o in attesa di rilascio), sulle attività "
         "consegnate. Sopra 1 si lavora più di quanto si aspetta."),
        ("release_share", "Tempo in attesa di rilascio", _share(last), _share(prev), "", pct, "low", "pp",
         ("release_share", "value", pct),
         "Parte del tempo totale delle attività consegnate passata a lavoro finito, in attesa di collaudo o di rilascio."),
        ("balance", "Bilancio ingressi / uscite", bal_l, bal_p, "", num, "low", "rel", ("balance", "value", num),
         "Attività create diviso attività consegnate nel periodo. Sopra 1 il lavoro si accumula, sotto 1 si smaltisce."),
        ("backlog", "Dimensione del backlog", float(bl_now), float(bl_before), "item", lambda x: f"{x:.0f}", "low", "rel",
         ("backlog_delta", "delta", rel),
         "Attività in coda, non ancora iniziate, alla fine dei dati; il riferimento è la coda all'inizio degli ultimi "
         f"{cfg.get('kpi_months', 3)} mesi. Il semaforo guarda quanto è cresciuta."),
    ]
    out = []
    for key, label, x, ref, unit, fmt, better, mode, (th_key, on, th_fmt), definition in spec:
        ok = x == x and ref == ref
        if mode == "pp":
            delta = (x - ref) if ok else np.nan
            delta_txt = f"{delta * 100:+.0f} pp" if delta == delta else "-"
            trend = metrics._trend(delta, better, tol_pp)   # soglia in punti (kpi_tolerance_pp)
        else:
            delta = metrics._rel(x, ref) if ok else np.nan
            delta_txt = rel(delta) if delta == delta else "-"
            trend = metrics._trend(delta, better, tol)
        th = o[th_key]
        st = status(delta if on == "delta" else x, th, better)
        rule = _rule(th, better, th_fmt) + (" (sulla variazione)" if on == "delta" else "")
        extra = f"pari a {x / tp_l:.1f} settimane di consegne" if key in ("backlog", "wip") and tp_l else ""
        out.append({"key": key, "label": label, "value": x, "ref": ref, "value_txt": fmt(x) if x == x else "-",
                    "ref_txt": fmt(ref) if ref == ref else "-", "unit": unit, "delta": delta, "delta_txt": delta_txt,
                    "trend": trend, "status": st, "rule": rule, "definition": definition, "extra": extra,
                    "calc": calc[key], "trend_txt": _trend_txt(delta, delta_txt, trend, better, mode, tol, tol_pp),
                    "status_txt": f"Stato {st}: {rule}." if st != "n/d" else "Stato non calcolabile (dati insufficienti)."})
    return {"kpis": out, "bounds": (start, cut, end),
            "last_txt": f"{cut + pd.Timedelta(minutes=1):%d/%m}–{end:%d/%m/%Y}",
            "prev_txt": f"{start + pd.Timedelta(minutes=1):%d/%m}–{cut:%d/%m/%Y}"}
