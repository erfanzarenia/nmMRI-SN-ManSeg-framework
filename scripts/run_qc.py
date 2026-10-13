#!/usr/bin/env python3
"""QC in one command: metrics, figures, worst/best and all-slice montages and
reports for each segmentation attempt and the attempts-average, plus the CP
protocol checks in phases that segment CP.

    python scripts/run_qc.py phase2            # one phase
    python scripts/run_qc.py phase1 phase2     # several, plus a pooled summary
    python scripts/run_qc.py                   # every phase with rater outputs

Paths come from config/paths.yaml (--workspace and --qc-root override them).
Each run starts clean: earlier results in the QC folder are first moved to
<qc-root>/archive/<timestamp>/, never deleted.

Attempts (a1, a2, ...) are detected per phase from the file names and each is
analyzed on its own in <phase>/a<N>/; with more than one attempt,
<phase>/attempts_average/ holds the mean of the per-attempt metrics and each
rater's attempt-to-attempt reproducibility. <phase>/coverage.md shows who has
which attempt for which scan and notes gaps, odd file names and set-aside masks;
the metrics use whatever masks exist. With two or more phases, a pooled summary
goes to <qc-root>/summary/.
"""
from __future__ import annotations

import argparse
import os
import shutil
import time
from pathlib import Path

import pandas as pd

import utils
import qc_analysis as qa
import qc_protocol as qp
import qc_report as qr
import qc_visualize as qv


def has_outputs(phase_dir, raters):
    """True when any rater has a NIfTI file anywhere under outputs/ in a phase."""
    for r in raters:
        out = Path(phase_dir) / r / "outputs"
        if out.is_dir() and any(p.is_file() for p in out.rglob("*.nii*")):
            return True
    return False


def archive_previous(qc_root):
    """Move everything in qc_root except archive/ into archive/<timestamp>/.
    Returns that folder, or None when there was nothing to move."""
    qc_root = Path(qc_root)
    old = sorted(p for p in qc_root.iterdir() if p.name != "archive") if qc_root.is_dir() else []
    if not old:
        return None
    dest = qc_root / "archive" / time.strftime("%Y%m%d-%H%M%S")
    dest.mkdir(parents=True)
    for p in old:
        shutil.move(str(p), str(dest / p.name))
    return dest


def process_attempt(ph, n, workspace, phases, raters, qc_root, label, note, experts):
    """Full analysis for one integer attempt (metrics -> agreement figures ->
    worst/best + all-slice montages -> CP protocol checks -> report) into
    <qc_root>/<ph>/a<N>. Returns (tables, findings, report_path, n_figs, n_slices)."""
    folder, fig_tag = f"a{n}", f"{ph}_a{n}"
    base = qc_root / ph / folder
    tables = qa.compute_phase(ph, workspace, phases, raters, n, label=label, out_dir=base,
                              experts=experts)
    figs = qv.generate_agreement_figures(tables, base / "plots", fig_tag)
    rve = tables.get("rater_vs_expert_per_scan")
    n_slices = 0
    if rve is not None and not rve.empty:
        qv.worst_slices_for_phase(ph, workspace, None, phases, raters, rve,
                                  base / "worst_slices", label=label, attempt=n,
                                  experts=experts)
        made = qv.all_slices_for_phase(ph, workspace, None, phases, raters, rve,
                                       base / "all_slices", label=label, attempt=n,
                                       experts=experts)
        n_slices = len(made)
    if "CP" in utils.phase_labels(phases[ph]):
        qp.run_protocol(ph, workspace, phases, raters, n, base)
    path, findings = qr.phase_report(f"{ph} · a{n}", tables, base, fig_tag,
                                     label=label, coverage_note=note)
    return tables, findings, path, len(figs), n_slices


def process_average(ph, per_tables, repro, qc_root, label, note):
    """Mean-of-metrics average across attempts: metrics + agreement figures +
    report (no slice montages, since there is no single mask). The
    reproducibility table lives here. Returns (tables, findings, report_path, n_figs)."""
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
    path, findings = qr.phase_report(f"{ph} · attempts-average", avg, base, fig_tag,
                                     reproducibility=repro, has_slices=False,
                                     label=label, coverage_note=note)
    return avg, findings, path, len(figs)


def write_summary(pooled, rep_tables, rep_findings, rep_reports, qc_root, label):
    """Pool the phases (attempts-average where available) into <qc_root>/summary/."""
    def pool(name):
        frames = [rep_tables[p][name] for p in pooled
                  if name in rep_tables[p] and not rep_tables[p][name].empty]
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    overall = {n: pool(n) for n in ["rater_vs_expert_per_scan", "rater_vs_rater_per_scan",
                                    "volumes", "inventory"]}
    breaks, rve = [], overall["rater_vs_expert_per_scan"]   # phase separators for plots
    if not rve.empty:
        prev = None
        for i, s in enumerate(sorted(rve["subject"].unique())):
            ph = rve[rve.subject == s]["phase"].iloc[0]
            if prev is not None and ph != prev:
                breaks.append((i, ph))
            prev = ph
    overall["_phase_breaks"] = breaks

    sdir = qc_root / "summary"
    (sdir / "metrics").mkdir(parents=True, exist_ok=True)
    for n, df in overall.items():
        if isinstance(df, pd.DataFrame) and not df.empty:
            df.to_csv(sdir / "metrics" / f"{n}.csv", index=False)
    if not rve.empty:
        qa.summarize(rve, ["rater"], qa.SD_METRICS).to_csv(
            sdir / "metrics" / "rve_by_rater_overall.csv", index=False)
        qa.summarize(rve, ["phase", "rater"], qa.SD_METRICS).to_csv(
            sdir / "metrics" / "rve_by_phase_rater.csv", index=False)
        qa.summarize(rve, None, qa.SD_METRICS).to_csv(
            sdir / "metrics" / "rve_grand_average.csv", index=False)
    rvr = overall["rater_vs_rater_per_scan"]
    if not rvr.empty:
        qa.summarize(rvr, ["pair"], qa.SD_METRICS).to_csv(
            sdir / "metrics" / "rvr_by_pair_overall.csv", index=False)
        qa.summarize(rvr, None, qa.SD_METRICS).to_csv(
            sdir / "metrics" / "rvr_grand_average.csv", index=False)

    figs = qv.generate_agreement_figures(overall, sdir / "plots", "overall")
    links = {p: os.path.relpath(rep_reports[p], sdir) for p in pooled}
    opath = qr.overall_report(rep_findings, overall, qc_root, links, label=label)
    print(f"  pooled summary: {len(figs)} figures, report {opath}")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("phases", nargs="*", metavar="PHASE",
                    help="Phases to QC, e.g. phase2 (default: every phase with rater outputs).")
    ap.add_argument("--workspace", type=Path, default=None,
                    help="Override workspace_root from paths.yaml.")
    ap.add_argument("--qc-root", type=Path, default=None,
                    help="Override qc_output_root (default with --workspace: <workspace>/qc).")
    ap.add_argument("--label", type=int, default=1, help="Label value to measure (SN = 1).")
    ap.add_argument("--config-dir", type=Path, default=utils.CONFIG_DIR)
    args = ap.parse_args()

    paths, phases, raters = utils.load_config(args.config_dir)
    workspace = Path(args.workspace or paths["workspace_root"])
    qc_root = Path(args.qc_root or (workspace / "qc" if args.workspace
                                     else paths["qc_output_root"]))
    if not workspace.is_dir():
        ap.error(f"workspace not found: {workspace}")
    unknown = [k for k in args.phases if k not in phases]
    if unknown:
        ap.error(f"unknown phase(s): {', '.join(unknown)}")
    todo = args.phases or [k for k in utils.phase_keys(phases)
                           if has_outputs(workspace / utils.phase_dirname(k, phases[k]), raters)]
    if not todo:
        print(f"No rater outputs under {workspace}; nothing to QC.")
        return 1
    split_path = Path(args.config_dir) / "subjects_split.json"
    split = utils.load_json(split_path) if split_path.exists() else None

    archived = archive_previous(qc_root)
    if archived:
        print(f"Previous QC results moved to {archived}")

    rep_tables, rep_findings, rep_reports, phase_folders = {}, {}, {}, {}
    for ph in todo:
        pcfg = phases[ph]
        phase_dir = workspace / utils.phase_dirname(ph, pcfg)
        print(f"\n========== {ph} ==========")
        cov = qa.coverage(ph, workspace, phases, raters, split)
        (qc_root / ph).mkdir(parents=True, exist_ok=True)
        cov.to_csv(qc_root / ph / "coverage.csv", index=False)
        print(f"  coverage (details in {qc_root / ph / 'coverage.md'}):")
        for line in qr.coverage_report(ph, cov, qa.expected_attempts(pcfg),
                                       qc_root / ph / "coverage.md"):
            print(f"    {line}")
        attempts = qa.detect_attempts(phase_dir, raters)
        experts = qa.expert_masks(ph, workspace, phases, raters, paths, split)
        if experts and not pcfg.get("expert_mask"):
            print(f"  expert masks read from the source data for {len(experts)} scan(s) "
                  "(not given to raters)")
        per, folders = [], []
        for n in attempts:
            tables, findings, path, nf, ns = process_attempt(
                ph, n, workspace, phases, raters, qc_root, args.label,
                qr.attempt_note(cov, f"a{n}"), experts)
            per.append(tables)
            folders.append(f"a{n}")
            rep_tables[ph], rep_findings[ph], rep_reports[ph] = tables, findings, path
            print(f"  [a{n}] {len(tables['rater_vs_expert_per_scan'])} rater-vs-expert, "
                  f"{len(tables['rater_vs_rater_per_scan'])} rater-vs-rater rows; "
                  f"{nf} figures, {ns} slice montages")
        if len(attempts) > 1:
            repro = qa.compute_attempt_reproducibility(ph, workspace, phases, raters, args.label)
            avg, findings, path, nf = process_average(
                ph, per, repro, qc_root, args.label,
                qr.attempt_note(cov, None))
            folders.append(qa.AVERAGE_DIR)
            rep_tables[ph], rep_findings[ph], rep_reports[ph] = avg, findings, path
            print(f"  [{qa.AVERAGE_DIR}] mean of {len(attempts)} attempts, {nf} figures, "
                  f"{len(repro)} attempt-to-attempt comparisons")
        phase_folders[ph] = folders

    pooled = [ph for ph in todo if ph in rep_tables]
    if len(pooled) > 1:
        write_summary(pooled, rep_tables, rep_findings, rep_reports, qc_root, args.label)
    _write_readme(qc_root, phase_folders, pooled if len(pooled) > 1 else [], archived)
    print(f"\nDone. QC results in {qc_root}")
    return 0


def _write_readme(qc_root, phase_folders, pooled, archived):
    """qc/README.md: what this run covers and where to start reading."""
    lines = [
        "# QC outputs", "",
        f"Generated by `scripts/run_qc.py` on {time.strftime('%Y-%m-%d %H:%M')} for "
        f"{', '.join(phase_folders)}. Earlier runs are in `archive/`"
        + (f" (the one just before this is `archive/{archived.name}/`)." if archived else "."), "",
        "Start with each phase's `coverage.md` (who has which attempt, plus notes on "
        "gaps, file names and set-aside masks), then the reports in "
        "`<phase>/<attempt>/reports/`. Each attempt (`a1`, `a2`, ...) is analyzed on "
        "its own; `attempts_average/` is the mean of the per-attempt metrics for each "
        "scan/rater and holds the attempt-to-attempt reproducibility table.", "",
        "```", "qc/",
    ]
    full = "{metrics, plots, worst_slices, all_slices, reports}"
    for ph, folders in phase_folders.items():
        lines += [f"├── {ph}/", "│   ├── coverage.md, coverage.csv"]
        for folder in folders:
            if folder == qa.AVERAGE_DIR:
                lines.append(f"│   ├── {folder}/  {{metrics, plots, reports}} "
                             "(mean of per-attempt metrics; no montages)")
            else:
                lines.append(f"│   ├── {folder}/  {full}")
    if pooled:
        lines.append("├── summary/  pooled across " + ", ".join(pooled))
    lines += ["└── archive/  earlier runs", "```", ""]
    (Path(qc_root) / "README.md").write_text("\n".join(lines))


if __name__ == "__main__":
    raise SystemExit(main())
