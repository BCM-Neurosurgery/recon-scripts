#!/bin/bash

set -euo pipefail

timestamp() {
  date "+%Y-%m-%d %H:%M:%S"
}

log() {
  printf '[%s] %s\n' "$(timestamp)" "$*"
}

run_step() {
  local name="$1"
  shift
  local start_time
  start_time="$(date +%s)"
  log "START ${name}"
  log "CMD   $*"
  "$@"
  local end_time
  end_time="$(date +%s)"
  log "DONE  ${name} ($((end_time - start_time))s)"
}

bootstrap_neuro_env() {
  if command -v bet >/dev/null 2>&1 && command -v flirt >/dev/null 2>&1 && command -v fnirt >/dev/null 2>&1 && command -v applywarp >/dev/null 2>&1; then
    return
  fi

  if [ -n "${FREESURFER_HOME:-}" ] && [ -f "${FREESURFER_HOME}/SetUpFreeSurfer.sh" ]; then
    # shellcheck disable=SC1091
    source "${FREESURFER_HOME}/SetUpFreeSurfer.sh"
    return
  fi

  for candidate in /Applications/freesurfer/*/SetUpFreeSurfer.sh /Applications/freesurfer/SetUpFreeSurfer.sh; do
    if [ -f "$candidate" ]; then
      # shellcheck disable=SC1090
      source "$candidate"
      return
    fi
  done
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1"
    exit 1
  fi
}

first_existing() {
  for candidate in "$@"; do
    if [ -f "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

if [ "$#" -lt 2 ]; then
  echo "Usage: $0 <subject_root> <atlas_assets_root>"
  echo "Example: $0 /Users/me/rave_data/raw_dir/YFW /Volumes/projectworlds/EMU-18112/ElectrodeLabelsROIs"
  exit 1
fi

subject_root="$1"
atlas_assets_root="$2"
subject_id="$(basename "$subject_root")"
rave_root="${subject_root}/rave-imaging"
derivative_root="${rave_root}/derivative"
fs_mri_root="${rave_root}/fs/mri"

t1_image="$(first_existing \
  "${derivative_root}/T1.nii" \
  "${derivative_root}/T1.nii.gz" \
  "${fs_mri_root}/T1.nii" \
  "${derivative_root}/MRI_reference.nii.gz" || true)"
brain_t1="${derivative_root}/bet_T1.nii.gz"
affine_mat="${derivative_root}/MNI152_to_${subject_id}.mat"
warped_mni="${derivative_root}/MNI152_in_${subject_id}.nii.gz"
warped_field="${derivative_root}/MNI152_in_${subject_id}_fnirt.nii.gz"
xtract_input="${atlas_assets_root}/xtract_label_all.nii.gz"
xtract_output_nii="${derivative_root}/xtract_label_in_${subject_id}.nii.gz"
xtract_output_mgz="${derivative_root}/xtract_label_in_${subject_id}.mgz"

if [ ! -d "$subject_root" ]; then
  echo "Subject root not found: $subject_root"
  exit 1
fi

if [ -z "$t1_image" ]; then
  echo "Missing subject T1 image under ${derivative_root} or ${fs_mri_root}"
  exit 1
fi

if [ ! -f "$xtract_input" ]; then
  echo "Missing xtract atlas image: $xtract_input"
  exit 1
fi

mkdir -p "$derivative_root"

bootstrap_neuro_env

for cmd in bet flirt fnirt applywarp; do
  require_command "$cmd"
done

echo "Running xtract warp for subject ${subject_id}"
echo "Subject root: $subject_root"
echo "Atlas assets: $atlas_assets_root"
echo "Using T1 image: $t1_image"
log "Step 1/4: skull-strip subject T1 with BET"
run_step "bet" bet "$t1_image" "$brain_t1" -f 0.45

log "Step 2/4: affine registration of MNI brain to subject brain with FLIRT"
run_step "flirt" \
  flirt \
  -in "${atlas_assets_root}/MNI152_T1_1mm_brain.nii.gz" \
  -ref "$brain_t1" \
  -omat "$affine_mat" \
  -out "$warped_mni"

log "Step 3/4: nonlinear warp with FNIRT; this is the slow step and can take several minutes"
run_step "fnirt" \
  fnirt \
  --in="${atlas_assets_root}/MNI152_T1_1mm_brain.nii.gz" \
  --ref="$brain_t1" \
  --aff="$affine_mat" \
  --cout="$warped_field"

log "Step 4/4: warp xtract labels into subject space with APPLYWARP"
run_step "applywarp" \
  applywarp \
  -i "$xtract_input" \
  -o "$xtract_output_nii" \
  -r "$brain_t1" \
  --warp="$warped_field" \
  --interp=nn

if command -v mri_convert >/dev/null 2>&1; then
  log "Optional step: convert warped xtract labels to MGZ"
  run_step "mri_convert" mri_convert "$xtract_output_nii" "$xtract_output_mgz"
else
  log "mri_convert not found; skipping MGZ conversion"
fi

log "Wrote:"
echo "  $xtract_output_nii"
if [ -f "$xtract_output_mgz" ]; then
  echo "  $xtract_output_mgz"
fi
