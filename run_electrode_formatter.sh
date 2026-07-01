#!/bin/bash

# Run the electrode formatter
recon-electrodes build \
  --input-csv /Volumes/projectworlds/EMU-18112/rave_data/data_dir/YAEL/YFX/rave/meta/electrodes.csv \
  --montage /Volumes/projectworlds/EMU-18112/YFX_Datafile/INFO/YFX_montage.xlsx \
  --subject-root /Volumes/projectworlds/EMU-18112/rave_data/raw_dir/YFX \
  --ns3 /Volumes/datalake/emu/YFXDatafile/DATA/20260513-142941/NSP1-20260513-142941-052.ns3  \
  --ns5 /Volumes/datalake/emu/YFXDatafile/DATA/20260513-142941/NSP2-20260513-142941-019.ns5 \
  --run-xtract \
  --xtract-assets-root /Volumes/projectworlds/EMU-18112/ElectrodeLabelsROIs \
  --output /Volumes/projectworlds/EMU-18112/YFX_Datafile/IMG/YFX-electrodes_v2026.csv
