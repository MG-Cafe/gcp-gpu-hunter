#!/usr/bin/env bash
# ========================================================================================
# Ultra-Fast 1-VM Sniper & Multi-Zone Parallel GPU Reservation Hunter (gcloud Bash Edition)
# ========================================================================================
# Customer Default Configuration (runs out-of-the-box with zero arguments):
#   - Host Project:      prj-d-gai-launchpad
#   - Shared Project:    prj-d-gai-aipgaisvc-1 (--share-setting=projects --reservation-sharing-policy=ALLOW_ALL)
#   - Target Quantity:   3 VMs total, hunted 1 VM at a time (--vm-count=1)
#   - Default Chip/SKU:  a3-highgpu-8g (count=8,type=nvidia-h100-80gb)
#   - Default Regions:   us-east4,us-central1 -> Automatically expands to all available
#                        a3-highgpu-8g zones in those 2 regions (priority-ordered):
#                        [us-east4-a, us-central1-a, us-central1-c, us-east4-b]
#   - Consumption Mode:  Long-Term On-Demand Reservations ONLY (No DWS / No Spot)
# ========================================================================================
set -uo pipefail

PROJECT_ID="${PROJECT_ID:-prj-d-gai-launchpad}"
SHARE_WITH_PROJECT="${SHARE_WITH_PROJECT:-prj-d-gai-aipgaisvc-1}"
MACHINE_TYPES="${MACHINE_TYPES:-a3-highgpu-8g}"
ACCELERATOR_TYPE="${ACCELERATOR_TYPE:-}"
ACCELERATOR_COUNT="${ACCELERATOR_COUNT:-0}"
REGIONS="${REGIONS:-us-east4,us-central1}"
ZONES="${ZONES:-}"
TARGET_VM_COUNT="${TARGET_VM_COUNT:-3}"
PER_RESERVATION_VM_COUNT="${PER_RESERVATION_VM_COUNT:-1}"
WAVE_INTERVAL_SECONDS="${WAVE_INTERVAL_SECONDS:-20}"
MAX_WAVES="${MAX_WAVES:-0}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT_ID="$2"; shift 2 ;;
    --share-with) SHARE_WITH_PROJECT="$2"; shift 2 ;;
    --machine-types) MACHINE_TYPES="$2"; shift 2 ;;
    --accelerator-type) ACCELERATOR_TYPE="$2"; shift 2 ;;
    --accelerator-count) ACCELERATOR_COUNT="$2"; shift 2 ;;
    --regions) REGIONS="$2"; shift 2 ;;
    --zones) ZONES="$2"; shift 2 ;;
    --target-vms) TARGET_VM_COUNT="$2"; shift 2 ;;
    --per-reservation-vms) PER_RESERVATION_VM_COUNT="$2"; shift 2 ;;
    --wave-interval) WAVE_INTERVAL_SECONDS="$2"; shift 2 ;;
    --max-waves) MAX_WAVES="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

log() {
  echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] $*"
}

# On-Demand Reservation Supported GPU Catalog
get_default_accel_type() {
  case "$1" in
    a3-highgpu-8g|a3-edgegpu-8g) echo "nvidia-h100-80gb" ;;
    a3-megagpu-8g)               echo "nvidia-h100-mega-80gb" ;;
    a2-ultragpu-8g)              echo "nvidia-a100-80gb" ;;
    a2-highgpu-8g)               echo "nvidia-tesla-a100" ;;
    g4-standard-*)               echo "nvidia-rtx-pro-6000" ;;
    g2-standard-*)               echo "nvidia-l4" ;;
    *)                           echo "nvidia-h100-80gb" ;;
  esac
}

get_default_accel_count() {
  case "$1" in
    a3-highgpu-8g|a3-megagpu-8g|a3-edgegpu-8g|a2-ultragpu-8g|a2-highgpu-8g) echo "8" ;;
    *) echo "1" ;;
  esac
}

get_catalog_zones() {
  case "$1" in
    a3-highgpu-8g)
      echo "us-east4-a us-central1-a us-central1-c us-east4-b us-east5-a us-west1-a us-west1-b us-west4-a europe-west4-c europe-west1-b europe-west1-c asia-east1-c asia-northeast1-b asia-southeast1-b asia-southeast1-c"
      ;;
    a3-megagpu-8g)
      echo "us-east4-a us-central1-a us-central1-c us-central1-b us-east4-b us-east4-c us-east5-a us-west1-a us-west1-b us-west4-a europe-west4-b europe-west4-c europe-west1-b europe-west1-c"
      ;;
    a3-edgegpu-8g)
      echo "us-east4-a us-central1-a us-central1-c us-central1-b us-east4-b us-east1-b us-east1-c us-west1-a us-west1-b"
      ;;
    *)
      echo "us-east4-a us-central1-a us-central1-c us-east4-b"
      ;;
  esac
}

resolve_zones_for_mt() {
  local mt="$1"
  if [[ -n "${ZONES}" ]]; then
    echo "${ZONES//,/ }"
    return
  fi
  local catalog
  catalog="$(get_catalog_zones "${mt}")"
  local allowed_regions=",${REGIONS},"
  local matched=()
  for z in ${catalog}; do
    local reg="${z%-*}"
    if [[ "${allowed_regions}" == *",all,"* ]] || [[ "${allowed_regions}" == *",${reg},"* ]]; then
      matched+=("${z}")
    fi
  done
  echo "${matched[*]}"
}

create_one_reservation() {
  local mt="$1"
  local accel_type="$2"
  local accel_count="$3"
  local zone="$4"
  local vm_count="$5"
  local res_name="$6"

  local cmd=(
    gcloud compute reservations create "${res_name}"
    --vm-count="${vm_count}"
    --machine-type="${mt}"
    --accelerator="count=${accel_count},type=${accel_type}"
    --zone="${zone}"
    --project="${PROJECT_ID}"
    --reservation-sharing-policy=ALLOW_ALL
    --quiet
  )
  if [[ -n "${SHARE_WITH_PROJECT}" ]]; then
    cmd+=(--share-setting=projects --share-with="${SHARE_WITH_PROJECT}")
  fi

  "${cmd[@]}" 2>&1
}

log "=============================================================================="
log "Starting Ultra-Fast 1-VM Sniper & Multi-Zone Parallel GPU Hunter (gcloud Bash)"
log "  Host Project:        ${PROJECT_ID}"
log "  Shared With:         ${SHARE_WITH_PROJECT:-(None)}"
log "  Machine Types:       ${MACHINE_TYPES}"
log "  Target Regions:      ${ZONES:-${REGIONS}}"
log "  Target Quantity:     ${TARGET_VM_COUNT} VMs total (${PER_RESERVATION_VM_COUNT} VM per reservation)"
log "=============================================================================="

SECURED_VMS=0
SECURED_LIST=()
WAVE_NUM=0
SEQ=0
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

IFS=',' read -r -a MT_ARRAY <<< "${MACHINE_TYPES}"

while [[ "${SECURED_VMS}" -lt "${TARGET_VM_COUNT}" ]]; do
  WAVE_NUM=$((WAVE_NUM + 1))
  REMAINING=$((TARGET_VM_COUNT - SECURED_VMS))
  log ""
  log "--- Wave #${WAVE_NUM} | Need ${REMAINING} more VM(s) (Secured: ${SECURED_VMS}/${TARGET_VM_COUNT}) ---"

  for mt in "${MT_ARRAY[@]}"; do
    [[ "${SECURED_VMS}" -ge "${TARGET_VM_COUNT}" ]] && break

    accel_type="${ACCELERATOR_TYPE:-$(get_default_accel_type "${mt}")}"
    if [[ "${ACCELERATOR_COUNT}" -gt 0 ]]; then
      accel_count="${ACCELERATOR_COUNT}"
    else
      accel_count="$(get_default_accel_count "${mt}")"
    fi

    read -r -a ZONE_ARRAY <<< "$(resolve_zones_for_mt "${mt}")"
    if [[ "${#ZONE_ARRAY[@]}" -eq 0 ]]; then
      continue
    fi

    # Copy base gcloud config per worker to prevent SQLite lock contention in parallel runs
    BASE_GCLOUD_CFG="${CLOUDSDK_CONFIG:-${HOME}/.config/gcloud}"

    log "Probing ${mt} (${accel_count}x ${accel_type}) across ${#ZONE_ARRAY[@]} zones in parallel: ${ZONE_ARRAY[*]}"
    PIDS=()
    ZONE_NAMES=()
    RES_NAMES=()
    OUT_FILES=()

    for idx in "${!ZONE_ARRAY[@]}"; do
      zone="${ZONE_ARRAY[$idx]}"
      SEQ=$((SEQ + 1))
      short_zone="${zone//us-/}"
      short_zone="${short_zone//-/}"
      ts_tag="$(date -u '+%m%d-%H%M%S')"
      res_name="$(printf 'launchpad-reservations-%s-%s-%s-%02d' "${mt}" "${short_zone}" "${ts_tag}" "${SEQ}" | cut -c1-63)"
      out_file="${TMP_DIR}/wave_${WAVE_NUM}_${idx}.out"

      worker_cfg="${TMP_DIR}/gcloud_cfg_${idx}"
      if [[ ! -d "${worker_cfg}" ]] && [[ -d "${BASE_GCLOUD_CFG}" ]]; then
        mkdir -p "${worker_cfg}"
        cp -r "${BASE_GCLOUD_CFG}/." "${worker_cfg}/" 2>/dev/null || true
      fi

      (
        export CLOUDSDK_CONFIG="${worker_cfg}"
        create_one_reservation "${mt}" "${accel_type}" "${accel_count}" "${zone}" "${PER_RESERVATION_VM_COUNT}" "${res_name}" > "${out_file}" 2>&1
      ) &
      PIDS+=("$!")
      ZONE_NAMES+=("${zone}")
      RES_NAMES+=("${res_name}")
      OUT_FILES+=("${out_file}")
    done

    HOT_ZONES=()
    for idx in "${!PIDS[@]}"; do
      pid="${PIDS[$idx]}"
      zone="${ZONE_NAMES[$idx]}"
      res_name="${RES_NAMES[$idx]}"
      out_file="${OUT_FILES[$idx]}"

      if wait "${pid}"; then
        log "  [HIT]  ${zone}: Reserved ${PER_RESERVATION_VM_COUNT}x ${mt} -> ${res_name}"
        if [[ $((SECURED_VMS + PER_RESERVATION_VM_COUNT)) -le "${TARGET_VM_COUNT}" ]]; then
          SECURED_VMS=$((SECURED_VMS + PER_RESERVATION_VM_COUNT))
          SECURED_LIST+=("${res_name} (${zone}: ${PER_RESERVATION_VM_COUNT}x ${mt})")
          HOT_ZONES+=("${zone}")
        else
          log "  [Cleanup] Deleting excess parallel reservation ${res_name} in ${zone}..."
          gcloud compute reservations delete "${res_name}" --zone="${zone}" --project="${PROJECT_ID}" --quiet >/dev/null 2>&1 || true
        fi
      else
        err_line="$(tr '\n' ' ' < "${out_file}" | cut -c1-160)"
        log "  [MISS] ${zone} (${PER_RESERVATION_VM_COUNT}x ${mt}): ${err_line}"
      fi
    done

    # Hot-Zone Double-Tap: Immediately re-probe any winning zone with 0s wait
    for hot_zone in "${HOT_ZONES[@]}"; do
      while [[ "${SECURED_VMS}" -lt "${TARGET_VM_COUNT}" ]]; do
        SEQ=$((SEQ + 1))
        short_zone="${hot_zone//us-/}"
        short_zone="${short_zone//-/}"
        ts_tag="$(date -u '+%m%d-%H%M%S')"
        follow_name="$(printf 'launchpad-reservations-%s-%s-%s-%02d' "${mt}" "${short_zone}" "${ts_tag}" "${SEQ}" | cut -c1-63)"
        log "  [HOT-ZONE BURST] Immediately re-probing winning zone ${hot_zone} for ${PER_RESERVATION_VM_COUNT} more VM(s)..."
        if create_one_reservation "${mt}" "${accel_type}" "${accel_count}" "${hot_zone}" "${PER_RESERVATION_VM_COUNT}" "${follow_name}" >/dev/null 2>&1; then
          SECURED_VMS=$((SECURED_VMS + PER_RESERVATION_VM_COUNT))
          SECURED_LIST+=("${follow_name} (${hot_zone}: ${PER_RESERVATION_VM_COUNT}x ${mt})")
          log "  [HOT-ZONE HIT!] ${hot_zone}: Secured another ${PER_RESERVATION_VM_COUNT}x ${mt} -> ${follow_name} (${SECURED_VMS}/${TARGET_VM_COUNT})"
        else
          log "  [HOT-ZONE DONE] ${hot_zone} has no additional free hosts right now."
          break
        fi
      done
    done
  done

  [[ "${SECURED_VMS}" -ge "${TARGET_VM_COUNT}" ]] && break
  if [[ "${MAX_WAVES}" -gt 0 ]] && [[ "${WAVE_NUM}" -ge "${MAX_WAVES}" ]]; then
    break
  fi

  JITTER=$((RANDOM % 4))
  SLEEP_SECS=$((WAVE_INTERVAL_SECONDS + JITTER))
  log "Wave #${WAVE_NUM} complete (${SECURED_VMS}/${TARGET_VM_COUNT} secured). Sleeping ${SLEEP_SECS}s before next wave..."
  sleep "${SLEEP_SECS}"
done

log "=============================================================================="
if [[ "${SECURED_VMS}" -ge "${TARGET_VM_COUNT}" ]]; then
  log "SUCCESS! Secured all ${SECURED_VMS}/${TARGET_VM_COUNT} requested GPU VMs:"
  for item in "${SECURED_LIST[@]}"; do
    log "  - ${item}"
  done
  log "=============================================================================="
  exit 0
else
  log "STOPPED: Hunting ended with ${SECURED_VMS}/${TARGET_VM_COUNT} VMs secured."
  for item in "${SECURED_LIST[@]}"; do
    log "  - Partial: ${item}"
  done
  log "=============================================================================="
  exit 1
fi
