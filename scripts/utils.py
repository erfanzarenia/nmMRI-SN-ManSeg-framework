"""Shared helpers for the SN manual-segmentation scripts.

Kept deliberately small: config loading, the phase -> rater -> subject path
conventions, and NIfTI/Dice helpers. The on-disk layout has a single source of
truth here, so make_workspace.py and qc_metrics.py never disagree about it.

numpy/nibabel are imported lazily, so split_subjects.py and make_workspace.py
run with only PyYAML installed.
"""
from __future__ import annotations

import json
import re
from glob import glob
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"

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
    """Read a subject-list file: one BIDS label per line, '#' starts a comment."""
    subjects = []
    for line in Path(path).read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            subjects.append(line)
    return subjects


def phase_keys(phases):
    """Phase keys (phase1, phase2, ...) ordered numerically, ignoring any
    non-phase keys such as the top-level 'seed'."""
    keys = [k for k in phases if _PHASE_RE.match(k)]
    return sorted(keys, key=lambda k: int(_PHASE_RE.match(k).group(1)))


# -------------------------- path conventions -------------------------
# Workspace layout built by make_workspace.py (same for every phase):
#   <ws>/<phaseN_name>/<rater>/inputs/<sub>/image.nii.gz [+ expert_mask.nii.gz]
#   <ws>/<phaseN_name>/<rater>/outputs/<sub>/   (rater saves masks here)
def phase_dirname(key, pcfg):
    return f"{key}_{pcfg['name']}"


def input_dir(phase_dir, rater, subject):
    return Path(phase_dir) / rater / "inputs" / subject


def output_dir(phase_dir, rater, subject):
    return Path(phase_dir) / rater / "outputs" / subject


def rater_mask_pattern(phase_dir, rater, subject, pcfg):
    """Expected rater output mask; a glob when the phase has multiple attempts."""
    out = output_dir(phase_dir, rater, subject)
    if int(pcfg.get("attempts", 1)) > 1:
        return out / f"{rater}_mask_a*.nii.gz"   # latest attempt wins
    return out / f"{rater}_mask.nii.gz"


def find_subjects(phase_dir, raters):
    """All sub-* directories present under any rater's inputs/ folder."""
    found = set()
    for rater in raters:
        search = Path(phase_dir) / rater / "inputs"
        if search.is_dir():
            found.update(p.name for p in search.iterdir()
                         if p.is_dir() and p.name.startswith("sub-"))
    return sorted(found)


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
