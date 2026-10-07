"""Parsing dell'export Status Time Free -> DataFrame di item + tabella eventi (transizioni)."""
import re
import pandas as pd

STAMP = "%Y-%m-%d %H:%M"


def _num(s):
    if s is None or (isinstance(s, float) and pd.isna(s)) or str(s).strip() in ("", "-"):
        return float("nan")
    return float(str(s).replace(",", ""))


def read_export(path):
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df["Created"] = pd.to_datetime(df["Created"], format=STAMP, errors="coerce")
    df["Resolved"] = pd.to_datetime(df["Resolved"], format=STAMP, errors="coerce")
    return df


def read_exports(done_path, all_path=None):
    """Export 'done' (item entrati in Done nel periodo) + export facoltativo 'all' (tutte le issue del periodo,
    aperte comprese), stesso formato. Unione per Key: se una issue e' in entrambi vale la riga dell'export 'all'
    (fotografia completa); colonna `fonte` = 'entrambi' / 'solo done' / 'solo all'."""
    done = read_export(done_path)
    if not all_path:
        return done.assign(fonte="solo done")
    al = read_export(all_path)
    in_done, in_all = set(done.Key), set(al.Key)
    df = pd.concat([al, done[~done.Key.isin(in_all)]], ignore_index=True)
    text = [c for c in df.columns if c not in ("Created", "Resolved")]
    df[text] = df[text].fillna("")      # colonne presenti in un solo export
    df["fonte"] = [("entrambi" if k in in_done else "solo all") if k in in_all else "solo done" for k in df.Key]
    return df


def status_names(df):
    return [c[3:] for c in df.columns if c.startswith("'->")]


def events(df):
    """Tabella lunga: Key, status, ts (ogni ingresso in uno stato)."""
    rows = []
    for st in status_names(df):
        col = df["'->" + st]
        for key, cell in zip(df["Key"], col):
            if not cell:
                continue
            for t in cell.split(","):
                t = t.strip()
                if re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}$", t):
                    rows.append((key, st, t))
    ev = pd.DataFrame(rows, columns=["Key", "status", "ts"])
    ev["ts"] = pd.to_datetime(ev["ts"], format=STAMP)
    return ev.sort_values(["Key", "ts"]).reset_index(drop=True)


def status_days(df):
    """Giorni per stato (numerici) come DataFrame indicizzato per Key."""
    cols = status_names(df)
    out = df.set_index("Key")[cols].apply(lambda s: s.map(_num))
    return out
