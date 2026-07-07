#!/usr/bin/env python3
"""Assemble concise Markdown QC reports from the qc_analysis.py tables.

phase_report() writes <qc-root>/<phase>/reports/<phase>_qc_report.md and returns
a structured findings dict; overall_report() merges the phase findings into
<qc-root>/summary/overall_qc_report.md. Reports highlight the strongest/weakest
raters, difficult scans, outliers, disagreement patterns, and anything that
warrants a second look. Pure text assembly -- no recomputation of metrics.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import qc_analysis as qa


def df_to_md(df, cols=None, floatfmt="{:.3f}"):
    if df is None or len(df) == 0:
        return "_none_\n"
    df = df[cols] if cols else df
    cols = list(df.columns)

    def fmt(v):
        if isinstance(v, (float, np.floating)):
            if pd.isna(v):
                return "—"
            return str(int(v)) if float(v).is_integer() else floatfmt.format(v)
        return "" if v is None else str(v)

    out = ["| " + " | ".join(cols) + " |",
           "| " + " | ".join("---" for _ in cols) + " |"]
    out += ["| " + " | ".join(fmt(r[c]) for c in cols) + " |"
            for _, r in df.iterrows()]
    return "\n".join(out) + "\n"


def _round(df, nd=3):
    if df is None or df.empty:
        return df
    df = df.copy()
    for c in df.select_dtypes("number").columns:
        df[c] = df[c].round(nd)
    return df


def compute_findings(tag, tables):
    """Extract the human-facing highlights used by both phase and overall
    reports. Returns a dict of small DataFrames / scalars."""
    f = {"tag": tag}
    rve = tables.get("rater_vs_expert_per_scan")
    rvr = tables.get("rater_vs_rater_per_scan")
    vol = tables.get("volumes")
    inv = tables.get("inventory")

    if rve is not None and not rve.empty:
        by_rater = qa.summarize(rve, ["rater"], qa.SD_METRICS)
        f["by_rater"] = by_rater
        f["strongest_rater"] = by_rater.iloc[-1]["rater"]      # highest mean Dice
        f["weakest_rater"] = by_rater.iloc[0]["rater"]
        by_scan = qa.summarize(rve, ["subject"], qa.SD_METRICS)
        f["by_scan"] = by_scan
        f["hardest_scans"] = by_scan.head(3)                   # lowest mean Dice
        f["phase_summary_rve"] = qa.summarize(rve, None, qa.SD_METRICS)
        # statistical outlier rater-scan cells (Dice z<=-2 or HD95 z>=+2)
        d, h = rve["dice"], rve["hd95"]
        zc = rve.assign(
            dice_z=(d - d.mean()) / (d.std(ddof=0) or 1),
            hd95_z=(h - h.mean()) / (h.std(ddof=0) or 1))
        out = zc[(zc.dice_z <= -2) | (zc.hd95_z >= 2) | (zc.dice < 0.6)]
        f["outliers"] = out[["subject", "rater", "dice", "hd95", "dice_z", "hd95_z"]]

    if rvr is not None and not rvr.empty:
        by_pair = qa.summarize(rvr, ["pair"], qa.SD_METRICS)
        f["by_pair"] = by_pair
        f["least_concordant_pair"] = by_pair.iloc[0]["pair"]
        f["most_concordant_pair"] = by_pair.iloc[-1]["pair"]
        f["phase_summary_rvr"] = qa.summarize(rvr, None, qa.SD_METRICS)

    if vol is not None and not vol.empty and "expert" in set(vol["source"]):
        exp = vol[vol.source == "expert"].set_index("subject")["vol_mm3"]
        rat = vol[vol.source != "expert"].copy()
        rat["vol_ratio"] = rat.apply(
            lambda r: r["vol_mm3"] / exp.get(r["subject"], np.nan), axis=1)
        f["vol_bias"] = (rat.groupby("source")["vol_ratio"]
                         .agg(mean="mean", sd="std", n="count").reset_index()
                         .rename(columns={"source": "rater"}).sort_values("mean"))

    if inv is not None and not inv.empty:
        f["anomalies"] = inv[inv["flags"].fillna("") != ""]
    return f


METHODS = (
    "**Methods.** Dice and Jaccard (overlap), and surface distances in physical "
    "mm — max Hausdorff (HD), 95th-percentile Hausdorff (HD95, robust to "
    "single-voxel outliers) and average symmetric surface distance (ASSD) — were "
    "computed on the SN label (value 1) at the native voxel spacing "
    "(0.4×0.4×1.0 mm). Each segmentation attempt (`a1`, `a2`, …) is analyzed "
    "independently; the **attempts-average** is the mean of the per-attempt "
    "metrics for each scan/rater (`n_attempts` = how many were averaged), "
    "mirroring every other table. Only rater/scans that actually have an attempt "
    "contribute, so the counts adapt to the data. HD95 is the recommended "
    "distance metric for ranking.\n")


def phase_report(tag, tables, base_dir, fig_tag, reproducibility=None,
                 has_slices=True, plots_rel="../plots", slices_rel="../worst_slices"):
    f = compute_findings(tag, tables)
    rdir = Path(base_dir) / "reports"
    rdir.mkdir(parents=True, exist_ok=True)
    L = []
    A = L.append

    rve = tables.get("rater_vs_expert_per_scan")
    rvr = tables.get("rater_vs_rater_per_scan")
    n_sub = rve["subject"].nunique() if rve is not None and not rve.empty else (
        rvr["subject"].nunique() if rvr is not None and not rvr.empty else 0)

    A(f"# QC Report — {tag}\n")
    A(f"Subjects: **{n_sub}** · Raters: **R1, R2, R3** · "
      "Comparison: rater-vs-expert and rater-vs-rater.\n")
    A(METHODS)

    # headline
    A("## Headline\n")
    if "phase_summary_rve" in f:
        s = f["phase_summary_rve"].iloc[0]
        A(f"- **Rater vs expert** (grand mean over {int(s['n'])} rater-scans): "
          f"Dice **{s['dice_mean']:.3f} ± {s['dice_sd']:.3f}**, "
          f"HD95 **{s['hd95_mean']:.2f} mm**, HD {s['hd_mean']:.2f} mm, "
          f"ASSD {s['assd_mean']:.3f} mm.")
        A(f"- **Strongest rater:** {f['strongest_rater']} · "
          f"**Weakest rater:** {f['weakest_rater']}.")
    if "phase_summary_rvr" in f:
        s = f["phase_summary_rvr"].iloc[0]
        A(f"- **Inter-rater** (grand mean over {int(s['n'])} pair-scans): "
          f"Dice **{s['dice_mean']:.3f} ± {s['dice_sd']:.3f}**, "
          f"HD95 {s['hd95_mean']:.2f} mm. "
          f"Least concordant pair: **{f.get('least_concordant_pair','—')}**, "
          f"most concordant: **{f.get('most_concordant_pair','—')}**.")
    A("")

    # data quality
    A("## Data inventory & anomalies\n")
    if "anomalies" in f and not f["anomalies"].empty:
        A("Flags raised while locating masks (handled, not dropped silently):\n")
        A(df_to_md(f["anomalies"][["subject", "rater", "available",
                                   "used", "flags"]]))
    else:
        A("No anomalies: every rater/subject had a standard mask for this attempt.\n")

    # rater vs expert
    if rve is not None and not rve.empty:
        A("\n## Rater vs expert\n")
        A("### Per rater (averaged across scans, worst first)\n")
        A(df_to_md(_round(f["by_rater"][["rater", "n", "dice_mean", "dice_sd",
            "hd_mean", "hd95_mean", "assd_mean"]])))
        A("\n### Per scan (averaged across raters, hardest first)\n")
        A(df_to_md(_round(f["by_scan"][["subject", "n", "dice_mean", "dice_sd",
            "hd95_mean", "hd95_max"]])))
        A("\n### Outlier rater-scan cells (Dice z≤−2, HD95 z≥+2, or Dice<0.60)\n")
        A(df_to_md(_round(f["outliers"])) if not f["outliers"].empty
          else "_none flagged_\n")
        A("\n### Full per-scan table\n")
        A(df_to_md(_round(rve[["subject", "rater", "dice", "hd", "hd95",
            "assd", "vol_ratio"]])))
        A(f"\n![Dice vs expert]({plots_rel}/dice_rater_vs_expert_{fig_tag}.png)\n")
        A(f"![Hausdorff vs expert]({plots_rel}/hausdorff_rater_vs_expert_{fig_tag}.png)\n")
        A(f"![Dice by rater]({plots_rel}/dice_by_rater_{fig_tag}.png)\n")

    # volume bias
    if "vol_bias" in f:
        A("\n### Volume bias (rater SN volume / expert volume)\n")
        A("Ratio >1 = over-segmentation, <1 = under-segmentation.\n")
        A(df_to_md(_round(f["vol_bias"])))

    # rater vs rater
    if rvr is not None and not rvr.empty:
        A("\n## Rater vs rater (inter-rater)\n")
        A("### Per rater pair (worst first)\n")
        A(df_to_md(_round(f["by_pair"][["pair", "n", "dice_mean", "dice_sd",
            "hd95_mean", "assd_mean"]])))
        A(f"\n![Inter-rater Dice]({plots_rel}/dice_rater_vs_rater_{fig_tag}.png)\n")
        A(f"![Inter-rater Dice by pair]({plots_rel}/dice_by_pair_{fig_tag}.png)\n")

    # intra-rater reproducibility between attempts (a_i vs a_j)
    if reproducibility is not None and not reproducibility.empty:
        A("\n## Intra-rater reproducibility (agreement between attempts)\n")
        A(df_to_md(_round(reproducibility[["subject", "rater", "attempt_a",
            "attempt_b", "dice", "jaccard", "hd95", "n_vox_a", "n_vox_b"]])))
        A("_Low attempt-to-attempt Dice with a large voxel-count gap indicates an "
          "incomplete attempt rather than genuine boundary disagreement._\n")

    # findings
    A("\n## Findings & recommendations\n")
    for b in _bullets(f):
        A(f"- {b}")
    A("\n## Worst & best slices\n")
    if has_slices:
        A(f"Per-rater montages contrasting the 3 worst (top row) and 3 best "
          f"(bottom row) axial slices by per-slice Dice vs expert are in "
          f"[`{slices_rel}/`]({slices_rel}/) (`<subject>_<rater>_worst_best.png`); "
          f"every panel overlays the rater and expert contours on the NM image. "
          f"Full per-slice montages are in [`../all_slices/`](../all_slices/).\n")
    else:
        A("This folder is the **mean of the per-attempt metrics**, so it has no "
          "slice montages (those need a single mask). See the per-attempt "
          "folders, e.g. [`../a1/worst_slices/`](../a1/worst_slices/) and "
          "[`../a1/all_slices/`](../a1/all_slices/).\n")

    slug = tag.replace(" · ", "_").replace(" ", "_")
    path = rdir / f"{slug}_qc_report.md"
    path.write_text("\n".join(L))
    return path, f


def _bullets(f):
    b = []
    if "strongest_rater" in f:
        br = f["by_rater"]
        s, w = br.iloc[-1], br.iloc[0]
        b.append(f"**Strongest rater vs expert:** {s['rater']} "
                 f"(Dice {s['dice_mean']:.3f}, HD95 {s['hd95_mean']:.2f} mm).")
        b.append(f"**Weakest rater vs expert:** {w['rater']} "
                 f"(Dice {w['dice_mean']:.3f}, HD95 {w['hd95_mean']:.2f} mm).")
    if "hardest_scans" in f:
        hs = ", ".join(f"{r['subject']} (Dice {r['dice_mean']:.3f})"
                       for _, r in f["hardest_scans"].iterrows())
        b.append(f"**Most difficult scans:** {hs}.")
    if "outliers" in f and not f["outliers"].empty:
        cells = ", ".join(f"{r['rater']}/{r['subject']} (Dice {r['dice']:.3f})"
                          for _, r in f["outliers"].iterrows())
        b.append(f"**Outlier rater-scan cells to review:** {cells}.")
    if "vol_bias" in f:
        vb = f["vol_bias"]
        hi, lo = vb.iloc[-1], vb.iloc[0]
        b.append(f"**Volume bias:** {hi['rater']} over-segments most "
                 f"(×{hi['mean']:.2f}); {lo['rater']} smallest (×{lo['mean']:.2f}).")
    if "least_concordant_pair" in f:
        b.append(f"**Disagreement pattern:** the {f['least_concordant_pair']} "
                 f"pair agrees least; {f['most_concordant_pair']} agrees most.")
    if "anomalies" in f and not f["anomalies"].empty:
        b.append(f"**Data issues:** {len(f['anomalies'])} file/availability flag(s) "
                 "— see inventory; resolve before final analysis.")
    return b


def overall_report(phase_findings, overall_tables, qc_root):
    """Cross-phase summary from the per-phase findings + combined tables."""
    rdir = Path(qc_root) / "summary"
    rdir.mkdir(parents=True, exist_ok=True)
    L = []
    A = L.append
    A("# QC Report — Overall (Phase 1 + Phase 2)\n")
    A(METHODS)

    rve = overall_tables.get("rater_vs_expert_per_scan")
    rvr = overall_tables.get("rater_vs_rater_per_scan")

    A("## Phase comparison (rater vs expert)\n")
    rows = []
    for tag, f in phase_findings.items():
        if "phase_summary_rve" in f:
            s = f["phase_summary_rve"].iloc[0]
            rows.append({"phase": tag, "n": int(s["n"]),
                         "dice_mean": s["dice_mean"], "dice_sd": s["dice_sd"],
                         "hd95_mean": s["hd95_mean"], "assd_mean": s["assd_mean"],
                         "strongest": f.get("strongest_rater"),
                         "weakest": f.get("weakest_rater")})
    A(df_to_md(_round(pd.DataFrame(rows))))

    if rve is not None and not rve.empty:
        A("\n## Pooled rater vs expert (both phases)\n")
        A("### Grand average\n")
        A(df_to_md(_round(qa.summarize(rve, None, qa.SD_METRICS))))
        A("\n### Per rater (pooled, worst first)\n")
        A(df_to_md(_round(qa.summarize(rve, ["rater"], qa.SD_METRICS)[
            ["rater", "n", "dice_mean", "dice_sd", "hd95_mean", "assd_mean"]])))
        A(f"\n![Overall Dice vs expert](plots/dice_rater_vs_expert_overall.png)\n")
        A(f"![Overall Hausdorff vs expert](plots/hausdorff_rater_vs_expert_overall.png)\n")
        A(f"![Overall Dice by rater](plots/dice_by_rater_overall.png)\n")

    if rvr is not None and not rvr.empty:
        A("\n## Pooled inter-rater (both phases)\n")
        A(df_to_md(_round(qa.summarize(rvr, ["pair"], qa.SD_METRICS)[
            ["pair", "n", "dice_mean", "dice_sd", "hd95_mean", "assd_mean"]])))
        A(f"\n![Overall inter-rater Dice by pair](plots/dice_by_pair_overall.png)\n")

    A("\n## Cross-phase findings\n")
    # pooled strongest/weakest
    if rve is not None and not rve.empty:
        pr = qa.summarize(rve, ["rater"], qa.SD_METRICS)
        A(f"- **Overall strongest rater:** {pr.iloc[-1]['rater']} "
          f"(Dice {pr.iloc[-1]['dice_mean']:.3f}).")
        A(f"- **Overall weakest rater:** {pr.iloc[0]['rater']} "
          f"(Dice {pr.iloc[0]['dice_mean']:.3f}).")
        # phase1 -> phase2 trend (phase1 has few subjects, so this is indicative
        # only; deltas under 0.02 are reported as "stable").
        A("- _Phase-1 has only ~2 scans/rater, so phase1→phase2 trends are "
          "indicative, not powered:_")
        for r in sorted(rve["rater"].unique()):
            d1 = rve[(rve.rater == r) & (rve.phase == "phase1")]["dice"].mean()
            d2 = rve[(rve.rater == r) & (rve.phase == "phase2")]["dice"].mean()
            if not np.isnan(d1) and not np.isnan(d2):
                delta = d2 - d1
                trend = ("stable" if abs(delta) < 0.02 else
                         "improved" if delta > 0 else "declined")
                A(f"  - **{r}** {trend} (Dice {d1:.3f}→{d2:.3f}, Δ{delta:+.3f}).")
    all_anom = sum(len(f.get("anomalies", [])) for f in phase_findings.values())
    A(f"- **Data issues across phases:** {all_anom} flag(s) total "
      "(see per-phase inventories).")
    A("\n## Per-phase reports\n")
    for tag in phase_findings:
        A(f"- [{tag}](../{tag}/reports/{tag}_qc_report.md)")

    path = rdir / "overall_qc_report.md"
    path.write_text("\n".join(L))
    return path
