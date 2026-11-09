from pathlib import Path
import pandas as pd, yaml
from flowmetrics import analysis, cli, load, metrics

ROOT = Path(__file__).resolve().parent.parent
with open(ROOT / "config.example.yaml", encoding="utf-8") as _f:
    CFG = yaml.safe_load(_f)
CFG.pop("period_start", None); CFG.pop("period_end", None)


def _df(rows):
    cols = ["Key", "Issue Type", "Summary", "Status", "Created", "Resolution", "Resolved", "'->To Do", "'->In Progress", "'->Done"]
    d = pd.DataFrame([dict(zip(cols, r)) for r in rows])
    d["Created"] = pd.to_datetime(d["Created"]); d["Resolved"] = pd.NaT
    return d


def test_rientro_done_da_done_e_sospetto_solo_senza_stati_intermedi():
    d = _df([
        # chiuso nel 2024, "richiuso" nel 2026 senza passare da altri stati: bonifica
        ("A-1", "Task", "x", "Done", "2024-01-01 00:00", "Done", "", "", "", "2024-02-01 00:00,2026-01-10 00:00"),
        # riaperto (In Progress) e richiuso: lavoro vero
        ("A-2", "Task", "x", "Done", "2024-01-01 00:00", "Done", "", "", "2026-01-05 00:00", "2024-02-01 00:00,2026-01-10 00:00"),
        # due Done a pochi giorni: normale correzione di stato
        ("A-3", "Task", "x", "Done", "2026-01-01 00:00", "Done", "", "", "", "2026-01-05 00:00,2026-01-10 00:00"),
    ])
    ev = load.events(d)
    it = metrics.prepare(d, ev, CFG)
    assert analysis.reentries(it, ev, CFG, 30).Key.tolist() == ["A-1"]
    # gia' escluso da config: non e' piu' valido, quindi non va segnalato di nuovo
    it2 = metrics.prepare(d, ev, {**CFG, "exclude_keys": ["A-1"]})
    assert analysis.reentries(it2, ev, CFG, 30).empty


def test_flussi_wip_distinguono_consegne_ritorni_in_coda_e_avvii():
    d = _df([
        ("A-1", "Bug", "x", "Done", "2026-01-01 00:00", "Done", "", "2026-01-01 00:00", "2026-01-02 00:00", "2026-02-10 00:00"),
        ("A-2", "Bug", "x", "To Do", "2026-01-01 00:00", "", "", "2026-01-01 00:00,2026-02-05 00:00", "2026-01-02 00:00", ""),
        ("A-3", "Bug", "x", "In Progress", "2026-02-03 00:00", "", "", "2026-02-03 00:00", "2026-02-04 00:00", ""),
    ])
    ev = load.events(d)
    it = metrics.prepare(d, ev, CFG)
    f = analysis.wip_flows(it, ev, CFG, pd.Timestamp("2026-01-31 23:59"), pd.Timestamp("2026-02-28 23:59"))
    assert (f["wip_start"], f["wip_end"]) == (2, 1)
    assert (f["consegnati"], f["tornati_in_coda"], f["avviati"], f["chiusi_senza_consegna"]) == (1, 1, 1, 0)


BASE = {"reentries": 0, "release_share_recent": 0.1, "release_share_base": 0.1, "slow_epics": [], "release_open": 0,
        "release_open_detail": "", "release_open_noepic": 0, "release_day": None, "wip_best": ("Maggio 2026", 50, 20.0),
        "wip_worst": ("Giugno 2026", 60, 25.0), "red_open": 0, "long_wip": 0, "p_alert": 95, "long_wip_keys": [],
        "queue": 20, "stale_queue": 0, "backlog_growth": 0.0, "created_recent": 10, "delivered_recent": 20,
        "wide_classes": [], "expedite_p85": 5.0}


def test_nessuna_attivita_proposta_senza_segnali():
    assert analysis.actions(BASE, analysis.DEFAULTS) == []


def test_attivita_proposte_in_ordine_di_priorita_e_al_massimo_cinque():
    f = {**BASE, "reentries": 3, "release_share_recent": 0.4, "release_open": 25, "release_open_detail": "25 in UAT",
         "wip_worst": ("Agosto 2026", 216, 89.0), "red_open": 10, "stale_queue": 30, "created_recent": 50,
         "wide_classes": ["Engine"], "expedite_p85": 40.0}
    acts = analysis.actions(f, analysis.DEFAULTS)
    assert len(acts) == 5
    assert [a["titolo"].split()[0] for a in acts] == ["Escludere", "Rilasciare", "Dare", "Fissare", "Rivedere"]
    assert "circa 50" in acts[3]["proposta"]   # limite WIP = WIP del mese con il flusso migliore


def test_limite_wip_proposto_solo_se_il_tempo_implicito_raddoppia():
    near = {**BASE, "wip_worst": ("Giugno 2026", 70, 39.0)}
    far = {**BASE, "wip_worst": ("Giugno 2026", 70, 40.0)}
    assert not analysis.actions(near, analysis.DEFAULTS)
    assert analysis.actions(far, analysis.DEFAULTS)[0]["titolo"].startswith("Fissare")


def test_cli_genera_report_e_analisi_separati(tmp_path):
    cfg = {**yaml.safe_load(open(ROOT / "config.example.yaml", encoding="utf-8")), "output_dir": str(tmp_path), "project_name": "T"}
    cfg.pop("period_start", None); cfg.pop("period_end", None)   # i dati di esempio sono del 2025
    p = tmp_path / "cfg.yaml"
    p.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    cli.main(["--config", str(p), "--input-completed", str(ROOT / "sample-data-done.csv"),
              "--input-all", str(ROOT / "sample-data-all.csv"), "--today", "2025-12-31"])
    html = list(tmp_path.glob("T_flow_report_*.html")); md = list(tmp_path.glob("T_flow_analysis_*.md"))
    assert len(html) == 1 and len(md) == 1
    text = md[0].read_text(encoding="utf-8")
    for h in ("## In sintesi", "## 1. Quanto ci mettiamo?", "## 2. Quando arriverà?", "## 3. Dove si ferma il lavoro?",
              "## 4. Stiamo migliorando?", "## 5. Come siamo messi a carico di lavoro?", "## Dati significativi",
              "## Cosa non si può affermare dai soli dati"):
        assert h in text
    assert "Analisi del flusso" not in html[0].read_text(encoding="utf-8")   # l'analisi non finisce nel report


def test_attrs_conservati_dopo_phases():
    d = _df([("A-1", "Bug", "x", "Done", "2026-01-01 00:00", "Done", "", "", "", "2026-01-10 00:00")])
    ev = load.events(d)
    it = metrics.prepare(d, ev, {**CFG, "bulk_done_threshold": 0})
    assert metrics.phases(it, ev, CFG, pd.Timestamp("2026-02-01")).attrs["bulk_days"] == ["2026-01-10"]


# --- KPI standard (flowmetrics/kpi.py)
from flowmetrics import kpi


def test_semaforo_kpi_verde_giallo_rosso_nei_due_versi():
    th = {"green": 15, "red": 30}
    assert [kpi.status(x, th, "low") for x in (10, 15, 20, 31)] == ["verde", "verde", "giallo", "rosso"]
    th = {"green": 0.6, "red": 0.4}
    assert [kpi.status(x, th, "high") for x in (0.7, 0.5, 0.3)] == ["verde", "giallo", "rosso"]
    assert kpi.status(float("nan"), th, "high") == "n/d"


def _kpi_cfg():
    return {**CFG, "kpi_months": 1, "kpi_prev_months": 1, "trend_months": 3}


def test_kpi_standard_valori_su_dati_sintetici():
    # finestre: precedente = (31/12, 31/01], ultima = (31/01, 28/02]
    d = _df([
        # prima: 4 gg in coda, 6 di lavoro, consegnata in 10 gg
        ("A-1", "Bug", "x", "Done", "2026-01-01 00:00", "Done", "", "2026-01-01 00:00", "2026-01-05 00:00", "2026-01-11 00:00"),
        # ultima: 10 gg in coda, 5 di lavoro, consegnata in 15 gg
        ("B-1", "Bug", "x", "Done", "2026-02-01 00:00", "Done", "", "2026-02-01 00:00", "2026-02-11 00:00", "2026-02-16 00:00"),
        # ultima: creata e ancora in coda a fine dati -> backlog 1 (era 0)
        ("B-2", "Bug", "x", "To Do", "2026-02-10 00:00", "", "", "2026-02-10 00:00", "", ""),
    ])
    ev = load.events(d)
    cfg = _kpi_cfg()
    end = pd.Timestamp("2026-02-28 23:59")
    it = metrics.phases(metrics.prepare(d, ev, cfg), ev, cfg, end)
    k = {x["key"]: x for x in kpi.standard_kpis(it, ev, cfg, end)["kpis"]}
    assert len(k) == 8
    assert k["work_wait"]["value"] == 0.5 and k["work_wait"]["ref"] == 1.5          # 5/10 contro 6/4
    assert k["work_wait"]["trend"] == "peggio" and k["work_wait"]["status"] == "giallo"
    assert k["balance"]["value"] == 2.0 and k["balance"]["status"] == "rosso"        # 2 create, 1 consegnata
    assert k["backlog"]["value"] == 1 and k["backlog"]["ref"] == 0
    assert list(k) == ["throughput", "lead_p50", "cycle_p50", "wip", "work_wait", "release_share", "balance", "backlog"]
    assert k["lead_p50"]["value"] == 15 and k["lead_p50"]["ref"] == 10 and k["lead_p50"]["status"] == "verde"
    assert k["cycle_p50"]["value"] == 5 and k["cycle_p50"]["ref"] == 6
    assert "10.0 giorni ora, 4.0 nel riferimento" in k["lead_p50"]["calc"]      # attesa prima dell'avvio
    assert k["wip"]["value"] == 0 and k["wip"]["ref"] == 0                      # a fine finestre nessuno in lavorazione
    # note: ingredienti del calcolo e spiegazione di trend e stato
    assert "5 giorni di lavoro diviso 10 di attesa" in k["work_wait"]["calc"]
    assert "Trend peggio: variazione -67%" in k["work_wait"]["trend_txt"] and "oltre la tolleranza di ±10%" in k["work_wait"]["trend_txt"]
    assert k["release_share"]["trend_txt"].startswith("Trend stabile: variazione +0 pp") and "±5 punti" in k["release_share"]["trend_txt"]
    assert k["balance"]["status_txt"] == "Stato rosso: verde ≤ 1.00, rosso > 1.20."
    assert k["throughput"]["calc"].startswith("1 consegne in 31 giorni")   # (28/01, 28/02]
    assert "durano diverso" not in k["throughput"]["calc"]                  # finestre di uguale durata


def test_report_mostra_kpi_standard_nella_sintesi(tmp_path):
    cfg = {**yaml.safe_load(open(ROOT / "config.example.yaml", encoding="utf-8")), "output_dir": str(tmp_path)}
    cfg.pop("period_start", None); cfg.pop("period_end", None)
    p = tmp_path / "cfg.yaml"
    p.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    cli.main(["--config", str(p), "--input-completed", str(ROOT / "sample-data-done.csv"),
              "--input-all", str(ROOT / "sample-data-all.csv"), "--today", "2025-12-31"])
    html = next(tmp_path.glob("*.html")).read_text(encoding="utf-8")
    md = next(tmp_path.glob("*.md")).read_text(encoding="utf-8")
    for label in ("Consegne a settimana", "Lead time P50", "Work in progress", "Rapporto lavoro / attesa",
                  "Bilancio ingressi / uscite", "Dimensione del backlog"):
        assert label in html and label in md
    assert html.count("kpi-std st-") == 8
    assert "Cosa sono i KPI standard?" in html


def test_report_si_apre_sulla_sintesi_senza_hash():
    # il primo link con data-tab nella pagina e' "Metodologia" (intestazione): il ripiego deve essere il primo tab del menu
    tpl = (ROOT / "flowmetrics" / "templates" / "report.html.j2").read_text(encoding="utf-8")
    assert "if (tabIds.indexOf(id) < 0) id = mainIds[0];" in tpl
    assert "id = tabIds[0]" not in tpl
