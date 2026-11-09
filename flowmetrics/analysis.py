"""Analisi del flusso in Markdown, generata a ogni esecuzione accanto al report HTML (file separato, non incluso nel report).

Struttura fissa: sintesi, le cinque domande del report (quanto ci mettiamo, quando arrivera', dove si ferma il lavoro,
stiamo migliorando, carico di lavoro), dati significativi, attivita' proposte, limiti dell'analisi.
I testi interpretativi nascono da regole esplicite con soglie in config (blocco `analysis`), non da giudizi liberi:
ogni frase deve essere verificabile sui dati del report.
"""
import numpy as np
import pandas as pd
from . import kpi, metrics, report

DEFAULTS = {
    "release_share_max": 0.15,      # attesa rilascio oltre questa quota del tempo totale = rilasci da accorciare
    "release_wait_epic_days": 30,   # epic con attesa rilascio mediana oltre N giorni = lotto rilasciato tardi
    "min_epic_items": 5,            # epic con meno item non vengono commentate
    "uat_open_min": 10,             # item aperti fermi negli stati di attesa rilascio oltre cui si propone un responsabile
    "stale_queue_days": 90,         # item in coda da oltre N giorni = da rivedere
    "long_wip_days": 120,           # item in lavorazione da oltre N giorni = da sbloccare
    "reentry_gap_days": 30,         # rientro Done -> Done dopo oltre N giorni = probabile bonifica, non consegna
    "spread_wide": 3.5,             # P85/P50 oltre questo rapporto = tempi poco prevedibili
    "expedite_p85_max": 30,         # Expedite con P85 oltre N giorni = corsia veloce che non funziona
    "max_actions": 5,               # attivita' proposte al massimo
}
WEEKDAYS = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]


def _opts(cfg):
    return {**DEFAULTS, **(cfg.get("analysis") or {})}


def _f(x, nd=1):
    return "-" if x is None or x != x else f"{x:.{nd}f}"


def _i(x):
    return "-" if x is None or x != x else f"{x:.0f}"


def _p(x):
    return "-" if x is None or x != x else f"{x * 100:.0f}%"


def _cell(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


def _table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def _key(cfg, k):
    u = report.issue_url(cfg, k)
    return f"[{k}]({u})" if u else k


def _mese(m):
    return report.month_name(m)


def _dir(new, old, tol, low_is_good=True, unit="", nd=1):
    """'migliora/peggiora/stabile' fra due valori, con i numeri."""
    if new != new or old != old or not old:
        return f"{_f(new, nd)}{unit}"
    d = (new - old) / old
    good = -d if low_is_good else d
    word = "in miglioramento" if good > tol else "in peggioramento" if good < -tol else "stabile"
    return f"da {_f(old, nd)} a {_f(new, nd)}{unit} ({d:+.0%}, {word})"


def _fase(cfg, status):
    return pd.Series(np.where(status.isin(cfg["queue_statuses"]), "coda",
                              np.where(status.isin(cfg.get("release_wait_statuses", [])), "attesa rilascio", "lavoro")),
                     index=status.index)


# ---------------------------------------------------------------------------------------------------------------
# Calcoli (funzioni pure, testate in tests/test_analysis.py)
# ---------------------------------------------------------------------------------------------------------------

def discarded(it, ev, cfg):
    """Item chiusi senza consegna (Resolution scartata), con la data dell'ultima chiusura."""
    d = it[metrics.in_scope(it, cfg) & (it.excl_reason == "resolution scartata") & it.Status.isin(cfg["closed_statuses"])]
    last = ev[ev.status.isin(cfg["closed_statuses"])].groupby("Key").ts.max()
    return d.assign(closed_ts=d.Key.map(last))


def monthly(it, ev, cfg, end):
    """Una riga per mese del trend: consegne, tempi, quote di tempo per fase, stato a fine mese, arrivi e scarti.
    little = WIP a fine mese / consegne al giorno del mese: giorni per smaltire il lavoro in corso (legge di Little)."""
    v = it[it.valid & it.prod_date.notna()].assign(month=lambda d: d.prod_date.dt.to_period("M"))
    sc = it[metrics.in_scope(it, cfg)]
    disc = discarded(it, ev, cfg)
    rows = []
    for m in metrics._trend_months(cfg, end):
        t = min(m.end_time, end)
        days = max((t - m.start_time).total_seconds() / 86400, 1)
        g = v[v.month == m]
        st = metrics.state_at(it, ev, cfg, t)
        flow = g.flow_days.sum()
        wip = int((st.fase == "wip").sum())
        rate = len(g) / days
        rows.append({
            "month": m, "partial": t < m.end_time - pd.Timedelta(days=1), "consegne": len(g), "a_sett": rate * 7,
            "lead50": g.lead_days.median(), "lead85": g.lead_days.quantile(.85), "cyc50": g.cycle_days.median(),
            "entro30": (g.lead_days <= 30).mean() if len(g) else np.nan,
            **{f"{p}_share": g[f"{p}_days"].sum() / flow if flow else np.nan for p in ("wait", "work", "release")},
            "ril50": g.release_days.median(),
            "backlog": int((st.fase == "backlog").sum()), "wip": wip,
            "creati": int(((sc.Created >= m.start_time) & (sc.Created <= t)).sum()),
            "scartati": int(((disc.closed_ts >= m.start_time) & (disc.closed_ts <= t)).sum()),
            "little": wip / rate if rate else np.nan})
    return pd.DataFrame(rows).set_index("month") if rows else pd.DataFrame()


def reentries(it, ev, cfg, gap_days):
    """Item validi la cui consegna conteggiata e' un rientro in Done direttamente da Done (nessun altro stato in mezzo)
    dopo oltre gap_days giorni: tipico di una bonifica o modifica di massa, non di una consegna vera.
    Gli item gia' in exclude_keys non sono validi e quindi non compaiono."""
    done = cfg["done_status"]
    v = it[it.valid & it.prod_date.notna()][["Key", "prod_date"]]
    e = ev[ev.Key.isin(v.Key)].sort_values(["Key", "ts"], kind="stable")
    e = e.assign(prev_status=e.groupby("Key").status.shift(), prev_ts=e.groupby("Key").ts.shift())
    r = e.merge(v, on="Key")
    r = r[(r.ts == r.prod_date) & (r.status == done) & (r.prev_status == done)]
    r = r.assign(gap=(r.ts - r.prev_ts).dt.total_seconds() / 86400)
    return r[r.gap > gap_days][["Key", "prev_ts", "ts", "gap"]].reset_index(drop=True)


def wip_flows(it, ev, cfg, t0, t1):
    """Da dove viene la variazione del WIP fra t0 e t1: consegne, chiusure senza consegna, ritorni in coda, avvii."""
    a = metrics.state_at(it, ev, cfg, t0).fase
    b = metrics.state_at(it, ev, cfg, t1).fase
    j = pd.DataFrame({"a": a, "b": b}).fillna({"a": "nuovo"})
    valid = np.asarray(j.index.map(it.set_index("Key").valid), dtype=bool)
    w = (j.a == "wip").to_numpy()
    closed = (j.b == "chiuso").to_numpy()
    return {"wip_start": int(w.sum()), "wip_end": int((j.b == "wip").sum()),
            "consegnati": int((w & closed & valid).sum()),
            "chiusi_senza_consegna": int((w & closed & ~valid).sum()),
            "tornati_in_coda": int((w & (j.b == "backlog").to_numpy()).sum()),
            "avviati": int((j.a.isin(["backlog", "nuovo"]) & (j.b == "wip")).sum()),
            "nuovi_in_coda": int(((j.a == "nuovo") & (j.b == "backlog")).sum())}


def open_items(it, cfg, end):
    """Item aperti (Status dell'export) con fase attuale, eta' e anomalie oltre il percentile alto."""
    o = it[metrics.in_scope(it, cfg) & ~it.Status.isin(cfg["closed_statuses"]) & (it.Created <= end)].copy()
    o["fase"] = _fase(cfg, o.Status)
    o["eta"] = (end - o.Created).dt.total_seconds() / 86400
    o["in_lavoro"] = ((end - o.cycle_start_ts).dt.total_seconds() / 86400).clip(lower=0)
    o["rosso"] = (o[[f"{p}_flag" for p in metrics.PHASES]] == 2).any(axis=1)
    return o


def epics(it, since=None):
    """Consegne per Epic Link (se la colonna c'e'): quante, quando, tempi tipici e attesa rilascio mediana."""
    cols = ["n", "da", "a", "lead50", "cyc50", "ril50", "titolo"]
    if "Epic Link" not in it:
        return pd.DataFrame(columns=cols)
    v = it[it.valid & it.prod_date.notna() & (it["Epic Link"].fillna("") != "")]
    if since is not None:
        v = v[v.prod_date >= since]
    if not len(v):
        return pd.DataFrame(columns=cols)
    titles = it.drop_duplicates("Key").set_index("Key").Summary
    g = v.groupby("Epic Link").agg(n=("Key", "size"), da=("prod_date", "min"), a=("prod_date", "max"),
                                   lead50=("lead_days", "median"), cyc50=("cycle_days", "median"), ril50=("release_days", "median"))
    g["titolo"] = [titles.get(k, "") for k in g.index]
    return g.sort_values("n", ascending=False)


def exclusion_effect(df, ev, cfg, end):
    """Confronto della Sintesi ricalcolato senza exclude_keys: quanto avrebbero pesato gli item esclusi."""
    if not cfg.get("exclude_keys"):
        return None
    c0 = {**cfg, "exclude_keys": []}
    return metrics.compare_periods(metrics.phases(metrics.prepare(df, ev, c0), ev, c0, end), c0, end)


def max_same_minute(it, ev, cfg):
    """Il gruppo piu' grande di item entrati nello stesso stato nello stesso minuto, nel periodo (operazioni di massa)."""
    e = ev[ev.Key.isin(it[metrics.in_scope(it, cfg)].Key)]
    p_start = metrics.period_bounds(cfg)[0]
    if p_start is not None:
        e = e[e.ts >= p_start]
    if not len(e):
        return None
    g = e.groupby(["ts", "status"]).Key.nunique()
    (ts, status), n = g.idxmax(), int(g.max())
    return {"ts": ts, "status": status, "n": n}


def actions(f, o):
    """Attivita' proposte: regole in ordine di priorita', ognuna attiva solo se i dati la giustificano.
    f = fatti calcolati in render(); restituisce al massimo o['max_actions'] dict con titolo, perche', proposta."""
    out = []
    if f["reentries"]:
        out.append({"titolo": "Escludere le bonifiche ancora nei dati e concordare regole d'uso di Jira",
                    "perche": f"{f['reentries']} attività risultano consegnate ma sono solo rientrate in Done da Done dopo oltre "
                              f"{o['reentry_gap_days']} giorni: alzano lead time e consegne senza essere lavoro nuovo.",
                    "proposta": "Verificarle e aggiungerle a `exclude_keys`; concordare che le modifiche di massa in Jira "
                                "vengano annunciate prima, così da escluderle subito."})
    if f["release_share_recent"] > o["release_share_max"] or f["slow_epics"]:
        ep = "; ".join(f["slow_epics"][:3])
        out.append({"titolo": "Rilasciare i progetti a lotti piccoli",
                    "perche": f"L'attesa rilascio vale fino al {_p(f['release_share_recent'])} del tempo negli ultimi mesi "
                              f"(prima {_p(f['release_share_base'])})." + (f" Epic con lunga attesa di rilascio: {ep}." if ep else ""),
                    "proposta": "Rilasciare man mano che le attività sono pronte (rilasci intermedi, feature toggle) invece di "
                                f"attendere la fine del progetto. Obiettivo: attesa rilascio sotto il {_p(o['release_share_max'])} del tempo totale."})
    if f["release_open"] >= o["uat_open_min"]:
        out.append({"titolo": "Dare un responsabile e un tempo massimo al collaudo",
                    "perche": f"{f['release_open']} attività aperte sono ferme in attesa di rilascio ({f['release_open_detail']})"
                              + (f", {f['release_open_noepic']} senza epic" if f["release_open_noepic"] else "") + ".",
                    "proposta": "Un referente per collaudo e rilascio, uno slot fisso"
                                + (f" prima del rilascio del {f['release_day']}" if f["release_day"] else "")
                                + " e un tempo massimo concordato (es. 5 giorni lavorativi) per le attività in attesa."})
    if f["wip_best"] and f["wip_worst"] and f["wip_worst"][2] >= 2 * f["wip_best"][2]:
        (bm, bw, bl), (wm, ww, wl) = f["wip_best"], f["wip_worst"]
        out.append({"titolo": "Fissare un limite al lavoro in corso",
                    "perche": f"Con {ww} attività in corso ({wm}) servivano circa {wl:.0f} giorni per smaltirle al ritmo di quel mese; "
                              f"con {bw} ({bm}) ne bastavano {bl:.0f}. Meno lavoro iniziato insieme, tempi più brevi.",
                    "proposta": f"Un limite di circa {int(round(bw, -1))} attività in corso (eventualmente diviso per classe), "
                                "con la regola «si inizia solo quando si finisce»."})
    if f["red_open"] or f["long_wip"]:
        cand = ", ".join(f["long_wip_keys"][:4])
        out.append({"titolo": "Rivedere ogni due settimane le attività ferme",
                    "perche": f"{f['red_open']} attività aperte sono già oltre il P{f['p_alert']} della propria classe e "
                              f"{f['long_wip']} sono in lavorazione o in attesa di rilascio da oltre {o['long_wip_days']} giorni.",
                    "proposta": "Partire dal filtro «Solo anomalie / Solo aperti» del tab Issue: per ogni attività decidere se "
                                "sbloccarla, dividerla o chiuderla." + (f" Primi candidati: {cand}." if cand else "")})
    if f["stale_queue"] >= 10 or f["backlog_growth"] > 0.10:
        out.append({"titolo": "Pulire il backlog",
                    "perche": f"{f['queue']} attività in coda, {f['stale_queue']} da oltre {o['stale_queue_days']} giorni"
                              + (f"; la coda è cresciuta del {_p(f['backlog_growth'])} nell'ultimo mese" if f["backlog_growth"] > 0.10 else "") + ".",
                    "proposta": f"Chiudere (Won't Do) o ripianificare con una data ciò che è in coda da oltre {o['stale_queue_days']} giorni; "
                                "ripetere la revisione ogni mese."})
    if f["created_recent"] > f["delivered_recent"]:
        out.append({"titolo": "Regolare gli ingressi",
                    "perche": f"Negli ultimi 2 mesi sono state create {f['created_recent']} attività e consegnate {f['delivered_recent']}: "
                              "il lavoro si accumula.",
                    "proposta": "Concordare quante attività nuove si accettano a settimana, in base al ritmo di consegna misurato."})
    if f["wide_classes"]:
        out.append({"titolo": "Rendere più prevedibili le classi con tempi dispersi",
                    "perche": "Il P85 è più di " + _f(o["spread_wide"]) + " volte il P50 per: " + ", ".join(f["wide_classes"]) + ".",
                    "proposta": "Dividere le attività grandi in parti piccole e dare una capacità minima fissa a queste classi, "
                                "così le date promesse diventano affidabili."})
    if f["expedite_p85"] == f["expedite_p85"] and f["expedite_p85"] > o["expedite_p85_max"]:
        out.append({"titolo": "Rivedere l'uso della classe Expedite",
                    "perche": f"L'85% delle urgenze arriva in produzione entro {_f(f['expedite_p85'])} giorni: oltre i "
                              f"{o['expedite_p85_max']} previsti per una corsia veloce.",
                    "proposta": "Riservare Expedite alle vere urgenze, con un numero massimo contemporaneo."})
    return out[:o["max_actions"]]


# ---------------------------------------------------------------------------------------------------------------
# Testo
# ---------------------------------------------------------------------------------------------------------------

def render(cfg, today, end, df, it, ev, fc, cyc, snap, cmp, src, report_name, path):
    o = _opts(cfg)
    tol = cfg.get("trend_tolerance", 0.10)
    p_alert = cfg.get("anomaly_percentiles", [85, 95])[1]
    mt = monthly(it, ev, cfg, end)
    v = it[it.valid & it.prod_date.notna()]
    op = open_items(it, cfg, end)
    queue = op[op.fase == "coda"]
    w0 = cfg["windows_months"][0]
    fw, cw = fc[fc.window == w0].set_index("class"), cyc[cyc.window == w0].set_index("class")
    tl, tc = fw.loc["TUTTI"], cw.loc["TUTTI"]
    since = pd.Timestamp(pd.to_datetime(tl["from"], format="%d/%m/%Y"))
    ep_all, ep_win = epics(it), epics(it, since)
    re_ = reentries(it, ev, cfg, o["reentry_gap_days"])
    d = max(cfg.get("recent_days", [30, 60]))
    cm = cmp.set_index("metric")
    verdict = metrics.verdict(cmp, cfg)[0]
    L = []
    title = f"Analisi del flusso{' · ' + str(cfg['project_name']) if cfg.get('project_name') else ''}"
    p_start, _ = metrics.period_bounds(cfg)
    L += [f"# {title}", "",
          f"Dati dal {(p_start or v.prod_date.min()):%d/%m/%Y} al {end:%d/%m/%Y} · generata il {today:%d/%m/%Y} · "
          f"report: `{report_name}`", ""]
    if not len(mt) or not len(v):
        L += ["Nessuna consegna valida nel periodo: analisi non disponibile."]
        _write(path, L)
        return

    last, prev = mt.index[-1], (mt.index[-2] if len(mt) > 1 else None)
    r_last = mt.loc[last]
    lname = _mese(last) + (f" (fino al {end:%d/%m})" if r_last.partial else "")

    # --- In sintesi
    base = mt.iloc[:-1]
    shares = {"attesa in coda": r_last.wait_share, "lavoro": r_last.work_share, "attesa rilascio": r_last.release_share}
    top_phase = max(shares, key=lambda k: -1 if shares[k] != shares[k] else shares[k])
    L += ["## In sintesi", ""]
    rec = " (massimo del periodo)" if len(base) and r_last.consegne > base.consegne.max() else ""
    L.append(f"- **Consegne:** {lname} ha {int(r_last.consegne)} attività in produzione, {_f(r_last.a_sett)} a settimana{rec}"
             + (f"; media dei mesi precedenti {_f(base.a_sett.mean())} a settimana." if len(base) else "."))
    if prev is not None:
        L.append(f"- **Lavoro in corso:** a fine mese {_dir(r_last.wip, mt.loc[prev].wip, tol, unit=' attività', nd=0)}.")
    L.append(f"- **Rispetto allo storico** (ultimi {d} giorni): lead time P50 {_dir(cm.loc['lead_p50', f'r{d}'], cm.loc['lead_p50', 'hist'], tol, unit=' gg')}; "
             f"verdetto del report: **{verdict}**.")
    L.append(f"- **Dove va il tempo** in {_mese(last)}: la quota maggiore è {top_phase} ({_p(shares[top_phase])} del tempo totale).")
    L.append("")

    # --- KPI standard (stessi della Sintesi del report)
    std = kpi.standard_kpis(it, ev, cfg, end)
    icon = {"verde": "🟢", "giallo": "🟡", "rosso": "🔴"}
    L += ["## KPI standard", "",
          f"Ultimi {cfg.get('kpi_months', 3)} mesi ({std['last_txt']}) rispetto ai {cfg.get('kpi_prev_months', cfg.get('kpi_months', 3))} "
          f"precedenti ({std['prev_txt']}). Stato = valore rispetto alle soglie; trend = variazione rispetto al riferimento.", "",
          _table(["KPI", "Valore", "Riferimento", "Variazione", "Trend", "Stato", "Cosa significa", "Note"],
                 [[k["label"], f"{k['value_txt']} {k['unit']}".strip(), f"{k['ref_txt']} {k['unit']}".strip(), k["delta_txt"],
                   k["trend"], f"{icon.get(k['status'], '')} {k['status']}".strip(), k["definition"],
                   f"{k['calc']} {k['trend_txt']} {k['status_txt']}"]
                  for k in std["kpis"]]), ""]
    cnt = {s: [k["label"] for k in std["kpis"] if k["status"] == s] for s in ("verde", "giallo", "rosso")}
    L += [f"**{len(cnt['verde'])} verdi, {len(cnt['giallo'])} gialli, {len(cnt['rosso'])} rossi.**"
          + (f" Rossi: {', '.join(cnt['rosso'])}." if cnt["rosso"] else ""), ""]

    # --- 1. Quanto ci mettiamo
    L += ["## 1. Quanto ci mettiamo?", "",
          f"Sulle consegne degli ultimi {w0} mesi (dal {tl['from']}):",
          f"- **Lead time** (dalla creazione alla produzione): P50 {_f(tl.P50)} giorni, P85 {_f(tl.P85)}.",
          f"- **Cycle time** (dall'avvio alla produzione): P50 {_f(tc.P50)} giorni, P85 {_f(tc.P85)}.",
          f"- **Presa in carico** (dalla creazione al primo stato di lavoro): P50 {_f(tl.pickup_P50)} giorni.", ""]
    if len(queue):
        L.append(f"In coda oggi ci sono {len(queue)} attività, con età P50 {_i(queue.eta.median())} giorni e P85 {_i(queue.eta.quantile(.85))}.")
        if tl.pickup_P50 == tl.pickup_P50 and tl.pickup_P50 < 2 and queue.eta.median() > 30:
            L.append("Le attività sono quindi di due tipi: la maggior parte parte quasi appena creata (lead time vicino al cycle "
                     "time), mentre quelle in coda restano parcheggiate a lungo.")
        L.append("")
    drivers = []
    if len(snap):
        sb = snap.pivot_table(index="month", columns="class", values="backlog", aggfunc="sum").fillna(0)
        for c in sb.columns:
            a, b = sb[c].iloc[0], sb[c].iloc[-1]
            if a >= 10 and b <= a * 0.7 and c in fw.index and fw.loc[c].P85 > tl.P85:
                drivers.append(f"**backlog {c} smaltito** (in coda da {a:.0f} a {b:.0f}, lead time P85 {_f(fw.loc[c].P85)} giorni): "
                               "consegnare lavoro vecchio alza il lead time, ma è un segnale positivo")
    for k, r in ep_win[(ep_win.n >= o["min_epic_items"]) & (ep_win.lead50 > tl.P85)].iterrows():
        drivers.append(f"**lotto di progetto {_key(cfg, k)}** «{r.titolo}»: {r.n} attività con lead time P50 {_f(r.lead50)} giorni")
    if drivers:
        L += ["**Cosa allunga la coda dei tempi (P85):**"] + [f"- {x}." for x in drivers] + [""]

    # --- 2. Quando arrivera'
    wd = cfg["within_days"]
    rows, wide, tight = [], [], []
    for c, r in fw.iterrows():
        ratio = r.P85 / r.P50 if r.P50 and r.P50 == r.P50 else np.nan
        lab = "-" if ratio != ratio else "stretta" if ratio <= 2.5 else "media" if ratio < o["spread_wide"] else "ampia"
        name = "Tutti" if c == "TUTTI" else c
        if r.reliable and c != "TUTTI":
            (wide if lab == "ampia" else tight if lab == "stretta" else []).append(f"{name} (P85 {ratio:.1f} volte il P50)")
        rows.append([name + ("" if r.reliable else " ⚠"), int(r.n), _f(r.P50), _f(r.P85)]
                    + [_p(r[f"<= {n}g"]) for n in wd] + [f"{lab} (×{_f(ratio)})" if ratio == ratio else "-"])
    L += ["## 2. Quando arriverà?", "", f"Lead time per classe, consegne degli ultimi {w0} mesi.", "",
          _table(["Classe", "N", "P50", "P85"] + [f"Entro {n} gg" for n in wd] + ["Dispersione P85/P50"], rows), ""]
    if any(not r.reliable for _, r in fw.iterrows()):
        L += [f"⚠ meno di {cfg['min_samples']} consegne: stima indicativa.", ""]
    if tight:
        L.append(f"- **Prevedibili:** {', '.join(tight)}: i tempi sono stretti attorno al valore tipico.")
    if wide:
        L.append(f"- **Poco prevedibili:** {', '.join(wide)}: difficile promettere una data senza un margine ampio.")
    L.append("")

    # --- 3. Dove si ferma il lavoro
    L += ["## 3. Dove si ferma il lavoro?", "", "Quota del tempo totale per fase, sulle consegne di ogni mese.", "",
          _table(["Mese", "Coda", "Lavoro", "Attesa rilascio", "Attesa rilascio mediana (gg)"],
                 [[_mese(m), _p(r.wait_share), _p(r.work_share), _p(r.release_share), _f(r.ril50)] for m, r in mt.iterrows()]), ""]
    slow = ep_all[(ep_all.n >= o["min_epic_items"]) & (ep_all.ril50 > o["release_wait_epic_days"])]
    if len(slow):
        L.append("**Lotti di progetto rimasti fermi in attesa di rilascio:**")
        L += [f"- {_key(cfg, k)} «{r.titolo}»: {r.n} attività, attesa rilascio mediana **{_f(r.ril50)} giorni**, "
              f"consegnate dal {r.da:%d/%m} al {r.a:%d/%m}." for k, r in slow.iterrows()]
        L.append("")
    rw = op[op.fase == "attesa rilascio"]
    rw_noepic = int((rw["Epic Link"].fillna("") == "").sum()) if "Epic Link" in rw else 0
    rw_detail = ", ".join(f"{n} in {s}" for s, n in rw.Status.value_counts().items())
    if len(rw):
        L += [f"**Oggi in attesa di rilascio:** {len(rw)} attività ({rw_detail})"
              + (f", di cui {rw_noepic} senza epic." if rw_noepic else "."), ""]
    wday = v.prod_date.dt.dayofweek.value_counts(normalize=True)
    release_day = WEEKDAYS[wday.idxmax()] if len(wday) and wday.max() >= 0.4 else None
    if release_day:
        L += [f"**Rilascio a cadenza fissa:** il {_p(wday.max())} delle consegne avviene di {release_day}.", ""]

    # --- 4. Stiamo migliorando
    L += ["## 4. Stiamo migliorando?", "",
          _table(["Mese", "Consegne", "A settimana", "Lead P50", "Lead P85", "Cycle P50", "Entro 30 gg"],
                 [[_mese(m) + (" *" if r.partial else ""), int(r.consegne), _f(r.a_sett), _f(r.lead50), _i(r.lead85), _f(r.cyc50), _p(r.entro30)]
                  for m, r in mt.iterrows()]), ""]
    if r_last.partial:
        L += [f"\\* mese parziale, dati fino al {end:%d/%m/%Y}.", ""]
    if prev is not None:
        rp = mt.loc[prev]
        L += [f"**{_mese(last)} rispetto a {_mese(prev)}:**",
              f"- consegne a settimana {_dir(r_last.a_sett, rp.a_sett, tol, low_is_good=False)};",
              f"- lead time P50 {_dir(r_last.lead50, rp.lead50, tol, unit=' gg')}, P85 {_dir(r_last.lead85, rp.lead85, tol, unit=' gg')};",
              f"- cycle time P50 {_dir(r_last.cyc50, rp.cyc50, tol, unit=' gg')};",
              f"- consegne entro 30 giorni dal {_p(rp.entro30)} al {_p(r_last.entro30)}."]
        if "Epic Link" in v:
            ne = v[v["Epic Link"].fillna("") == ""]
            ne_m = ne.prod_date.dt.to_period("M")
            a, b = ne[ne_m == prev].cycle_days.median(), ne[ne_m == last].cycle_days.median()
            if a == a and b == b:
                L.append(f"- fuori dai progetti (senza epic): cycle time P50 {_dir(b, a, tol, unit=' gg')}.")
        L.append("")
    old = mt.iloc[:-2]
    if len(old):
        L.append(f"**Rispetto a {_mese(old.index[0])}–{_mese(old.index[-1])}:** lead time P50 mediano dei mesi "
                 f"{_f(old.lead50.median())} giorni, contro {_f(r_last.lead50)} di {_mese(last)}.")
    L += [f"Verdetto del report (ultimi {d} giorni contro lo storico): **{verdict}**.", ""]

    # --- 5. Carico di lavoro
    L += ["## 5. Come siamo messi a carico di lavoro?", "",
          _table(["Fine mese", "Backlog", "WIP", "Create", "Consegnate", "Tempo implicito nel WIP*"],
                 [[_mese(m), r.backlog, r.wip, r.creati, r.consegne, f"{_i(r.little)} gg"] for m, r in mt.iterrows()]), "",
          "\\*WIP diviso per le consegne al giorno del mese: quanto servirebbe a smaltire il lavoro in corso a quel ritmo "
          "(legge di Little).", ""]
    rec2, old2 = mt.iloc[-2:], mt.iloc[:-2]
    if len(old2):
        L.append(f"- **{_mese(old2.index[0])}–{_mese(old2.index[-1])}:** create {int(old2.creati.sum())}, consegnate {int(old2.consegne.sum())}.")
    L.append(f"- **Ultimi 2 mesi:** create {int(rec2.creati.sum())}, consegnate {int(rec2.consegne.sum())}"
             + (": il lavoro si accumula." if rec2.creati.sum() > rec2.consegne.sum() else ": si consegna più di quanto entra."))
    pk = mt.wip.idxmax()
    L.append(f"- **Picco di lavoro in corso:** {int(mt.loc[pk].wip)} attività a fine {_mese(pk)}"
             + (f" (tempo implicito {_i(mt.loc[pk].little)} giorni)." if mt.loc[pk].little == mt.loc[pk].little else "."))
    if len(snap) and prev is not None:
        sb = snap.pivot_table(index="month", columns="class", values="backlog", aggfunc="sum").fillna(0)
        if len(sb) > 1:
            ch = (sb.iloc[-1] - sb.iloc[-2]).sort_values(ascending=False)
            up = [f"{c} da {sb.iloc[-2][c]:.0f} a {sb.iloc[-1][c]:.0f}" for c, x in ch.items() if x >= 5][:3]
            L.append(f"- **Coda nell'ultimo mese:** da {int(mt.loc[prev].backlog)} a {int(r_last.backlog)}"
                     + (f"; crescono {', '.join(up)}." if up else "."))
    L.append("")

    # --- Dati significativi
    L += ["## Dati significativi", ""]
    ex = exclusion_effect(df, ev, cfg, end)
    bulk = it.attrs.get("bulk_days", [])
    L.append("**Pulizie e operazioni di massa**")
    if ex is not None:
        e = ex.set_index("metric")
        L.append(f"- {len(cfg['exclude_keys'])} attività escluse da configurazione (`exclude_keys`): senza esclusione il lead time "
                 f"P85 degli ultimi {d} giorni sarebbe {_f(e.loc['lead_p85', f'r{d}'])} giorni invece di {_f(cm.loc['lead_p85', f'r{d}'])}.")
    if bulk:
        L.append(f"- Giorni di chiusura massiva ignorati (oltre {cfg['bulk_done_threshold']} ingressi in Done): {', '.join(bulk)}.")
    if len(re_):
        days_ = re_.groupby(re_.ts.dt.date).size().sort_values(ascending=False)
        L.append(f"- ⚠ **{len(re_)} attività ancora conteggiate come consegne sono solo rientrate in Done da Done** dopo oltre "
                 f"{o['reentry_gap_days']} giorni (soprattutto il " + ", ".join(f"{d_:%d/%m} ({n})" for d_, n in days_.head(3).items())
                 + "). Valutare di aggiungerle a `exclude_keys`: " + ", ".join(re_.Key.head(20)) + ("…" if len(re_) > 20 else "") + ".")
    ms = max_same_minute(it, ev, cfg)
    if ms:
        L.append(f"- Gruppo più grande di attività con lo stesso cambio di stato nello stesso minuto: {ms['n']} in {ms['status']} "
                 f"il {ms['ts']:%d/%m/%Y %H:%M}.")
    if ex is None and not bulk and not len(re_):
        L.append("- Nessuna pulizia o rientro in Done sospetto nel periodo.")
    L.append("")
    if prev is not None:
        fl = wip_flows(it, ev, cfg, min(prev.end_time, end), end)
        L += [f"**Lavoro in corso in {_mese(last)}: da {fl['wip_start']} a {fl['wip_end']}**",
              f"- delle {fl['wip_start']} attività in corso a inizio mese: {fl['consegnati']} consegnate, "
              f"{fl['chiusi_senza_consegna']} chiuse senza consegna, {fl['tornati_in_coda']} tornate in coda;",
              f"- {fl['avviati']} attività avviate nel mese e ancora in corso a fine mese; {fl['nuovi_in_coda']} nuove rimaste in coda.",
              "- La variazione è dovuta a consegne reali, non a una pulizia." if fl["consegnati"] > max(fl["tornati_in_coda"] + fl["chiusi_senza_consegna"], 0) * 3
              else "- ⚠ Una parte rilevante della variazione viene da chiusure senza consegna o ritorni in coda: verificare.", ""]
    disc = discarded(it, ev, cfg)
    disc = disc[disc.closed_ts.notna() & (disc.closed_ts >= (p_start or disc.closed_ts.min())) & (disc.closed_ts <= end)]
    if len(disc):
        per_day = disc.groupby(disc.closed_ts.dt.date).size()
        L += ["**Attività scartate** (chiuse senza consegna: Won't Do, Duplicate…)",
              f"- {len(disc)} nel periodo; mese con più scarti: {_mese(mt.scartati.idxmax())} ({int(mt.scartati.max())}); "
              f"ultimo mese: {int(r_last.scartati)}.",
              f"- Massimo in un giorno: {int(per_day.max())} ({per_day.idxmax():%d/%m})"
              + (": nessuna chiusura in blocco." if per_day.max() < 20 else ": ⚠ possibile chiusura in blocco, verificare."), ""]
    red = op[op.rosso]
    long_wip = op[(op.fase != "coda") & (op.in_lavoro > o["long_wip_days"])].sort_values("in_lavoro", ascending=False)
    stale = queue[queue.eta > o["stale_queue_days"]]
    L.append("**Attività lente ancora aperte**")
    if len(red):
        L.append(f"- {len(red)} attività aperte oltre il P{p_alert} della propria classe: "
                 + ", ".join(f"{n} in {k}" for k, n in red.fase.value_counts().items()) + ".")
    if len(long_wip):
        L.append(f"- In lavorazione o in attesa di rilascio da oltre {o['long_wip_days']} giorni ({len(long_wip)}):")
        L += [f"  - {_key(cfg, r.Key)} «{r.Summary}» ({r.Status}): {r.in_lavoro:.0f} giorni" for _, r in long_wip.head(8).iterrows()]
    if len(queue):
        before = int((queue.Created < p_start).sum()) if p_start is not None else 0
        L.append(f"- Coda: {len(stale)} attività da oltre {o['stale_queue_days']} giorni"
                 + (f", {before} create prima dell'inizio del periodo" if before else "")
                 + f"; la più vecchia ha {queue.eta.max():.0f} giorni.")
    L.append("")
    exp = fw.loc["Expedite"] if "Expedite" in fw.index else None
    if exp is not None and exp.n:
        L += ["**Expedite**",
              f"- {int(exp.n)} urgenze negli ultimi {w0} mesi: P50 {_f(exp.P50)} giorni, P85 {_f(exp.P85)}, "
              f"entro 30 giorni {_p(exp['<= 30g']) if '<= 30g' in exp else '-'}"
              + (": la corsia veloce funziona." if exp.P85 <= o["expedite_p85_max"] else ": ⚠ la corsia veloce non è veloce."), ""]

    # --- Attivita' proposte
    best = mt[mt.consegne > 0].little.dropna()
    facts = {
        "reentries": len(re_),
        "release_share_recent": mt.release_share.iloc[-2:].max(),
        "release_share_base": mt.release_share.iloc[:-2].median() if len(mt) > 2 else np.nan,
        "slow_epics": [f"{k} «{r.titolo}» ({_f(r.ril50)} gg)" for k, r in slow.iterrows()],
        "release_open": len(rw), "release_open_detail": rw_detail, "release_open_noepic": rw_noepic, "release_day": release_day,
        "wip_best": (_mese(best.idxmin()), int(mt.loc[best.idxmin()].wip), best.min()) if len(best) else None,
        "wip_worst": (_mese(best.idxmax()), int(mt.loc[best.idxmax()].wip), best.max()) if len(best) else None,
        "red_open": len(red), "long_wip": len(long_wip), "p_alert": p_alert,
        "long_wip_keys": [_key(cfg, k) for k in long_wip.Key],
        "queue": len(queue), "stale_queue": len(stale),
        "backlog_growth": (r_last.backlog - mt.loc[prev].backlog) / mt.loc[prev].backlog if prev is not None and mt.loc[prev].backlog else 0,
        "created_recent": int(rec2.creati.sum()), "delivered_recent": int(rec2.consegne.sum()),
        "wide_classes": [x.split(" (")[0] for x in wide],
        "expedite_p85": exp.P85 if exp is not None and exp.reliable else np.nan,
    }
    acts = actions(facts, o)
    L += [f"## {len(acts)} attività per migliorare il flusso" if acts else "## Attività per migliorare il flusso", ""]
    if not acts:
        L += ["Nessuna regola di miglioramento attivata dai dati di questo periodo.", ""]
    for n, a in enumerate(acts, 1):
        L += [f"{n}. **{a['titolo']}.** {a['perche']}", f"   *Proposta:* {a['proposta']}", ""]

    # --- Limiti
    L += ["## Cosa non si può affermare dai soli dati",
          "- **Le cause.** I dati mostrano dove e quanto il lavoro si ferma, non perché: dipendenze, collaudo e finestre di "
          "rilascio vanno verificati con i referenti.",
          f"- **Le attività ancora aperte.** I tempi sono calcolati sulle consegne: le {len(red)} attività lente ancora aperte "
          "entreranno nei numeri solo quando verranno chiuse.",
          "- **I valori mensili per classe.** Con poche consegne al mese oscillano molto: meglio leggerli sull'intero periodo.",
          "- **Lo stato delle attività aperte** è quello dell'export (" + f"{today:%d/%m/%Y}" + "), non necessariamente quello "
          f"alla fine dei dati ({end:%d/%m/%Y}).", "",
          f"*Analisi generata automaticamente da flowmetrics con regole e soglie in configurazione (blocco `analysis`). "
          f"Gli avvisi della verifica sugli export sono {len(src['warnings'])}.*"]
    _write(path, L)


def _write(path, lines):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines).rstrip() + "\n")
