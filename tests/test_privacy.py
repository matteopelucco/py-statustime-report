"""Titoli riservati: tutti i titoli usati qui sono inventati."""
import csv
from pathlib import Path
import pandas as pd, pytest, yaml
from flowmetrics import cli, load, privacy

ROOT = Path(__file__).resolve().parent.parent
HEAD = ["Key", "Issue Type", "Summary", "Status", "Created", "Resolution", "Resolved", "Epic Link", "'->To Do", "'->In Progress", "'->Done"]
NASTY = 'Pagina <b>saldo</b> "nuova" | </script><script>alert(1)</script>'
ROWS = [
    ["A-1", "Epic", "Progetto riservato Aurora", "In Progress", "2025-01-01 00:00", "", "", "", "2025-01-01 00:00", "2025-01-02 00:00", ""],
    ["A-2", "Story", NASTY, "Done", "2025-10-01 00:00", "Done", "", "A-1", "2025-10-01 00:00", "2025-10-03 00:00", "2025-10-20 00:00"],
    ["A-3", "Bug", "Errore nel calcolo interessi cliente", "Done", "2025-11-01 00:00", "Done", "", "", "2025-11-01 00:00", "2025-11-02 00:00", "2025-11-10 00:00"],
    ["A-4", "Bug", "Fix", "To Do", "2025-11-05 00:00", "", "", "", "2025-11-05 00:00", "", ""],
]
TITLES = {r[0]: r[2] for r in ROWS}


def _write(path, rows=ROWS):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f, quoting=csv.QUOTE_ALL).writerows([HEAD] + rows)
    return path


def test_anonymize_sostituisce_solo_i_titoli(tmp_path):
    src, dst = _write(tmp_path / "raw.csv"), tmp_path / "anon.csv"
    assert privacy.anonymize_file(src, dst) == TITLES
    a, b = load.read_export(src), load.read_export(dst)
    assert b.Summary.tolist() == ["[[T:A-1]]", "[[T:A-2]]", "[[T:A-3]]", "[[T:A-4]]"]
    assert a.drop(columns="Summary").equals(b.drop(columns="Summary"))      # tutto il resto identico
    text = dst.read_text(encoding="utf-8")
    assert not any(t in text for t in TITLES.values())
    assert privacy.all_placeholders(b) and not privacy.all_placeholders(a)


def test_dizionario_cumulativo_e_file_gia_anonimo(tmp_path):
    priv, data = tmp_path / "private", tmp_path / "data"
    _write(priv / "raw" / "P" / "ott.csv", ROWS[:3])
    r = privacy.anonymize_all(priv, data)
    assert (r["titles"], r["new"], r["changed"]) == (3, 3, 0) and (data / "P" / "ott.csv").exists()
    # export successivo: A-2 non c'e' piu', A-3 ha cambiato titolo, A-4 e' nuova
    (priv / "raw" / "P" / "ott.csv").unlink()
    _write(priv / "raw" / "P" / "nov.csv", [ROWS[0], ROWS[2][:2] + ["Titolo corretto"] + ROWS[2][3:], ROWS[3]])
    r = privacy.anonymize_all(priv, data)
    assert (r["titles"], r["new"], r["changed"]) == (4, 1, 1)
    t = privacy.load_titles(priv / "titles.csv")
    assert t["A-2"] == NASTY and t["A-3"] == "Titolo corretto"            # A-2 resta nel dizionario
    # un file gia' anonimo finito per errore in raw non sovrascrive i titoli veri con i segnaposto
    (priv / "raw" / "P" / "anon.csv").write_text((data / "P" / "nov.csv").read_text(encoding="utf-8"), encoding="utf-8")
    privacy.anonymize_all(priv, data)
    assert privacy.load_titles(priv / "titles.csv")["A-3"] == "Titolo corretto"


def test_restore_titles_e_chiave_mancante():
    df = pd.DataFrame({"Key": ["A-2", "A-9"], "Summary": ["[[T:A-2]]", "[[T:A-9]]"]})
    assert privacy.restore_titles(df.iloc[:1], TITLES).Summary.tolist() == [NASTY]
    with pytest.raises(ValueError) as e:
        privacy.restore_titles(df, TITLES)
    assert "A-9" in str(e.value) and NASTY not in str(e.value)


def test_reveal_text_con_escape_per_tipo():
    src = "Epic A-1 «[[T:A-1]]»: lenta [[T:A-2]] e [[T:Z-9]]"
    out, n, unknown = privacy.reveal_text(src, TITLES, ".html")
    assert n == 2 and unknown == ["Z-9"] and "[[T:Z-9]]" in out
    assert "&lt;b&gt;saldo&lt;/b&gt;" in out and "<script>" not in out
    md, _, _ = privacy.reveal_text("| A-2 | [[T:A-2]] |", TITLES, ".md")
    assert md.count("\\|") == 1 and md.startswith("| A-2 | Pagina <b>saldo</b>")      # '|' del titolo protetto: la tabella regge
    assert privacy.reveal_text("[[T:A-3]]", TITLES, ".txt")[0] == TITLES["A-3"]


def test_check_trova_le_fughe_senza_stamparle(tmp_path):
    priv = tmp_path / "private"
    (priv / "config").mkdir(parents=True)
    (priv / "config" / "c.yaml").write_text('issue_url: "https://jira.interno.example/browse/${issueKey}"\n', encoding="utf-8")
    work = tmp_path / "work"
    _write(work / "ok.csv", [r[:2] + [privacy.placeholder(r[0])] + r[3:] for r in ROWS])
    assert privacy.check([work], TITLES, priv) == []
    _write(work / "raw.csv")                                                       # export reale nel posto sbagliato
    (work / "nota.md").write_text("Sbloccare: errore nel CALCOLO interessi cliente.", encoding="utf-8")
    (work / "cfg.yaml").write_text("issue_url: https://JIRA.interno.example/browse/X\n", encoding="utf-8")
    found = {Path(f).name: (kind, n, ex) for f, kind, n, ex in privacy.check([work], TITLES, priv)}
    assert found["nota.md"] == ("titoli riconosciuti nel testo", 1, ["A-3"])
    assert found["cfg.yaml"] == ("valori riservati di config", 1, ["issue_url"])
    kinds = [k for f, k, n, ex in privacy.check([work], TITLES, priv) if Path(f).name == "raw.csv"]
    assert "titoli in chiaro nella colonna Summary" in kinds                       # anche il titolo corto "Fix" (A-4)
    assert not any(t in str(privacy.check([work], TITLES, priv)) for t in TITLES.values())
    assert privacy.check([priv], TITLES, priv) == []                                # la cartella riservata non si controlla


def test_check_ignora_titoli_identici_a_testi_non_riservati(tmp_path):
    # una issue intitolata come uno stato o un'intestazione non rivela nulla: quel testo e' gia' in chiaro negli export
    priv = tmp_path / "private"
    rows = ROWS + [["A-5", "Bug", "In Progress", "Done", "2025-11-06 00:00", "Done", "", "", "", "", "2025-11-07 00:00"],
                   ["A-6", "Bug", "Ready for acceptance", "Ready for acceptance", "2025-11-06 00:00", "", "", "", "", "", ""]]
    _write(priv / "raw" / "x.csv", rows)
    privacy.anonymize_all(priv, tmp_path / "data")
    titles = privacy.load_titles(priv / "titles.csv")
    assert privacy.generic_titles(titles, priv) == ["A-5", "A-6"]                    # "In Progress" e' lo stato di A-1
    assert privacy.check([tmp_path / "data"], titles, priv) == []                   # "Ready for acceptance" resta come stato
    (tmp_path / "data" / "nota.md").write_text("vedi errore nel calcolo interessi cliente", encoding="utf-8")
    assert len(privacy.check([tmp_path / "data"], titles, priv)) == 1               # i titoli veri si trovano ancora


def _cfg(tmp_path, **extra):
    cfg = yaml.safe_load(open(ROOT / "config.example.yaml", encoding="utf-8"))
    cfg.pop("period_start", None); cfg.pop("period_end", None)
    cfg |= {"output_dir": str(tmp_path / "out"), "private_dir": str(tmp_path / "private"), "issue_url": "",
            "input_completed": str(tmp_path / "data" / "x.csv"), "input_all": "", "min_samples": 1, **extra}
    p = tmp_path / "cfg.yaml"
    p.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return p


def test_flusso_completo_lavoro_anonimo_e_report_finale(tmp_path, capsys):
    priv = tmp_path / "private"
    _write(priv / "raw" / "x.csv")
    privacy.anonymize_all(priv, tmp_path / "data")
    (priv / "config").mkdir()
    (priv / "config" / "cfg.yaml").write_text('issue_url: "https://jira.interno.example/browse/${issueKey}"\n', encoding="utf-8")
    cfg = _cfg(tmp_path, require_anonymized=True)

    cli.main(["--config", str(cfg), "--today", "2025-12-31"])                     # lavoro: cio' che l'AI puo' leggere
    work = "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "out").iterdir() if p.suffix in (".html", ".md", ".csv"))
    assert "[[T:A-2]]" in work and "titoli nascosti" in work
    assert not any(t in work for t in ("Aurora", "saldo", "interessi cliente", "jira.interno"))
    assert privacy.check([tmp_path / "out", tmp_path / "data"], privacy.load_titles(priv / "titles.csv"), priv) == []

    cli.main(["--config", str(cfg), "--today", "2025-12-31", "--reveal"])          # finale: solo in private/out
    final = next((priv / "out").glob("*.html")).read_text(encoding="utf-8")
    assert "interessi cliente" in final and "jira.interno.example/browse/A-3" in final and "titoli nascosti" not in final
    assert "[[T:" not in final and "<script>alert(1)" not in final                # nessun segnaposto rimasto; titolo ostile: escape corretto
    assert "Pagina \\u003cb>saldo" in final                                        # il titolo ostile e' nei dati della tabella, protetto
    final_md = next((priv / "out").glob("*.md")).read_text(encoding="utf-8")
    assert "[[T:" not in final_md and "titoli nascosti" not in final_md
    assert not list((tmp_path / "out").glob("*reveal*")) and "interessi cliente" not in work
    printed = capsys.readouterr().out
    assert not any(t in printed for t in ("Aurora", "saldo", "interessi cliente"))   # a video mai i titoli


def test_require_anonymized_blocca_gli_export_reali(tmp_path):
    _write(tmp_path / "data" / "x.csv")                                            # export reale usato per errore
    cfg = _cfg(tmp_path, require_anonymized=True)
    with pytest.raises(SystemExit):
        cli.main(["--config", str(cfg), "--today", "2025-12-31"])
    assert not (tmp_path / "out").exists() or not list((tmp_path / "out").glob("*.html"))


def test_reveal_senza_dizionario_si_ferma(tmp_path):
    _write(tmp_path / "data" / "x.csv", [r[:2] + [privacy.placeholder(r[0])] + r[3:] for r in ROWS])
    with pytest.raises(SystemExit):
        cli.main(["--config", str(_cfg(tmp_path)), "--today", "2025-12-31", "--reveal"])
