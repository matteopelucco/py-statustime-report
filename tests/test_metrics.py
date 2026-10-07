from pathlib import Path
import pandas as pd, yaml
from flowmetrics import load, metrics

with open(Path(__file__).resolve().parent.parent / "config.yaml", encoding="utf-8") as _f:
    CFG = yaml.safe_load(_f)
CFG["bulk_done_threshold"] = 2
CFG.pop("period_start", None); CFG.pop("period_end", None)   # i test usano date di gennaio


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
    assert it.loc[0, "class"] == "Speciali"


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


def test_story_progettuale_da_epic_link():
    d = _df([("A-1", "Story", "x", "Done", "2026-01-01", "Done", "", "", "", "2026-01-05"),
             ("A-2", "Story", "x", "Done", "2026-01-01", "Done", "", "", "", "2026-01-05"),
             ("A-3", "Story", "x", "Done", "2026-01-01", "Done", "", "", "", "2026-01-05")])
    d["Epic Link"] = ["PS-1", "", "PS-1"]
    d["Labels"] = ["", "", "expedite"]
    assert list(metrics.classify(d, CFG)) == ["Story progettuale", "Story (non progettuale)", "Expedite"]
