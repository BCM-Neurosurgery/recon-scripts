#!/bin/bash

set -euo pipefail

timestamp() {
  date "+%Y-%m-%d %H:%M:%S"
}

log() {
  printf '[%s] %s\n' "$(timestamp)" "$*"
}

tool_verbose_enabled() {
  case "${RECON_XTRACT_TOOL_VERBOSE:-1}" in
    0|false|FALSE|no|NO)
      return 1
      ;;
    *)
      return 0
      ;;
  esac
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

file_ready() {
  [ -f "$1" ] && [ -s "$1" ]
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
if tool_verbose_enabled; then
  log "Child command verbosity: enabled"
else
  log "Child command verbosity: suppressed; Python/shell step logging remains enabled"
fi

bet_args=(bet "$t1_image" "$brain_t1" -f 0.45)
if tool_verbose_enabled; then
  bet_args+=(-v)
fi
log "Step 1/4: skull-strip subject T1 with BET"
if file_ready "$brain_t1"; then
  log "SKIP  bet (existing output: $brain_t1)"
else
  run_step "bet" "${bet_args[@]}"
fi

flirt_args=(
  flirt
  -in "${atlas_assets_root}/MNI152_T1_1mm_brain.nii.gz"
  -ref "$brain_t1"
  -omat "$affine_mat"
  -out "$warped_mni"
)
if tool_verbose_enabled; then
  flirt_args+=(-v)
fi
log "Step 2/4: affine registration of MNI brain to subject brain with FLIRT"
if file_ready "$affine_mat" && file_ready "$warped_mni"; then
  log "SKIP  flirt (existing outputs: $affine_mat, $warped_mni)"
else
  run_step "flirt" "${flirt_args[@]}"
fi

fnirt_args=(
  fnirt
  --in="${atlas_assets_root}/MNI152_T1_1mm_brain.nii.gz"
  --ref="$brain_t1"
  --aff="$affine_mat"
  --cout="$warped_field"
)
if tool_verbose_enabled; then
  fnirt_args+=(--verbose)
fi
log "Step 3/4: nonlinear warp with FNIRT; this is the slow step and can take several minutes"
if file_ready "$warped_field"; then
  log "SKIP  fnirt (existing output: $warped_field)"
else
  run_step "fnirt" "${fnirt_args[@]}"
fi

applywarp_args=(
  applywarp
  -i "$xtract_input"
  -o "$xtract_output_nii"
  -r "$brain_t1"
  --warp="$warped_field"
  --interp=nn
)
if tool_verbose_enabled; then
  applywarp_args+=(--verbose)
fi
log "Step 4/4: warp xtract labels into subject space with APPLYWARP"
if file_ready "$xtract_output_nii"; then
  log "SKIP  applywarp (existing output: $xtract_output_nii)"
else
  run_step "applywarp" "${applywarp_args[@]}"
fi

if command -v mri_convert >/dev/null 2>&1; then
  log "Optional step: convert warped xtract labels to MGZ"
  if file_ready "$xtract_output_mgz"; then
    log "SKIP  mri_convert (existing output: $xtract_output_mgz)"
  else
    run_step "mri_convert" mri_convert "$xtract_output_nii" "$xtract_output_mgz"
  fi
else
  log "mri_convert not found; skipping MGZ conversion"
fi

log "Wrote:"
echo "  $xtract_output_nii"
if [ -f "$xtract_output_mgz" ]; then
  echo "  $xtract_output_mgz"
fi
