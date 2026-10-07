import pandas as pd, yaml
from flowmetrics import load, metrics

CFG = yaml.safe_load(open("config.yaml", encoding="utf-8"))
CFG["bulk_done_threshold"] = 2


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
                ("A-2", "Bug", "x", "Done", "2026-01-01 00:00", "Won't Do", "", "", "", "2026-01-05 00:00")]).set_index("Key")
    assert not it.valid.any()


def test_classi():
    it = _prep([("A-1", "Analysis", "x", "Done", "2026-01-01 00:00", "Done", "", "", "", "2026-01-05 00:00")])
    assert it.loc[0, "class"] == "Speciali"
