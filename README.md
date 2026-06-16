# recon-scripts

Scripting for electrode reconstruction.

## `electrodes_v2026`

This repo now includes a reusable Python package and CLI for converting a
RAVE `electrodes.csv` into a lab-ready `{patient}-electrodes_v2026.csv`.

### Features

- Validates macro `ElectrodeID` and labels against montage `Sheet1`
- Synthesizes one bundled micro row per 8-contact micro set from montage `Sheet2`
- Preserves the first 27 v2025 output columns in the expected order
- Appends lab bookkeeping columns:
  `MicroContactRange`, `MontageElectrodeIDRange`, `NSxSource`, `NSxIndex`, `NSxElectrodeID`
- Uses RAVE-native subject roots under `raw_dir/<subject>/rave-imaging/...` first
- Maps macro atlas fields directly from existing RAVE `FSLabel_*` columns
- Falls back to subject imaging products for transform-derived coordinates and optional atlas sampling
- Attempts NSx channel metadata extraction with `neo` when installed

### CLI

```bash
recon-electrodes build \
  --input-csv /path/to/electrodes.csv \
  --montage /path/to/YFW_montage.xlsx \
  --subject-root /path/to/raw_dir/YFW \
  --ns3 /path/to/file.ns3 \
  --ns5 /path/to/file.ns5 \
  --run-xtract \
  --xtract-assets-root /Volumes/projectworlds/EMU-18112/ElectrodeLabelsROIs \
  --output /path/to/YFW-electrodes_v2026.csv
```

### Notes

- `--subject-root` should usually be the RAVE subject root, for example `.../rave_data/raw_dir/YFW`.
- Legacy `elec_recon`-style sidecars are still used as a fallback when present.
- NSx parsing is best-effort. If `neo` is unavailable or a file cannot be parsed, the CSV is still written and NSx columns are left blank.
- Synthetic micro rows receive unique `ElectrodeID` values; montage and NS5 ranges are stored in their own columns.
- Macro atlas fields come from RAVE first; synthetic micro atlas fields are sampled from subject imaging products when available and otherwise fall back to the owning macro row.
- `--run-xtract` uses verbose BET/FLIRT/FNIRT/APPLYWARP output by default. If you want only the higher-level Python and shell step logs, add `--suppress-xtract-tool-output` or its alias `--suppress-output`.

### Optional Xtract Helper

If `ROI_XTRACT_3mm` is missing because no warped xtract volume exists yet, use:

```bash
scripts/run_xtract_rave_subject.sh \
  /path/to/rave_data/raw_dir/YFW \
  /Volumes/projectworlds/EMU-18112/ElectrodeLabelsROIs
```

This helper writes subject-space xtract volumes under `rave-imaging/derivative/`, where the formatter will auto-discover them on later runs.

The helper is RAVE-native: it uses the subject T1 under `rave-imaging/derivative/` or `rave-imaging/fs/mri/`, plus the atlas assets under `ElectrodeLabelsROIs`. The legacy `elec_recon` text exports like `.PIAL`, `.PIALVOX`, `.LEPTO`, and `.FSAVERAGE` are not required for the xtract warp itself.
