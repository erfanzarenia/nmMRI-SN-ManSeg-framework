#!/usr/bin/env python3
"""Comprehensive QC metrics for one phase of the SN segmentation workspace.

In addition to Dice it computes Hausdorff (max + 95th percentile) and average
surface distance, for BOTH rater-vs-expert and rater-vs-rater, then rolls the
per-scan numbers up the full reporting hierarchy (per scan, per rater, per
rater-pair, and a phase grand average). It records exactly which mask file was
used for each rater/subject (inventory) and who has which attempt (coverage),
and flags anomalies instead of silently dropping them.

Mask discovery is tolerant but never silent. The standard name is
<rater>_mask_a<N>.nii.gz; other names are used when their meaning is clear
(a case-insensitive _A1, the 'ai' typo for a1, a lone mask with no attempt
token), each with a flag. Copies of the inputs, leftovers, empty masks and masks
that are not on the scan's image grid (e.g. saved into the wrong subject folder)
are set aside, also with a flag.

Attempt-aware: the attempts present (a1, a2, ...) are detected from the file
names and each is analyzed on its own; raters or scans without an attempt simply
do not contribute to it. The "attempts-average" is the mean of the per-attempt
metrics for each scan/rater (n_attempts records how many), not a combined mask,
and compute_attempt_reproducibility() gives each rater's agreement between their
own attempts.

run_qc.py is the entry point (figures, reports, coverage, summary). Standalone,
this writes the metric tables for one phase:

    python scripts/qc_analysis.py --phase phase2
"""
from __future__ import annotations

import argparse
import re
from functools import lru_cache
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

import utils

# Surface-distance columns carried through every table, in display order.
SD_METRICS = ["dice", "jaccard", "hd", "hd95", "assd"]
_TOKEN_RE = re.compile(r"(?:^|_)a(\d+|i)(?=_|$)", re.IGNORECASE)   # _a1, _A2, _ai (a1 typo)
_NIFTI_RE = re.compile(r"\.nii(\.gz)?$", re.IGNORECASE)
_INPUT_COPIES = {"image", "expert_mask"}


def _stem(name):
    return _NIFTI_RE.sub("", name)


def _standard(name, rater):
    return re.fullmatch(rf"{re.escape(rater)}_mask(_a\d+)?\.nii(\.gz)?", name) is not None


# ----------------------------- mask discovery ----------------------------
def find_rater_attempts(out_dir, rater):
    """Find a rater's mask for each attempt in one subject's outputs folder.

    Standard names are <rater>_mask_a<N>.nii.gz, or <rater>_mask.nii.gz (a1) in
    single-attempt phases. Other names are used when their meaning is clear, each
    with a flag: the attempt token is read case-insensitively (_A1), 'ai' is read
    as the a1 typo, and a lone mask with no attempt token counts as a1. Copies of
    the inputs and leftover files are ignored, also with a flag.

    Returns {"attempts": {N: Path}, "files": [all NIfTI files], "flags": [notes]}.
    """
    out_dir = Path(out_dir)
    files = sorted(p for p in out_dir.iterdir()
                   if p.is_file() and _NIFTI_RE.search(p.name)) if out_dir.is_dir() else []
    attempts, untagged, flags = {}, [], []
    for p in files:
        stem = _stem(p.name)
        if stem.lower() in _INPUT_COPIES:
            flags.append(f"ignored input copy {p.name}")
            continue
        m = _TOKEN_RE.search(stem)
        if not m:
            untagged.append(p)
            continue
        n = 1 if m.group(1).lower() == "i" else int(m.group(1))
        if n in attempts:                            # keep the standard name if any
            keep, drop = sorted((attempts[n], p), key=lambda q: not _standard(q.name, rater))
            attempts[n] = keep
            flags.append(f"two a{n} files, ignored {drop.name}")
        else:
            attempts[n] = p
    plain = [p for p in untagged if _stem(p.name) == f"{rater}_mask"]
    if plain and 1 not in attempts:                  # single-attempt name -> a1
        attempts[1] = plain[0]
        untagged.remove(plain[0])
    if untagged and not attempts:                    # one unnamed mask -> a1
        own = [p for p in untagged if p.name.upper().startswith(rater.upper())]
        pick = untagged if len(untagged) == 1 else own if len(own) == 1 else []
        if pick:
            attempts[1] = pick[0]
            untagged.remove(pick[0])
    if untagged:
        flags.append("ignored " + ", ".join(p.name for p in untagged))
    flags += [f"{p.name} read as a{n}" for n, p in sorted(attempts.items())
              if not _standard(p.name, rater)]
    return {"attempts": dict(sorted(attempts.items())), "files": files, "flags": flags}


def stray_files(phase_dir, rater):
    """NIfTI files saved directly in a rater's outputs/ folder, outside any
    subject folder. The analysis ignores them."""
    base = Path(phase_dir) / rater / "outputs"
    return sorted(p.name for p in base.iterdir()
                  if p.is_file() and _NIFTI_RE.search(p.name)) if base.is_dir() else []


def _grid(path):
    """(shape, affine) of a NIfTI, from its header when nibabel can read it."""
    import nibabel as nib
    try:
        img = nib.load(str(path))
        return tuple(img.shape[:3]), img.affine
    except Exception:
        data, aff, _ = utils.load_nii(path)
        return tuple(data.shape[:3]), aff


@lru_cache(maxsize=None)
def _input_grids(inputs_dir):
    """{subject: (shape, affine)} of the input images under one rater's inputs/."""
    grids = {}
    base = Path(inputs_dir)
    for sub in sorted(base.iterdir()) if base.is_dir() else []:
        img = utils.resolve(sub / "image.nii.gz")
        if img is not None:
            grids[sub.name] = _grid(img)
    return grids


def grid_problem(path, sub, grids):
    """Why a mask cannot be used for `sub`: it is not on that scan's image grid
    (naming the scan whose grid it does match, if any). None when it fits."""
    ref = grids.get(sub)
    if ref is None:
        return None
    shape, aff = _grid(path)

    def same(g):
        return g[0] == shape and np.allclose(g[1], aff, atol=1e-3)

    if same(ref):
        return None
    match = [s for s, g in grids.items() if s != sub and same(g)]
    return "not on this scan's image grid" + (f" (matches {', '.join(match)})" if match else "")


def rater_attempts(phase_dir, rater, sub):
    """find_rater_attempts for one subject, minus masks that are not on the
    subject's image grid (each set aside with a flag)."""
    info = find_rater_attempts(utils.output_dir(phase_dir, rater, sub), rater)
    grids = _input_grids(str(Path(phase_dir) / rater / "inputs"))
    for n, p in list(info["attempts"].items()):
        problem = grid_problem(p, sub, grids)
        if problem:
            del info["attempts"][n]
            info["flags"].append(f"set aside {p.name}: {problem}")
    return info


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
    info = rater_attempts(phase_dir, rater, sub)
    sel = select_mask(info, label, attempt)
    return None if sel is None or not sel[0].any() else (*sel, info)


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
            info = rater_attempts(phase_dir, r, sub)
            sel = select_mask(info, label, attempt)
            if sel is not None and not sel[0].any():
                info["flags"].append(f"{info['attempts'][attempt].name} has no "
                                     f"label-{label} voxels; not used")
                sel = None
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
            A = rater_attempts(phase_dir, r, sub)["attempts"]
            if len(A) < 2:
                continue
            for i, j in combinations(sorted(A), 2):
                mi, zi = load_bin(A[i], label)
                mj, _ = load_bin(A[j], label)
                if not mi.any() or not mj.any():
                    continue
                sdm = utils.surface_distance_metrics(mi, mj, zi)
                rows.append({
                    "phase": phase_key, "subject": sub, "rater": r,
                    "attempt_a": f"a{i}", "attempt_b": f"a{j}",
                    "dice": utils.dice(mi, mj), "jaccard": utils.jaccard(mi, mj),
                    "hd": sdm["hd"], "hd95": sdm["hd95"], "assd": sdm["assd"],
                    "n_vox_a": int(mi.sum()), "n_vox_b": int(mj.sum())})
    return pd.DataFrame(rows)


def expected_attempts(pcfg):
    """Attempt labels a phase asks for: a1 .. a<attempts>."""
    return [f"a{n}" for n in range(1, int(pcfg.get("attempts", 1)) + 1)]


def coverage(phase_key, workspace, phases, raters, split=None):
    """Who has which attempt for which scan in one phase.

    One row per rater x assigned subject (the split's assignment when given,
    else the subjects in the rater's inputs/): the attempts found, the expected
    ones missing, any beyond the phase's `attempts`, the files used and every
    discovery flag; plus a row per rater with files saved outside the subject
    folders. Gaps are reported, never fatal: the metrics use whatever exists."""
    pcfg = phases[phase_key]
    phase_dir = Path(workspace) / utils.phase_dirname(phase_key, pcfg)
    expected = expected_attempts(pcfg)
    assigned = ((split or {}).get("phases", {}).get(phase_key) or {}).get("by_rater", {})
    rows = []
    for r in raters:
        subs = assigned.get(r)
        if subs is None:
            inputs = phase_dir / r / "inputs"
            subs = sorted(p.name for p in inputs.iterdir() if p.is_dir()) if inputs.is_dir() else []
        for sub in subs:
            info = rater_attempts(phase_dir, r, sub)
            found = [f"a{n}" for n in info["attempts"]]
            rows.append({"phase": phase_key, "rater": r, "subject": sub,
                         "found": ",".join(found) or "-",
                         "missing": ",".join(a for a in expected if a not in found),
                         "extra": ",".join(a for a in found if a not in expected),
                         "files": ", ".join(p.name for p in info["attempts"].values()),
                         "flags": "; ".join(info["flags"])})
        stray = stray_files(phase_dir, r)
        if stray:
            rows.append({"phase": phase_key, "rater": r, "subject": "(outputs root)",
                         "found": "-", "missing": "", "extra": "", "files": "",
                         "flags": "ignored, outside the subject folders: " + ", ".join(stray)})
    return pd.DataFrame(rows, columns=["phase", "rater", "subject", "found", "missing",
                                       "extra", "files", "flags"])


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
