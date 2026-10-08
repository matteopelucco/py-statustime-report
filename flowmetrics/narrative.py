"""Commento discorsivo per il business, generato dai numeri del report ("come stiamo andando")."""
import pandas as pd
from markupsafe import Markup

MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre",
        "novembre", "dicembre"]
TOL = 0.10          # sotto questa variazione relativa si parla di "in linea"


def _b(text, *args):
    """Frase con i valori dinamici in grassetto (<b>) ed escapati."""
    return Markup(text).format(*[Markup("<b>{}</b>").format(a) for a in args])


def _row(df, window, cls):
    r = df[(df.window == window) & (df["class"] == cls)]
    return r.iloc[0] if len(r) else None


def _trend(new, old, up_word, down_word, same_word):
    if not old or old != old:
        return same_word, 0
    d = (new - old) / old
    return (up_word if d > TOL else down_word if d < -TOL else same_word), d


def _giorni(x):
    return f"{x:.0f} giorni"


def build(cfg, today, fc, cyc, thr, open_n):
    """Elenco di {title, text}: ogni testo e' una frase o due, senza gergo, con i numeri in grassetto."""
    items = []
    wmax, wmin = int(fc.window.max()), int(fc.window.min())
    min_n = cfg["min_samples"]

    # 1. ritmo di consegna
    complete = thr[thr.index < today.to_period("M")]["TOTALE"]
    if len(complete) >= 3:
        last, prev = complete.iloc[-1], complete.iloc[:-1].mean()
        word, d = _trend(last, prev, "in crescita", "in calo", "in linea")
        mese = f"{MESI[complete.index[-1].month - 1]} {complete.index[-1].year}"
        items.append({"title": "Quanto consegniamo", "text": _b(
            "Nell'ultimo mese completo ({}) sono andati in produzione {} item, contro una media di {} nei mesi precedenti del periodo ({}): il ritmo è {}.",
            mese, int(last), f"{prev:.0f}", f"{d:+.0%}".replace("-", "\u2212"), word)})

    # 2. tempi "standard"
    t, t_old = _row(fc, wmax, "TUTTI"), _row(fc, wmin, "TUTTI")
    if t is not None and t.n >= min_n:
        text = _b("Un item che entra oggi in board arriva in produzione, nella metà dei casi, entro {}; nell'85% dei casi entro {} (ultimi {} mesi, tutte le classi di lavoro).",
                  _giorni(t.P50), _giorni(t.P85), wmax)
        if wmin != wmax and t_old is not None and t_old.n >= min_n:
            word, d = _trend(t_old.P50, t.P50, "più lunghi", "più brevi", "stabili")
            text += _b(" Guardando solo gli ultimi {} mesi i tempi sono {} (metà dei casi entro {}).", wmin, word, _giorni(t_old.P50))
        items.append({"title": "Quanto tempo ci vuole", "text": text})

    # 3. differenze tra tipi di lavoro
    rows = fc[(fc.window == wmax) & ~fc["class"].isin(["TUTTI", "Expedite"]) & (fc.n >= min_n)].sort_values("P50")
    if len(rows) >= 2:
        fast, slow = rows.iloc[0], rows.iloc[-1]
        items.append({"title": "Differenze per tipo di lavoro", "text": _b(
            "{} è il lavoro più rapido (metà dei casi entro {}), {} il più lento (entro {}): conviene dare le date in base al tipo di richiesta, non con un'unica media.",
            fast["class"], _giorni(fast.P50), slow["class"], _giorni(slow.P50))})

    # 4. urgenze: un item alla volta, preso subito in carico
    c = _row(cyc, wmax, "TUTTI")
    if c is not None and c.n >= min_n:
        text = _b("Se un item è urgente e viene preso in carico subito, saltando la coda (uno solo alla volta), il tempo si accorcia: metà dei casi entro {}, l'85% entro {} dall'avvio del lavoro.",
                  _giorni(c.P50), _giorni(c.P85))
        e = _row(cyc, wmax, "Expedite")
        if e is not None and e.n >= min_n:
            text += _b(" Gli item già gestiti come Expedite sono andati così: metà entro {}, l'85% entro {} ({} item).",
                       _giorni(e.P50), _giorni(e.P85), int(e.n))
        items.append({"title": "Se c'è un'urgenza", "text": text})

    # 5. cautele sui dati
    caut = []
    if open_n < 30:
        caut.append(_b("L'export contiene solo {} item non chiusi: il backlog di oggi non si può misurare e quello storico è sottostimato.", open_n))
    if cfg.get("period_start"):
        caut.append(_b("I numeri riguardano solo gli item chiusi dal {}: sono stime sul passato recente, non garanzie.",
                       pd.Timestamp(cfg["period_start"]).strftime("%d/%m/%Y")))
    if caut:
        items.append({"title": "Da tenere presente", "text": Markup(" ").join(caut)})
    return items
