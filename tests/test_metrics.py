from pathlib import Path
import pandas as pd, yaml
from flowmetrics import load, metrics

with open(Path(__file__).resolve().parent.parent / "config.yaml", encoding="utf-8") as _f:
    CFG = yaml.safe_load(_f)
CFG["bulk_done_threshold"] = 2
CFG.pop("period_start", None); CFG.pop("period_end", None)   # i test usano date di gennaio
CFG["kpi_months"] = CFG["kpi_prev_months"] = 3               # i test KPI esistenti assumono 3 + 3 mesi


def _df(rows):
    cols = ["Key", "Issue Type", "Summary", "Status", "Created", "Resolution", "Resolved", "'->To Do", "'->In Progress", "'->Done"]
    return pd.DataFrame([dict(zip(cols, r)) for r in rows])


def _prep(rows):
    d = _df(rows)
    d["Created"] = pd.to_datetime(d["Created"]); d["Resolved"] = pd.NaT
    return metrics.prepare(d, load.events(d), CFG)


def test_lead_time_giorni_calendario():
    it = _prep([("A-1", "Bug", "x", "Done", "2026-01-01 00:00", "Done", "", "2026-01-01 00:00", "2026-01-02 00:00", "2026-01-11 00:00")])
    assert it.loc[0, "lead_days"] == 10 and it.loc[0, "valid"]
    assert it.loc[0, "pickup_days"] == 1


def test_bonifica_ignorata_e_ultimo_done():
    rows = [("A-1", "Bug", "x", "Done", "2026-01-01 00:00", "Done", "", "", "", "2026-01-05 00:00,2026-03-01 00:00"),
            ("A-2", "Bug", "x", "Done", "2026-01-01 00:00", "Done", "", "", "", "2026-03-01 00:00"),
            ("A-3", "Bug", "x", "Done", "2026-01-01 00:00", "Done", "", "", "", "2026-03-01 00:00")]
    it = _prep(rows).set_index("Key")
    assert it.loc["A-1", "lead_days"] == 4          # 2026-03-01 e' bonifica (3 ingressi > soglia 2)
    assert it.loc["A-2", "excl_reason"] == "chiuso solo da bonifica"


def test_filtri_tipo_e_resolution():
    it = _prep([("A-1", "Epic", "x", "Done", "2026-01-01 00:00", "Done", "", "", "", "2026-01-05 00:00"),
                ("A-2", "Bug", "x", "Done", "2026-01-01 00:00", "Won't Do", "", "", "", "2026-01-08 00:00")]).set_index("Key")
    assert not it.valid.any()


def test_classi():
    it = _prep([("A-1", "Analysis", "x", "Done", "2026-01-01 00:00", "Done", "", "", "", "2026-01-05 00:00")])
    assert it.loc[0, "class"] == "Altro"


def test_mese_senza_consegne_appare_a_zero():
    rows = [("A-1", "Bug", "x", "Done", "2026-01-01 00:00", "Done", "", "", "", "2026-01-10 00:00"),
            ("A-2", "Bug", "x", "Done", "2026-01-01 00:00", "Done", "", "", "", "2026-03-10 00:00")]
    it = _prep(rows)
    today = pd.Timestamp("2026-03-31")
    cfg = {**CFG, "trend_months": 2}
    t = metrics.monthly_throughput(it, cfg, today)
    assert [str(m) for m in t.index] == ["2026-01", "2026-02", "2026-03"]
    assert t.loc[pd.Period("2026-02"), "TOTALE"] == 0
    lt = metrics.monthly_leadtime(it, cfg, today)
    assert list(lt.index) == list(t.index) and lt.loc[pd.Period("2026-02"), "n"] == 0


def test_resolution_di_default_vale_solo_se_in_done():
    it = _prep([("A-1", "Bug", "x", "Done", "2026-01-01 00:00", "Unresolved", "", "", "", "2026-01-05 00:00"),
                ("A-2", "Bug", "x", "Done", "2026-01-01 00:00", "Open", "", "", "", "2026-01-06 00:00"),
                ("A-3", "Bug", "x", "In Progress", "2026-01-01 00:00", "Unresolved", "", "", "", "2026-01-07 00:00"),
                ("A-4", "Bug", "x", "Done", "2026-01-01 00:00", "Won't Do", "", "", "", "2026-01-08 00:00")]).set_index("Key")
    assert it.loc["A-1", "valid"] and it.loc["A-2", "valid"]
    assert it.loc["A-3", "excl_reason"] == "resolution scartata"
    assert it.loc["A-4", "excl_reason"] == "resolution scartata"


def test_periodo_di_estrazione_esclude_consegne_fuori_periodo():
    rows = [("A-1", "Bug", "x", "Done", "2026-01-01 00:00", "Done", "", "", "", "2026-02-10 00:00"),
            ("A-2", "Bug", "x", "Done", "2026-01-01 00:00", "Done", "", "", "", "2026-04-10 00:00"),
            ("A-3", "Bug", "x", "Done", "2026-01-01 00:00", "Done", "", "", "", "2026-06-10 00:00")]
    cfg = {**CFG, "period_start": "2026-04-01", "period_end": "2026-05-31", "trend_months": 12}
    d = _df(rows); d["Created"] = pd.to_datetime(d["Created"]); d["Resolved"] = pd.NaT
    it = metrics.prepare(d, load.events(d), cfg).set_index("Key")
    assert it.loc["A-1", "excl_reason"] == "fuori periodo" and it.loc["A-3", "excl_reason"] == "fuori periodo"
    assert it.loc["A-2", "valid"]
    today = pd.Timestamp("2026-10-07")
    t = metrics.monthly_throughput(it.reset_index(), cfg, today)
    assert [str(m) for m in t.index] == ["2026-04", "2026-05"]


def test_cycle_time_da_primo_in_progress():
    it = _prep([("A-1", "Bug", "x", "Done", "2026-01-01 00:00", "Done", "", "2026-01-01 00:00", "2026-01-02 00:00,2026-01-04 00:00", "2026-01-11 00:00"),
                ("A-2", "Bug", "x", "Done", "2026-01-01 00:00", "Done", "", "2026-01-01 00:00", "", "2026-01-12 00:00")]).set_index("Key")
    assert it.loc["A-1", "cycle_days"] == 9 and it.loc["A-1", "lead_days"] == 10
    assert pd.isna(it.loc["A-2", "cycle_days"])      # mai passato da In Progress: nessun cycle time


def test_expedite_da_label():
    d = _df([("A-1", "Bug", "x", "Done", "2026-01-01", "Done", "", "", "", "2026-01-05"),
             ("A-2", "Bug", "x", "Done", "2026-01-01", "Done", "", "", "", "2026-01-05")])
    d["Labels"] = ["web, Expedite", "web"]
    assert list(metrics.classify(d, CFG)) == ["Expedite", "Bug"]
    assert list(metrics.classify(d.drop(columns="Labels"), CFG)) == ["Bug", "Bug"]    # senza colonna Labels non si distingue


def test_story_con_epic_da_epic_link():
    d = _df([("A-1", "Story", "x", "Done", "2026-01-01", "Done", "", "", "", "2026-01-05"),
             ("A-2", "Story", "x", "Done", "2026-01-01", "Done", "", "", "", "2026-01-05"),
             ("A-3", "Story", "x", "Done", "2026-01-01", "Done", "", "", "", "2026-01-05")])
    d["Epic Link"] = ["PROJ-1", "", "PROJ-1"]
    d["Labels"] = ["", "", "expedite"]
    assert list(metrics.classify(d, CFG)) == ["Story (con Epic)", "Story (senza Epic)", "Expedite"]


def _df2(rows, statuses):
    cols = ["Key", "Issue Type", "Summary", "Status", "Created", "Resolution", "Resolved"] + ["'->" + s for s in statuses]
    d = pd.DataFrame([dict(zip(cols, r)) for r in rows])
    d["Created"] = pd.to_datetime(d["Created"]); d["Resolved"] = pd.NaT
    return d


def test_fasi_attesa_lavoro_rilascio():
    st = ["To Do", "In Progress", "Ready for Release", "Done"]
    d = _df2([("A-1", "Bug", "x", "Done", "2026-01-01 00:00", "Done", "", "2026-01-01 00:00", "2026-01-03 00:00", "2026-01-08 00:00", "2026-01-10 00:00"),
              ("A-2", "Bug", "x", "In Progress", "2026-01-01 00:00", "Unresolved", "", "2026-01-01 00:00", "2026-01-04 00:00", "", "")], st)
    ev = load.events(d)
    it = metrics.phases(metrics.prepare(d, ev, CFG), ev, CFG, pd.Timestamp("2026-01-20")).set_index("Key")
    assert list(it.loc["A-1", ["wait_days", "work_days", "release_days", "flow_days"]]) == [2, 5, 2, 9]
    assert it.loc["A-1", "flow_days"] == it.loc["A-1", "lead_days"]
    assert list(it.loc["A-2", ["wait_days", "work_days", "release_days"]]) == [3, 16, 0]   # aperto: lavoro fino a fine dati


def test_rientro_in_coda_conta_come_attesa():
    st = ["To Do", "In Progress", "Done"]
    d = _df2([("A-1", "Bug", "x", "Done", "2026-01-01 00:00", "Done", "", "2026-01-01 00:00,2026-01-03 00:00", "2026-01-02 00:00,2026-01-05 00:00", "2026-01-06 00:00")], st)
    ev = load.events(d)
    it = metrics.phases(metrics.prepare(d, ev, CFG), ev, CFG, pd.Timestamp("2026-02-01"))
    assert it.loc[0, "wait_days"] == 3 and it.loc[0, "work_days"] == 2


def _many(n, lead_of, start="2026-01-01"):
    """n Bug consegnati, uno al giorno; lead_of(i) = giorni di lavoro dell'i-esimo."""
    rows = []
    for i in range(n):
        done = pd.Timestamp(start) + pd.Timedelta(days=i)
        cr = done - pd.Timedelta(days=lead_of(i))
        f = lambda t: t.strftime("%Y-%m-%d %H:%M")
        rows.append((f"A-{i}", "Bug", "x", "Done", f(cr), "Done", "", f(cr), f(done)))
    d = _df2(rows, ["In Progress", "Done"])
    ev = load.events(d)
    it = metrics.prepare(d, ev, CFG)
    end = metrics.data_end(ev, CFG, pd.Timestamp("2027-01-01"))
    return metrics.flag_phases(metrics.phases(it, ev, CFG, end), CFG), end


def test_anomalie_rispetto_alla_classe():
    it, _ = _many(40, lambda i: 100 if i == 0 else 5 + i % 5)
    it = it.set_index("Key")
    assert it.loc["A-0", "work_days_flag"] == 2 and it.loc["A-0", "work_days_ref"] == 7
    assert (it.drop("A-0").work_days_flag < 2).all()
    assert (it.wait_days_flag == 0).all()          # attesa sempre 0: sotto anomaly_min_days


def test_confronto_periodi_e_verdetto():
    # 120 consegne giornaliere: le ultime 60 con lead time dimezzato -> migliora
    it, end = _many(120, lambda i: 4 if i >= 60 else 8)
    cmp = metrics.compare_periods(it, CFG, end).set_index("metric")
    assert cmp.loc["lead_p50", "hist"] == 8 and cmp.loc["lead_p50", "r60"] == 4
    assert cmp.loc["lead_p50", "trend_r60"] == "meglio" and cmp.loc["throughput", "trend_r60"] == "stabile"
    assert metrics.verdict(cmp.reset_index(), CFG)[0] == "migliora"
    it2, end2 = _many(120, lambda i: 12 if i >= 60 else 8)
    assert metrics.verdict(metrics.compare_periods(it2, CFG, end2), CFG)[0] == "peggiora"


def test_url_di_dettaglio_issue():
    from flowmetrics.report import issue_url
    assert issue_url({"issue_url": "https://jira.example.com/browse/${issueKey}"}, "PROJ-12") == "https://jira.example.com/browse/PROJ-12"
    assert issue_url({"issue_url": "https://x/?q=${issueKey}&k=${issueKey}"}, "A B") == "https://x/?q=A%20B&k=A%20B"
    assert issue_url({}, "PROJ-12") is None and issue_url({"issue_url": ""}, "PROJ-12") is None
    assert issue_url({"issue_url": "javascript:alert(1)//${issueKey}"}, "PROJ-12") is None


def test_nome_mese_leggibile():
    from flowmetrics.report import month_name
    assert month_name(pd.Period("2026-04")) == "Aprile 2026"
    assert month_name("2025-12") == "Dicembre 2025"


def _csv(path, rows):
    cols = ["Key", "Issue Type", "Summary", "Status", "Created", "Resolution", "Resolved", "'->To Do", "'->In Progress", "'->Done"]
    pd.DataFrame([dict(zip(cols, r)) for r in rows]).to_csv(path, index=False)


def test_export_completo_unito_a_quello_delle_consegne(tmp_path):
    _csv(tmp_path / "done.csv", [("A-1", "Bug", "x", "Done", "2026-01-01 00:00", "Done", "", "2026-01-01 00:00", "", "2026-01-05 00:00"),
                                 ("A-2", "Bug", "x", "Done", "2026-01-01 00:00", "Done", "", "2026-01-01 00:00", "", "2026-01-06 00:00")])
    _csv(tmp_path / "all.csv", [("A-1", "Bug", "x", "Done", "2026-01-01 00:00", "Done", "", "2026-01-01 00:00", "", "2026-01-05 00:00"),
                                ("A-3", "Bug", "x", "In Progress", "2026-01-02 00:00", "Unresolved", "", "2026-01-02 00:00", "2026-01-03 00:00", ""),
                                ("A-4", "Bug", "x", "Done", "2026-01-02 00:00", "Done", "", "2026-01-02 00:00", "", "2026-01-09 00:00")])
    df = load.read_exports(tmp_path / "done.csv", tmp_path / "all.csv")
    assert dict(zip(df.Key, df.fonte)) == {"A-1": "entrambi", "A-3": "solo all", "A-4": "solo all", "A-2": "solo done"}
    it = metrics.prepare(df, load.events(df), CFG)
    src = metrics.sources_check(it, CFG, True)
    assert (src["both"], src["only_all"], src["only_done"], src["open"]) == (1, 2, 1, 1)
    assert len(src["warnings"]) == 2          # A-4 consegnata ma assente dalle consegne; A-2 assente dall'export completo
    st = metrics.state_at(it, load.events(df), CFG, pd.Timestamp("2026-01-04"))
    assert st.loc["A-3", "fase"] == "wip"     # l'aperto entra nel backlog/WIP solo grazie all'export completo


def test_senza_export_completo():
    d = _df([("A-1", "Bug", "x", "Done", "2026-01-01 00:00", "Done", "", "", "", "2026-01-05 00:00")])
    d["Created"] = pd.to_datetime(d["Created"]); d["Resolved"] = pd.NaT; d["fonte"] = "solo done"
    src = metrics.sources_check(metrics.prepare(d, load.events(d), CFG), CFG, False)
    assert not src["has_all"] and src["warnings"] == []


def test_kpi_entro_14_giorni_ultimi_3_mesi_contro_precedenti():
    # consegne giornaliere dal 01/01 al 30/06 (fine dati): taglio a 3 mesi = 30/03; prima lead 20 gg (0% entro 14), dopo 5 gg
    it, end = _many(181, lambda i: 5 if i >= 89 else 20)
    cfg = {**CFG, "kpi_classes": ["Bug"]}
    k = {r["class"]: r for r in metrics.within_kpi(it, cfg, end)}
    b = k["Bug"]
    assert b["pct_last"] == 1.0 and b["pct_prev"] == 0.0 and b["delta_pp"] == 100 and b["trend"] == "meglio"
    assert b["n_last"] + b["n_prev"] == k["TUTTI"]["n_last"] + k["TUTTI"]["n_prev"]
    assert b["cum"][-1] == b["n_last"] / (b["n_last"] + b["n_prev"])      # cumulato sull'intero confronto


def test_kpi_deployments_e_progressivo():
    it, end = _many(181, lambda i: 3)
    k = metrics.deploy_kpi(it, CFG, end)
    assert k["now"] == k["last"][-1] and k["before"] == k["prev"][-1]
    assert k["last"] == sorted(k["last"]) and k["trend"] == "stabile"   # progressivo non decrescente, ritmo costante


def test_kpi_backlog_consegne():
    it, end = _many(181, lambda i: 3)
    k = metrics.backlog_kpi(it, metrics_events(it), CFG, end)
    assert k["delivered"]["series"][-1] == k["delivered"]["now"] + k["delivered"]["before"]
    assert k["backlog"]["now"] == 0 and k["wip"]["now"] == 0          # tutto consegnato


def metrics_events(it):
    rows = [(k, "In Progress", c) for k, c in zip(it.Key, it.Created)] + [(k, "Done", p) for k, p in zip(it.Key, it.prod_date)]
    return pd.DataFrame(rows, columns=["Key", "status", "ts"]).sort_values(["Key", "ts"]).reset_index(drop=True)


def test_issue_degli_ultimi_3_mesi():
    # fine dati 30/06: taglio al 30/03. Aperta (anche se vecchia) = si'; chiusa dopo il taglio = si'; chiusa prima = no
    rows = [("A-1", "Bug", "x", "To Do", "2025-10-01 00:00", "Unresolved", "", "2025-10-01 00:00", "", ""),
            ("A-2", "Bug", "x", "Done", "2026-04-01 00:00", "Done", "", "2026-04-01 00:00", "", "2026-05-01 00:00"),
            ("A-3", "Bug", "x", "Done", "2026-01-01 00:00", "Done", "", "2026-01-01 00:00", "", "2026-02-01 00:00"),
            ("A-4", "Bug", "x", "Done", "2026-01-01 00:00", "Done", "", "2026-01-01 00:00", "", "2026-06-30 00:00")]
    d = _df(rows); d["Created"] = pd.to_datetime(d["Created"]); d["Resolved"] = pd.NaT
    ev = load.events(d); it = metrics.prepare(d, ev, CFG)
    m = metrics.recent_mask(it, ev, CFG, pd.Timestamp("2026-06-30"))
    assert dict(zip(it.Key, m)) == {"A-1": True, "A-2": True, "A-3": False, "A-4": True}


def test_kpi_soglie_7_14_30_60():
    it, end = _many(181, lambda i: 3 + i % 50)          # lead da 3 a 52 giorni
    cfg = {**CFG, "kpi_classes": ["Bug"]}
    pct = [metrics.within_kpi(it, cfg, end, "lead_days", n)[0]["pct_last"] for n in (7, 14, 30, 60)]
    assert pct == sorted(pct) and pct[0] < pct[-1] == 1.0      # soglia piu' alta -> quota mai piu' bassa


def test_transizioni_nello_stesso_minuto_finiscono_nello_stato_attuale():
    # colonne in ordine alfabetico come nell'export reale: Done prima di In Progress. Entrambi alle 10:00, Status = Done:
    # l'ultimo evento deve essere Done, altrimenti l'item risulta ancora in lavorazione (e accumula giorni fino a oggi)
    d = pd.DataFrame([{"Key": "A-1", "Issue Type": "Bug", "Summary": "x", "Status": "Done", "Created": pd.Timestamp("2026-01-01"),
                       "Resolution": "Done", "Resolved": pd.NaT, "'->Done": "2026-01-05 10:00",
                       "'->In Progress": "2026-01-02 10:00,2026-01-05 10:00", "'->To Do": "2026-01-01 00:00"}])
    ev = load.events(d)
    assert ev.groupby("Key").tail(1).status.iloc[0] == "Done"
    it = metrics.phases(metrics.prepare(d, ev, CFG), ev, CFG, pd.Timestamp("2026-03-01"))
    assert it.loc[0, "work_days"] == 3 and abs(it.loc[0, "wait_days"] - 1.4167) < 1e-3     # niente giorni fantasma fino a "end"
    assert metrics.state_at(it, ev, CFG, pd.Timestamp("2026-02-01")).loc["A-1", "fase"] == "chiuso"


def test_diagnostica_export_tipi_senza_regola_e_query_non_sovrainsieme(tmp_path):
    cfg = {**CFG, "period_start": "2026-01-01"}
    _csv(tmp_path / "done.csv", [("A-1", "Bug", "x", "Done", "2025-11-01 00:00", "Done", "", "", "", "2026-01-05 00:00")])
    _csv(tmp_path / "all.csv", [("A-2", "Spike", "x", "Done", "2026-01-02 00:00", "", "", "2026-01-02 00:00", "", "")])
    df = load.read_exports(tmp_path / "done.csv", tmp_path / "all.csv")
    src = metrics.sources_check(metrics.prepare(df, load.events(df), cfg), cfg, True)
    w = " ".join(src["warnings"])
    assert "nate prima di 01/01/2026" in w            # la consegna A-1 e' vecchia e manca nell'export completo
    assert "Spike (1)" in w                   # tipo nuovo, senza regola


def test_kpi_periodi_di_durata_diversa_2_mesi_contro_4():
    # consegne costanti (3 al giorno) dal 01/01 al 30/06: taglio a 2 mesi = 30/04, precedente 4 mesi = dal 31/12
    cfg = {**CFG, "kpi_months": 2, "kpi_prev_months": 4}
    it, end = _many(181, lambda i: 3)
    start, cut, e = metrics.kpi_bounds(cfg, end)
    assert (e - cut).days < (cut - start).days
    d = metrics.deploy_kpi(it, cfg, end)
    assert d["before"] > d["now"]                       # il precedente dura il doppio: totale piu' alto...
    assert d["trend"] == "stabile" and abs(d["delta"]) < 0.05   # ...ma il ritmo e' lo stesso
    assert abs(d["per_week"] - d["per_week_before"]) < 1
    b = metrics.backlog_kpi(it, metrics_events(it), cfg, end)["delivered"]
    assert b["trend"] == "stabile" and abs(b["per_week"] - b["per_week_before"]) < 1
