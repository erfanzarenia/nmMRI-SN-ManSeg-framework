#!/usr/bin/env python3
"""Publication-quality QC figures for the SN segmentation workspace.

Two families of figure, both reading the per-scan tables written by
qc_analysis.py (and reloading the masks/images for the slice views):

  Agreement plots  per scan, every rater drawn individually PLUS the mean
                   across raters on the same axes, for Dice and Hausdorff
                   (HD95 + max HD), rater-vs-expert; bar summaries per rater
                   and per rater-pair.

  Worst slices     for each scan x rater, the three axial slices with the
                   lowest per-slice Dice against the expert, shown as the NM
                   image with the rater (top row) and expert (bottom row)
                   segmentations overlaid, annotated with slice/Dice/HD.

Importable; run_qc.py calls generate_phase_figures() / worst_slices_for_phase().
Standalone it renders both for one phase:

    python scripts/qc_visualize.py --phase phase1
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

import utils
import qc_analysis as qa

# Consistent identity colors across every figure.
RATER_COLORS = {"R1": "#1f77b4", "R2": "#2ca02c", "R3": "#d62728",
                "R4": "#17becf", "R5": "#9467bd", "R6": "#8c564b", "R7": "#e377c2",
                "R8": "#bcbd22"}
PAIR_COLORS = {"R1-R2": "#9467bd", "R1-R3": "#8c564b", "R3-R2": "#e377c2",
               "R2-R3": "#e377c2"}
MEAN_COLOR = "#111111"
RATER_OVERLAY = RATER_COLORS                     # contour color per rater in montages
EXPERT_COLOR = "#ffb000"

METRIC_LABEL = {"dice": "Dice", "jaccard": "Jaccard (IoU)",
                "hd": "Hausdorff distance (mm)", "hd95": "95% Hausdorff (mm)",
                "assd": "Avg. surface dist. (mm)"}


def _style():
    plt.rcParams.update({
        "figure.dpi": 130, "savefig.dpi": 200, "font.size": 10,
        "axes.titlesize": 12, "axes.titleweight": "bold", "axes.grid": True,
        "grid.alpha": 0.3, "axes.axisbelow": True, "axes.spines.top": False,
        "axes.spines.right": False, "legend.frameon": False,
        "figure.constrained_layout.use": True})


def _color(rater):
    return RATER_COLORS.get(rater, "#555555")


def _series_colors(keys, fixed):
    """Fixed identity colors where defined, then tab20 colors not already
    used, so every rater or pair in a figure stays distinguishable."""
    used = {matplotlib.colors.to_hex(c) for c in fixed.values()}
    spare = (c for c in plt.get_cmap("tab20").colors
             if matplotlib.colors.to_hex(c) not in used)
    return {k: fixed.get(k) or next(spare, "#555555") for k in keys}


# ----------------------------- agreement plots ---------------------------
def plot_per_scan(df, metric, out_path, title, higher_is_better=True,
                  group="rater", phase_breaks=None):
    """One marker+line series per rater (or pair) across scans, plus the bold
    mean-across-series line. df has columns: subject, <group>, <metric>."""
    _style()
    subjects = sorted(df["subject"].unique())
    xpos = {s: i for i, s in enumerate(subjects)}
    fig, ax = plt.subplots(figsize=(max(6, 0.7 * len(subjects) + 3), 4.2))
    cmap = _series_colors(sorted(df[group].unique()),
                          RATER_COLORS if group == "rater" else PAIR_COLORS)

    for key, g in df.groupby(group):
        g = g.sort_values("subject")
        ax.plot([xpos[s] for s in g["subject"]], g[metric], marker="o", ms=6,
                lw=1.4, color=cmap[key], label=str(key), alpha=0.9)

    mean = df.groupby("subject")[metric].mean().reindex(subjects)
    ax.plot(range(len(subjects)), mean.values, marker="D", ms=7, lw=2.6,
            color=MEAN_COLOR, label="mean", zorder=5)

    ax.set_xticks(range(len(subjects)))
    ax.set_xticklabels(subjects, rotation=45, ha="right")
    ax.set_ylabel(METRIC_LABEL.get(metric, metric))
    ax.set_title(title)
    if metric in ("dice", "jaccard"):
        ax.set_ylim(min(0.4, df[metric].min() - 0.05), 1.0)
    else:
        ax.set_ylim(0, df[metric].max() * 1.15)
    if phase_breaks:
        for xb, lbl in phase_breaks:
            ax.axvline(xb - 0.5, color="#999999", ls=":", lw=1)
            ax.text(xb, ax.get_ylim()[1], lbl, ha="left", va="top",
                    fontsize=8, color="#666666")
    ax.legend(ncol=min(5, df[group].nunique() + 1), loc="best", fontsize=8)
    fig.savefig(out_path)
    plt.close(fig)


def plot_by_group_bar(summary_df, metric, group_col, out_path, title,
                      color_map=None):
    """Bar of <metric>_mean +/- _sd per group (rater or pair)."""
    _style()
    df = summary_df.sort_values(f"{metric}_mean")
    fig, ax = plt.subplots(figsize=(max(4, 0.9 * len(df) + 2), 4))
    colors = [(color_map or {}).get(k, "#4c78a8") for k in df[group_col]]
    ax.bar(df[group_col].astype(str), df[f"{metric}_mean"],
           yerr=df[f"{metric}_sd"], capsize=4, color=colors, alpha=0.9,
           edgecolor="black", linewidth=0.6)
    for x, (m, n) in enumerate(zip(df[f"{metric}_mean"], df["n"])):
        ax.text(x, m, f"{m:.3f}\n(n={n})", ha="center", va="bottom", fontsize=8)
    ax.set_ylabel(METRIC_LABEL.get(metric, metric))
    ax.set_title(title)
    if metric in ("dice", "jaccard"):
        ax.set_ylim(0, 1.0)
    fig.savefig(out_path)
    plt.close(fig)


def generate_agreement_figures(tables, out_dir, tag):
    """Render the Dice + Hausdorff agreement figures for a set of tables.
    `tag` (e.g. 'phase1' or 'overall') is used in titles. Returns saved paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    rve = tables.get("rater_vs_expert_per_scan")
    rvr = tables.get("rater_vs_rater_per_scan")
    breaks = tables.get("_phase_breaks")

    if rve is not None and not rve.empty:
        p = out_dir / f"dice_rater_vs_expert_{tag}.png"
        plot_per_scan(rve, "dice", p, f"{tag}: Dice vs expert (per rater + mean)",
                      phase_breaks=breaks)
        saved.append(p)
        # Hausdorff: HD95 + max HD stacked, raters + mean on each.
        _style()
        subjects = sorted(rve["subject"].unique())
        xpos = {s: i for i, s in enumerate(subjects)}
        fig, axes = plt.subplots(2, 1, sharex=True,
                                 figsize=(max(6, 0.7 * len(subjects) + 3), 7))
        for ax, metric in zip(axes, ["hd95", "hd"]):
            for r, g in rve.groupby("rater"):
                g = g.sort_values("subject")
                ax.plot([xpos[s] for s in g["subject"]], g[metric], marker="o",
                        ms=6, lw=1.4, color=_color(r), label=r, alpha=0.9)
            mean = rve.groupby("subject")[metric].mean().reindex(subjects)
            ax.plot(range(len(subjects)), mean.values, marker="D", ms=7, lw=2.6,
                    color=MEAN_COLOR, label="mean", zorder=5)
            ax.set_ylabel(METRIC_LABEL[metric])
            ax.set_ylim(0, rve[metric].max() * 1.15)
            if breaks:
                for xb, _l in breaks:
                    ax.axvline(xb - 0.5, color="#999999", ls=":", lw=1)
        axes[0].set_title(f"{tag}: Hausdorff vs expert (per rater + mean)")
        axes[0].legend(ncol=4, fontsize=8)
        axes[-1].set_xticks(range(len(subjects)))
        axes[-1].set_xticklabels(subjects, rotation=45, ha="right")
        p = out_dir / f"hausdorff_rater_vs_expert_{tag}.png"
        fig.savefig(p)
        plt.close(fig)
        saved.append(p)

        by_rater = qa.summarize(rve, ["rater"], qa.SD_METRICS)
        for metric in ["dice", "hd95"]:
            p = out_dir / f"{metric}_by_rater_{tag}.png"
            plot_by_group_bar(by_rater, metric, "rater", p,
                              f"{tag}: mean {METRIC_LABEL[metric]} vs expert by rater",
                              RATER_COLORS)
            saved.append(p)

    if rvr is not None and not rvr.empty:
        p = out_dir / f"dice_rater_vs_rater_{tag}.png"
        plot_per_scan(rvr, "dice", p, f"{tag}: inter-rater Dice (per pair + mean)",
                      group="pair", phase_breaks=breaks)
        saved.append(p)
        by_pair = qa.summarize(rvr, ["pair"], qa.SD_METRICS)
        for metric in ["dice", "hd95"]:
            p = out_dir / f"{metric}_by_pair_{tag}.png"
            plot_by_group_bar(by_pair, metric, "pair", p,
                              f"{tag}: mean inter-rater {METRIC_LABEL[metric]} by pair",
                              PAIR_COLORS)
            saved.append(p)
    return saved


# ------------------------------ worst slices -----------------------------
def _bbox_2d(mask3d, axis, pad, shape):
    """In-plane bounding box (over the two non-axial axes) of a 3D mask, padded
    and clipped. Returns ((lo0,hi0),(lo1,hi1)) on the kept axes."""
    keep = [a for a in range(3) if a != axis]
    idx = np.where(mask3d.any(axis=axis))         # 2D projection over axial axis
    if idx[0].size == 0:
        return None
    box = []
    for d, k in enumerate(keep):
        lo, hi = idx[d].min(), idx[d].max()
        box.append((max(0, lo - pad), min(shape[k] - 1, hi + pad)))
    return box


def worst_best_montage(image, rater_mask, expert_mask, spacing, out_path,
                       title, rater_color="#d62728", n=3, axis=2,
                       min_union=10, pad=8):
    """Montage contrasting the n WORST (top row) and n BEST (bottom row) axial
    slices by rater-vs-expert per-slice Dice. Every panel shows the NM image
    with the rater and expert contours overlaid once (no separate expert row),
    so each slice's agreement is read directly. Returns (out_path, info)|None."""
    sl = utils.per_slice_dice(rater_mask, expert_mask, axis=axis, min_union=min_union)
    if not sl:
        sl = utils.per_slice_dice(rater_mask, expert_mask, axis=axis, min_union=1)
    if not sl:
        return None
    order = sorted(sl, key=lambda k: sl[k])        # ascending Dice
    worst = sorted(order[:n])                       # n lowest, anatomical order
    best = sorted([k for k in reversed(order) if k not in worst][:n])  # n highest, distinct

    union = rater_mask | expert_mask
    box = _bbox_2d(union, axis, pad, image.shape)
    if box is None:
        return None
    (a0, b0), (a1, b1) = box

    def plane(vol, k):
        return np.rot90(np.take(vol, k, axis=axis)[a0:b0 + 1, a1:b1 + 1])

    # window intensities on the cropped SN neighbourhood for contrast
    crop = image[a0:b0 + 1, a1:b1 + 1, :]
    vmin, vmax = np.percentile(crop, 2), np.percentile(crop, 99.5)

    _style()
    plt.rcParams["axes.grid"] = False
    ncol = max(len(worst), len(best), 1)
    fig, axes = plt.subplots(2, ncol, figsize=(2.7 * ncol + 0.6, 6.0), squeeze=False)
    for row, (label, keys) in enumerate([("worst", worst), ("best", best)]):
        for col in range(ncol):
            ax = axes[row][col]
            if col >= len(keys):
                ax.axis("off")
                continue
            k = keys[col]
            ax.imshow(plane(image, k), cmap="gray", vmin=vmin, vmax=vmax,
                      interpolation="nearest")
            rm, em = plane(rater_mask, k), plane(expert_mask, k)
            if rm.any():
                ax.contour(rm, levels=[0.5], colors=[rater_color], linewidths=1.2)
            if em.any():
                ax.contour(em, levels=[0.5], colors=[EXPERT_COLOR], linewidths=1.2)
            ax.set_xticks([]); ax.set_yticks([])
            ax.set_title(f"slice {k}\nDice={sl[k]:.2f}", fontsize=10)
            if col == 0:
                ax.set_ylabel(label, fontsize=12, fontweight="bold")
    handles = [Line2D([0], [0], color=rater_color, lw=2, label="rater"),
               Line2D([0], [0], color=EXPERT_COLOR, lw=2, label="expert")]
    fig.legend(handles=handles, loc="lower center", ncol=2, fontsize=9,
               bbox_to_anchor=(0.5, -0.02))
    fig.suptitle(title, fontsize=12, fontweight="bold")
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)
    return out_path, {"worst": {k: sl[k] for k in worst},
                      "best": {k: sl[k] for k in best}}


def all_slices_montage(image, rater_mask, expert_mask, spacing, out_path,
                       title, rater_color="#d62728", axis=2, min_union=1,
                       pad=8, ncols=6):
    """Single figure with every axial slice that contains SN (rater or expert).
    Each panel is the NM image with the rater and expert contours overlaid once
    (no separate expert row), annotated with slice index and per-slice Dice."""
    sl = utils.per_slice_dice(rater_mask, expert_mask, axis=axis, min_union=min_union)
    if not sl:
        return None
    slices = sorted(sl)                                # all SN-bearing slices
    union = rater_mask | expert_mask
    box = _bbox_2d(union, axis, pad, image.shape)
    if box is None:
        return None
    (a0, b0), (a1, b1) = box

    def plane(vol, k):
        return np.rot90(np.take(vol, k, axis=axis)[a0:b0 + 1, a1:b1 + 1])

    crop = image[a0:b0 + 1, a1:b1 + 1, :]
    vmin, vmax = np.percentile(crop, 2), np.percentile(crop, 99.5)

    _style()
    plt.rcParams["axes.grid"] = False
    n = len(slices)
    ncols = min(ncols, n)
    nrows = int(np.ceil(n / ncols))
    fig, axes = plt.subplots(nrows, ncols, squeeze=False,
                             figsize=(2.4 * ncols + 0.4, 2.55 * nrows + 0.9))
    for i in range(nrows * ncols):
        ax = axes[i // ncols][i % ncols]
        if i >= n:
            ax.axis("off")
            continue
        k = slices[i]
        ax.imshow(plane(image, k), cmap="gray", vmin=vmin, vmax=vmax,
                  interpolation="nearest")
        rm, em = plane(rater_mask, k), plane(expert_mask, k)
        if rm.any():
            ax.contour(rm, levels=[0.5], colors=[rater_color], linewidths=1.2)
        if em.any():
            ax.contour(em, levels=[0.5], colors=[EXPERT_COLOR], linewidths=1.2)
        ax.set_xticks([]); ax.set_yticks([])
        ax.set_title(f"slice {k} · Dice={sl[k]:.2f}", fontsize=9)
    handles = [Line2D([0], [0], color=rater_color, lw=2, label="rater"),
               Line2D([0], [0], color=EXPERT_COLOR, lw=2, label="expert")]
    fig.legend(handles=handles, loc="lower center", ncol=2, fontsize=9,
               bbox_to_anchor=(0.5, -0.01))
    fig.suptitle(title, fontsize=12, fontweight="bold")
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)
    return out_path, slices


def _iter_scan_rater(phase_key, workspace, phases, raters, rve_df, label, attempt,
                     experts=None):
    """Yield (sub, rater, image, rmask, expert, spacing, row) for every rater x
    scan in rve_df, loading the rater mask for attempt N. Expert mask + image are
    read once per subject; `experts` is qa.expert_masks() (inputs/ copies only
    when omitted)."""
    pcfg = phases[phase_key]
    phase_dir = Path(workspace) / utils.phase_dirname(phase_key, pcfg)
    if experts is None:
        experts = qa.expert_masks(phase_key, workspace, phases, raters)
    lut = {(r["subject"], r["rater"]): r for _, r in rve_df.iterrows()}
    for sub in sorted(rve_df["subject"].unique()):
        ip = next((p for p in (utils.resolve(utils.input_dir(phase_dir, r, sub) / "image.nii.gz")
                               for r in raters) if p), None)
        if sub not in experts or ip is None:
            continue
        edata, _, spacing = utils.load_mask(experts[sub])
        expert = (edata == label)
        image, _, _ = utils.load_nii(ip)
        for r in raters:
            if (sub, r) not in lut:                     # no usable mask for this attempt
                continue
            res = qa.resolve_rater_mask(phase_dir, r, sub, label, attempt)
            if res is None:
                continue
            rmask = res[0]
            yield sub, r, image, rmask, expert, spacing, lut.get((sub, r))


def _scan_title(phase_key, sub, r, row, attempt=None):
    d = f"Dice={row['dice']:.3f}" if row is not None else ""
    h = f"HD95={row['hd95']:.2f}mm" if row is not None else ""
    tag = phase_key + (f" · {attempt}" if attempt else "")
    return f"{tag}  {sub}  rater {r}   {d}  {h}"


def worst_slices_for_phase(phase_key, workspace, paths, phases, raters,
                           rve_df, out_dir, label=1, n=3, attempt=1, experts=None):
    """Render a worst-vs-best montage (n worst on top, n best on bottom, by
    per-slice Dice) for every rater x scan vs expert, for attempt N. Returns a
    list of (subject, rater, path)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    att = f"a{attempt}"
    made = []
    for sub, r, image, rmask, expert, spacing, row in _iter_scan_rater(
            phase_key, workspace, phases, raters, rve_df, label, attempt, experts):
        p = out_dir / f"{sub}_{r}_worst_best.png"
        res = worst_best_montage(image, rmask, expert, spacing, p,
                                 _scan_title(phase_key, sub, r, row, att),
                                 rater_color=RATER_OVERLAY.get(r, "#d62728"), n=n)
        if res:
            made.append((sub, r, res[0]))
    return made


def all_slices_for_phase(phase_key, workspace, paths, phases, raters,
                         rve_df, out_dir, label=1, attempt=1, experts=None):
    """Render an all-SN-slices montage (one panel per slice, both contours) for
    every rater x scan vs expert, for attempt N."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    att = f"a{attempt}"
    made = []
    for sub, r, image, rmask, expert, spacing, row in _iter_scan_rater(
            phase_key, workspace, phases, raters, rve_df, label, attempt, experts):
        p = out_dir / f"{sub}_{r}_allslices.png"
        res = all_slices_montage(image, rmask, expert, spacing, p,
                                 _scan_title(phase_key, sub, r, row, att),
                                 rater_color=RATER_OVERLAY.get(r, "#d62728"))
        if res:
            made.append((sub, r, res[0]))
    return made


# -------------------------------- CLI ------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phase", default="phase1")
    ap.add_argument("--config-dir", type=Path, default=utils.CONFIG_DIR)
    ap.add_argument("--workspace", type=Path, default=None)
    ap.add_argument("--qc-root", type=Path, default=None)
    ap.add_argument("--label", type=int, default=1)
    args = ap.parse_args()

    paths, phases, raters = utils.load_config(args.config_dir)
    workspace = args.workspace or Path(paths["workspace_root"])
    qc_root = args.qc_root or Path(paths["qc_output_root"])
    phase_dir = workspace / utils.phase_dirname(args.phase, phases[args.phase])
    split_path = Path(args.config_dir) / "subjects_split.json"
    experts = qa.expert_masks(args.phase, workspace, phases, raters, paths,
                              utils.load_json(split_path) if split_path.exists() else None)
    attempts = qa.detect_attempts(phase_dir, raters)
    print(f"{args.phase}: attempts {attempts}")

    per = []
    for n in attempts:
        folder = f"a{n}"
        base = qc_root / args.phase / folder
        tables = qa.compute_phase(args.phase, workspace, phases, raters, n,
                                  label=args.label, out_dir=base, experts=experts)
        per.append(tables)
        figs = generate_agreement_figures(tables, base / "plots", f"{args.phase}_{folder}")
        rve = tables.get("rater_vs_expert_per_scan")
        n_slices = 0
        if rve is not None and not rve.empty:
            worst_slices_for_phase(args.phase, workspace, paths, phases, raters,
                                   rve, base / "worst_slices", label=args.label, attempt=n,
                                   experts=experts)
            allm = all_slices_for_phase(args.phase, workspace, paths, phases, raters,
                                        rve, base / "all_slices", label=args.label, attempt=n,
                                        experts=experts)
            n_slices = len(allm)
        print(f"  [{folder}] {len(figs)} figures, {n_slices} slice montages")

    # mean-of-metrics average: metric plots only (no masks -> no montages)
    if len(attempts) > 1:
        avg = qa.average_attempt_metrics(per, args.phase)
        base = qc_root / args.phase / qa.AVERAGE_DIR
        (base / "metrics").mkdir(parents=True, exist_ok=True)
        for name, df in avg.items():
            if df is not None and not df.empty:
                df.to_csv(base / "metrics" / f"{name}.csv", index=False)
        figs = generate_agreement_figures(avg, base / "plots", f"{args.phase}_{qa.AVERAGE_DIR}")
        print(f"  [{qa.AVERAGE_DIR}] {len(figs)} figures (mean of metrics; no montages)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
