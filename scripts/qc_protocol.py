#!/usr/bin/env python3
"""Protocol and reference-region QC for phases that segment SN + CP.

The cerebral peduncle (CP, label 2) is a reference region: what matters is that
it follows the rules in docs/segmentation_protocol.md and gives a stable
reference intensity, more than its overlap between raters. For one phase and
attempt this writes, under <qc-root>/<phase>/<a<N>|latest>/protocol/:

    protocol_per_mask.csv   per rater x scan: labels present, SN/CP volume,
                            hemispheres covered, CP slice coverage vs SN, CP
                            voxels touching SN, mean SN/CP image intensity,
                            contrast (SN - CP) / CP, and rule flags
    agreement_per_scan.csv  per rater x scan with >= 2 raters: mean SN Dice,
                            SN HD95 and CP Dice vs the other raters, SN volume
                            / scan median, CP mean intensity deviation from the
                            scan median, contrast minus the scan median
    verdict.csv/.md         only for a phase with `criteria` in phases.yaml
                            (the calibration): pass/review per rater and the
                            scans behind each missed criterion

run_qc.py runs these checks for every attempt of a phase that segments CP;
standalone, e.g. for the calibration re-check on re-segmentations:

    python scripts/qc_protocol.py --phase phase5
    python scripts/qc_protocol.py --phase phase5 --latest   # include _a2 re-segmentations
"""
from __future__ import annotations

import argparse
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import ndimage

import utils
import qc_analysis as qa

SN, CP = utils.LABELS["SN"], utils.LABELS["CP"]
CRITERIA = ("sn_dice_min", "sn_hd95_max_mm", "sn_volume_tol", "cp_mean_tol", "min_scans")


def _axes(affine):
    """Voxel axes closest to world left-right and inferior-superior."""
    return (int(np.argmax(np.abs(affine[0, :3]))),
            int(np.argmax(np.abs(affine[2, :3]))))


def _other_axes(axis):
    return tuple(a for a in range(3) if a != axis)


def hemispheres(mask, lr_axis, min_frac=0.1):
    """Left-right separated parts of a mask holding >= min_frac of its voxels:
    2 when the label is drawn in both hemispheres, 1 for one side, 0 if empty."""
    total = int(mask.sum())
    runs, run = [], 0
    for n in np.append(mask.sum(axis=_other_axes(lr_axis)), 0):
        if n:
            run += int(n)
        elif run:
            runs.append(run)
            run = 0
    return sum(r >= min_frac * total for r in runs)


def axial_slices(mask, si_axis):
    """Indices of the axial slices containing the mask."""
    return set(np.flatnonzero(mask.any(axis=_other_axes(si_axis))).tolist())


def touching(a, b, si_axis):
    """Voxels of b next to a within the same axial slice (8-neighbourhood)."""
    size = [3, 3, 3]
    size[si_axis] = 1
    return int((ndimage.binary_dilation(a, structure=np.ones(size, bool)) & b).sum())


def check_mask(labels, image, affine, zooms, phase_labels):
    """Protocol measurements and rule flags for one rater mask."""
    lr, si = _axes(affine)
    vox = float(np.prod(zooms))
    sn, cp = labels == SN, labels == CP
    extra = sorted(set(np.unique(labels).tolist())
                   - {0} - {utils.LABELS[n] for n in phase_labels})
    sn_sl, cp_sl = axial_slices(sn, si), axial_slices(cp, si)
    row = {"extra_labels": ",".join(map(str, extra)),
           "sn_vox": int(sn.sum()), "sn_mm3": int(sn.sum()) * vox,
           "cp_vox": int(cp.sum()), "cp_mm3": int(cp.sum()) * vox,
           "sn_sides": hemispheres(sn, lr), "cp_sides": hemispheres(cp, lr),
           "sn_slices": len(sn_sl),
           "cp_missing_slices": len(sn_sl - cp_sl),
           "cp_extra_slices": len(cp_sl - sn_sl),
           "cp_touching_sn": touching(sn, cp, si),
           "sn_mean": float(image[sn].mean()) if sn.any() else np.nan,
           "cp_mean": float(image[cp].mean()) if cp.any() else np.nan}
    row["contrast"] = ((row["sn_mean"] - row["cp_mean"]) / row["cp_mean"]
                       if row["cp_mean"] else np.nan)

    flags = [f"unexpected label(s) {row['extra_labels']}"] if extra else []
    for name in phase_labels:
        sides = row[f"{name.lower()}_sides"]
        if sides < 2:
            flags.append(f"{name} missing" if sides == 0 else f"{name} in one hemisphere only")
    if "CP" in phase_labels:
        flags += [f"{row[col]} {text}" for col, text in (
            ("cp_missing_slices", "SN slice(s) without CP"),
            ("cp_extra_slices", "CP slice(s) without SN"),
            ("cp_touching_sn", "CP voxel(s) touching SN")) if row[col]]
    row["flags"] = "; ".join(flags)
    return row


def agreement(masks, rows, zooms):
    """Each rater against the other raters on one scan. `masks` maps rater ->
    label array, `rows` rater -> check_mask row."""
    sn = {r: m == SN for r, m in masks.items()}
    cp = {r: m == CP for r, m in masks.items()}
    pairs = {}
    for a, b in combinations(masks, 2):
        pairs[a, b] = pairs[b, a] = (
            utils.dice(sn[a], sn[b]),
            utils.surface_distance_metrics(sn[a], sn[b], zooms)["hd95"],
            utils.dice(cp[a], cp[b]))
    median = {k: pd.Series([rows[r][k] for r in masks]).median()
              for k in ("sn_vox", "cp_mean", "contrast")}
    out = []
    for r in masks:
        vs_others = pd.DataFrame([pairs[r, o] for o in masks if o != r],
                                 columns=["sn_dice", "sn_hd95", "cp_dice"]).mean()
        out.append({"rater": r, "n_raters": len(masks), **vs_others.to_dict(),
                    "sn_volume_ratio": rows[r]["sn_vox"] / median["sn_vox"],
                    "cp_mean_dev": rows[r]["cp_mean"] / median["cp_mean"] - 1,
                    "contrast_diff": rows[r]["contrast"] - median["contrast"]})
    return out


def compute_protocol(phase_key, workspace, phases, raters, attempt=1, out_dir=None):
    """Protocol rows for every rater mask of one phase, plus per-scan agreement
    wherever >= 2 raters segmented a scan. `attempt` is an attempt number or
    "latest" (each rater's highest attempt per scan, e.g. a calibration redo).
    Returns a dict of DataFrames, also written to <out_dir>/protocol/."""
    pcfg = phases[phase_key]
    labels = utils.phase_labels(pcfg)
    phase_dir = Path(workspace) / utils.phase_dirname(phase_key, pcfg)
    tag = {"phase": phase_key, "attempt": "latest" if attempt == "latest" else f"a{attempt}"}
    prot, agree = [], []
    for sub in utils.find_subjects(phase_dir, raters):
        assigned = [r for r in raters if utils.input_image(phase_dir, r, sub)]
        if not assigned:                             # no input image for this scan
            continue
        image_path = utils.input_image(phase_dir, assigned[0], sub)
        image = affine = zooms = None
        masks, rows = {}, {}
        for r in assigned:
            found = qa.rater_attempts(phase_dir, r, sub)["attempts"]
            if attempt == "latest":
                path = found[max(found)] if found else None
            else:
                path = found.get(attempt)
            if path is None and utils.split_scan(sub)[1] is not None:
                continue            # a run or image not segmented; coverage.md lists those
            row = tag | {"subject": sub, "rater": r, "mask": path.name if path else "(missing)"}
            if path is None or image_path is None:
                prot.append(row | {"flags": "mask missing" if path is None else "image missing"})
                continue
            if image is None:
                image, affine, zooms = utils.load_nii(image_path)
            data = utils.load_mask(path)[0]
            if data.shape != image.shape:
                prot.append(row | {"flags": f"mask grid {data.shape} != image {image.shape}"})
                continue
            rows[r] = row | check_mask(data, image, affine, zooms, labels)
            prot.append(rows[r])
            masks[r] = data
        if len(masks) > 1:
            agree += [tag | {"subject": sub} | a for a in agreement(masks, rows, zooms)]

    tables = {"protocol_per_mask": pd.DataFrame(prot),
              "agreement_per_scan": pd.DataFrame(agree)}
    if out_dir is not None:
        pdir = Path(out_dir) / "protocol"
        pdir.mkdir(parents=True, exist_ok=True)
        for name, df in tables.items():
            if not df.empty:
                df.to_csv(pdir / f"{name}.csv", index=False)
    return tables


def verdict(tables, criteria, raters):
    """Pass/review per rater against a phase's `criteria`, with the scans
    behind each missed criterion."""
    prot, agree = tables["protocol_per_mask"], tables["agreement_per_scan"]
    if agree.empty:
        agree = pd.DataFrame(columns=["rater", "subject", "sn_dice", "sn_hd95",
                                      "sn_volume_ratio", "cp_mean_dev"], dtype=float)
    n_scans = prot["subject"].nunique()
    present = set(prot.loc[prot["mask"] != "(missing)", "rater"])
    rows = []
    for r in (r for r in raters if r in present):     # raters who have started
        p, a = prot[prot["rater"] == r], agree[agree["rater"] == r]
        flagged = set(p.loc[p["flags"].fillna("") != "", "subject"])
        low = set(a.loc[(a["sn_dice"] < criteria["sn_dice_min"]) |
                        (a["sn_hd95"] > criteria["sn_hd95_max_mm"]), "subject"])
        # negated <= so that a NaN deviation counts as out of tolerance
        vol_off = set(a.loc[~((a["sn_volume_ratio"] - 1).abs() <= criteria["sn_volume_tol"]),
                            "subject"])
        cp_off = set(a.loc[~(a["cp_mean_dev"].abs() <= criteria["cp_mean_tol"]), "subject"])
        checks = {
            "protocol": (len(p) == n_scans and not flagged, flagged),
            "sn_agreement": (a["sn_dice"].median() >= criteria["sn_dice_min"] and
                             a["sn_hd95"].median() <= criteria["sn_hd95_max_mm"], low),
            "sn_volume": (len(a) - len(vol_off) >= criteria["min_scans"], vol_off),
            "cp_reference": (len(a) - len(cp_off) >= criteria["min_scans"], cp_off),
        }
        review = set().union(*(scans for ok, scans in checks.values() if not ok))
        rows.append({"rater": r, "sn_dice_median": a["sn_dice"].median(),
                     "sn_hd95_median": a["sn_hd95"].median(),
                     "sn_volume_scans_ok": len(a) - len(vol_off),
                     "cp_mean_scans_ok": len(a) - len(cp_off),
                     **{name: bool(ok) for name, (ok, _) in checks.items()},
                     "verdict": "pass" if all(ok for ok, _ in checks.values()) else "review",
                     "review_scans": " ".join(sorted(review))})
    return pd.DataFrame(rows)


def run_protocol(phase_key, workspace, phases, raters, attempt, out_dir):
    """compute_protocol plus, for a phase with `criteria`, the per-rater verdict,
    all written to <out_dir>/protocol/. Returns (tables, verdict or None)."""
    tables = compute_protocol(phase_key, workspace, phases, raters, attempt, out_dir)
    criteria = phases[phase_key].get("criteria")
    if not criteria or tables["protocol_per_mask"].empty:
        return tables, None
    v = verdict(tables, criteria, raters)
    pdir = Path(out_dir) / "protocol"
    v.to_csv(pdir / "verdict.csv", index=False)
    tag = "latest" if attempt == "latest" else f"a{attempt}"
    (pdir / "verdict.md").write_text(verdict_markdown(v, criteria, f"{phase_key} ({tag})"))
    return tables, v


def verdict_markdown(v, criteria, title):
    c = criteria
    mark = {True: "ok", False: "**miss**"}
    lines = [
        f"# Calibration verdict: {title}", "",
        "Consistency with the other raters on the same scans (criteria in "
        f"`config/phases.yaml`): no protocol flags; median SN Dice ≥ {c['sn_dice_min']} "
        f"and median SN HD95 ≤ {c['sn_hd95_max_mm']} mm; SN volume within "
        f"±{c['sn_volume_tol']:.0%} and CP mean intensity within ±{c['cp_mean_tol']:.0%} "
        f"of the scan median on ≥ {c['min_scans']} scans.", "",
        "| Rater | Protocol | SN Dice / HD95 | SN volume (scans ok) | CP mean (scans ok) "
        "| Verdict | Scans to review |",
        "|---|---|---|---|---|---|---|"]
    for _, r in v.iterrows():
        lines.append(
            f"| {r['rater']} | {mark[bool(r['protocol'])]} "
            f"| {r['sn_dice_median']:.3f} / {r['sn_hd95_median']:.2f} mm, "
            f"{mark[bool(r['sn_agreement'])]} "
            f"| {r['sn_volume_scans_ok']}, {mark[bool(r['sn_volume'])]} "
            f"| {r['cp_mean_scans_ok']}, {mark[bool(r['cp_reference'])]} "
            f"| {r['verdict']} | {r['review_scans'] or 'none'} |")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phase", default="phase4")
    ap.add_argument("--config-dir", type=Path, default=utils.CONFIG_DIR)
    ap.add_argument("--workspace", type=Path, default=None,
                    help="Override workspace_root from paths.yaml.")
    ap.add_argument("--qc-root", type=Path, default=None,
                    help="Override qc_output_root from paths.yaml.")
    ap.add_argument("--attempt", type=int, default=1, help="Attempt to check (default: 1).")
    ap.add_argument("--latest", action="store_true",
                    help="Check each rater's highest attempt per scan (re-segmentations).")
    args = ap.parse_args()

    paths, phases, raters = utils.load_config(args.config_dir)
    criteria = phases[args.phase].get("criteria")
    if criteria and any(k not in criteria for k in CRITERIA):
        ap.error(f"{args.phase} criteria must define {', '.join(CRITERIA)}")
    workspace = args.workspace or Path(paths["workspace_root"])
    folder = "latest" if args.latest else qa.attempt_dirname(args.attempt)
    out_dir = Path(args.qc_root or paths["qc_output_root"]) / args.phase / folder
    tables, v = run_protocol(args.phase, workspace, phases, raters,
                             "latest" if args.latest else args.attempt, out_dir)
    prot, agree = tables["protocol_per_mask"], tables["agreement_per_scan"]
    if prot.empty:
        print(f"{args.phase}: no subjects found in {workspace}")
        return 1

    flagged = prot[prot["flags"].fillna("") != ""]
    print(f"{args.phase} {folder}: {len(prot)} rater masks, {len(flagged)} flagged")
    for _, r in flagged.iterrows():
        print(f"  {r['rater']}/{r['subject']}: {r['flags']}")
    if not agree.empty:
        pd.set_option("display.float_format", lambda v: f"{v:.3f}")
        cols = ["sn_dice", "sn_hd95", "cp_dice", "sn_volume_ratio", "cp_mean_dev", "contrast_diff"]
        print("\n-- per rater, median across scans (vs the other raters) --")
        print(agree.groupby("rater")[cols].median().to_string())
    if v is not None:
        print("\n-- verdict --")
        print(v[["rater", "protocol", "sn_agreement", "sn_volume", "cp_reference",
                 "verdict", "review_scans"]].to_string(index=False))
    print(f"\nTables written to {out_dir / 'protocol'}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
