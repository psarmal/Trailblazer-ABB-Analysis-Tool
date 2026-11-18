#!/usr/bin/env python3
"""
CSV Simulator
-------------
Writes fake BB DAQ run files in the same layout as the real instrument CSVs,
so the Multi-File Analyzer can be tried without real data.

File layout:

    # BB DAQ run 1 · experiment "Sim test" · started 2026-10-16 08:25:50
    # sample_rate_hz 50000
    # led 2 at 100%
    # pmt_gain_mv 800
    # concentration_nm 15
    # position_mm -33 -56 5e-06
    # recipe "Sim recipe" step 7 channel CH12      (only with --recipe)
    time_s,value_mV
    0.00000,-0.9375
    ...
    # samples 250880
    # duration_s 5.01758
    # max_mv 9.5911
    # min_mv -15.5859
    # mean_mv -0.4569
    # std_mv 1.7929
    # stopped_early no

Run:
    python csv_simulator.py                         # 5 runs, 1 kHz, 2 s, into ./simulated_data
    python csv_simulator.py -n 3 -r 50000 -d 5      # bigger, faster-sampled runs
    python csv_simulator.py --recipe "Scan Titration" -n 10 -d 1
    python csv_simulator.py --help
"""

import argparse
import math
import os
from datetime import datetime, timedelta

import numpy as np

ADC_STEP_MV = 2000.0 / 65536          # 16-bit over ±1 V, like the real data (0.0305 mV steps)
CONCENTRATIONS = [0, 1, 5, 10, 15, 25, 50, 100]   # nM


def simulate_signal(n, rate, conc, gain, rng, baseline, noise, signal_per_nm):
    """Baseline + first-order rise (size grows with concentration and PMT gain) + noise, ADC-quantized."""
    t = np.arange(n) / rate
    duration = max(n / rate, 1e-9)
    t_on = 0.25 * duration
    tau = max(0.08 * duration, 2.0 / rate)
    amp = signal_per_nm * conc * gain / 800.0
    rise = np.where(t >= t_on, 1 - np.exp(-(t - t_on) / tau), 0.0)
    mv = baseline + amp * rise + rng.normal(0, noise * gain / 800.0 + 0.3, n)
    spikes = rng.random(n) < 0.0005                    # occasional PMT spikes
    mv[spikes] += rng.normal(0, 6 * noise, spikes.sum())
    mv = np.round(mv / ADC_STEP_MV) * ADC_STEP_MV
    return t, mv


def write_file(path, header_lines, t, mv, stopped_early, t_decimals):
    with open(path, "w", newline="\n", encoding="utf-8") as fh:
        for line in header_lines:
            fh.write(f"# {line}\n")
        fh.write("time_s,value_mV\n")
        np.savetxt(fh, np.column_stack([t, mv]), fmt=[f"%.{t_decimals}f", "%.4f"], delimiter=",")
        fh.write(f"# samples {len(mv)}\n")
        fh.write(f"# duration_s {t[-1]:.{t_decimals}f}\n")
        fh.write(f"# max_mv {mv.max():.4f}\n")
        fh.write(f"# min_mv {mv.min():.4f}\n")
        fh.write(f"# mean_mv {mv.mean():.4f}\n")
        fh.write(f"# std_mv {mv.std(ddof=0):.4f}\n")   # instrument uses population std (divide by n)
        fh.write(f"# stopped_early {'yes' if stopped_early else 'no'}\n")


def main():
    ap = argparse.ArgumentParser(description="Generate simulated BB DAQ run CSV files.")
    ap.add_argument("-n", "--files", type=int, default=5, help="number of runs (default 5)")
    ap.add_argument("-d", "--duration", type=float, default=2.0, help="run length in seconds (default 2)")
    ap.add_argument("-r", "--rate", type=float, default=1000.0, help="sample rate in Hz (default 1000)")
    ap.add_argument("--experiment", default="Simulated test", help="experiment name")
    ap.add_argument("--recipe", default=None,
                    help="recipe name; adds a '# recipe' line and steps gain/position like a scan")
    ap.add_argument("--gain", type=float, default=800, help="PMT gain in mV (default 800)")
    ap.add_argument("--baseline", type=float, default=-0.45, help="baseline in mV (default -0.45)")
    ap.add_argument("--noise", type=float, default=1.8, help="noise std in mV at gain 800 (default 1.8)")
    ap.add_argument("--signal", type=float, default=0.5, help="signal mV per nM at gain 800 (default 0.5)")
    ap.add_argument("--early", type=float, default=0.2, help="chance a run stops early, 0-1 (default 0.2)")
    ap.add_argument("--seed", type=int, default=None, help="random seed for repeatable files")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "simulated_data"),
                    help="output folder (default: simulated_data next to this script)")
    a = ap.parse_args()

    rng = np.random.default_rng(a.seed)
    os.makedirs(a.out, exist_ok=True)
    full_n = max(2, int(round(a.duration * a.rate)))
    t_dec = max(1, math.ceil(math.log10(a.rate)))
    start = datetime.now().replace(microsecond=0)
    x0, y0, z0 = (9.0, -59.276, 2.798) if a.recipe else (-33.0, -56.0, 5e-06)

    for i in range(a.files):
        run = i + 1
        if a.recipe:          # titration-style scan: gain steps up, stage moves along x
            gain = a.gain + 50 * i
            conc = 0 if i < a.files // 2 else CONCENTRATIONS[i % len(CONCENTRATIONS)]
            pos = f"{x0 + 0.663 * i:.5f} {y0} {z0 + rng.normal(0, 5e-5):.6f}"
        else:
            gain = a.gain
            conc = CONCENTRATIONS[i % len(CONCENTRATIONS)]
            pos = f"{x0:g} {y0:g} {z0 * run:g}"
        stopped = bool(rng.random() < a.early)
        n = int(full_n * rng.uniform(0.4, 0.9)) if stopped else int(full_n * rng.uniform(0.98, 1.02))

        t, mv = simulate_signal(n, a.rate, conc, gain, rng, a.baseline, a.noise, a.signal)
        started = (start + timedelta(seconds=15 * i)).strftime("%Y-%m-%d %H:%M:%S")
        header = [
            f'BB DAQ run {run} · experiment "{a.experiment}" · started {started}',
            f"sample_rate_hz {a.rate:g}",
            "led 2 at 100%",
            f"pmt_gain_mv {gain:g}",
            f"concentration_nm {conc:g}",
            f"position_mm {pos}",
        ]
        if a.recipe:
            header.append(f'recipe "{a.recipe}" step 7 channel CH12')
        path = os.path.join(a.out, f"run_{run:03d}.csv")
        write_file(path, header, t, mv, stopped, t_dec)
        print(f"wrote {path}  ({n:,} samples, gain {gain:g}, {conc:g} nM"
              f"{', stopped early' if stopped else ''})")


if __name__ == "__main__":
    main()
