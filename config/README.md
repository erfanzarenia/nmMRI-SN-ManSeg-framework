# Config

The files here are a working example of the format (`split_subjects.py --dry-run`
runs with them as they are). Your own config goes in
`config/local/`, which git ignores:

```bash
mkdir -p config/local && cp config/*.yaml config/*.json config/subjects_* config/local/
```

The scripts read `config/local/` whenever it has a `paths.yaml`, and `config/`
otherwise. `MANSEG_CONFIG_DIR=<dir>` or `--config-dir <dir>` points them elsewhere.

| File | What it holds |
|---|---|
| `paths.yaml` | data pools (dataset roots, layout, file patterns), workspace and QC locations |
| `phases.yaml` | phases, split seed, workspace ID naming, per-phase raters and criteria |
| `raters.json` | rater initials; optionally their QC plot colors, `colors` (`{"R1": "#1f77b4"}`) and `pair_colors` (`{"R1-R2": "#2ca02c"}`) |
| `subjects_labeled.txt` | subjects with expert masks (phases 1-4) |
| `subjects_study.csv` | study subjects with their age group (phases 5-6) |
| `docs/` (optional) | your own versions of the rater guides in the repo's `docs/` (e.g. `phase5.md` with your scanner's settings); `make_workspace.py` copies them into the workspace instead of the repo's |

The scripts also write private files next to the config: `subjects_split.json`
(the workspace to source ID key, with timestamped backups) and the `<phase>_*.csv`
manifests. Never share them with raters.
