#!/bin/bash

# Run the electrode formatter
recon-electrodes build \
  --input-csv /Users/tahaismail/rave_data/data_dir/YAEL/YFW/rave/meta/electrodes.csv \
  --montage /Volumes/projectworlds/EMU-18112/YFW_Datafile/INFO/YFW_montage.xlsx \
  --subject-root /Users/tahaismail/rave_data/raw_dir/YFW \
  --ns3 /Volumes/datalake/emu/YFWDatafile/DATA/20260501-124013/NSP1-20260501-124013-001.ns3 \
  --ns5 /Volumes/datalake/emu/YFWDatafile/DATA/20260501-124013/NSP2-20260501-124013-057.ns5 \
  --run-xtract \
  --xtract-assets-root /Volumes/projectworlds/EMU-18112/ElectrodeLabelsROIs \
  --output /Users/tahaismail/Downloads/YFW-electrodes_v2026.csv
