"""Persistent settings for noise analysis (watch-list, spike k, lowest frequency)."""
import json
import math
import os
import re
import tempfile

DEFAULTS = {"watch_hz": [], "spike_k": 10.0, "f_min_hz": 2.0}


def default_settings_path():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "BBAnalyzer", "settings.json")


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _fresh_defaults():
    return {"watch_hz": list(DEFAULTS["watch_hz"]), "spike_k": DEFAULTS["spike_k"],
            "f_min_hz": DEFAULTS["f_min_hz"]}


def load_settings(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return _fresh_defaults()
        out = _fresh_defaults()
        if "watch_hz" in raw:
            w = raw["watch_hz"]
            if not isinstance(w, list) or not all(_is_num(x) for x in w):
                return _fresh_defaults()
            out["watch_hz"] = [float(x) for x in w]
        for key in ("spike_k", "f_min_hz"):
            if key in raw:
                if not _is_num(raw[key]):
                    return _fresh_defaults()
                out[key] = float(raw[key])
        return out
    except (OSError, ValueError):
        return _fresh_defaults()


def save_settings(path, settings):
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _number(text):
    try:
        v = float(text)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def parse_settings(watch_text, k_text, f_min_text):
    errors = []
    watch = []
    for tok in [t for t in re.split(r"[,\s]+", watch_text or "") if t]:
        v = _number(tok)
        if v is None:
            errors.append(f'Watch-list entry "{tok}" is not a number.')
        elif v <= 0:
            errors.append(f'Watch-list entry "{tok}" must be greater than 0.')
        elif v not in watch:
            watch.append(v)
    k = _number((k_text or "").strip())
    if k is None:
        errors.append(f'Spike k "{k_text}" is not a number.')
    elif not 0.5 <= k <= 1000:
        errors.append(f'Spike k "{k_text}" must be between 0.5 and 1000.')
    f_min = _number((f_min_text or "").strip())
    if f_min is None:
        errors.append(f'Lowest frequency (f_min) "{f_min_text}" is not a number.')
    elif f_min <= 0:
        errors.append(f'Lowest frequency (f_min) "{f_min_text}" must be greater than 0.')
    return {"watch_hz": watch, "spike_k": k, "f_min_hz": f_min}, errors
