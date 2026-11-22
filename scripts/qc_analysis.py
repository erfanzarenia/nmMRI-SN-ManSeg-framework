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

Run-aware: in a multi-run phase each acquisition is its own scan (sub-0001_run_2),
with its own row in every table and its own image grid. With `echoes` a run is
offered as more than one image (combo, echo) and each image is its own scan
(sub-0001_run_2_echo): masks are only compared with masks of the same run and
image. A mask counts for the run its name gives: the number after "run" with any
separator or none (run_2, run-02, Run 2, run2), else the one number left once the
attempt (a1), echo (echo1) and subject (sub-0029) tokens are set aside
(R4_mask_2). It counts for the image its name gives (_combo or _echo, any
separator). A file naming no run, several runs or no image is flagged and not
used. A <rater>_mask_run_1_combo symlink to an older mask is that mask, once.

Fill-aware: a rater may also save the mask as it was before its holes were
filled, with "_unfilled" in the name (R4_mask_run_1_combo_unfilled). Those masks
get a QC of their own: set_variant("unfilled") makes every lookup here use only
them, read as if the token were not there, while the default ("filled") ignores
them. run_qc.py runs both, into separate folders.

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
_RUN_RE = re.compile(r"[\W_]*run[\W_]*(\d+)", re.IGNORECASE)   # run_2, run-02, Run 2, run2
# Tokens whose digits are not a run: an attempt (_a1), Echo 1 (echo1, echo-1) and a
# subject ID (sub-0029). A bare number left after them is the run (R4_mask_2).
_NOT_RUN_RE = re.compile(r"(?:^|[\W_])(?:a(?:\d+|i)|echo[\W_]*1?|sub[\W_]*\d+)(?![^\W_])",
                         re.IGNORECASE)
_NUM_RE = re.compile(r"[\W_]*(?<!\d)(\d+)(?!\d)")
# _combo, also _comb/_combined; _echo, also _echo1/_echo-1 (Echo 1)
# (run_1combo: a run number right before it counts as a separator)
_ECHO_RE = re.compile(r"(?:^|[\W_]|(?<=\d))(comb(?:o|ined)?|echo(?:[\W_]?1)?)(?![^\W_])",
                      re.IGNORECASE)
# A file meant as a mask whose name is not .nii/.nii.gz (R7_mask_run_2_echo2nii.gz)
_ALMOST_NIFTI_RE = re.compile(r"nii|\.gz$", re.IGNORECASE)
_NIFTI_RE = re.compile(r"\.nii(\.gz)?$", re.IGNORECASE)
_INPUT_COPIES = {"image", "expert_mask"}
# _unfilled, also _un-filled, _Unfilled: the mask saved before its holes were filled
_UNFILLED_RE = re.compile(r"[\W_]*un[\W_]?filled(?![^\W_])", re.IGNORECASE)
VARIANTS = ("filled", "unfilled")
MASK_VARIANT = "filled"          # which masks every lookup uses; see set_variant


def set_variant(variant):
    """Use only the "unfilled" masks (named _unfilled) from now on, or only the
    others ("filled", the default). Each variant is a QC of its own."""
    global MASK_VARIANT
    if variant not in VARIANTS:
        raise ValueError(f"unknown mask variant {variant!r}; use one of {VARIANTS}")
    MASK_VARIANT = variant


def is_unfilled(name):
    """True when a file name marks the mask saved before hole filling."""
    return _UNFILLED_RE.search(_NIFTI_RE.sub("", name)) is not None


def _of_variant(files):
    """The files of the current mask variant (see set_variant)."""
    want = MASK_VARIANT == "unfilled"
    return [p for p in files if is_unfilled(p.name) == want]


def _run_match(stem):
    """The run token of a file stem: the number after "run", else the only
    number not in an attempt, echo or subject token. None when there is no
    such number, or more than one distinct."""
    m = _RUN_RE.search(stem)
    if m:
        return m
    masked = _NOT_RUN_RE.sub(lambda t: "x" * len(t.group(0)), stem)   # same length
    nums = list(_NUM_RE.finditer(masked))
    return nums[0] if len({int(n.group(1)) for n in nums}) == 1 else None


def _stem(name):
    stem = _UNFILLED_RE.sub("", _NIFTI_RE.sub("", name))
    m = _run_match(stem)
    if m:
        stem = stem[:m.start()] + stem[m.end():]
    return _ECHO_RE.sub("", stem)


def _standard(name, rater):
    return re.fullmatch(rf"{re.escape(rater)}_mask(_run_\d+)?(_a\d+)?(_combo|_echo)?"
                        r"(_unfilled)?\.nii(\.gz)?", name) is not None


def _run_of(p):
    """Run number named in a file name (see _run_match), or None."""
    m = _run_match(_NIFTI_RE.sub("", p.name))
    return int(m.group(1)) if m else None


def _echo_of(p):
    """Echo label named in a file name ("combo" or "echo"), or None."""
    m = _ECHO_RE.search(_NIFTI_RE.sub("", p.name))
    return None if not m else "combo" if m.group(1).lower().startswith("comb") else "echo"


def _of_run(files, run):
    """The masks of one run among a subject's NIfTI files, and the files naming no
    run. Only a mask whose name gives the run (see _run_match) is used for it; a
    file with no run in its name is never assumed to be any run. Files that are
    the same file as a run-named one (an older mask has a run 1 label symlinked
    to it) are dropped from both lists."""
    named = {p.resolve() for p in files if _run_of(p) is not None}
    kept, seen = [], set()
    for p in files:
        if _run_of(p) == run and p.resolve() not in seen:
            seen.add(p.resolve())
            kept.append(p)
    unnamed = [p for p in files if _run_of(p) is None and p.resolve() not in named
               and _stem(p.name).lower() not in _INPUT_COPIES]
    return sorted(kept), sorted(unnamed)


# ----------------------------- mask discovery ----------------------------
def find_rater_attempts(out_dir, rater, run=None, echo=None):
    """Find a rater's mask for each attempt in one subject's outputs folder (of
    one `run` in a multi-run phase: only masks naming that run, see _of_run; the
    others with no run in the name are flagged, not used). With `echo` (the
    run's image, e.g. "combo"), only masks naming that image are used; a mask of
    the run naming no image is flagged and not used.

    Standard names are <rater>_mask_a<N>.nii.gz, or <rater>_mask.nii.gz (a1) in
    single-attempt phases. Other names are used when their meaning is clear, each
    with a flag: the attempt token is read case-insensitively (_A1), 'ai' is read
    as the a1 typo, and a lone mask with no attempt token counts as a1. Copies of
    the inputs and leftover files are ignored, also with a flag.

    Only masks of the current variant are looked at (see set_variant): by default
    a file named _unfilled is not seen, and in the "unfilled" QC only those are.

    Returns {"attempts": {N: Path}, "files": [all NIfTI files], "flags": [notes]}.
    """
    out_dir = Path(out_dir)
    found = _of_variant(sorted(p for p in out_dir.iterdir() if p.is_file())
                        if out_dir.is_dir() else [])
    files = [p for p in found if _NIFTI_RE.search(p.name)]
    attempts, untagged, flags = {}, [], []
    odd = [p.name for p in found if p not in files and _ALMOST_NIFTI_RE.search(p.name)]
    if odd:
        flags.append("not used, not a .nii.gz file name: " + ", ".join(odd))
    if run is not None:
        files, unnamed = _of_run(files, run)
        if unnamed:
            flags.append("not used, no run in the name: " + ", ".join(p.name for p in unnamed))
        if echo:
            unlabeled = [p.name for p in files if _echo_of(p) is None
                         and _stem(p.name).lower() not in _INPUT_COPIES]
            if unlabeled:
                flags.append("not used, no image (_combo or _echo) in the name: "
                             + ", ".join(unlabeled))
            files = [p for p in files if _echo_of(p) == echo]
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
    return sorted(p.name for p in _of_variant(base.iterdir())
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
        for scan in utils.subject_scans(sub):
            img = utils.scan_image(sub, scan)
            if img is not None:
                grids[scan] = _grid(img)
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


def expert_masks(phase_key, workspace, phases, raters, paths=None, split=None):
    """Expert mask per scan of a phase, {subject: Path}, for every scan that has
    one. The source expert is used for every phase, found through the private
    key (split["mapping"]) and the pool's expert_mask_pattern in paths.yaml, so
    blind phases are compared against the expert without raters ever seeing it.
    A copy in a rater's inputs/ is the fallback when the key or the source data
    is unavailable. A mask not on the scan's image grid is skipped."""
    pcfg = phases[phase_key]
    phase_dir = Path(workspace) / utils.phase_dirname(phase_key, pcfg)
    mapping = (split or {}).get("mapping", {})
    pool = ((paths or {}).get("pools") or {}).get(pcfg.get("pool"))
    found = {}
    for sub in utils.find_subjects(phase_dir, raters):
        grids = next((g for g in (_input_grids(str(phase_dir / r / "inputs"))
                                  for r in raters) if sub in g), {})
        candidates = []
        if pool and pool.get("expert_mask_pattern") and sub in mapping:
            candidates.append(utils.resolve(utils.source_paths(pool, mapping[sub])[1]))
        candidates += [utils.resolve(utils.input_dir(phase_dir, r, sub) / "expert_mask.nii.gz")
                       for r in raters]
        path = next((p for p in candidates
                     if p is not None and grid_problem(p, sub, grids) is None), None)
        if path is not None:
            found[sub] = path
    return found

def rater_attempts(phase_dir, rater, sub):
    """find_rater_attempts for one subject, minus masks that are not on the
    subject's image grid (each set aside with a flag)."""
    _, run = utils.split_scan(sub)
    info = find_rater_attempts(utils.output_dir(phase_dir, rater, sub), rater, run,
                               utils.scan_echo(sub))
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


def extent(mask, affine, zooms):
    """Slices a mask spans along each anatomical direction: axial (inferior-superior),
    coronal (anterior-posterior), sagittal (left-right), as counts and in mm."""
    import nibabel as nib
    codes = nib.aff2axcodes(affine)
    names = {"S": "axial", "I": "axial", "A": "coronal", "P": "coronal",
             "L": "sagittal", "R": "sagittal"}
    out = {}
    for ax, code in enumerate(codes):
        other = tuple(a for a in range(3) if a != ax)
        n = int(np.count_nonzero(mask.any(axis=other)))
        out[f"{names[code]}_slices"] = n
        out[f"{names[code]}_mm"] = n * float(zooms[ax])
    return out


def _affine(path):
    return _grid(path)[1]


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
                  out_dir=None, experts=None):
    """Compute every QC table for one phase under a single integer `attempt`.
    Only rater/scans that actually have that attempt contribute, so the analysis
    adapts to what is present. `experts` ({subject: Path}, from expert_masks())
    gives the expert masks, including those of blind phases; without it only
    the copies in the raters' inputs/ are used. Returns a dict of DataFrames;
    writes them to <out_dir>/metrics/ when given.
    """
    pcfg = phases[phase_key]
    phase_dir = Path(workspace) / utils.phase_dirname(phase_key, pcfg)
    subjects = utils.find_subjects(phase_dir, raters)
    if experts is None:
        experts = expert_masks(phase_key, workspace, phases, raters)
    att = f"a{attempt}"

    rve_rows, rvr_rows, vol_rows, inv_rows = [], [], [], []
    for sub in subjects:
        expert, espace = None, None
        if sub in experts:
            expert, espace = load_bin(experts[sub], label)

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
                             "vol_mm3": int(mask.sum()) * vox,
                             **extent(mask, _affine(info["attempts"][attempt]), zooms)})

        if expert is not None:
            vox = float(np.prod(espace))
            vol_rows.append({"phase": phase_key, "attempt": att, "subject": sub,
                             "source": "expert", "n_vox": int(expert.sum()),
                             "vol_mm3": int(expert.sum()) * vox,
                             **extent(expert, _affine(experts[sub]), espace)})
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

    One row per rater x assigned scan (the split's assignment when given, else
    the subjects in the rater's inputs/; each run is a scan in a multi-run phase,
    and a run offered as several images is one row whose `image` column names the
    images segmented): the attempts found, the expected ones missing, any beyond
    the phase's `attempts`, the files used and every discovery flag; plus a row
    per rater with files saved outside the subject folders. Gaps are reported,
    never fatal: the metrics use whatever exists."""
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
        runs = {}                                    # run -> its scans (one per image)
        for sub in subs:
            for s in utils.subject_scans(phase_dir / r / "inputs" / sub):
                runs.setdefault(utils.run_scan(s), []).append(s)
        for run, scans in runs.items():
            nums, images, files, flags = set(), [], [], []
            for sub in scans:
                info = rater_attempts(phase_dir, r, sub)
                nums |= set(info["attempts"])
                if info["attempts"] and utils.scan_echo(sub):
                    images.append(utils.scan_echo(sub))
                files += [p.name for p in info["attempts"].values()]
                flags += [f for f in info["flags"] if f not in flags]
            found = [f"a{n}" for n in sorted(nums)]
            rows.append({"phase": phase_key, "rater": r, "subject": run,
                         "image": "+".join(images),
                         "found": ",".join(found) or "-",
                         "missing": ",".join(a for a in expected if a not in found),
                         "extra": ",".join(a for a in found if a not in expected),
                         "files": ", ".join(files),
                         "flags": "; ".join(flags)})
        stray = stray_files(phase_dir, r)
        if stray:
            rows.append({"phase": phase_key, "rater": r, "subject": "(outputs root)",
                         "image": "", "found": "-", "missing": "", "extra": "", "files": "",
                         "flags": "ignored, outside the subject folders: " + ", ".join(stray)})
    return pd.DataFrame(rows, columns=["phase", "rater", "subject", "image", "found",
                                       "missing", "extra", "files", "flags"])


def run_check(cov):
    """Gate for a multi-run phase, from the coverage table: which runs (and, with
    echo images, which image of each run) each rater has a mask for, per subject.
    Runs are done in order, so a rater's runs should be 1..k; raters at different
    k are only at different points and still share run 1. On an overlap subject
    every rater must segment the same image of each run, or that run cannot be
    compared. Status per rater x subject:
      fail   an overlap subject (2+ raters with masks) where this rater skipped a
             run (e.g. run 2 without run 1), so their runs do not line up with
             the other raters' (often a mask saved under the wrong run number), or
             segmented another image of a run than another rater did
      check  a skipped run on a subject only this rater has, or on an overlap
             subject where every rater skipped the same run (they still line up)
      ok     otherwise
    Raters with no mask for the subject are left out. Empty when no scan has a run."""
    scans = cov[cov["subject"] != "(outputs root)"]
    split = scans["subject"].map(utils.split_scan)
    scans = scans.assign(sub=[s for s, _ in split], run=[r for _, r in split])
    scans = scans[scans["run"].notna() & (scans["found"] != "-")]
    image = scans["image"] if "image" in scans else pd.Series("", index=scans.index)

    def show(have):
        return ", ".join(f"{n} {'+'.join(sorted(e))}".strip() for n, e in sorted(have.items()))

    rows = []
    for sub, g in scans.assign(img=image.fillna("")).groupby("sub"):
        runs = {r: {int(x.run): set(filter(None, x.img.split("+"))) for x in h.itertuples()}
                for r, h in g.groupby("rater", sort=False)}
        for r, have in runs.items():
            others = {o: v for o, v in runs.items() if o != r}
            notes = []
            if sorted(have) != list(range(1, len(have) + 1)):
                notes.append("skipped a run")
            differ = sorted({n for v in others.values() for n in have
                             if n in v and have[n] and v[n] and not have[n] & v[n]})
            if differ:
                notes.append("another image than the other raters on run "
                             + ", ".join(map(str, differ)))
            alike = others and all(v == have for v in others.values())
            if not notes:
                status = "ok"
            elif alike and not differ:
                status = "check"
                notes = ["skipped a run, as every other rater did (they line up)"]
            elif others:
                status = "fail"
                notes.append("others: " + "; ".join(f"{o} (run {show(v)})"
                                                    for o, v in others.items()))
            else:
                status = "check"
                notes = ["skipped a run (e.g. run 2 without run 1)"]
            rows.append({"subject": sub, "rater": r, "n_raters": len(runs),
                         "runs": show(have), "status": status, "note": "; ".join(notes)})
    return pd.DataFrame(rows, columns=["subject", "rater", "n_raters", "runs", "status", "note"])


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
    """mean/median/sd/min/max (+ n) of each metric, grouped by `by` (list) or overall
    when by is None. Sorted by the first metric's mean ascending (worst first)."""
    metrics = [m for m in metrics if m in df.columns]
    if df.empty:
        return pd.DataFrame()
    if by is None:
        row = {"n": len(df)}
        for m in metrics:
            row |= {f"{m}_mean": df[m].mean(), f"{m}_median": df[m].median(),
                    f"{m}_sd": df[m].std(),
                    f"{m}_min": df[m].min(), f"{m}_max": df[m].max()}
        return pd.DataFrame([row])
    rows = []
    for key, g in df.groupby(by):
        key = key if isinstance(key, tuple) else (key,)
        row = dict(zip(by, key)) | {"n": len(g)}
        for m in metrics:
            row |= {f"{m}_mean": g[m].mean(), f"{m}_median": g[m].median(),
                    f"{m}_sd": g[m].std(),
                    f"{m}_min": g[m].min(), f"{m}_max": g[m].max()}
        rows.append(row)
    out = pd.DataFrame(rows)
    return out.sort_values(f"{metrics[0]}_mean").reset_index(drop=True) if metrics else out


def average_of_averages(df, metrics, by="subject"):
    """Phase summary that weights every scan equally: each metric is averaged
    within a scan first (over its raters or rater pairs), then mean/median/sd/min/max
    are taken over those per-scan means. n is the number of scans. Equals the grand
    mean when every scan has the same number of rows."""
    metrics = [m for m in metrics if m in df.columns]
    if df.empty:
        return pd.DataFrame()
    return summarize(df.groupby(by)[metrics].mean().reset_index(), None, metrics)


VOLUME_METRICS = ["vol_mm3", "axial_slices", "coronal_slices", "sagittal_slices",
                  "axial_mm", "coronal_mm", "sagittal_mm"]


def volume_stats(vol):
    """Volume and extent summaries from the volumes table (raters only):
      by_rater          per rater over all their scans
      overlap_by_rater  per rater over the overlap scans (2+ raters)
      overlap_by_scan   per overlap scan over its raters, with the volume CV
      overlap_summary   average of the overlap scans' means (each scan once)"""
    out = {}
    if vol is None or vol.empty:
        return out
    rat = vol[vol["source"] != "expert"].rename(columns={"source": "rater"})
    metrics = [m for m in VOLUME_METRICS if m in rat.columns]
    if rat.empty:
        return out
    out["volume_by_rater"] = summarize(rat, ["rater"], metrics)
    n = rat.groupby("subject")["rater"].transform("nunique")
    ov = rat[n > 1]
    if not ov.empty:
        out["volume_overlap_by_rater"] = summarize(ov, ["rater"], metrics)
        by_scan = summarize(ov, ["subject"], metrics)
        by_scan["vol_cv"] = by_scan["vol_mm3_sd"] / by_scan["vol_mm3_mean"]
        out["volume_overlap_by_scan"] = by_scan
        out["volume_overlap_summary"] = average_of_averages(ov, metrics)
    return out


def _aggregate(phase_key, attempt, tables):
    rve, rvr = tables["rater_vs_expert_per_scan"], tables["rater_vs_rater_per_scan"]
    agg = volume_stats(tables.get("volumes"))
    if not rve.empty:
        agg["rve_by_rater"] = summarize(rve, ["rater"], SD_METRICS)
        agg["rve_by_scan"] = summarize(rve, ["subject"], SD_METRICS)
        agg["rve_phase_summary"] = pd.concat([
            summarize(rve, None, SD_METRICS).assign(summary="grand_mean"),
            average_of_averages(rve, SD_METRICS).assign(summary="average_of_scan_means"),
        ]).assign(phase=phase_key, attempt=attempt, comparison="rater_vs_expert")
    if not rvr.empty:
        agg["rvr_by_pair"] = summarize(rvr, ["pair"], SD_METRICS)
        agg["rvr_by_scan"] = summarize(rvr, ["subject"], SD_METRICS)
        agg["rvr_phase_summary"] = pd.concat([
            summarize(rvr, None, SD_METRICS).assign(summary="grand_mean"),
            average_of_averages(rvr, SD_METRICS).assign(summary="average_of_scan_means"),
        ]).assign(phase=phase_key, attempt=attempt, comparison="rater_vs_rater")
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
    split_path = Path(args.config_dir) / "subjects_split.json"
    experts = expert_masks(args.phase, workspace, phases, raters, paths,
                           utils.load_json(split_path) if split_path.exists() else None)

    attempts = [args.attempt] if args.attempt is not None else detect_attempts(phase_dir, raters)
    print(f"{args.phase}: attempts {attempts}")

    per = []
    for n in attempts:
        out_dir = qc_root / args.phase / attempt_dirname(n)
        tables = compute_phase(args.phase, workspace, phases, raters, n,
                               label=args.label, out_dir=out_dir, experts=experts)
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
