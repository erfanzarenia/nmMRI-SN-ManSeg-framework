"""Shared helpers for the SN manual-segmentation scripts.

Kept deliberately small: config loading, label values, source-data and
phase -> rater -> subject path conventions, and NIfTI/Dice helpers. The on-disk
layout has a single source of truth here, so make_workspace.py, split_subjects.py
and the QC scripts never disagree about it.

numpy/nibabel are imported lazily, so split_subjects.py and make_workspace.py
run with only PyYAML installed.
"""
from __future__ import annotations

import json
import os
import re
from glob import glob
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
# A group's own config (paths, raters, phases, subject lists, the private split
# key and lists) lives in config/local/, which git ignores; config/ holds the
# tracked example. config/local/ is used whenever it has a paths.yaml, and
# MANSEG_CONFIG_DIR or --config-dir points elsewhere.
_LOCAL = REPO_ROOT / "config" / "local"
CONFIG_DIR = Path(os.environ.get("MANSEG_CONFIG_DIR")
                  or (_LOCAL if (_LOCAL / "paths.yaml").is_file() else REPO_ROOT / "config"))

_PHASE_RE = re.compile(r"^phase(\d+)$")


# ------------------------------ config -------------------------------
def load_yaml(path):
    with open(path) as f:
        return yaml.safe_load(f)


def load_json(path):
    with open(path) as f:
        return json.load(f)


def load_config(config_dir=CONFIG_DIR):
    """Return (paths, phases, raters) loaded from a config directory."""
    config_dir = Path(config_dir)
    paths = load_yaml(config_dir / "paths.yaml")
    phases = load_yaml(config_dir / "phases.yaml")
    raters = load_json(config_dir / "raters.json")["raters"]
    return paths, phases, raters


def read_subject_list(path):
    """Read a subject list: a text file with one BIDS label per line ('#' starts
    a comment), or a CSV with a `participant` column (see read_subject_info)."""
    if Path(path).suffix == ".csv":
        return list(read_subject_info(path))
    subjects = []
    for line in Path(path).read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            subjects.append(line)
    return subjects


def read_subject_info(path):
    """{participant: {column: value}} from a CSV subject list, in file order.
    Text subject lists carry no columns, so they give empty dicts."""
    import csv
    if Path(path).suffix != ".csv":
        return {s: {} for s in read_subject_list(path)}
    with open(path, newline="") as f:                # '#' lines are comments
        lines = [line for line in f if not line.lstrip().startswith("#")]
        rows = [r for r in csv.DictReader(lines) if (r.get("participant") or "").strip()]
    return {r["participant"].strip(): r for r in rows}


def phase_keys(phases):
    """Phase keys (phase1, phase2, ...) ordered numerically, ignoring any
    non-phase keys such as the top-level 'seed'."""
    keys = [k for k in phases if _PHASE_RE.match(k)]
    return sorted(keys, key=lambda k: int(_PHASE_RE.match(k).group(1)))


# ------------------------------ labels -------------------------------
# Label values in rater and expert masks, matching the ITK-SNAP label file
# docs/SN_label_definition.txt. CP (cerebral peduncle) is the reference region,
# segmented from Phase 3 on; each phase lists its labels in phases.yaml.
LABELS = {"SN": 1, "CP": 2}


def phase_labels(pcfg):
    """Label names segmented in a phase (SN only when unspecified)."""
    return list(pcfg.get("labels", ["SN"]))


# -------------------------- path conventions -------------------------
# Workspace layout built by make_workspace.py (same for every phase):
#   <ws>/<phaseN_name>/<rater>/inputs/<sub>/image.nii.gz [+ expert_mask.nii.gz]
#   <ws>/<phaseN_name>/<rater>/outputs/<sub>/   (rater saves masks here)
# A phase with `runs` in phases.yaml holds one image per acquisition instead:
#   inputs/<sub>/image_run_<N>.nii.gz, outputs/<sub>/<rater>_mask_run_<N>.nii.gz
# and a phase with `echoes` too offers each run as more than one image, the name
# ending in the echo label (image_run_1_combo, image_run_1_echo). The rater
# segments one of them per run, whichever shows the SN best, and says which in
# the mask name (<rater>_mask_run_1_combo). QC uses a mask only for the run and
# image in its name; one naming no run or no image is flagged and not used. Masks
# saved before runs existed have a <rater>_mask_run_1_combo symlink naming them.
#
# A "scan" is what gets segmented and compared: the subject (sub-0001) in a
# single-image phase, the subject and run (sub-0001_run_2) in a multi-run phase,
# and with echo images the run and image too (sub-0001_run_2_echo). Masks are only
# ever compared on the same scan. The path helpers below accept any of them.
_SCAN_RE = re.compile(r"^(.*?)_run_(\d+)(?:_([a-z]+))?$")
RUN_IMAGE_RE = re.compile(r"^image_run_(\d+)(?:_([a-z]+))?\.nii(\.gz)?$")


def phase_dirname(key, pcfg):
    return f"{key}_{pcfg['name']}"


def phase_runs(pcfg, subject=None):
    """Runs a phase segments per subject, or None when it uses a single image.
    Given a source subject ID, honors the phase's `run1_only` list."""
    if subject in pcfg.get("run1_only", []):
        return [1]
    return pcfg.get("runs")


def runs_required(pcfg):
    """Runs a subject needs a mask on to be done in a multi-run phase: the phase's
    `runs_required` (e.g. 1: any one run is enough), else None (every run)."""
    n = pcfg.get("runs_required")
    return int(n) if n else None


def phase_echoes(pcfg):
    """Echo images a multi-run phase holds per run (e.g. [combo, echo]), or None."""
    return pcfg.get("echoes")


def split_scan(scan):
    """(subject, run) of a scan ID; run is None for a plain subject ID."""
    m = _SCAN_RE.match(scan)
    return (m.group(1), int(m.group(2))) if m else (scan, None)


def scan_echo(scan):
    """Echo label of a scan ID (sub-0001_run_1_echo -> "echo"), or None."""
    m = _SCAN_RE.match(scan)
    return m.group(3) if m else None


def input_dir(phase_dir, rater, scan):
    return Path(phase_dir) / rater / "inputs" / split_scan(scan)[0]


def output_dir(phase_dir, rater, scan):
    return Path(phase_dir) / rater / "outputs" / split_scan(scan)[0]


def image_name(run=None, echo=None):
    """Input image file name stem for a run and echo image (None: the
    single-image name)."""
    if run is None:
        return "image"
    return f"image_run_{run}" + (f"_{echo}" if echo else "")


def scan_image_name(scan):
    """Input image file name stem of a scan ID."""
    return image_name(split_scan(scan)[1], scan_echo(scan))


def scan_image(subject_dir, scan):
    """A scan's input image in one subject's inputs/ folder, or None if absent. A
    run offered as several images gives the first by name (image_run_1_combo
    before image_run_1_echo); they share one image grid."""
    subject_dir = Path(subject_dir)
    found = resolve(subject_dir / f"{scan_image_name(scan)}.nii.gz")
    _, run = split_scan(scan)
    if found is not None or run is None or scan_echo(scan):
        return found
    return next((p for p in sorted(subject_dir.glob(f"image_run_{run}_*.nii*"))
                 if RUN_IMAGE_RE.match(p.name)), None)


def input_image(phase_dir, rater, scan):
    """Path of a scan's input image in a rater's inputs/, or None if absent."""
    return scan_image(input_dir(phase_dir, rater, scan), scan)


def subject_scans(subject_dir):
    """Scan IDs of one subject's inputs/ folder: one per image_run_<N>[_<echo>]
    file (sub-0001_run_1_combo, sub-0001_run_1_echo), or just the subject when the
    folder has none (a single-image phase)."""
    subject_dir = Path(subject_dir)
    found = sorted({(int(m.group(1)), m.group(2) or "") for m in map(RUN_IMAGE_RE.match, (
        p.name for p in subject_dir.iterdir())) if m}) if subject_dir.is_dir() else []
    return ([f"{subject_dir.name}_run_{r}" + (f"_{e}" if e else "") for r, e in found]
            or [subject_dir.name])


def run_scan(scan):
    """The run of a scan, without its echo label (sub-0001_run_1_echo ->
    sub-0001_run_1); a scan with no run is returned as is."""
    sub, run = split_scan(scan)
    return scan if run is None else f"{sub}_run_{run}"


def find_subjects(phase_dir, raters):
    """All scans under any rater's inputs/ folder: the sub-* directories, split
    by run where they hold one image per run."""
    found = set()
    for rater in raters:
        search = Path(phase_dir) / rater / "inputs"
        if search.is_dir():
            for p in search.iterdir():
                if p.is_dir() and p.name.startswith("sub-"):
                    found.update(subject_scans(p))
    return sorted(found)


# ---------------------------- source data ----------------------------
# Default file patterns per source layout. A pool selects one with `layout:`
# (default "bids") and may override either pattern explicitly in paths.yaml.
LAYOUT_DEFAULTS = {
    # BIDS-like: per-subject sub-dirs; expert masks under derivatives/.
    "bids": {
        "image_pattern": "{subject}/anat/{subject}_acq-NM_T1w.nii.gz",
        "expert_mask_pattern": "derivatives/expert/{subject}/{subject}_acq-NM_label-SN_mask.nii.gz",
    },
    # Flat: every file directly in data_root.
    "flat": {
        "image_pattern": "{subject}.nii.gz",
        "expert_mask_pattern": "{subject}_SN.nii.gz",
    },
}


def pool_patterns(pdef):
    """(image_pattern, expert_mask_pattern) for a pool: explicit patterns win,
    otherwise the `layout` defaults apply."""
    defaults = LAYOUT_DEFAULTS.get(pdef.get("layout", "bids"), {})
    image = pdef.get("image_pattern", defaults.get("image_pattern"))
    if image is None:
        raise KeyError(f"pool needs image_pattern or a known layout {list(LAYOUT_DEFAULTS)}")
    return image, pdef.get("expert_mask_pattern", defaults.get("expert_mask_pattern"))


def source_paths(pdef, subject, run=None, echo=None):
    """(image, expert_mask or None) source paths for one subject of a pool.
    Patterns are filled with the subject and the pool's own keys (e.g. run;
    `run` given here replaces the pool's default). An `echo` listed in the pool's
    echo_patterns takes its image from there; any other (e.g. combo) uses
    image_pattern. image_pattern may be a list: each pattern is tried in every data root
    before the next pattern (fallbacks, e.g. an older file name for the same
    image). The first existing image wins; if none exists, the first pattern
    under the first root is returned (and reported missing)."""
    roots = pdef["data_root"]
    roots = [roots] if isinstance(roots, str) else roots
    image_pats, expert_pat = pool_patterns(pdef)
    image_pats = (pdef.get("echo_patterns") or {}).get(echo, image_pats)
    image_pats = [image_pats] if isinstance(image_pats, str) else image_pats
    fields = {**pdef, "subject": subject} | ({} if run is None else {"run": run})
    images = [Path(r) / p.format(**fields) for p in image_pats for r in roots]
    image = next((i for i in images if resolve(i)), images[0])
    root = next(Path(r) for r in roots if image.is_relative_to(r))
    expert = root / expert_pat.format(**fields) if expert_pat else None
    return image, expert


# ------------------------------ NIfTI --------------------------------
def resolve(path):
    """Resolve a path that may contain '*' (glob, highest match wins) and/or a
    .nii <-> .nii.gz extension mismatch. Returns a Path or None."""
    s = str(path)
    if "*" in s:
        matches = sorted(glob(s))
        return Path(matches[-1]) if matches else None
    p = Path(s)
    if p.exists():
        return p
    if s.endswith(".nii.gz"):
        alt = Path(s[:-7] + ".nii")
    elif s.endswith(".nii"):
        alt = Path(s + ".gz")
    else:
        return None
    return alt if alt.exists() else None


def load_nii(path):
    """Load a NIfTI as (data, affine, zooms). Tolerant of files whose extension
    misreports compression (detected by magic bytes, not the name)."""
    import gzip
    import nibabel as nib
    path = str(path)
    with open(path, "rb") as f:
        is_gz = f.read(2) == b"\x1f\x8b"
    if is_gz == path.endswith(".gz"):
        img = nib.load(path)                       # extension matches contents
    else:                                          # extension lies -> load by bytes
        raw = open(path, "rb").read()
        raw = gzip.decompress(raw) if is_gz else raw
        try:
            img = nib.Nifti1Image.from_bytes(raw)
        except Exception:
            img = nib.Nifti2Image.from_bytes(raw)
    return img.get_fdata(), img.affine, img.header.get_zooms()[:3]


def load_mask(path):
    """Load a mask as (int16 array, affine, zooms)."""
    import numpy as np
    data, aff, zooms = load_nii(path)
    return data.astype(np.int16), aff, zooms


def dice(a, b):
    """Dice overlap of two boolean arrays. NaN if both are empty."""
    import numpy as np
    a = np.asarray(a, bool)
    b = np.asarray(b, bool)
    denom = int(a.sum()) + int(b.sum())
    if denom == 0:
        return float("nan")
    return 2.0 * int(np.logical_and(a, b).sum()) / denom


def jaccard(a, b):
    """Jaccard (IoU) of two boolean arrays. NaN if both are empty."""
    import numpy as np
    a = np.asarray(a, bool)
    b = np.asarray(b, bool)
    union = int(np.logical_or(a, b).sum())
    if union == 0:
        return float("nan")
    return int(np.logical_and(a, b).sum()) / union


# ------------------------ surface / Hausdorff ------------------------
# Surface-distance metrics in physical units. Spacing is the voxel size
# (zooms) so anisotropic scans (e.g. 0.4x0.4x1.0 mm) give mm distances, not
# voxel counts. scipy is imported lazily to keep the lighter scripts dependency
# free, matching the numpy/nibabel pattern above.
#
# A mask's surface is the set of voxel faces between foreground and background,
# each taken at its centre and weighted by its area. Measuring between voxel
# centres instead puts every surface point half a voxel inside the mask and
# counts a 0.16 mm2 through-plane face like a 0.4 mm2 in-plane one, so on 1 mm
# slices a one-slice difference at either end of the SN pins HD95 at the slice
# thickness.
def _surface_faces(mask, spacing):
    """Boundary faces of a binary mask on the half-voxel lattice (voxel i sits
    at 2i+1, the face between voxels i and i+1 at 2i+2): (face points, area in
    mm2 at each point). The mask must not touch its own edge."""
    import numpy as np
    pts = np.zeros(tuple(2 * n + 1 for n in mask.shape), bool)
    area = np.zeros(pts.shape)
    for ax in range(mask.ndim):
        lo, hi = [slice(None)] * mask.ndim, [slice(None)] * mask.ndim
        at = [slice(1, None, 2)] * mask.ndim
        lo[ax], hi[ax], at[ax] = slice(None, -1), slice(1, None), slice(2, -1, 2)
        face = mask[tuple(lo)] != mask[tuple(hi)]
        pts[tuple(at)] = face
        area[tuple(at)] = face * np.prod(np.delete(spacing, ax))
    return pts, area


def surface_distances(a, b, spacing):
    """Nearest-surface distances (mm) a-surface -> b-surface and b-surface ->
    a-surface, with the area (mm2) of each surface element:
    (d_ab, d_ba, area_a, area_b). All None if either mask is empty."""
    import numpy as np
    from scipy import ndimage
    a = np.asarray(a, bool)
    b = np.asarray(b, bool)
    if not a.any() or not b.any():
        return None, None, None, None
    # Crop to a padded bounding box of the union: both surfaces lie fully inside
    # it, so every surface-to-surface distance is identical to the full-volume
    # result, but the distance transforms run on a small block. The 1-voxel pad
    # keeps every boundary face inside the block.
    idx = np.where(a | b)
    crop = tuple(slice(max(0, i.min() - 1), i.max() + 2) for i in idx)
    a, b = np.pad(a[crop], 1), np.pad(b[crop], 1)
    spacing = np.asarray(spacing, float)
    fa, wa = _surface_faces(a, spacing)
    fb, wb = _surface_faces(b, spacing)
    # EDT of the complement gives, at every lattice point, the distance to the
    # nearest face; sample it at the *other* mask's faces.
    dt_to_b = ndimage.distance_transform_edt(~fb, sampling=spacing / 2)
    dt_to_a = ndimage.distance_transform_edt(~fa, sampling=spacing / 2)
    return dt_to_b[fa], dt_to_a[fb], wa[fa], wb[fb]


def _weighted_percentile(d, w, percentile):
    """Smallest distance that covers `percentile`% of the surface area."""
    import numpy as np
    order = np.argsort(d, kind="stable")
    cum = np.cumsum(w[order]) / w.sum()
    return float(d[order][min(np.searchsorted(cum, percentile / 100), len(d) - 1)])


def surface_distance_metrics(a, b, spacing, percentile=95):
    """Hausdorff and average surface distances between two binary masks,
    area-weighted over the surface faces.

    Returns a dict (all mm): hd (max symmetric Hausdorff), hd95 (robust
    percentile Hausdorff), assd (average symmetric surface distance), masd
    (mean of the two directed mean distances). NaN when either mask is empty.
    """
    import numpy as np
    d_ab, d_ba, w_a, w_b = surface_distances(a, b, spacing)
    if d_ab is None or len(d_ab) == 0 or len(d_ba) == 0:
        return {"hd": float("nan"), "hd95": float("nan"),
                "assd": float("nan"), "masd": float("nan")}
    hd = max(d_ab.max(), d_ba.max())
    hd95 = max(_weighted_percentile(d_ab, w_a, percentile),
               _weighted_percentile(d_ba, w_b, percentile))
    assd = float((d_ab @ w_a + d_ba @ w_b) / (w_a.sum() + w_b.sum()))
    masd = max(np.average(d_ab, weights=w_a), np.average(d_ba, weights=w_b))
    return {"hd": float(hd), "hd95": float(hd95), "assd": assd, "masd": float(masd)}


def per_slice_dice(a, b, axis=2, min_union=1):
    """Per-slice Dice along `axis`. Returns dict {slice_index: dice} over slices
    whose union has >= min_union voxels (slices both empty are skipped)."""
    import numpy as np
    a = np.asarray(a, bool)
    b = np.asarray(b, bool)
    out = {}
    for k in range(a.shape[axis]):
        sa = np.take(a, k, axis=axis)
        sb = np.take(b, k, axis=axis)
        union = int(np.logical_or(sa, sb).sum())
        if union < min_union:
            continue
        denom = int(sa.sum()) + int(sb.sum())
        out[k] = (2.0 * int(np.logical_and(sa, sb).sum()) / denom) if denom else float("nan")
    return out
