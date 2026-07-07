#!/usr/bin/env python3
"""Comprehensive QC metrics for one phase of the SN segmentation workspace.

Extends qc_metrics.py: in addition to Dice it computes Hausdorff (max + 95th
percentile) and average surface distance, for BOTH rater-vs-expert and
rater-vs-rater, then rolls the per-scan numbers up the full reporting hierarchy
(per scan, per rater, per rater-pair, and a phase grand average). It also writes
a data inventory that records exactly which mask file was used for each
rater/subject and flags anomalies (missing masks, non-standard names, extra
attempts) instead of silently dropping them.

Attempt-aware: the available attempts (``a1``, ``a2``, ...) are detected per
phase from the file names and each is analyzed independently. The
"attempts-average" is the **mean of the per-attempt metrics** (Dice, Hausdorff,
volumes, ...) for each scan/rater -- mirroring every other table, not a combined
mask. Nothing assumes a fixed attempt count; each scan is averaged over only the
attempts it has (``n_attempts`` records how many). Intra-rater agreement between
a rater's own attempts is reported by compute_attempt_reproducibility().

    python scripts/qc_analysis.py --phase phase2 --attempt 1 \
        --workspace ~/path/to/ManSeg_Workspace \
        --qc-root   ~/path/to/ManSeg_Workspace/qc
    # or omit --attempt to do every detected attempt + the mean-of-metrics average

Outputs go to <qc-root>/<phase>/<attempt>/metrics/ (attempt = a1, a2, ... or
attempts_average). Importable: compute_phase() and average_attempt_metrics()
return the tables so run_qc.py can drive the figures, reports and cross-phase
summary.
"""
from __future__ import annotations

import argparse
import re
from glob import glob
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

import utils

# Surface-distance columns carried through every table, in display order.
SD_METRICS = ["dice", "jaccard", "hd", "hd95", "assd"]
_ATTEMPT_RE = re.compile(r"_a(\d+)(?:_|\.|$)")   # _a1.  _a2.  _a1_sub0002.


# ----------------------------- mask discovery ----------------------------
def find_rater_attempts(out_dir, rater):
    """Detect a rater's segmentation attempts for one subject from the file
    naming convention (`<rater>_mask_a<N>...`).

    Returns dict: attempts {N: Path} for each detected attempt number, files
    (all matching), flags (anomaly notes). A lone file with no recognizable
    `_a<N>` token is treated as attempt 1 (a common single-attempt / typo case).
    """
    out_dir = Path(out_dir)
    files = sorted(glob(str(out_dir / f"{rater}_mask*.nii.gz")) +
                   glob(str(out_dir / f"{rater}_mask*.nii")))
    attempts, unrec, flags = {}, [], []
    for f in files:
        stem = Path(f).name
        m = _ATTEMPT_RE.search(stem)
        if m:
            n = int(m.group(1))
            if n in attempts:
                flags.append(f"duplicate a{n}: {stem} (kept {attempts[n].name})")
            else:
                attempts[n] = Path(f)
        else:
            unrec.append(Path(f))
        if not re.fullmatch(rf"{rater}_mask(_a\d+)?\.nii(\.gz)?", stem):
            flags.append(f"non-standard filename: {stem}")
    if unrec and not attempts:
        attempts[1] = unrec[0]                       # sole non-standard file -> a1
        flags.append(f"unrecognized attempt token; treated {unrec[0].name} as a1")
    elif unrec:
        flags.append("ignored non-attempt file(s): " +
                     ", ".join(p.name for p in unrec))
    return {"attempts": attempts, "files": [Path(f) for f in files], "flags": flags}


AVERAGE_DIR = "attempts_average"     # folder for the mean-of-metrics average


def attempt_dirname(selector):
    """Folder name for an integer attempt: 'a1', 'a2', … (the mean-of-metrics
    average uses AVERAGE_DIR directly)."""
    return AVERAGE_DIR if selector == "average" else f"a{selector}"


def detect_attempts(phase_dir, raters):
    """Sorted list of attempt numbers present anywhere under a phase's rater
    outputs. Adapts to the data rather than the configured `attempts` count."""
    found = set()
    for r in raters:
        base = Path(phase_dir) / r / "outputs"
        if base.is_dir():
            for sub_out in base.iterdir():
                if sub_out.is_dir():
                    found |= set(find_rater_attempts(sub_out, r)["attempts"])
    return sorted(found)


def load_bin(path, label):
    data, _, zooms = utils.load_mask(path)
    return (data == label), zooms


def select_mask(info, label, attempt):
    """Pick a rater's mask for attempt N (int) from a find_rater_attempts()
    result. Returns (mask_bool, zooms, 'a<N>') or None when that attempt is
    absent. Attempts are analyzed individually; the attempts-average is a
    mean of the per-attempt metrics (average_attempt_metrics), not a mask."""
    p = info["attempts"].get(int(attempt))
    if p is None:
        return None
    d, _, zooms = utils.load_mask(p)
    return (d == label), zooms, f"a{attempt}"


def resolve_rater_mask(phase_dir, rater, sub, label, attempt):
    """find_rater_attempts + select_mask for a subject. Returns
    (mask, zooms, provenance, info) or None."""
    info = find_rater_attempts(utils.output_dir(phase_dir, rater, sub), rater)
    sel = select_mask(info, label, attempt)
    return None if sel is None else (*sel, info)


# ----------------------------- core compute ------------------------------
def compute_phase(phase_key, workspace, phases, raters, attempt, label=1,
                  out_dir=None):
    """Compute every QC table for one phase under a single integer `attempt`.
    Only rater/scans that actually have that attempt contribute, so the analysis
    adapts to what is present. Returns a dict of DataFrames; writes them to
    <out_dir>/metrics/ when given.
    """
    pcfg = phases[phase_key]
    phase_dir = Path(workspace) / utils.phase_dirname(phase_key, pcfg)
    subjects = utils.find_subjects(phase_dir, raters)
    has_expert = bool(pcfg.get("expert_mask"))
    att = f"a{attempt}"

    rve_rows, rvr_rows, vol_rows, inv_rows = [], [], [], []
    for sub in subjects:
        expert, espace = None, None
        if has_expert:
            for r in raters:
                ep = utils.resolve(utils.input_dir(phase_dir, r, sub) / "expert_mask.nii.gz")
                if ep is not None:
                    expert, espace = load_bin(ep, label)
                    break

        masks, spacing = {}, espace
        for r in raters:
            info = find_rater_attempts(utils.output_dir(phase_dir, r, sub), r)
            sel = select_mask(info, label, attempt)
            inv_rows.append({
                "phase": phase_key, "attempt": att, "subject": sub, "rater": r,
                "available": ",".join(f"a{n}" for n in sorted(info["attempts"])) or "-",
                "used": sel[2] if sel else "(absent)",
                "flags": "; ".join(info["flags"]),
            })
            if sel is None:
                continue
            mask, zooms, _ = sel
            spacing = spacing or zooms
            masks[r] = mask
            vox = float(np.prod(zooms))
            vol_rows.append({"phase": phase_key, "attempt": att, "subject": sub,
                             "source": r, "n_vox": int(mask.sum()),
                             "vol_mm3": int(mask.sum()) * vox})

        if expert is not None:
            vox = float(np.prod(espace))
            vol_rows.append({"phase": phase_key, "attempt": att, "subject": sub,
                             "source": "expert", "n_vox": int(expert.sum()),
                             "vol_mm3": int(expert.sum()) * vox})
            for r, m in masks.items():
                sdm = utils.surface_distance_metrics(m, expert, spacing)
                rve_rows.append({
                    "phase": phase_key, "attempt": att, "subject": sub, "rater": r,
                    "dice": utils.dice(m, expert), "jaccard": utils.jaccard(m, expert),
                    "hd": sdm["hd"], "hd95": sdm["hd95"], "assd": sdm["assd"],
                    "vol_rater_mm3": int(m.sum()) * vox,
                    "vol_expert_mm3": int(expert.sum()) * vox,
                    "vol_ratio": (int(m.sum()) / int(expert.sum())) if expert.sum() else np.nan})

        for a, b in combinations(sorted(masks), 2):
            sdm = utils.surface_distance_metrics(masks[a], masks[b], spacing)
            rvr_rows.append({
                "phase": phase_key, "attempt": att, "subject": sub,
                "rater_a": a, "rater_b": b, "pair": f"{a}-{b}",
                "dice": utils.dice(masks[a], masks[b]),
                "jaccard": utils.jaccard(masks[a], masks[b]),
                "hd": sdm["hd"], "hd95": sdm["hd95"], "assd": sdm["assd"]})

    tables = {
        "rater_vs_expert_per_scan": pd.DataFrame(rve_rows),
        "rater_vs_rater_per_scan": pd.DataFrame(rvr_rows),
        "volumes": pd.DataFrame(vol_rows),
        "inventory": pd.DataFrame(inv_rows),
    }
    tables.update(_aggregate(phase_key, att, tables))

    if out_dir is not None:
        mdir = Path(out_dir) / "metrics"
        mdir.mkdir(parents=True, exist_ok=True)
        for name, df in tables.items():
            if df is not None and not df.empty:
                df.to_csv(mdir / f"{name}.csv", index=False)
    return tables


def compute_attempt_reproducibility(phase_key, workspace, phases, raters, label=1):
    """Intra-rater agreement between a rater's own attempts (a_i vs a_j) per
    subject, for every rater/scan that has >=2 attempts. Empty when the phase is
    single-attempt. This is the 'consistency across attempts' companion table."""
    pcfg = phases[phase_key]
    phase_dir = Path(workspace) / utils.phase_dirname(phase_key, pcfg)
    rows = []
    for sub in utils.find_subjects(phase_dir, raters):
        for r in raters:
            A = find_rater_attempts(utils.output_dir(phase_dir, r, sub), r)["attempts"]
            if len(A) < 2:
                continue
            for i, j in combinations(sorted(A), 2):
                mi, zi = load_bin(A[i], label)
                mj, _ = load_bin(A[j], label)
                sdm = utils.surface_distance_metrics(mi, mj, zi)
                rows.append({
                    "phase": phase_key, "subject": sub, "rater": r,
                    "attempt_a": f"a{i}", "attempt_b": f"a{j}",
                    "dice": utils.dice(mi, mj), "jaccard": utils.jaccard(mi, mj),
                    "hd": sdm["hd"], "hd95": sdm["hd95"], "assd": sdm["assd"],
                    "n_vox_a": int(mi.sum()), "n_vox_b": int(mj.sum())})
    return pd.DataFrame(rows)


def average_attempt_metrics(attempt_tables, phase_key):
    """Mean-of-metrics average across attempts, mirroring compute_phase output.

    `attempt_tables` is the list of per-attempt `tables` dicts. For each per-scan
    identity (subject x rater, subject x pair, subject x source) the metric
    columns are averaged over the attempts that produced it (`n_attempts` records
    how many, so it adapts when a scan has fewer attempts). The aggregation
    hierarchy is rebuilt from the averaged per-scan tables, so this folder mirrors
    a normal per-attempt analysis. No masks are combined -- this is purely the
    mean of the metrics. The 'attempt' label is 'mean'.
    """
    def _mean(name, keys):
        frames = [t[name] for t in attempt_tables
                  if name in t and not t[name].empty]
        if not frames:
            return pd.DataFrame()
        df = pd.concat(frames, ignore_index=True)
        metrics = [c for c in df.columns
                   if c not in keys + ["phase", "attempt"] and
                   pd.api.types.is_numeric_dtype(df[c])]
        g = df.groupby(keys, sort=False)
        agg = g[metrics].mean()
        agg["n_attempts"] = g.size()
        out = agg.reset_index()
        out.insert(0, "attempt", "mean")
        return out

    rve = _mean("rater_vs_expert_per_scan", ["phase", "subject", "rater"])
    rvr = _mean("rater_vs_rater_per_scan",
                ["phase", "subject", "rater_a", "rater_b", "pair"])
    vol = _mean("volumes", ["phase", "subject", "source"])

    # compact inventory: which attempts were averaged for each rater/scan
    inv_frames = [t["inventory"] for t in attempt_tables
                  if "inventory" in t and not t["inventory"].empty]
    inv = pd.DataFrame()
    if inv_frames:
        present = pd.concat(inv_frames, ignore_index=True)
        present = present[present["used"] != "(absent)"]
        rows = []
        for (ph, sub, r), g in present.groupby(["phase", "subject", "rater"], sort=False):
            flags = "; ".join(sorted({x for x in g["flags"] if isinstance(x, str) and x}))
            rows.append({"phase": ph, "attempt": "mean", "subject": sub, "rater": r,
                         "available": ",".join(sorted(g["attempt"])),
                         "used": f"mean({len(g)} attempt(s))", "flags": flags})
        inv = pd.DataFrame(rows)

    tables = {"rater_vs_expert_per_scan": rve, "rater_vs_rater_per_scan": rvr,
              "volumes": vol, "inventory": inv}
    tables.update(_aggregate(phase_key, "mean", tables))
    return tables


# ----------------------------- aggregation -------------------------------
def summarize(df, by, metrics):
    """mean/sd/min/max (+ n) of each metric, grouped by `by` (list) or overall
    when by is None. Sorted by the first metric's mean ascending (worst first)."""
    metrics = [m for m in metrics if m in df.columns]
    if df.empty:
        return pd.DataFrame()
    if by is None:
        row = {"n": len(df)}
        for m in metrics:
            row |= {f"{m}_mean": df[m].mean(), f"{m}_sd": df[m].std(),
                    f"{m}_min": df[m].min(), f"{m}_max": df[m].max()}
        return pd.DataFrame([row])
    rows = []
    for key, g in df.groupby(by):
        key = key if isinstance(key, tuple) else (key,)
        row = dict(zip(by, key)) | {"n": len(g)}
        for m in metrics:
            row |= {f"{m}_mean": g[m].mean(), f"{m}_sd": g[m].std(),
                    f"{m}_min": g[m].min(), f"{m}_max": g[m].max()}
        rows.append(row)
    out = pd.DataFrame(rows)
    return out.sort_values(f"{metrics[0]}_mean").reset_index(drop=True) if metrics else out


def _aggregate(phase_key, attempt, tables):
    rve, rvr = tables["rater_vs_expert_per_scan"], tables["rater_vs_rater_per_scan"]
    agg = {}
    if not rve.empty:
        agg["rve_by_rater"] = summarize(rve, ["rater"], SD_METRICS)
        agg["rve_by_scan"] = summarize(rve, ["subject"], SD_METRICS)
        agg["rve_phase_summary"] = summarize(rve, None, SD_METRICS).assign(
            phase=phase_key, attempt=attempt, comparison="rater_vs_expert")
    if not rvr.empty:
        agg["rvr_by_pair"] = summarize(rvr, ["pair"], SD_METRICS)
        agg["rvr_by_scan"] = summarize(rvr, ["subject"], SD_METRICS)
        agg["rvr_phase_summary"] = summarize(rvr, None, SD_METRICS).assign(
            phase=phase_key, attempt=attempt, comparison="rater_vs_rater")
    return agg


# -------------------------------- CLI ------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phase", default="phase1")
    ap.add_argument("--config-dir", type=Path, default=utils.CONFIG_DIR)
    ap.add_argument("--workspace", type=Path, default=None,
                    help="Override workspace_root from paths.yaml.")
    ap.add_argument("--qc-root", type=Path, default=None,
                    help="Override qc_output_root from paths.yaml.")
    ap.add_argument("--attempt", type=int, default=None,
                    help="Analyze only this attempt number; omit to analyze every "
                         "detected attempt plus the mean-of-metrics average.")
    ap.add_argument("--label", type=int, default=1)
    args = ap.parse_args()

    paths, phases, raters = utils.load_config(args.config_dir)
    workspace = args.workspace or Path(paths["workspace_root"])
    qc_root = args.qc_root or Path(paths["qc_output_root"])
    phase_dir = Path(workspace) / utils.phase_dirname(args.phase, phases[args.phase])

    attempts = [args.attempt] if args.attempt is not None else detect_attempts(phase_dir, raters)
    print(f"{args.phase}: attempts {attempts}")

    per = []
    for n in attempts:
        out_dir = qc_root / args.phase / attempt_dirname(n)
        tables = compute_phase(args.phase, workspace, phases, raters, n,
                               label=args.label, out_dir=out_dir)
        per.append(tables)
        print(f"  [a{n}] {len(tables['rater_vs_expert_per_scan'])} rater-vs-expert, "
              f"{len(tables['rater_vs_rater_per_scan'])} rater-vs-rater rows")

    if len(attempts) > 1:
        avg = average_attempt_metrics(per, args.phase)
        adir = qc_root / args.phase / AVERAGE_DIR / "metrics"
        adir.mkdir(parents=True, exist_ok=True)
        for name, df in avg.items():
            if df is not None and not df.empty:
                df.to_csv(adir / f"{name}.csv", index=False)
        repro = compute_attempt_reproducibility(args.phase, workspace, phases, raters,
                                                args.label)
        if not repro.empty:
            repro.to_csv(adir / "attempts_reproducibility.csv", index=False)
        print(f"  [{AVERAGE_DIR}] mean-of-metrics over {len(attempts)} attempts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
