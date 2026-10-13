#!/usr/bin/env python3
"""Summarize inter-rater agreement from qc_metrics.py output.

Reads dice_pairwise.csv (and dice_rater_vs_expert.csv if present) for a phase
and writes clean summaries to the same directory:

    summary_overall.csv     mean/sd Dice for the phase
    summary_by_pair.csv     mean/sd pairwise Dice per rater pair
    summary_by_subject.csv  mean pairwise Dice per subject (worst first)
    summary_by_rater.csv    mean Dice vs expert per rater (where experts exist)

    python scripts/interrater_analysis.py --phase phase4
"""
import argparse
from pathlib import Path

import pandas as pd

import utils


def summarize(df, group=None):
    """Count/mean/sd of the 'dice' column, optionally grouped, sorted by mean."""
    if group is None:
        d = df["dice"]
        return pd.DataFrame([{"n": d.count(), "mean": d.mean(), "sd": d.std(),
                              "min": d.min(), "max": d.max()}])
    return (df.groupby(group)["dice"].agg(n="count", mean="mean", sd="std")
              .reset_index().sort_values("mean"))


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phase", default="phase4")
    ap.add_argument("--config-dir", type=Path, default=utils.CONFIG_DIR)
    ap.add_argument("--qc-dir", type=Path, default=None,
                    help="Directory with qc_metrics CSVs (default: <qc_output_root>/<phase>).")
    ap.add_argument("--top-n", type=int, default=5, help="How many worst subjects to print.")
    args = ap.parse_args()

    paths, _, _ = utils.load_config(args.config_dir)
    qc_dir = args.qc_dir or Path(paths["qc_output_root"]) / args.phase
    pw_path = qc_dir / "dice_pairwise.csv"
    if not pw_path.exists():
        print(f"Not found: {pw_path}\nRun: python scripts/qc_metrics.py --phase {args.phase}")
        return 1

    pw = pd.read_csv(pw_path)
    overall = summarize(pw).assign(metric="pairwise_dice")
    by_pair = summarize(pw.assign(pair=pw.rater_a + "-" + pw.rater_b), "pair")
    by_subject = summarize(pw, "subject")

    by_rater = None
    rve_path = qc_dir / "dice_rater_vs_expert.csv"
    if rve_path.exists():
        rve = pd.read_csv(rve_path)
        overall = pd.concat([overall, summarize(rve).assign(metric="rater_vs_expert_dice")],
                            ignore_index=True)
        by_rater = summarize(rve, "rater")

    overall = overall[["metric", "n", "mean", "sd", "min", "max"]]
    overall.to_csv(qc_dir / "summary_overall.csv", index=False)
    by_pair.to_csv(qc_dir / "summary_by_pair.csv", index=False)
    by_subject.to_csv(qc_dir / "summary_by_subject.csv", index=False)
    if by_rater is not None:
        by_rater.to_csv(qc_dir / "summary_by_rater.csv", index=False)

    pd.set_option("display.float_format", lambda v: f"{v:.3f}")
    print(f"\n=== {args.phase}: inter-rater agreement ===")
    print(overall.to_string(index=False))
    print("\n-- mean pairwise Dice by rater pair --")
    print(by_pair.to_string(index=False))
    if by_rater is not None:
        print("\n-- mean Dice vs expert by rater --")
        print(by_rater.to_string(index=False))
    print(f"\n-- {args.top_n} worst subjects by mean pairwise Dice --")
    print(by_subject.head(args.top_n).to_string(index=False))
    print(f"\nSummaries written to {qc_dir}/summary_*.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
