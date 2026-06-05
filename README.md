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
  `MicroContactRange`, `NSxSource`, `NSxIndex`, `NSxElectrodeID`
- Backfills missing atlas and matter fields from subject recon sidecars when available
- Attempts NSx channel metadata extraction with `neo` when installed

### CLI

```bash
recon-electrodes build \
  --input-csv /path/to/electrodes.csv \
  --montage /path/to/YFW_montage.xlsx \
  --subject-root /path/to/subject/or/elec_recon/root \
  --ns3 /path/to/file.ns3 \
  --ns5 /path/to/file.ns5 \
  --output /path/to/YFW-electrodes_v2026.csv
```

### Notes

- `--subject-root` is explicit by design; the tool does not assume one fixed lab path layout.
- NSx parsing is best-effort. If `neo` is unavailable or a file cannot be parsed, the CSV is still written and NSx columns are left blank.
- The current implementation uses recon sidecar tables and text coordinate files when present. Richer atlas-volume-derived recomputation can be layered in later without changing the CLI contract.
