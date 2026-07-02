#!/bin/bash

# Run the electrode formatter
recon-electrodes build \
  --input-csv /Volumes/projectworlds/EMU-18112/rave_data/data_dir/YAEL/YFY/rave/meta/electrodes.csv \
  --montage /Volumes/projectworlds/EMU-18112/YFY_Datafile/INFO/YFY_montage.xlsx \
  --subject-root /Volumes/projectworlds/EMU-18112/rave_data/raw_dir/YFY \
  --ns3 /Volumes/datalake/emu/YFYDatafile/DATA/20260621-102920/NSP1-20260621-102920-003.ns3 \
  --ns5 /Volumes/datalake/emu/YFYDatafile/DATA/20260621-102920/NSP2-20260621-102920-003.ns5 \
  --run-xtract \
  --xtract-assets-root /Volumes/projectworlds/EMU-18112/ElectrodeLabelsROIs \
  --output /Volumes/projectworlds/EMU-18112/YFY_Datafile/IMG/YFY-electrodes_v2026.csv
