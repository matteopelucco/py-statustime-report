"""Titoli riservati: i titoli di issue ed epiche non devono mai arrivare a un LLM.

Gli export reali e il dizionario stanno in `private/` (ignorata da git, vietata in lettura all'AI). Tutto il resto del
progetto lavora su copie anonime, dove il titolo (colonna Summary) e' sostituito da un segnaposto derivato dalla chiave:
`[[T:PS-1234]]`. La chiave non e' riservata, quindi il segnaposto e' stabile fra un export e l'altro.

    private/raw/**.csv        export reali                      -> anonymize -> data/**.csv (stessi nomi, titoli = segnaposto)
    private/titles.csv        dizionario cumulativo Key,Summary (scritto da anonymize)
    private/config/<nome>     valori riservati della config (es. issue_url con l'host Jira), usati solo con --reveal
    private/out/              report e testi finali, con i titoli veri

    python -m flowmetrics.privacy anonymize            # export reali -> copie anonime + dizionario
    python -m flowmetrics --config <cfg>               # report e analisi di lavoro (segnaposto), in out/
    python -m flowmetrics --config <cfg> --reveal      # report e analisi finali (titoli veri), in private/out/
    python -m flowmetrics.privacy reveal testo.md      # testi scritti a mano o dall'AI: segnaposto -> titoli, in private/out/
    python -m flowmetrics.privacy check                # nessun titolo o valore riservato nei file leggibili dall'AI?

Regola per ogni funzione di questo modulo: a video vanno solo conteggi, nomi di file e chiavi, MAI un titolo.
"""
import argparse, csv, html, re, sys
from pathlib import Path
import yaml
from . import load

TITLE_COL = "Summary"
RX = re.compile(r"\[\[T:([^\[\]\s]+)\]\]")
TEXT_EXT = {".csv", ".md", ".txt", ".html", ".htm", ".yaml", ".yml", ".json", ".py", ".j2"}


def placeholder(key):
    return f"[[T:{key}]]"


def is_placeholder(s):
    return bool(RX.fullmatch(str(s)))


def private_dir(cfg=None):
    return Path((cfg or {}).get("private_dir") or "private")


# --- dizionario ---------------------------------------------------------------------------------------------------

def load_titles(path):
    """Dizionario chiave -> titolo (vuoto se il file non esiste)."""
    path = Path(path)
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {r["Key"]: r[TITLE_COL] for r in csv.DictReader(f)}


def save_titles(path, titles):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL)
        w.writerow(["Key", TITLE_COL])
        w.writerows(sorted(titles.items()))


# --- anonimizzazione ------------------------------------------------------------------------------------------------

def anonymize_file(src, dst):
    """Copia un export sostituendo ogni titolo con il segnaposto della sua chiave. Restituisce {chiave: titolo}.
    Verifica che, titoli a parte, la copia sia identica all'originale (stesse righe, stessi valori)."""
    with open(src, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    if not rows or "Key" not in rows[0] or TITLE_COL not in rows[0]:
        raise ValueError(f"{src}: non e' un export Status Time Free (mancano le colonne Key e {TITLE_COL})")
    ik, it = rows[0].index("Key"), rows[0].index(TITLE_COL)
    titles = {}
    for r in rows[1:]:
        if len(r) <= max(ik, it):
            continue
        if not is_placeholder(r[it]):      # un file gia' anonimo non deve sporcare il dizionario
            titles[r[ik]] = r[it]
        r[it] = placeholder(r[ik])
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(dst, "w", newline="", encoding="utf-8") as f:
        csv.writer(f, quoting=csv.QUOTE_ALL).writerows(rows)
    a, b = load.read_export(src).drop(columns=TITLE_COL), load.read_export(dst).drop(columns=TITLE_COL)
    if not a.equals(b):
        dst.unlink()
        raise ValueError(f"{src}: la copia anonima differisce dall'originale oltre ai titoli (copia eliminata)")
    return titles


def anonymize_all(private, data_dir="data"):
    """Tutti i CSV di private/raw -> data_dir (stessi percorsi relativi); aggiorna il dizionario cumulativo.
    Restituisce un riepilogo senza titoli."""
    raw = Path(private) / "raw"
    files = sorted(raw.rglob("*.csv"))
    known = load_titles(Path(private) / "titles.csv")
    before, changed, done = len(known), 0, []
    for src in files:
        t = anonymize_file(src, Path(data_dir) / src.relative_to(raw))
        changed += sum(1 for k, v in t.items() if k in known and known[k] != v)
        known.update(t)
        done.append((str(src.relative_to(raw)), len(t)))
    save_titles(Path(private) / "titles.csv", known)
    return {"files": done, "titles": len(known), "new": len(known) - before, "changed": changed}


def all_placeholders(df):
    """True se ogni titolo del DataFrame e' un segnaposto (dati anonimi)."""
    return bool(df[TITLE_COL].map(lambda s: s == "" or is_placeholder(s)).all())


# --- ripristino -----------------------------------------------------------------------------------------------------

def restore_titles(df, titles):
    """Rimette i titoli veri al posto dei segnaposto (solo in locale, per il report finale).
    Una chiave assente dal dizionario e' un errore: meglio fermarsi che pubblicare un report con segnaposto."""
    def one(s):
        m = RX.fullmatch(str(s))
        return titles.get(m.group(1), None) if m else s
    out = df.copy()
    out[TITLE_COL] = out[TITLE_COL].map(one)
    missing = out.loc[out[TITLE_COL].isna(), "Key"].tolist()
    if missing:
        raise ValueError(f"{len(missing)} chiavi senza titolo nel dizionario (es. {', '.join(missing[:5])}): "
                         "rilanciare `python -m flowmetrics.privacy anonymize` sugli export reali")
    return out


def private_config(cfg, config_path):
    """Config per il report finale: valori riservati da private/config/<stesso nome> (facoltativo, es. issue_url)
    e output in private/out, cosi' il report con i titoli non finisce dove l'AI puo' leggerlo."""
    priv = private_dir(cfg)
    over = priv / "config" / Path(config_path).name
    extra = {}
    if over.exists():
        with open(over, encoding="utf-8") as f:
            extra = yaml.safe_load(f) or {}
    return {**cfg, **extra, "output_dir": extra.get("output_dir") or str(priv / "out")}


def reveal_text(text, titles, kind=""):
    """Sostituisce i segnaposto in un testo qualunque (mail, pagina, analisi scritta a mano o dall'AI).
    L'escape dipende dal tipo: HTML -> entita', Markdown -> niente a capo e '|' protetto (tabelle).
    Restituisce (testo, sostituiti, chiavi senza titolo)."""
    unknown, n = [], 0

    def esc(t):
        if kind in (".html", ".htm"):
            return html.escape(t)
        if kind == ".md":
            return t.replace("\n", " ").replace("|", "\\|")
        return t

    def rep(m):
        nonlocal n
        if m.group(1) not in titles:
            unknown.append(m.group(1))
            return m.group(0)
        n += 1
        return esc(titles[m.group(1)])
    return RX.sub(rep, text), n, sorted(set(unknown))


# --- controllo di fuga ----------------------------------------------------------------------------------------------

def _secrets(private):
    """Valori riservati delle config private (stringhe lunghe), es. l'indirizzo interno di Jira."""
    out = {}
    for p in sorted((Path(private) / "config").glob("*.y*ml")):
        with open(p, encoding="utf-8") as f:
            for k, v in (yaml.safe_load(f) or {}).items():
                if isinstance(v, str) and len(v) >= 8 and k != "output_dir":
                    out[v.lower()] = k
                    host = re.match(r"https?://([^/\s]+)", v)
                    if host:
                        out[host.group(1).lower()] = k
    return out


def vocabulary(private):
    """Testi NON riservati degli export reali: intestazioni e valori delle colonne diverse dal titolo (stati, tipi,
    label...). Un titolo identico a uno di questi (es. una issue intitolata come uno stato) non rivela nulla."""
    out = set()
    for p in sorted((Path(private) / "raw").rglob("*.csv")):
        with open(p, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        if not rows or TITLE_COL not in rows[0]:
            continue
        it = rows[0].index(TITLE_COL)
        out.update(h.strip().lower() for h in rows[0])
        out.update(c.strip().lower() for r in rows[1:] for i, c in enumerate(r) if i != it and c)
    return out


def generic_titles(titles, private):
    """Chiavi dei titoli che coincidono con un testo non riservato degli export (ignorati dal controllo di fuga)."""
    vocab = vocabulary(private)
    return sorted(k for k, t in titles.items() if t.strip().lower() in vocab)


def check(paths, titles, private="private", min_len=12):
    """Cerca titoli reali e valori riservati nei file leggibili dall'AI. Restituisce una lista di
    (file, tipo, quanti, esempi) dove gli esempi sono chiavi o nomi di opzione, MAI il testo trovato.
    - CSV con colonne Key e Summary: ogni titolo deve essere un segnaposto (controllo esatto, vale per ogni lunghezza);
    - ogni file di testo: ricerca dei titoli lunghi almeno min_len caratteri (sotto, troppi falsi positivi), esclusi
      quelli identici a un testo non riservato degli export (generic_titles)."""
    generic = set(generic_titles(titles, private))
    long_titles = {t.lower(): k for k, t in titles.items() if len(t.strip()) >= min_len and k not in generic}
    secrets = _secrets(private)
    priv = Path(private).resolve()
    found = []
    files = []
    for p in map(Path, paths):
        files += [p] if p.is_file() else sorted(x for x in p.rglob("*") if x.is_file()) if p.exists() else []
    for p in files:
        if p.suffix.lower() not in TEXT_EXT or priv in p.resolve().parents:
            continue
        try:
            text = p.read_text(encoding="utf-8-sig")
        except (UnicodeDecodeError, OSError):
            continue
        if p.suffix.lower() == ".csv":
            rows = list(csv.reader(text.splitlines()))
            if rows and "Key" in rows[0] and TITLE_COL in rows[0]:
                ik, it = rows[0].index("Key"), rows[0].index(TITLE_COL)
                bad = [r[ik] for r in rows[1:] if len(r) > max(ik, it) and r[it] and not is_placeholder(r[it])]
                if bad:
                    found.append((str(p), "titoli in chiaro nella colonna Summary", len(bad), bad[:5]))
        low = text.lower()
        hit = sorted({k for t, k in long_titles.items() if t in low})
        if hit:
            found.append((str(p), "titoli riconosciuti nel testo", len(hit), hit[:5]))
        sec = sorted({k for v, k in secrets.items() if v in low})
        if sec:
            found.append((str(p), "valori riservati di config", len(sec), sec))
    return found


# --- riga di comando --------------------------------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(prog="flowmetrics.privacy", description="Titoli riservati: anonimizza, ripristina, controlla")
    ap.add_argument("--private", default="private", help="cartella riservata (default: private)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("anonymize", help="export reali (private/raw) -> copie anonime + dizionario")
    p.add_argument("--data", default="data", help="cartella delle copie anonime (default: data)")
    p = sub.add_parser("reveal", help="segnaposto -> titoli veri in file di testo; risultato in private/out")
    p.add_argument("files", nargs="+")
    p = sub.add_parser("check", help="cerca titoli e valori riservati nei file leggibili dall'AI")
    p.add_argument("paths", nargs="*", default=["data", "out", "config"])
    a = ap.parse_args(argv)
    priv = Path(a.private)

    if a.cmd == "anonymize":
        r = anonymize_all(priv, a.data)
        if not r["files"]:
            ap.error(f"nessun CSV in {priv / 'raw'}")
        for name, n in r["files"]:
            print(f"OK: {name}  ({n} titoli sostituiti)")
        print(f"Dizionario: {r['titles']} titoli ({r['new']} nuovi, {r['changed']} aggiornati)")
        return 0
    titles = load_titles(priv / "titles.csv")
    if not titles:
        ap.error(f"dizionario assente o vuoto: {priv / 'titles.csv'} (lanciare prima anonymize)")
    if a.cmd == "reveal":
        out = priv / "out"
        out.mkdir(parents=True, exist_ok=True)
        for f in map(Path, a.files):
            text, n, unknown = reveal_text(f.read_text(encoding="utf-8"), titles, f.suffix.lower())
            (out / f.name).write_text(text, encoding="utf-8")
            print(f"OK: {out / f.name}  ({n} segnaposto sostituiti"
                  + (f", {len(unknown)} chiavi senza titolo: {', '.join(unknown[:5])}" if unknown else "") + ")")
        return 0
    found = check(a.paths, titles, priv)
    for path, kind, n, ex in found:
        print(f"FUGA: {path}: {kind} ({n}; es. {', '.join(ex)})")
    short = sum(1 for t in titles.values() if len(t.strip()) < 12)
    print(f"Controllati i titoli di {len(titles)} issue; non ricercabili nel testo libero: {short} troppo corti, "
          f"{len(generic_titles(titles, priv))} identici a stati o intestazioni (nei CSV il controllo sulla colonna dei titoli vale comunque).")
    print("Nessun titolo o valore riservato nei file controllati." if not found else f"{len(found)} problemi: correggere prima di usare l'AI.")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
