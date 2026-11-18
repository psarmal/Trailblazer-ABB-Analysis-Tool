"""Per-file run info (sample role, label, PMT gain) that overrides what the CSV header says, and
import of that info from a summary workbook (no Tk, no app imports).

Stored in %LOCALAPPDATA%\\BBAnalyzer\\run_info.json keyed by the file's normalised absolute path;
the CSV files themselves are never changed.
"""
import difflib
import json
import math
import os
import re
import tempfile
from dataclasses import asdict, dataclass

import pandas as pd

ROLES = ("negative", "positive", "")
SAMPLE_HEADERS = ("conditions", "sample", "ch", "channel")
_NUM_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")


@dataclass
class RunInfo:
    role: str = ""
    label: str = ""
    gain_mv: object = None      # float or None


def default_path():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "BBAnalyzer", "run_info.json")


def file_key(path):
    return os.path.normcase(os.path.abspath(path))


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def load(path):
    """{file_key: RunInfo}; {} when the file is missing, unreadable or malformed."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        out = {}
        for k, v in raw.items():
            role, label, gain = v.get("role", ""), v.get("label", ""), v.get("gain_mv")
            if role not in ROLES or not isinstance(label, str) or not (gain is None or _is_num(gain)):
                return {}
            out[k] = RunInfo(role, label, None if gain is None else float(gain))
        return out
    except (OSError, ValueError, AttributeError, TypeError):
        return {}


def save(path, store):
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({k: asdict(v) for k, v in store.items()}, f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def effective_gain(info, meta):
    """(gain mV, "override" | "file" | None): the run-info gain if set, else the header's pmt_gain_mv."""
    if info is not None and info.gain_mv is not None:
        return float(info.gain_mv), "override"
    for k, v in (meta or {}).items():
        if k.lower() == "pmt_gain_mv":
            m = _NUM_RE.search(str(v))
            if m:
                return float(m.group()), "file"
    return None, None


def role_from_label(label):
    """"negative" / "positive" when the label contains "neg"/"pos" or a word spelled close to
    "negative"/"positive" (summary sheets have typos such as "Nagative Control"); else ""."""
    s = str(label).lower()
    if "neg" in s:
        return "negative"
    if "pos" in s:
        return "positive"
    for word in re.findall(r"[a-z]+", s):
        for target in ("negative", "positive"):
            if len(word) >= 6 and difflib.SequenceMatcher(None, word, target).ratio() >= 0.8:
                return target
    return ""


class ImportFailed(Exception):
    pass


def read_workbook(path):
    """{sheet name: DataFrame with the first row as headers}."""
    try:
        return pd.read_excel(path, sheet_name=None, header=0)
    except PermissionError:
        raise ImportFailed("The file is locked. Close it in Excel and try again.")
    except Exception as e:
        raise ImportFailed(f"The workbook couldn't be read: {e}")


@dataclass
class ImportChoice:
    sheet: str
    run_col: str
    gain_col: object = None     # str or None
    sample_col: object = None
    lookup: object = None       # (sheet, key column, label column) or None


@dataclass
class PreviewRow:
    run: int
    path: object
    gain: object
    label: str
    role: str
    status: str
    ticked: bool


def _norm(v):
    """Cell value as text for matching: 7.0 -> "7", " CH3 " -> "CH3", empty -> ""."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _as_int(v):
    s = _norm(v)
    try:
        f = float(s)
    except ValueError:
        return None
    return int(f) if f.is_integer() else None


def _filled(series):
    return any(_norm(v) for v in series)


def guess_choice(sheets):
    names = list(sheets)
    sheet = names[0]
    df = sheets[sheet]
    cols = [str(c) for c in df.columns]
    low = {c.strip().lower(): c for c in cols}
    run_col = low.get("run", cols[0])
    gain_col = next((c for c in cols if "gain" in c.lower()), None)
    sample_col = next((low[h] for h in SAMPLE_HEADERS if h in low and _filled(df[low[h]])), None)
    lookup = None
    if sample_col is not None:
        values = {_norm(v) for v in df[sample_col]} - {""}
        for other in names:
            if other == sheet:
                continue
            o = sheets[other]
            filled = [c for c in o.columns if _filled(o[c])]
            if len(filled) == 2 and values & ({_norm(v) for v in o[filled[0]]} - {""}):
                lookup = (other, str(filled[0]), str(filled[1]))
                break
    return ImportChoice(sheet, run_col, gain_col, sample_col, lookup)


def _under(path, folder):
    p, f = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(folder))
    return p.startswith(f.rstrip("\\/") + os.sep)


def build_preview(sheets, choice, excel_path, traces):
    """One PreviewRow per sheet row with a run number; traces = [(path, run #)] of loaded traces."""
    df = sheets[choice.sheet]
    table = {}
    if choice.lookup:
        lk = sheets[choice.lookup[0]]
        table = {_norm(k): _norm(v) for k, v in zip(lk[choice.lookup[1]], lk[choice.lookup[2]]) if _norm(k)}
    folder = os.path.dirname(os.path.abspath(excel_path))
    out = []
    for _, row in df.iterrows():
        run = _as_int(row[choice.run_col])
        if run is None:
            continue
        gain, gain_ok = None, True
        if choice.gain_col is not None and _norm(row[choice.gain_col]):
            try:
                gain = float(_norm(row[choice.gain_col]))
                gain_ok = math.isfinite(gain)
            except ValueError:
                gain_ok = False
        sample = _norm(row[choice.sample_col]) if choice.sample_col is not None else ""
        label = table.get(sample, sample) if choice.lookup else sample
        if not sample:
            label = ""
        matches = [p for p, r in traces if r == run and _under(p, folder)]
        if not matches:
            status = "no loaded trace"
        elif len(matches) > 1:
            status = f"conflict: {len(matches)} files"
        elif not gain_ok:
            status = "gain not a number"
        else:
            status = "ok"
        out.append(PreviewRow(run, matches[0] if len(matches) == 1 else None, gain if gain_ok else None,
                              label, role_from_label(label), status, status == "ok"))
    return out


def apply_preview(store, rows):
    n = 0
    for r in rows:
        if r.ticked and r.path:
            store[file_key(r.path)] = RunInfo(r.role, r.label, r.gain)
            n += 1
    return n
