#!/usr/bin/env python3
"""Deterministically assign subjects to phases and raters, with blinding.

Reads the pools (paths.yaml), raters (raters.json) and phase definitions +
seed + ID naming (phases.yaml) from config/, then writes
config/subjects_split.json. Each source scan is relabeled to an anonymized
workspace ID (e.g. sub-0001) so raters never see the source IDs; the
workspace -> source key lives in the "mapping" field and must be kept private.

Nothing is hardcoded: raters come from raters.json, subjects from each pool's
list, the seed/ID naming from phases.yaml (override seed with --seed). The
assignment is random but reproducible: the same seed yields the same split.
Pools are drawn down in phase order, so phases sharing a pool get disjoint
subjects.

For a partitioned phase, an `overlap` fraction of its subjects is given to every
rater (hidden repeats) so reliability can be tracked on the production set.

    python scripts/split_subjects.py            # uses seed from phases.yaml
    python scripts/split_subjects.py --seed 7   # override
"""
import argparse
import json
import random
from pathlib import Path

import utils


def allocate(subjects, n):
    """Take the first n subjects (or all if n == 'all'); return (taken, rest).
    Order is preserved (the pool is already shuffled), so taken is random."""
    if n == "all" or n is None:
        return subjects, []
    n = int(n)
    return subjects[:n], subjects[n:]


def partition(subjects, raters):
    """Divide subjects across raters as evenly as possible (round-robin)."""
    by_rater = {r: [] for r in raters}
    for i, sub in enumerate(subjects):
        by_rater[raters[i % len(raters)]].append(sub)
    return by_rater


def load_pools(paths, config_dir):
    """Read each pool's subject list. Returns {pool_name: [subjects]}."""
    return {name: utils.read_subject_list(config_dir / pdef["subjects"])
            for name, pdef in paths["pools"].items()}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config-dir", type=Path, default=utils.CONFIG_DIR)
    ap.add_argument("--seed", type=int, default=None,
                    help="Override the seed from phases.yaml.")
    ap.add_argument("--out", type=Path, default=None,
                    help="Output JSON (default: <config-dir>/subjects_split.json).")
    args = ap.parse_args()

    paths, phases, raters = utils.load_config(args.config_dir)
    if len(raters) < 2:
        ap.error("Need at least 2 raters in raters.json")
    seed = args.seed if args.seed is not None else int(phases.get("seed", 42))
    prefix = phases.get("id_prefix", "sub-")
    width = int(phases.get("id_width", 4))

    pools = load_pools(paths, args.config_dir)
    seen = {}
    for name, subs in pools.items():
        for s in subs:
            if s in seen:
                ap.error(f"Subject {s} appears in both '{seen[s]}' and '{name}' pools")
            seen[s] = name

    rng = random.Random(seed)
    remaining = {}
    for name in sorted(pools):                      # stable order -> reproducible
        pool = list(dict.fromkeys(pools[name]))     # de-dup, preserve order
        rng.shuffle(pool)
        remaining[name] = pool

    mapping = {}                                     # workspace ID -> source ID
    counter = 1
    split = {"seed": seed, "raters": raters, "mapping": mapping, "phases": {}}

    for key in utils.phase_keys(phases):
        pcfg = phases[key]
        pool_name = pcfg["pool"]
        if pool_name not in remaining:
            ap.error(f"{key}: unknown pool '{pool_name}' (not in paths.yaml pools)")
        taken, remaining[pool_name] = allocate(remaining[pool_name], pcfg.get("n_subjects"))
        if not taken:
            ap.error(f"{key}: pool '{pool_name}' is exhausted; add more subjects.")

        # Relabel to anonymized workspace IDs (random source order -> blind).
        ids = [f"{prefix}{i:0{width}d}" for i in range(counter, counter + len(taken))]
        counter += len(taken)
        for wid, src in zip(ids, taken):
            mapping[wid] = src

        entry = {"name": pcfg["name"], "pool": pool_name,
                 "assignment": pcfg["assignment"], "subjects": sorted(ids)}
        if pcfg["assignment"] == "partitioned":
            frac = float(pcfg.get("overlap", 0) or 0)
            n_overlap = round(frac * len(ids))
            overlap, rest = ids[:n_overlap], ids[n_overlap:]
            part = partition(rest, raters)
            entry["by_rater"] = {r: sorted(part[r] + overlap) for r in raters}
            if overlap:
                entry["overlap"] = sorted(overlap)
        else:                                       # shared: same subjects for all
            entry["by_rater"] = {r: sorted(ids) for r in raters}
        split["phases"][key] = entry

    out = args.out or (args.config_dir / "subjects_split.json")
    out.write_text(json.dumps(split, indent=2) + "\n")

    print(f"Wrote {out}  (seed={seed}, raters={raters})")
    for key, info in split["phases"].items():
        line = (f"  {key}_{info['name']:<11} pool={info['pool']:<7} "
                f"{info['assignment']:<11} {len(info['subjects'])} subjects")
        if "overlap" in info:
            sizes = "/".join(str(len(v)) for v in info["by_rater"].values())
            line += f"  (+{len(info['overlap'])} overlap; per-rater {sizes})"
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
