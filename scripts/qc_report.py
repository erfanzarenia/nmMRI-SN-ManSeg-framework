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
                return "n/a"
            return str(int(v)) if float(v).is_integer() else floatfmt.format(v)
        return "" if v is None else str(v)

    out = ["| " + " | ".join(cols) + " |",
           "| " + " | ".join("---" for _ in cols) + " |"]
    out += ["| " + " | ".join(fmt(r[c]) for c in cols) + " |"
            for _, r in df.iterrows()]
    return "\n".join(out) + "\n"


def _round(df, nd=3):
    """Round for display. A <metric>_mean column with its <metric>_median next to
    it becomes one "mean (median)" column."""
    if df is None or df.empty:
        return df
    df = df.copy()
    for c in df.select_dtypes("number").columns:
        df[c] = df[c].round(nd)
    for c in [c for c in df.columns if c.endswith("_mean")]:
        med = c[:-len("_mean")] + "_median"
        if med in df.columns:
            df[c] = [a if pd.isna(b) else f"{a:g} ({b:g})" for a, b in zip(df[c], df[med])]
            df = df.drop(columns=med).rename(columns={c: f"{c} (median)"})
    return df


def _cols(df, cols):
    """Columns `cols` of df, each <metric>_mean followed by its median when present."""
    out = []
    for c in cols:
        out.append(c)
        if c.endswith("_mean") and f"{c[:-5]}_median" in df.columns:
            out.append(f"{c[:-5]}_median")
    return df[out]


def _med(s, metric, fmt):
    """" (median X)" for a summary row, empty when it has no median."""
    v = s.get(f"{metric}_median")
    return f" (median {v:{fmt}})" if v is not None and pd.notna(v) else ""


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
        f["phase_avg_rve"] = qa.average_of_averages(rve, qa.SD_METRICS)
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
        f["phase_avg_rvr"] = qa.average_of_averages(rvr, qa.SD_METRICS)

    if vol is not None and not vol.empty and "expert" in set(vol["source"]):
        exp = vol[vol.source == "expert"].set_index("subject")["vol_mm3"]
        rat = vol[vol.source != "expert"].copy()
        rat["vol_ratio"] = rat.apply(
            lambda r: r["vol_mm3"] / exp.get(r["subject"], np.nan), axis=1)
        f["vol_bias"] = (rat.groupby("source")["vol_ratio"]
                         .agg(mean="mean", sd="std", n="count").reset_index()
                         .rename(columns={"source": "rater"}).sort_values("mean"))

    if inv is not None and not inv.empty:            # notes on the masks actually used
        f["anomalies"] = inv[(inv["flags"].fillna("") != "") & (inv["used"] != "(absent)")]
    return f


def methods(label=1):
    """Methods note that opens every report."""
    target = "the SN label (value 1)" if label == 1 else f"label value {label}"
    return (
        "**Methods.** Dice and Jaccard (overlap) and three surface distances in "
        "physical mm, namely max Hausdorff (HD), 95th-percentile Hausdorff (HD95, "
        "robust to single-voxel outliers) and average symmetric surface distance "
        f"(ASSD), were computed on {target} at each scan's native voxel spacing. "
        "Surfaces are the voxel faces on each mask's boundary, weighted by face "
        "area, so distances are measured on the boundary itself rather than "
        "between voxel centres and are not tied to the 1 mm slice thickness. "
        "Each segmentation attempt (`a1`, `a2`, …) is analyzed independently; the "
        "**attempts-average** is the mean of the per-attempt metrics for each "
        "scan/rater (`n_attempts` = how many were averaged), mirroring every other "
        "table. Only rater/scans that actually have an attempt contribute, so the "
        "counts adapt to the data. HD95 is the recommended distance metric for "
        "ranking.\n")


ROOT_ROW = "(outputs root)"


def coverage_report(phase_key, cov, expected, path):
    """Write <phase>/coverage.md: which attempts each rater has for each scan,
    then the notes behind every '*'. Returns one summary line per rater."""
    scans = cov[cov["subject"] != ROOT_ROW]
    raters = list(dict.fromkeys(cov["rater"]))
    image = scans["image"].fillna("") if "image" in scans else pd.Series("", index=scans.index)
    found = {(r.rater, r.subject): r.found + (f" {i}" if i and r.found != "-" else "")
             for r, i in zip(scans.itertuples(), image)}
    noted = {(r.rater, r.subject) for r in scans.itertuples() if r.flags}
    summary = []
    for r in raters:
        g = scans[scans["rater"] == r]
        if g.empty:
            continue
        have = [set(f.split(",")) - {"-"} for f in g["found"]]
        if not any(have):
            summary.append(f"{r}: no masks yet")
            continue
        parts = []
        for a in expected:
            miss = [s for s, h in zip(g["subject"], have) if a not in h]
            parts.append(f"{a} {len(g) - len(miss)}/{len(g)}" +
                         (f" (no {a}: {', '.join(miss)})" if 0 < len(miss) <= 3 else ""))
        extra = sorted(set().union(*have) - set(expected))
        if extra:
            parts.append("also " + ", ".join(extra))
        summary.append(f"{r}: " + ", ".join(parts))

    L = [f"# Coverage: {phase_key}", "",
         f"Attempts found per rater and scan (expected: {', '.join(expected)}), with "
         "the image segmented where a run has several (combo, echo). `·` means no "
         "usable mask and `*` marks a scan with a note below. The QC "
         "metrics use whatever masks exist, so gaps only reduce what is compared.", "",
         "| scan | " + " | ".join(raters) + " |", "|---|" + "---|" * len(raters)]
    for s in sorted(scans["subject"].unique()):
        cells = []
        for r in raters:
            f = found.get((r, s))
            cell = "" if f is None else "·" if f == "-" else f.replace(",", " ")
            cells.append(cell + ("*" if (r, s) in noted else ""))
        L.append(f"| {s} | " + " | ".join(cells) + " |")
    L += ["", "## Per rater", ""] + [f"- {x}" for x in summary]
    notes = cov[cov["flags"] != ""]
    if not notes.empty:
        L += ["", "## Notes", ""] + [f"- {r.rater} / {r.subject}: {r.flags}"
                                     for r in notes.itertuples()]
    Path(path).write_text("\n".join(L) + "\n")
    return summary


def run_check_report(phase_key, gate, path):
    """Append the run gate to coverage.md. Returns summary lines for the console and
    one line for the reports (None when the phase has no runs)."""
    if gate.empty:
        return [], None
    bad = gate[gate["status"] != "ok"]
    fails, checks = bad[bad["status"] == "fail"], bad[bad["status"] == "check"]
    n_overlap = gate.loc[gate["n_raters"] > 1, "subject"].nunique()
    L = ["", "## Run check (gate)", "",
         "Runs are segmented in order (run 1, then run 2, ...), so the raters of an "
         "overlap subject always share their runs and can be compared. Masks are only "
         "compared on the same run and image, so on an overlap subject every rater "
         "must also segment the same image (combo or echo) of each run. A rater who "
         "skipped a run (e.g. has run 2 but no run 1) or segmented another image than "
         "the other raters fails on an overlap subject; a skipped run is listed for a "
         "check on their own subjects, and on an overlap subject where every rater "
         "skipped the same run (they still line up). A skipped run is often a mask saved under the "
         "wrong run number. Details in `run_check.csv`.", "",
         f"- **{'FAIL' if len(fails) else 'PASS'}**: {n_overlap} overlap subject(s) "
         f"with masks, {fails['subject'].nunique()} with runs or images that do not line up."]
    L += [f"  - {r.subject}, {r.rater}: run {r.runs}; {r.note}" for r in fails.itertuples()]
    L += [f"- **Check**: {len(checks)} subject(s) with a skipped run to check."]
    L += [f"  - {r.subject}, {r.rater}: run {r.runs}" + (f"; {r.note}" if r.n_raters > 1 else "")
          for r in checks.itertuples()]
    with open(path, "a") as f:
        f.write("\n".join(L) + "\n")
    console = [f"run check: {'FAIL' if len(fails) else 'PASS'} ({fails['subject'].nunique()} "
               f"overlap subject(s) with runs or images that do not line up); {len(checks)} "
               "subject(s) with a skipped run to check"]
    console += [f"  FAIL {r.subject} {r.rater}: run {r.runs}; {r.note}" for r in fails.itertuples()]
    console += [f"  check {r.subject} {r.rater}: run {r.runs} only" for r in checks.itertuples()]
    note = ("Run check: **FAIL**, runs or images do not line up between raters on overlap subject(s) "
            + ", ".join(sorted(fails["subject"].unique())) + " (see coverage.md)."
            if len(fails) else "Run check: pass, the runs and images of every overlap "
            "subject line up between raters.")
    if len(checks):
        note += (f" {len(checks)} subject(s) with a skipped run to check: "
                 + ", ".join(f"{r.rater} {r.subject}" for r in checks.itertuples()) + ".")
    return console, note


def attempt_note(cov, attempt):
    """One line for a report: which raters lack `attempt` (None: any expected
    attempt, for the attempts-average) on some of their scans."""
    scans = cov[cov["subject"] != ROOT_ROW]
    gaps = []
    for r, g in scans.groupby("rater", sort=False):
        n = (sum(attempt not in f.split(",") for f in g["found"]) if attempt
             else sum(bool(m) for m in g["missing"]))
        if n:
            gaps.append(f"{r} {n}/{len(g)}")
    what = f"an {attempt} mask" if attempt else "every expected attempt"
    if not gaps:
        return f"Coverage: every rater has {what} for every scan."
    return (f"Coverage: scans without {what}, per rater: {', '.join(gaps)}. They simply "
            "do not contribute here; see [coverage.md](../../coverage.md).")


def phase_report(tag, tables, base_dir, fig_tag, reproducibility=None,
                 has_slices=True, plots_rel="../plots", slices_rel="../worst_slices",
                 label=1, coverage_note=None):
    f = compute_findings(tag, tables)
    rdir = Path(base_dir) / "reports"
    rdir.mkdir(parents=True, exist_ok=True)
    L = []
    A = L.append

    rve = tables.get("rater_vs_expert_per_scan")
    rvr = tables.get("rater_vs_rater_per_scan")
    vol = tables.get("volumes")
    n_sub = rve["subject"].nunique() if rve is not None and not rve.empty else (
        rvr["subject"].nunique() if rvr is not None and not rvr.empty else 0)

    inv = tables.get("inventory")
    used = inv[inv["used"] != "(absent)"] if inv is not None and not inv.empty else None
    raters = ", ".join(dict.fromkeys(used["rater"])) if used is not None and len(used) else "none"
    A(f"# QC Report: {tag}\n")
    A(f"Subjects: **{n_sub}** · Raters with masks: **{raters}** · "
      "Comparison: rater-vs-expert and rater-vs-rater.\n")
    A(methods(label))

    # headline
    A("## Headline\n")
    if "phase_avg_rve" in f:
        s = f["phase_avg_rve"].iloc[0]
        A(f"- **Phase summary, rater vs expert** (average of {int(s['n'])} per-scan "
          f"means): Dice **{s['dice_mean']:.3f} ± {s['dice_sd']:.3f}**{_med(s, 'dice', '.3f')}, "
          f"HD95 **{s['hd95_mean']:.2f} mm**{_med(s, 'hd95', '.2f')}.")
    if "phase_avg_rvr" in f:
        s = f["phase_avg_rvr"].iloc[0]
        A(f"- **Phase summary, inter-rater** (average of {int(s['n'])} per-scan "
          f"means): Dice **{s['dice_mean']:.3f} ± {s['dice_sd']:.3f}**{_med(s, 'dice', '.3f')}, "
          f"HD95 **{s['hd95_mean']:.2f} mm**{_med(s, 'hd95', '.2f')}.")
    if "phase_summary_rve" in f:
        s = f["phase_summary_rve"].iloc[0]
        A(f"- **Rater vs expert** (grand mean over {int(s['n'])} rater-scans): "
          f"Dice **{s['dice_mean']:.3f} ± {s['dice_sd']:.3f}**{_med(s, 'dice', '.3f')}, "
          f"HD95 **{s['hd95_mean']:.2f} mm**{_med(s, 'hd95', '.2f')}, HD {s['hd_mean']:.2f} mm, "
          f"ASSD {s['assd_mean']:.3f} mm.")
        A(f"- **Strongest rater:** {f['strongest_rater']} · "
          f"**Weakest rater:** {f['weakest_rater']}.")
    if "phase_summary_rvr" in f:
        s = f["phase_summary_rvr"].iloc[0]
        A(f"- **Inter-rater** (grand mean over {int(s['n'])} pair-scans): "
          f"Dice **{s['dice_mean']:.3f} ± {s['dice_sd']:.3f}**{_med(s, 'dice', '.3f')}, "
          f"HD95 {s['hd95_mean']:.2f} mm{_med(s, 'hd95', '.2f')}. "
          f"Least concordant pair: **{f.get('least_concordant_pair','n/a')}**, "
          f"most concordant: **{f.get('most_concordant_pair','n/a')}**.")
    A("")

    # data quality
    A("## Data inventory & anomalies\n")
    if coverage_note:
        A(coverage_note + "\n")
    if "anomalies" in f and not f["anomalies"].empty:
        A("Notes on the masks used here (all handled; the full list is in coverage.md):\n")
        A(df_to_md(f["anomalies"][["subject", "rater", "available",
                                   "used", "flags"]]))
    else:
        A("No notes on the masks used here.\n")

    # rater vs expert
    if rve is not None and not rve.empty:
        A("\n## Rater vs expert\n")
        A("### Per rater (averaged across scans, worst first)\n")
        A(df_to_md(_round(_cols(f["by_rater"], ["rater", "n", "dice_mean", "dice_sd",
            "hd_mean", "hd95_mean", "assd_mean"]))))
        A("\n### Per scan (averaged across raters, hardest first)\n")
        A(df_to_md(_round(_cols(f["by_scan"], ["subject", "n", "dice_mean", "dice_sd",
            "hd95_mean", "hd95_max"]))))
        A("\n### Outlier rater-scan cells (Dice z≤−2, HD95 z≥+2, or Dice<0.60)\n")
        A(df_to_md(_round(f["outliers"])) if not f["outliers"].empty
          else "_none flagged_\n")
        A("\n### Full per-scan table\n")
        A(df_to_md(_round(rve[["subject", "rater", "dice", "hd", "hd95",
            "assd", "vol_ratio"]])))
        A(f"\n![Dice vs expert]({plots_rel}/dice_rater_vs_expert_{fig_tag}.png)\n")
        A(f"![Hausdorff vs expert]({plots_rel}/hausdorff_rater_vs_expert_{fig_tag}.png)\n")
        A(f"![Dice by rater]({plots_rel}/dice_by_rater_{fig_tag}.png)\n")

    # volume and extent
    vs = qa.volume_stats(vol)
    if vs:
        cols = ["n", "vol_mm3_mean", "vol_mm3_sd", "vol_mm3_min", "vol_mm3_max",
                "axial_slices_mean", "coronal_slices_mean", "sagittal_slices_mean"]
        A("\n## SN volume and extent\n")
        A("Volume in mm³; extent as the number of axial, coronal and sagittal slices the "
          "SN spans (mm in `metrics/volumes.csv`). Raters only.\n")
        A("### Per rater, all their scans\n")
        A(df_to_md(_round(_cols(vs["volume_by_rater"], ["rater"] + cols))))
        if "volume_overlap_by_rater" in vs:
            s = vs["volume_overlap_summary"].iloc[0]
            A(f"\n### Overlap scans (2+ raters)\n")
            A(f"Average of {int(s['n'])} overlap scans: volume **{s['vol_mm3_mean']:.0f} mm³**"
              f" (median {s['vol_mm3_median']:.0f}, SD across scans {s['vol_mm3_sd']:.0f}); axial "
              f"{s['axial_slices_mean']:.1f}, coronal {s['coronal_slices_mean']:.1f}, "
              f"sagittal {s['sagittal_slices_mean']:.1f} slices.\n")
            A("Per rater, overlap scans only:\n")
            A(df_to_md(_round(_cols(vs["volume_overlap_by_rater"], ["rater"] + cols))))
            A("\nPer overlap scan, across its raters (`vol_cv` = SD / mean):\n")
            A(df_to_md(_round(_cols(vs["volume_overlap_by_scan"].sort_values(
                "vol_cv", ascending=False), ["subject", "n", "vol_mm3_mean", "vol_mm3_sd",
                "vol_cv", "vol_mm3_min", "vol_mm3_max", "axial_slices_min",
                "axial_slices_max"]))))

    # volume bias
    if "vol_bias" in f:
        A("\n### Volume bias (rater SN volume / expert volume)\n")
        A("Ratio >1 = over-segmentation, <1 = under-segmentation.\n")
        A(df_to_md(_round(f["vol_bias"])))

    # rater vs rater
    if rvr is not None and not rvr.empty:
        A("\n## Rater vs rater (inter-rater)\n")
        A("### Per rater pair (worst first)\n")
        A(df_to_md(_round(_cols(f["by_pair"], ["pair", "n", "dice_mean", "dice_sd",
            "hd95_mean", "assd_mean"]))))
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
                 "(see inventory); resolve before final analysis.")
    return b


def overall_report(phase_findings, overall_tables, qc_root, links=None, label=1):
    """Cross-phase summary from the per-phase findings + combined tables.
    `links` maps each phase to its report, relative to summary/."""
    rdir = Path(qc_root) / "summary"
    rdir.mkdir(parents=True, exist_ok=True)
    L = []
    A = L.append
    A(f"# QC Report: Overall ({', '.join(phase_findings)})\n")
    A(methods(label))

    rve = overall_tables.get("rater_vs_expert_per_scan")
    rvr = overall_tables.get("rater_vs_rater_per_scan")

    A("## Phase comparison (rater vs expert)\n")
    rows = []
    for tag, f in phase_findings.items():
        if "phase_summary_rve" in f:
            s = f["phase_summary_rve"].iloc[0]
            rows.append({"phase": tag, "n": int(s["n"]),
                         "dice_mean": s["dice_mean"], "dice_median": s.get("dice_median"),
                         "dice_sd": s["dice_sd"],
                         "hd95_mean": s["hd95_mean"], "hd95_median": s.get("hd95_median"),
                         "assd_mean": s["assd_mean"], "assd_median": s.get("assd_median"),
                         "strongest": f.get("strongest_rater"),
                         "weakest": f.get("weakest_rater")})
    A(df_to_md(_round(pd.DataFrame(rows))))

    if rve is not None and not rve.empty:
        A("\n## Pooled rater vs expert\n")
        A("### Grand average\n")
        A(df_to_md(_round(qa.summarize(rve, None, qa.SD_METRICS))))
        A("\n### Per rater (pooled, worst first)\n")
        A(df_to_md(_round(_cols(qa.summarize(rve, ["rater"], qa.SD_METRICS),
            ["rater", "n", "dice_mean", "dice_sd", "hd95_mean", "assd_mean"]))))
        A(f"\n![Overall Dice vs expert](plots/dice_rater_vs_expert_overall.png)\n")
        A(f"![Overall Hausdorff vs expert](plots/hausdorff_rater_vs_expert_overall.png)\n")
        A(f"![Overall Dice by rater](plots/dice_by_rater_overall.png)\n")

    rows = []
    for tag, f in phase_findings.items():
        if "phase_avg_rvr" in f:
            s = f["phase_avg_rvr"].iloc[0]
            rows.append({"phase": tag, "scans": int(s["n"]),
                         "dice_mean": s["dice_mean"], "dice_median": s.get("dice_median"),
                         "dice_sd": s["dice_sd"],
                         "hd95_mean": s["hd95_mean"], "hd95_median": s.get("hd95_median"),
                         "assd_mean": s["assd_mean"], "assd_median": s.get("assd_median")})
    if rows:
        A("\n## Phase summary (inter-rater, average of per-scan means)\n")
        if rvr is not None and not rvr.empty:
            s = qa.average_of_averages(rvr, qa.SD_METRICS, by=["phase", "subject"]).iloc[0]
            rows.append({"phase": "all", "scans": int(s["n"]),
                         "dice_mean": s["dice_mean"], "dice_median": s.get("dice_median"),
                         "dice_sd": s["dice_sd"],
                         "hd95_mean": s["hd95_mean"], "hd95_median": s.get("hd95_median"),
                         "assd_mean": s["assd_mean"], "assd_median": s.get("assd_median")})
        A(df_to_md(_round(pd.DataFrame(rows))))

    if rvr is not None and not rvr.empty:
        A("\n## Pooled inter-rater\n")
        A(df_to_md(_round(_cols(qa.summarize(rvr, ["pair"], qa.SD_METRICS),
            ["pair", "n", "dice_mean", "dice_sd", "hd95_mean", "assd_mean"]))))
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
        trend = {"phase1", "phase2"} <= set(rve["phase"])
        if trend:
            A("- _Phase-1 has only ~2 scans/rater, so phase1→phase2 trends are "
              "indicative, not powered:_")
        for r in sorted(rve["rater"].unique()) if trend else []:
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
        A(f"- [{tag}]({(links or {}).get(tag, f'../{tag}/')})")

    path = rdir / "overall_qc_report.md"
    path.write_text("\n".join(L))
    return path
