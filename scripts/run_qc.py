#!/usr/bin/env python3
"""One-shot QC driver: metrics -> figures -> worst/best + all slices -> reports,
for each phase, each segmentation attempt, and the attempts-average, then pooled
across phases.

    python scripts/run_qc.py \
        --workspace ~/path/to/ManSeg_Workspace \
        --qc-root   ~/path/to/ManSeg_Workspace/qc \
        --phases phase1 phase2

Attempts (a1, a2, ...) are detected per phase from the file names. Each attempt
is analyzed independently into <qc-root>/<phase>/a<N>/{metrics,plots,
worst_slices,all_slices,reports}; when a phase has more than one attempt an
<phase>/attempts_average/ holds the **mean of the per-attempt metrics** (mirrors
the metric tables + agreement figures; no slice montages, since there is no
single mask) and carries the attempts_reproducibility table. The original
qc_metrics.py + interrater_analysis.py run into <phase>/legacy_scripts/ as an
independent cross-check. Phases are then pooled (attempts-average where
available) into <qc-root>/summary/. Only expert-guided phases get rater-vs-expert
outputs.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pandas as pd

import utils
import qc_analysis as qa
import qc_visualize as qv
import qc_report as qr

HERE = Path(__file__).resolve().parent


def run_legacy(phase, workspace, qc_root):
    """Run the original scripts into <phase>/legacy_scripts/ for cross-check."""
    out = Path(qc_root) / phase / "legacy_scripts"
    out.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run([sys.executable, str(HERE / "qc_metrics.py"),
                        "--phase", phase, "--workspace", str(workspace),
                        "--out-dir", str(out)], check=True, cwd=HERE,
                       capture_output=True, text=True)
        subprocess.run([sys.executable, str(HERE / "interrater_analysis.py"),
                        "--phase", phase, "--qc-dir", str(out)], check=True,
                       cwd=HERE, capture_output=True, text=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"  legacy scripts failed for {phase}: {e.stderr.strip()[:200]}")
        return False


def process_attempt(ph, n, workspace, phases, raters, qc_root, label):
    """Full analysis for one integer attempt (metrics -> agreement figures ->
    worst/best + all-slice montages -> report) into <qc_root>/<ph>/a<N>.
    Returns (tables, findings, n_figs, n_slices)."""
    folder, fig_tag = f"a{n}", f"{ph}_a{n}"
    base = qc_root / ph / folder
    tables = qa.compute_phase(ph, workspace, phases, raters, n,
                              label=label, out_dir=base)
    figs = qv.generate_agreement_figures(tables, base / "plots", fig_tag)
    rve = tables.get("rater_vs_expert_per_scan")
    n_slices = 0
    if rve is not None and not rve.empty:
        qv.worst_slices_for_phase(ph, workspace, None, phases, raters, rve,
                                  base / "worst_slices", label=label, attempt=n)
        made = qv.all_slices_for_phase(ph, workspace, None, phases, raters, rve,
                                       base / "all_slices", label=label, attempt=n)
        n_slices = len(made)
    _, findings = qr.phase_report(f"{ph} · a{n}", tables, base, fig_tag)
    return tables, findings, len(figs), n_slices


def process_average(ph, per_tables, repro, qc_root):
    """Mean-of-metrics average across attempts: metrics + agreement figures +
    report (no slice montages -- there is no single mask). The reproducibility
    table lives here. Returns (tables, findings, n_figs)."""
    folder, fig_tag = qa.AVERAGE_DIR, f"{ph}_{qa.AVERAGE_DIR}"
    base = qc_root / ph / folder
    avg = qa.average_attempt_metrics(per_tables, ph)
    (base / "metrics").mkdir(parents=True, exist_ok=True)
    for name, df in avg.items():
        if df is not None and not df.empty:
            df.to_csv(base / "metrics" / f"{name}.csv", index=False)
    if repro is not None and not repro.empty:
        repro.to_csv(base / "metrics" / "attempts_reproducibility.csv", index=False)
    figs = qv.generate_agreement_figures(avg, base / "plots", fig_tag)
    _, findings = qr.phase_report(f"{ph} · attempts-average", avg, base, fig_tag,
                                  reproducibility=repro, has_slices=False)
    return avg, findings, len(figs)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config-dir", type=Path, default=utils.CONFIG_DIR)
    ap.add_argument("--workspace", type=Path, default=None)
    ap.add_argument("--qc-root", type=Path, default=None)
    ap.add_argument("--phases", nargs="+", default=["phase1", "phase2"])
    ap.add_argument("--label", type=int, default=1)
    ap.add_argument("--skip-legacy", action="store_true")
    args = ap.parse_args()

    paths, phases, raters = utils.load_config(args.config_dir)
    workspace = args.workspace or Path(paths["workspace_root"])
    qc_root = Path(args.qc_root or paths["qc_output_root"])
    qc_root.mkdir(parents=True, exist_ok=True)

    rep_tables, rep_findings, phase_folders = {}, {}, {}
    for ph in args.phases:
        print(f"\n========== {ph} ==========")
        phase_dir = workspace / utils.phase_dirname(ph, phases[ph])
        attempts = qa.detect_attempts(phase_dir, raters)
        print(f"  detected attempts {attempts}")
        per, a_findings, folders = [], {}, []
        for n in attempts:
            tables, findings, nf, ns = process_attempt(
                ph, n, workspace, phases, raters, qc_root, args.label)
            per.append(tables)
            a_findings[f"a{n}"] = findings
            folders.append(f"a{n}")
            print(f"  [a{n}] {len(tables['rater_vs_expert_per_scan'])} rve rows, "
                  f"{nf} figures, {ns} slice montages")

        if len(attempts) > 1:
            repro = qa.compute_attempt_reproducibility(ph, workspace, phases,
                                                       raters, args.label)
            avg, avg_findings, nf = process_average(ph, per, repro, qc_root)
            folders.append(qa.AVERAGE_DIR)
            rep_tables[ph], rep_findings[ph] = avg, avg_findings
            print(f"  [{qa.AVERAGE_DIR}] mean-of-metrics, {nf} figures (no montages)")
        else:                                     # single attempt: it is the rep
            rep_tables[ph], rep_findings[ph] = per[0], a_findings[folders[0]]
        phase_folders[ph] = folders

        if not args.skip_legacy:
            ok = run_legacy(ph, workspace, qc_root)
            print(f"  legacy cross-check: {'ok' if ok else 'skipped/failed'}")

    # ---- pooled overall (attempts-average per phase where available) ----
    print("\n========== overall (pooled, attempts-average where available) ==========")
    def pool(name):
        frames = [rep_tables[p][name] for p in args.phases
                  if name in rep_tables[p] and not rep_tables[p][name].empty]
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    overall = {n: pool(n) for n in ["rater_vs_expert_per_scan",
                                    "rater_vs_rater_per_scan", "volumes",
                                    "inventory"]}
    # phase separators for the pooled per-scan plots
    breaks = []
    for name in ("rater_vs_expert_per_scan",):
        df = overall[name]
        if not df.empty:
            subs = sorted(df["subject"].unique())
            prev = None
            for i, s in enumerate(subs):
                ph = df[df.subject == s]["phase"].iloc[0]
                if prev is not None and ph != prev:
                    breaks.append((i, ph))
                prev = ph
    overall["_phase_breaks"] = breaks

    sdir = qc_root / "summary"
    (sdir / "metrics").mkdir(parents=True, exist_ok=True)
    for n, df in overall.items():
        if isinstance(df, pd.DataFrame) and not df.empty:
            df.to_csv(sdir / "metrics" / f"{n}.csv", index=False)
    # pooled aggregates
    if not overall["rater_vs_expert_per_scan"].empty:
        rve = overall["rater_vs_expert_per_scan"]
        qa.summarize(rve, ["rater"], qa.SD_METRICS).to_csv(
            sdir / "metrics" / "rve_by_rater_overall.csv", index=False)
        qa.summarize(rve, ["phase", "rater"], qa.SD_METRICS).to_csv(
            sdir / "metrics" / "rve_by_phase_rater.csv", index=False)
        qa.summarize(rve, None, qa.SD_METRICS).to_csv(
            sdir / "metrics" / "rve_grand_average.csv", index=False)
    if not overall["rater_vs_rater_per_scan"].empty:
        rvr = overall["rater_vs_rater_per_scan"]
        qa.summarize(rvr, ["pair"], qa.SD_METRICS).to_csv(
            sdir / "metrics" / "rvr_by_pair_overall.csv", index=False)
        qa.summarize(rvr, None, qa.SD_METRICS).to_csv(
            sdir / "metrics" / "rvr_grand_average.csv", index=False)

    figs = qv.generate_agreement_figures(overall, sdir / "plots", "overall")
    print(f"  overall plots: {len(figs)}")
    opath = qr.overall_report(rep_findings, overall, qc_root)
    print(f"  overall report: {opath}")

    _write_readme(qc_root, phase_folders)
    print(f"\nDone. QC tree under {qc_root}")
    return 0


def _write_readme(qc_root, phase_folders):
    """phase_folders: {phase: [attempt-folder names]} so the tree reflects the
    attempts actually found (e.g. a1 only, or a1/a2/attempts_average)."""
    lines = [
        "# QC outputs", "",
        "Generated by `scripts/run_qc.py` (metrics: `qc_analysis.py`, figures: "
        "`qc_visualize.py`, reports: `qc_report.py`). Each phase is analyzed "
        "separately **per segmentation attempt** (`a1`, `a2`, …) detected from "
        "the file names, plus an `attempts_average/` that is the **mean of the "
        "per-attempt metrics** for each scan/rater. Attempt folders are only "
        "created for attempts that exist, so the tree adapts to the data.", "",
        "```", "qc/",
    ]
    full = "{metrics, plots, worst_slices, all_slices, reports}"
    for ph in phase_folders:
        lines.append(f"├── {ph}/")
        for folder in phase_folders[ph]:
            if folder == qa.AVERAGE_DIR:
                lines.append(f"│   ├── {folder}/  {{metrics, plots, reports}} + "
                             "attempts_reproducibility.csv  (mean of per-attempt "
                             "metrics; no montages, since there is no single mask)")
            else:
                lines.append(f"│   ├── {folder}/  {full}  (attempt {folder[1:]})")
        lines.append("│   └── legacy_scripts/  original qc_metrics.py / interrater_analysis.py cross-check")
    lines += ["└── summary/  (pooled across phases, attempts-average where available)",
              "    ├── metrics/   pooled CSVs + grand averages",
              "    ├── plots/     pooled (overall) figures",
              "    └── overall_qc_report.md", "```", "",
        "Each attempt folder mirrors the same analysis. **Start with** "
        "`summary/overall_qc_report.md`, then per-phase per-attempt reports "
        "(`<phase>/<attempt>/reports/`). Metric definitions and the "
        "attempts-average rule are in each report's Methods note.", ""]
    (Path(qc_root) / "README.md").write_text("\n".join(lines))


if __name__ == "__main__":
    raise SystemExit(main())
