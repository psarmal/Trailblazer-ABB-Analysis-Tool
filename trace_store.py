"""Compact trace storage with per-block statistics for fast window stats.

Does not depend on multi_file_analyzer. Samples are held as int32 codes
(round(mV * 10**d)) when that is lossless, else float64; time is kept as
(t_start, dt) when uniform, else as an explicit float64 array.

Cache layout. <cache_dir> (default %LOCALAPPDATA%\\BBAnalyzer\\cache) holds one folder per
source file, named sha1(abspath|size|mtime_ns|FORMAT_VERSION); an edited or re-exported file
therefore gets a new entry and the old one stays until the cache is cleared. An entry holds
meta.json plus one folder per trace (0, 1, ...) with mv.npy, optional time.npy, blocks.npy and
ov64/ov512/ov4096.npy, opened memory-mapped. A conversion is written to "<entry>.tmp-<pid>"
and renamed into place, so an entry is either complete or absent; an entry that fails to open
for any reason is deleted and converted again. clear_cache renames a whole entry folder to
"<entry>.del-<pid>" first (this fails while any of its files is mapped, so entries of loaded
traces survive) and then deletes it; temp/del folders of other live processes are left alone.
If the cache folder cannot be written the trace is kept in memory ("memory" status).
"""
import hashlib
import json
import math
import os
import shutil
import sys

import numpy as np

BLOCK = 4096
UNIFORM_TOL = 1e-6  # relative to dt
OV_LEVELS = (64, 512, 4096)  # overview cell sizes (samples per min/max cell)

BLOCK_DTYPE = np.dtype([("n", "i4"), ("mean", "f8"), ("m2", "f8"), ("vmin", "f8"),
                        ("vmax", "f8"), ("imin", "i8"), ("imax", "i8")])

OV_DTYPE = np.dtype([("vmin", "f8"), ("vmax", "f8"), ("imin", "i8"), ("imax", "i8")])

_INT32_MAX = np.iinfo(np.int32).max
FORMAT_VERSION = 1


PREFIX = 65536  # samples used to guess the number of decimals before one full verifying pass


def _mv_codes(mv, d):
    """(codes, scale) when round(mv*10**d) fits int32 and reproduces mv exactly; "big" when it
    no longer fits (larger d only grows the codes); None otherwise."""
    scale = 10.0 ** d
    scaled = np.rint(mv * scale)
    if np.abs(scaled).max() > _INT32_MAX:
        return "big"
    codes = scaled.astype(np.int32)
    return (codes, scale) if np.array_equal(codes / scale, mv) else None


def encode_values(mv):
    """(int32 codes, 10.0**d) for the smallest lossless d in 0..6, else (float64, None).

    d is guessed on a prefix (cheap, up to 7 short passes). A d that fails on the prefix fails on
    the whole array, so the search over the whole array starts at the guess and the result is the
    same smallest d as a plain search from 0, usually after a single full pass."""
    mv = np.asarray(mv, dtype=np.float64)
    if mv.size == 0:
        return mv.astype(np.int32), 1.0
    if np.isfinite(mv).all():
        start = 0
        if mv.size > PREFIX:
            head = mv[:PREFIX]
            start = 7
            for d in range(7):
                r = _mv_codes(head, d)
                if r is not None:
                    start = d
                    break
        for d in range(start, 7):
            r = _mv_codes(mv, d)
            if r == "big":
                break
            if r is not None:
                return r
    return mv, None


def uniform_step(t):
    """dt if t is uniformly spaced within 1e-6*dt, else None (also None for n < 2)."""
    t = np.asarray(t, dtype=np.float64)
    n = len(t)
    if n < 2:
        return None
    dt = (t[-1] - t[0]) / (n - 1)
    if not dt > 0:
        return None
    err = np.abs(t - (t[0] + np.arange(n) * dt)).max()
    return float(dt) if err <= UNIFORM_TOL * dt else None


def time_decimals(t):
    """Smallest d in 0..9 with rint(t*10**d)/10**d == t for every sample, else None."""
    t = np.asarray(t, dtype=np.float64)
    if not np.isfinite(t).all():
        return None
    start = 0
    if t.size > PREFIX:   # a d that fails on the prefix fails on the whole array
        head = t[:PREFIX]
        start = 10
        for d in range(10):
            if np.array_equal(_grid(head, d), head):
                start = d
                break
    for d in range(start, 10):
        if np.array_equal(_grid(t, d), t):
            return d
    return None


def build_blocks(mv_f64, block=BLOCK):
    """Per-block (n, mean, M2, min, max, first argmin, first argmax); absolute indices."""
    mv = np.asarray(mv_f64, dtype=np.float64)
    nb = -(-len(mv) // block)
    out = np.zeros(nb, dtype=BLOCK_DTYPE)
    for b in range(nb):
        s = b * block
        seg = mv[s:s + block]
        mean = float(seg.mean())
        out[b] = (len(seg), mean, float(((seg - mean) ** 2).sum()), seg.min(), seg.max(),
                  s + int(seg.argmin()), s + int(seg.argmax()))
    return out


def build_overview(mv_f64, size):
    """Min/max (with first argmin/argmax, absolute) per `size` samples; last cell may be partial."""
    mv = np.asarray(mv_f64, dtype=np.float64)
    n = len(mv)
    nfull = n // size
    out = np.zeros(-(-n // size), dtype=OV_DTYPE)
    if nfull:
        m = mv[:nfull * size].reshape(nfull, size)
        amin, amax = m.argmin(axis=1), m.argmax(axis=1)
        rows = np.arange(nfull)
        out["vmin"][:nfull], out["vmax"][:nfull] = m[rows, amin], m[rows, amax]
        out["imin"][:nfull] = rows * size + amin
        out["imax"][:nfull] = rows * size + amax
    if nfull < len(out):
        seg = mv[nfull * size:]
        amin, amax = int(seg.argmin()), int(seg.argmax())
        out[nfull] = (seg[amin], seg[amax], nfull * size + amin, nfull * size + amax)
    return out


def _part(seg, base):
    """(n, mean, M2, vmin, imin, vmax, imax) of a raw segment starting at absolute index base."""
    mean = float(seg.mean())
    imin, imax = int(seg.argmin()), int(seg.argmax())
    return (len(seg), mean, float(((seg - mean) ** 2).sum()),
            float(seg[imin]), base + imin, float(seg[imax]), base + imax)


def _blocks_part(rows):
    """(n, mean, M2, vmin, imin, vmax, imax) of consecutive whole blocks, combined in one step:
    N = sum n, mean = sum(n*mean)/N, M2 = sum M2 + sum n*(mean_b - mean)**2 (no cancellation);
    the extremes are those of the first block holding the min/max (its stored index is the first)."""
    rows = np.asarray(rows)
    n = rows["n"].astype(np.float64)
    means = rows["mean"]
    total = int(rows["n"].sum(dtype=np.int64))
    mean = float((n * means).sum() / total)
    m2 = float(rows["m2"].sum() + (n * (means - mean) ** 2).sum())
    lo, hi = int(rows["vmin"].argmin()), int(rows["vmax"].argmax())
    return (total, mean, m2, float(rows["vmin"][lo]), int(rows["imin"][lo]),
            float(rows["vmax"][hi]), int(rows["imax"][hi]))


def _grid(x, decimals):
    """Round to `decimals` places the way text parsing does (integer, then divide: correctly rounded)."""
    scale = 10.0 ** decimals
    return np.rint(np.asarray(x, dtype=np.float64) * scale) / scale


class TraceData:
    def __init__(self, n, t_start, t_end, dt, t, codes, scale, blocks, block=BLOCK,
                 overviews=None, t_dec=None):
        self.n = n
        self.t_start = t_start
        self.t_end = t_end
        self.dt = dt          # None when time is explicit
        self.t = t            # explicit float64 time array, or None when uniform
        self.t_dec = t_dec    # decimals the uniform times are rounded to (None when explicit)
        self.codes = codes
        self.scale = scale    # None when codes are float64 mV
        self.blocks = blocks
        self.block = block
        self.overviews = overviews if overviews is not None else {}
        self.entry_dir = None  # cache entry folder; None for a trace held in memory

    @classmethod
    def from_arrays(cls, t, mv):
        t = np.asarray(t, dtype=np.float64)
        mv = np.asarray(mv, dtype=np.float64)
        codes, scale = encode_values(mv)
        n = len(t)
        dt = uniform_step(t)
        t_dec = None
        if dt is not None:
            # Uniform only if the rounded grid reproduces every parsed time bit for bit.
            t_dec = time_decimals(t)
            if t_dec is None or not np.array_equal(_grid(t[0] + np.arange(n) * dt, t_dec), t):
                dt, t_dec = None, None
        return cls(n, float(t[0]) if n else math.nan, float(t[-1]) if n else math.nan, dt,
                   None if dt is not None else t.copy(), codes, scale, build_blocks(mv),
                   overviews={s: build_overview(mv, s) for s in OV_LEVELS}, t_dec=t_dec)

    def index_range(self, t0, t1):
        """Index range [i0, i1) of samples with t0 <= t <= t1."""
        n = self.n
        if n == 0:
            return 0, 0
        if self.dt is not None:
            i0 = math.ceil((t0 - self.t_start) / self.dt - 1e-6)
            i1 = math.floor((t1 - self.t_start) / self.dt + 1e-6) + 1
        else:
            i0 = int(np.searchsorted(self.t, t0, "left"))
            i1 = int(np.searchsorted(self.t, t1, "right"))
        i0 = min(max(i0, 0), n)
        i1 = min(max(i1, i0), n)
        return i0, i1

    def times(self, i0, i1):
        if self.dt is not None:
            return _grid(self.t_start + np.arange(i0, i1) * self.dt, self.t_dec)
        return self.t[i0:i1]

    def values(self, i0, i1):
        seg = self.codes[i0:i1]
        if self.scale is None:
            return seg
        return seg / self.scale

    def values_into(self, i0, i1, out):
        """values(i0, i1) written into the float64 array out (length i1 - i0); no temporary."""
        seg = self.codes[i0:i1]
        if self.scale is None:
            np.copyto(out, seg)
        else:
            np.divide(seg, self.scale, out=out)
        return out

    def block_rows(self, i0, i1):
        """Split [i0, i1) into whole cache blocks and the partial ends.

        Returns (rows of the whole blocks, head index range, tail index range); the head, the
        blocks and the tail together cover [i0, i1) exactly. With no whole block, the head is
        all of [i0, i1) and the tail is empty."""
        B = self.block
        b0, b1 = -(-i0 // B), i1 // B
        if b0 >= b1:
            return self.blocks[0:0], (i0, i1), (i1, i1)
        return self.blocks[b0:b1], (i0, b0 * B), (b1 * B, i1)

    def _time_at(self, i):
        if self.dt is not None:
            return float(_grid(self.t_start + i * self.dt, self.t_dec))
        return float(self.t[i])

    def stats(self, t0, t1, population=True):
        i0, i1 = self.index_range(t0, t1)
        n = i1 - i0
        if n == 0:
            nan = math.nan
            return {"Points": 0, "Max": nan, "Min": nan, "Mean": nan, "Std": nan,
                    "Time of max": nan, "Time of min": nan, "RMS": nan}
        B = self.block
        b0, b1 = -(-i0 // B), i1 // B  # whole blocks [b0, b1)
        parts = []  # in index order
        if b0 >= b1:
            parts.append(_part(self.values(i0, i1), i0))
        else:
            if i0 < b0 * B:
                parts.append(_part(self.values(i0, b0 * B), i0))
            parts.append(_blocks_part(self.blocks[b0:b1]))
            if b1 * B < i1:
                parts.append(_part(self.values(b1 * B, i1), b1 * B))
        cn, mean, m2, vmin, imin, vmax, imax = parts[0]
        for pn, pm, pm2, pvmin, pimin, pvmax, pimax in parts[1:]:
            delta = pm - mean
            tot = cn + pn
            mean += delta * pn / tot
            m2 += pm2 + delta * delta * cn * pn / tot
            cn = tot
            if pvmin < vmin:   # strict: ties keep the earlier index
                vmin, imin = pvmin, pimin
            if pvmax > vmax:
                vmax, imax = pvmax, pimax
        ddof = 0 if population else 1
        return {
            "Points": n, "Max": vmax, "Min": vmin, "Mean": mean,
            "Std": math.sqrt(m2 / (n - ddof)) if n > ddof else math.nan,
            "Time of max": float(self._time_at(imax)), "Time of min": float(self._time_at(imin)),
            "RMS": math.sqrt(mean * mean + m2 / n),
        }

    def _extrema_idx(self, a, b):
        """Sorted indices of the min and max of raw samples [a, b) (b > a)."""
        seg = self.codes[a:b]
        return np.sort([a + int(seg.argmin()), a + int(seg.argmax())])

    def _edge(self, a, b):
        """(indices, values) of the min and max of raw samples [a, b), in index order."""
        idx = self._extrema_idx(a, b)
        return idx, self.values_at(idx)

    def view(self, x0, x1, raw_limit=12000, bins=3000):
        """Points to draw for [x0, x1]: (t, mv, every_point_shown).

        Same contract as multi_file_analyzer.view_points, but once k = m // bins reaches an
        overview size the min/max of each slice come from the overview cells (slice edges
        aligned to cells), so only the partial cells at the two ends read raw samples."""
        i0, i1 = self.index_range(x0, x1)
        i0, i1 = max(0, i0 - 1), min(self.n, i1 + 1)
        m = i1 - i0
        if m <= raw_limit:
            return self.times(i0, i1), self.values(i0, i1), True
        k = m // bins
        sizes = [s for s in OV_LEVELS if s <= k and s in self.overviews]
        if not sizes:
            end = i0 + k * bins
            ys = self.codes[i0:end].reshape(bins, k)
            amin, amax = ys.argmin(axis=1), ys.argmax(axis=1)
            base = i0 + np.arange(bins) * k
            idx = np.column_stack([base + np.minimum(amin, amax),
                                   base + np.maximum(amin, amax)]).ravel()
            if end < i1:
                idx = np.concatenate([idx, self._extrema_idx(end, i1)])
        else:
            size = sizes[-1]
            ov = self.overviews[size]
            ca, cb = -(-i0 // size), min(i1 // size, self.n // size)  # whole cells [ca, cb)
            C = cb - ca
            nb = min(bins, C)
            edges = ca + (np.arange(nb + 1) * C) // nb          # cell boundaries of the slices
            starts, ends = edges[:-1], edges[1:]
            cols = np.arange(int(np.max(ends - starts)))
            cells = starts[:, None] + cols
            ok = cells < ends[:, None]
            cells = np.minimum(cells, cb - 1)
            rows = np.arange(nb)
            lo = np.where(ok, ov["vmin"][cells], np.inf).argmin(axis=1)
            hi = np.where(ok, ov["vmax"][cells], -np.inf).argmax(axis=1)
            imin, imax = ov["imin"][cells[rows, lo]], ov["imax"][cells[rows, hi]]
            # Values come from the overview cells (they are those samples, bit for bit); raw
            # samples are read only at the two edges. Gathering ~2*bins scattered samples from
            # mv.npy would touch ~2*bins pages: hard page faults when the file is not cached.
            vlo, vhi = ov["vmin"][cells[rows, lo]], ov["vmax"][cells[rows, hi]]
            first = imin <= imax
            parts = []
            if i0 < ca * size:
                parts.append(self._edge(i0, ca * size))
            parts.append((
                np.column_stack([np.where(first, imin, imax), np.where(first, imax, imin)]).ravel(),
                np.column_stack([np.where(first, vlo, vhi), np.where(first, vhi, vlo)]).ravel()))
            if cb * size < i1:
                parts.append(self._edge(cb * size, i1))
            idx = np.concatenate([p[0] for p in parts])
            vals = np.concatenate([np.asarray(p[1], dtype=np.float64) for p in parts])
            return self.times_at(idx), vals, False
        return self.times_at(idx), self.values_at(idx), False

    def times_at(self, idx):
        if self.dt is not None:
            return _grid(self.t_start + idx * self.dt, self.t_dec)
        return self.t[idx]

    def values_at(self, idx):
        seg = self.codes[idx]
        return seg if self.scale is None else seg / self.scale

    def save(self, folder):
        """Write the arrays into `folder` (created); returns the info open() needs."""
        os.makedirs(folder)
        np.save(os.path.join(folder, "mv.npy"), self.codes)
        if self.t is not None:
            np.save(os.path.join(folder, "time.npy"), self.t)
        np.save(os.path.join(folder, "blocks.npy"), self.blocks)
        for size, ov in self.overviews.items():
            np.save(os.path.join(folder, f"ov{size}.npy"), ov)
        return {"n": self.n, "t_start": self.t_start, "t_end": self.t_end, "dt": self.dt,
                "t_dec": self.t_dec, "scale": self.scale, "block": self.block,
                "has_time": self.t is not None, "overviews": sorted(self.overviews)}

    @classmethod
    def open(cls, folder, info):
        """Memory-mapped TraceData from a folder written by save()."""
        def load(name):
            return np.load(os.path.join(folder, name), mmap_mode="r")
        if info["dt"] is not None and info["t_dec"] is None:
            raise ValueError("uniform time without t_dec")
        d = cls(info["n"], info["t_start"], info["t_end"], info["dt"],
                load("time.npy") if info["has_time"] else None,
                load("mv.npy"), info["scale"], load("blocks.npy"), info["block"],
                {int(s): load(f"ov{s}.npy") for s in info["overviews"]},
                t_dec=info["t_dec"])   # KeyError for an entry without it: treated as incomplete
        d.entry_dir = os.path.dirname(os.path.abspath(folder))
        return d


_INFO_KEYS = ("n", "t_start", "t_end", "dt", "t_dec", "scale", "block", "has_time", "overviews", "dir")


def default_cache_dir():
    return os.path.join(os.environ["LOCALAPPDATA"], "BBAnalyzer", "cache")


def entry_key(path):
    st = os.stat(path)
    raw = f"{os.path.normcase(os.path.abspath(path))}|{st.st_size}|{st.st_mtime_ns}|{FORMAT_VERSION}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def is_cloud_only(path):
    """True for an online-only OneDrive file (RECALL_ON_DATA_ACCESS / OFFLINE)."""
    try:
        return bool(getattr(os.stat(path), "st_file_attributes", 0) & (0x400000 | 0x1000))
    except OSError:
        return False


def _open_entry(entry):
    """[(fields, TraceData)] of a complete entry folder, or None if missing/incomplete/corrupt."""
    try:
        with open(os.path.join(entry, "meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
        if meta["version"] != FORMAT_VERSION:
            return None
        out = []
        for info in meta["traces"]:
            fields = {k: v for k, v in info.items() if k not in _INFO_KEYS}
            out.append((fields, TraceData.open(os.path.join(entry, info["dir"]), info)))
        return out
    except Exception:   # any unreadable/corrupt entry (np.load raises EOFError on an empty file, ...)
        return None


def is_cached(path, cache_dir):
    try:
        entry = os.path.join(cache_dir, entry_key(path))
    except OSError:
        return False
    return _open_entry(entry) is not None


def _as_traces(pairs):
    return [dict(fields, data=data, n=data.n) for fields, data in pairs]


def open_traces(path, parse, cache_dir=None):
    """(trace dicts, status). Dicts are what `parse` returns minus t/mv, plus data (TraceData)
    and n; status is "cached", "converted" or "memory" (cache unusable, held in RAM).
    Parser errors propagate; cache problems never do."""
    cache_dir = cache_dir or default_cache_dir()
    try:
        entry = os.path.join(cache_dir, entry_key(path))
    except OSError:
        entry = None  # let the parser report the unreadable source
    if entry is not None:
        pairs = _open_entry(entry)
        if pairs is not None:
            return _as_traces(pairs), "cached"
    parsed = parse(path)
    mem = []
    for tr in parsed:
        fields = {k: v for k, v in tr.items() if k not in ("t", "mv")}
        mem.append((fields, TraceData.from_arrays(tr["t"], tr["mv"])))
    del parsed
    if entry is None:
        return _as_traces(mem), "memory"
    tmp = f"{entry}.tmp-{os.getpid()}"
    try:
        pairs = _open_entry(entry)  # another opener may have finished during the parse
        if pairs is not None:
            return _as_traces(pairs), "cached"
        if os.path.isdir(entry):  # just failed the completeness check: corrupt
            shutil.rmtree(entry, ignore_errors=True)
            if os.path.isdir(entry):  # files still mapped; keep this run in memory
                return _as_traces(mem), "memory"
        os.makedirs(cache_dir, exist_ok=True)
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp)
        st = os.stat(path)
        traces = []
        for i, (fields, data) in enumerate(mem):
            info = data.save(os.path.join(tmp, str(i)))
            traces.append(dict(json.loads(json.dumps(fields, default=str)), dir=str(i), **info))
        meta = {"version": FORMAT_VERSION, "source": os.path.abspath(path),
                "size": st.st_size, "mtime_ns": st.st_mtime_ns, "traces": traces}
        with open(os.path.join(tmp, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f)
        try:
            os.replace(tmp, entry)
        except OSError:
            pass  # a concurrent conversion may have won; use its entry if it is complete
        shutil.rmtree(tmp, ignore_errors=True)
        pairs = _open_entry(entry)
        if pairs is not None:
            return _as_traces(pairs), "converted"
    except OSError:
        pass
    shutil.rmtree(tmp, ignore_errors=True)
    return _as_traces(mem), "memory"


def _dir_bytes(folder):
    total = 0
    for root, _dirs, files in os.walk(folder):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def _children(cache_dir):
    try:
        return [os.path.join(cache_dir, n) for n in os.listdir(cache_dir)]
    except OSError:
        return []


def cache_info(cache_dir):
    """(complete entries, total bytes including leftover temp folders)."""
    entries = size = 0
    for p in _children(cache_dir):
        if not os.path.isdir(p):
            continue
        size += _dir_bytes(p)
        if _is_entry_name(os.path.basename(p)):
            pairs = _open_entry(p)
            if pairs is not None:
                entries += 1
            del pairs
    return entries, size


def _is_entry_name(name):
    return ".tmp-" not in name and ".del-" not in name


def pid_alive(pid):
    """True if a process with this id is running (conservatively True when it cannot be told)."""
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            return True
        return True
    import ctypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    k32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    h = k32.OpenProcess(0x1000, 0, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ctypes.get_last_error() == 5   # access denied: exists; invalid parameter: gone
    try:
        code = ctypes.c_uint32()
        if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
            return True
        return code.value == 259   # STILL_ACTIVE
    finally:
        k32.CloseHandle(h)


def _owned_by_other_live_process(name):
    """True for a "<entry>.tmp-<pid>" / ".del-<pid>" folder whose pid is a live process other than
    this one (another window converting or clearing right now)."""
    _base, _sep, pid = name.rpartition("-")
    if not pid.isdigit():
        return False
    pid = int(pid)
    return pid != os.getpid() and pid_alive(pid)


def clear_cache(cache_dir):
    """(removed, kept). An entry is deleted by renaming its whole folder first; that fails while any
    file inside is mapped, leaving the entry untouched, and it is counted as kept. Leftover
    temp/del folders are removed when possible and never counted, except those of another live
    process (its conversion may be running; see pid_alive). Never raises."""
    removed = kept = 0
    for p in _children(cache_dir):
        if not os.path.isdir(p):
            continue
        if not _is_entry_name(os.path.basename(p)):
            if not _owned_by_other_live_process(os.path.basename(p)):
                shutil.rmtree(p, ignore_errors=True)
            continue
        gone = f"{p}.del-{os.getpid()}"
        try:
            shutil.rmtree(gone, ignore_errors=True)
            os.rename(p, gone)
        except OSError:
            kept += 1
            continue
        shutil.rmtree(gone, ignore_errors=True)
        removed += 1
    return removed, kept
