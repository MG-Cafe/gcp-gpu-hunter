#!/usr/bin/env python3
"""
Ultra-Fast 1-VM Sniper & Multi-Zone Parallel GPU Reservation Hunter (Python REST Edition)
========================================================================================
Customer Default Configuration (runs out-of-the-box with zero arguments):
  - Host Project:      prj-d-gai-launchpad
  - Shared Project:    prj-d-gai-aipgaisvc-1 (SPECIFIC_PROJECTS + ALLOW_ALL)
  - Target Quantity:   9 more VMs by default (12 total desired - 3 already secured),
                       hunted 1 VM at a time (--vm-count=1) with incremental lock-in:
                       every single 1-VM hit is immediately active and kept ("grab as
                       many as available right now"), even if stopped early via Ctrl+C.
  - Default Chip/SKU:  a3-highgpu-8g (8x nvidia-h100-80gb)
  - Default Regions:   us-east4, us-central1 -> Automatically expands to all available
                       a3-highgpu-8g zones in those 2 regions (priority-ordered):
                       [us-east4-a, us-central1-a, us-central1-c, us-east4-b]
  - Consumption Mode:  Long-Term On-Demand Reservations ONLY (No DWS / No Spot)
"""

import argparse
import concurrent.futures
import datetime
import json
import os
import random
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

# ==============================================================================
# CUSTOMER DEFAULT CONFIGURATION (Overridable via CLI flags or env vars)
# ==============================================================================
PROJECT_ID = os.environ.get("PROJECT_ID", "prj-d-gai-launchpad")
SHARE_WITH_PROJECT = os.environ.get("SHARE_WITH_PROJECT", "prj-d-gai-aipgaisvc-1")

# Customer wants 12 nodes total and already got 3 -> hunt up to 9 more by default
# (Every 1-VM reservation is locked in immediately as soon as it hits)
TARGET_VM_COUNT = int(os.environ.get("TARGET_VM_COUNT", "9"))
PER_RESERVATION_VM_COUNT = int(os.environ.get("PER_RESERVATION_VM_COUNT", "1"))

DEFAULT_MACHINE_TYPES = os.environ.get("MACHINE_TYPES", "a3-highgpu-8g")
DEFAULT_ACCELERATOR_TYPE = os.environ.get("ACCELERATOR_TYPE", "")
DEFAULT_ACCELERATOR_COUNT = int(os.environ.get("ACCELERATOR_COUNT", "0"))
DEFAULT_REGIONS = os.environ.get("REGIONS", "us-east4,us-central1")
CUSTOM_ZONES = os.environ.get("ZONES", "")

WAVE_INTERVAL_SECONDS = float(os.environ.get("WAVE_INTERVAL_SECONDS", "20"))
JITTER_SECONDS = float(os.environ.get("JITTER_SECONDS", "3"))
OP_POLL_INTERVAL_SECONDS = float(os.environ.get("OP_POLL_INTERVAL_SECONDS", "1.5"))
OP_POLL_TIMEOUT_SECONDS = float(os.environ.get("OP_POLL_TIMEOUT_SECONDS", "45"))
MAX_HUNT_HOURS = float(os.environ.get("MAX_HUNT_HOURS", "24"))
MAX_WAVES = int(os.environ.get("MAX_WAVES", "0"))

COMPUTE_API_BASE = os.environ.get(
    "COMPUTE_API_BASE", "https://compute.googleapis.com/compute/v1"
)

# ==============================================================================
# ON-DEMAND RESERVATION SUPPORTED GPU CATALOG (Priority-Ordered Zones)
# ==============================================================================
ON_DEMAND_GPU_CATALOG = {
    "a3-highgpu-8g": {
        "description": "H100 80GB (a3-highgpu-8g)",
        "accelerator_type": "nvidia-h100-80gb",
        "accelerator_count": 8,
        "zones": [
            "us-east4-a",       # Customer primary #1 (us-east4)
            "us-central1-a",    # Customer primary #2 (us-central1)
            "us-central1-c",    # Customer primary #3 (us-central1)
            "us-east4-b",       # Additional available zone in us-east4
            "us-east5-a",
            "us-west1-a",
            "us-west1-b",
            "us-west4-a",
            "europe-west4-c",
            "europe-west1-b",
            "europe-west1-c",
            "asia-east1-c",
            "asia-northeast1-b",
            "asia-southeast1-b",
            "asia-southeast1-c",
        ],
    },
    "a3-megagpu-8g": {
        "description": "H100 80GB Mega (a3-megagpu-8g)",
        "accelerator_type": "nvidia-h100-mega-80gb",
        "accelerator_count": 8,
        "zones": [
            "us-east4-a",
            "us-central1-a",
            "us-central1-c",
            "us-central1-b",
            "us-east4-b",
            "us-east4-c",
            "us-east5-a",
            "us-west1-a",
            "us-west1-b",
            "us-west4-a",
            "europe-west4-b",
            "europe-west4-c",
            "europe-west1-b",
            "europe-west1-c",
        ],
    },
    "a3-edgegpu-8g": {
        "description": "H100 80GB Edge (a3-edgegpu-8g)",
        "accelerator_type": "nvidia-h100-80gb",
        "accelerator_count": 8,
        "zones": [
            "us-east4-a",
            "us-central1-a",
            "us-central1-c",
            "us-central1-b",
            "us-east4-b",
            "us-east1-b",
            "us-east1-c",
            "us-west1-a",
            "us-west1-b",
        ],
    },
    "a2-ultragpu-8g": {
        "description": "A100 80GB (a2-ultragpu-8g)",
        "accelerator_type": "nvidia-a100-80gb",
        "accelerator_count": 8,
        "zones": [
            "us-central1-a",
            "us-central1-c",
            "us-east4-c",
            "us-east5-a",
            "europe-west4-a",
            "asia-southeast1-c",
        ],
    },
    "g2-standard-4": {
        "description": "L4 24GB (g2-standard-4)",
        "accelerator_type": "nvidia-l4",
        "accelerator_count": 1,
        "zones": [
            "us-east4-a",
            "us-central1-a",
            "us-central1-c",
            "us-central1-b",
            "us-east4-c",
            "us-west1-a",
        ],
    },
}


def filter_zones(catalog_zones: list, regions_csv: str, custom_zones_csv: str) -> list:
    if custom_zones_csv.strip():
        return [z.strip() for z in custom_zones_csv.split(",") if z.strip()]
    allowed_regions = [r.strip() for r in regions_csv.split(",") if r.strip()]
    if not allowed_regions or "all" in [r.lower() for r in allowed_regions]:
        return list(catalog_zones)
    return [
        z for z in catalog_zones
        if z.rsplit("-", 1)[0] in allowed_regions
    ]


def build_tiers(
    machine_types_csv: str,
    regions_csv: str,
    custom_zones_csv: str,
    override_accel_type: str = "",
    override_accel_count: int = 0,
):
    tiers = []
    mt_list = [m.strip() for m in machine_types_csv.split(",") if m.strip()]
    for idx, mt in enumerate(mt_list, start=1):
        spec = ON_DEMAND_GPU_CATALOG.get(mt, {
            "description": mt,
            "accelerator_type": override_accel_type or "nvidia-h100-80gb",
            "accelerator_count": override_accel_count or 8,
            "zones": ON_DEMAND_GPU_CATALOG["a3-highgpu-8g"]["zones"],
        })
        accel_type = override_accel_type or spec["accelerator_type"]
        accel_count = override_accel_count or spec["accelerator_count"]
        zones = filter_zones(spec["zones"], regions_csv, custom_zones_csv)
        if zones:
            tiers.append({
                "name": f"Tier {idx}: {spec['description']}",
                "machine_type": mt,
                "accelerator_type": accel_type,
                "accelerator_count": accel_count,
                "zones": zones,
                "prefix": f"launchpad-reservations-{mt}",
            })
    return tiers


def log(msg: str):
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {msg}", flush=True)


class TokenManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._token = os.environ.get("GCP_ACCESS_TOKEN", "")
        self._fetched_at = time.time() if self._token else 0.0
        self._gcloud_bin = shutil.which("gcloud") or "gcloud"

    def get_token(self, force_refresh=False) -> str:
        with self._lock:
            now = time.time()
            if not force_refresh and self._token and (now - self._fetched_at) < 2700:
                return self._token
            for subcmd in (
                [self._gcloud_bin, "auth", "print-access-token"],
                [self._gcloud_bin, "auth", "application-default", "print-access-token"],
            ):
                proc = subprocess.run(subcmd, capture_output=True, text=True, check=False)
                if proc.returncode == 0 and proc.stdout.strip():
                    self._token = proc.stdout.strip()
                    self._fetched_at = now
                    return self._token
            raise RuntimeError(
                "Failed to obtain GCP access token. Run `gcloud auth login` or set GCP_ACCESS_TOKEN."
            )


def api_request(method: str, url: str, token: str, body: dict = None, timeout: int = 30):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {"error": {"message": raw[:300], "code": e.code}}
        return e.code, parsed
    except Exception as e:
        return 0, {"error": {"message": str(e)}}


def delete_reservation(zone: str, res_name: str, token: str):
    url = f"{COMPUTE_API_BASE}/projects/{PROJECT_ID}/zones/{zone}/reservations/{res_name}"
    status, _ = api_request("DELETE", url, token, timeout=30)
    log(f"  [Cleanup] Deleted excess parallel reservation '{res_name}' in {zone} (HTTP {status})")


def poll_zone_operation(zone: str, op_name: str, token_mgr: TokenManager):
    url = f"{COMPUTE_API_BASE}/projects/{PROJECT_ID}/zones/{zone}/operations/{op_name}"
    deadline = time.time() + OP_POLL_TIMEOUT_SECONDS
    while time.time() < deadline:
        time.sleep(OP_POLL_INTERVAL_SECONDS)
        status, data = api_request("GET", url, token_mgr.get_token(), timeout=15)
        if status != 200:
            continue
        op_status = data.get("status", "")
        if op_status == "DONE":
            if "error" in data:
                errs = data["error"].get("errors", [])
                err_code = errs[0].get("code", "ERROR") if errs else "ERROR"
                err_msg = errs[0].get("message", str(data["error"])) if errs else str(data["error"])
                return False, f"{err_code}: {err_msg}"
            return True, "DONE"
    return False, "OPERATION_POLL_TIMEOUT"


def build_reservation_payload(
    res_name: str,
    zone: str,
    vm_count: int,
    machine_type: str,
    accelerator_type: str,
    accelerator_count: int,
) -> dict:
    payload = {
        "name": res_name,
        "zone": zone,
        "specificReservationRequired": False,
        "specificReservation": {
            "count": str(vm_count),
            "instanceProperties": {
                "machineType": machine_type,
                "guestAccelerators": [
                    {
                        "acceleratorCount": accelerator_count,
                        "acceleratorType": accelerator_type,
                    }
                ],
            },
        },
        "reservationSharingPolicy": {
            "serviceShareType": "ALLOW_ALL",
        },
    }
    if SHARE_WITH_PROJECT:
        payload["shareSettings"] = {
            "shareType": "SPECIFIC_PROJECTS",
            "projectMap": {
                SHARE_WITH_PROJECT: {
                    "projectId": SHARE_WITH_PROJECT,
                }
            },
        }
    return payload


_seq_lock = threading.Lock()
_seq_counter = 0


def next_seq() -> int:
    global _seq_counter
    with _seq_lock:
        _seq_counter += 1
        return _seq_counter


def probe_zone_once(
    tier: dict,
    zone: str,
    vm_count: int,
    token_mgr: TokenManager,
):
    short_zone = zone.replace("us-", "").replace("-", "")
    ts_tag = datetime.datetime.now(datetime.timezone.utc).strftime("%m%d-%H%M%S")
    seq = next_seq()
    res_name = f"{tier['prefix']}-{short_zone}-{ts_tag}-{seq:02d}"[:63]
    url = f"{COMPUTE_API_BASE}/projects/{PROJECT_ID}/zones/{zone}/reservations"
    payload = build_reservation_payload(
        res_name=res_name,
        zone=zone,
        vm_count=vm_count,
        machine_type=tier["machine_type"],
        accelerator_type=tier["accelerator_type"],
        accelerator_count=tier["accelerator_count"],
    )

    status, data = api_request("POST", url, token_mgr.get_token(), body=payload, timeout=30)

    if status in (200, 201):
        op_name = data.get("name", "")
        if data.get("status") == "DONE" and "error" not in data:
            return True, zone, res_name, vm_count, "Provisioned immediately", False
        if op_name:
            ok, reason = poll_zone_operation(zone, op_name, token_mgr)
            return ok, zone, res_name, vm_count, reason, False
        return False, zone, res_name, vm_count, "Missing operation name", False

    err = data.get("error", {})
    msg = err.get("message", str(data))
    msg_lower = msg.lower()
    is_rate_limit = (
        status == 429
        or "ratelimitexceeded" in msg_lower
        or "rate_limit_exceeded" in msg_lower
        or "rate limit" in msg_lower
    )
    return False, zone, res_name, vm_count, f"HTTP {status}: {msg[:140]}", is_rate_limit


def run_parallel_wave(
    tier: dict,
    vm_count: int,
    needed_vms: int,
    already_secured_vms: int,
    token_mgr: TokenManager,
):
    zones = list(tier["zones"])
    zone_rank = {z: idx for idx, z in enumerate(zones)}
    successes = []
    dead_zones = set()
    quota_full_regions = set()
    rate_limited = False
    wave_lock = threading.Lock()
    wave_secured = 0

    def worker(z: str):
        nonlocal wave_secured, rate_limited
        burst_idx = 0
        while True:
            with wave_lock:
                if wave_secured >= needed_vms:
                    return
            ok, zone, res_name, count, reason, rl = probe_zone_once(
                tier, z, vm_count, token_mgr
            )
            if rl:
                with wave_lock:
                    rate_limited = True
            if ok:
                with wave_lock:
                    wave_secured += count
                    total_now = already_secured_vms + wave_secured
                    successes.append((zone, res_name, count))
                tag = "[HIT]" if burst_idx == 0 else "[HOT-ZONE HIT!]"
                log(
                    f"  {tag} {zone}: Locked in {count}x {tier['machine_type']} -> {res_name} "
                    f"(Grabbed {min(total_now, TARGET_VM_COUNT)}/{TARGET_VM_COUNT} VMs — active & ready now!)"
                )
                burst_idx += 1
                with wave_lock:
                    if wave_secured < needed_vms:
                        log(f"  [HOT-ZONE BURST] Immediately re-probing winning zone {zone} for {vm_count} more VM(s) (0s wait)...")
                        continue
                return
            else:
                reason_lower = reason.lower()
                if (
                    "limit: 0.0" in reason_lower
                    or "not supported for on-demand reservations" in reason_lower
                    or "machine type with name" in reason_lower
                ):
                    with wave_lock:
                        dead_zones.add(zone)
                elif "quota_exceeded" in reason_lower or "quota" in reason_lower and "exceeded" in reason_lower:
                    if vm_count == 1:
                        with wave_lock:
                            quota_full_regions.add(zone.rsplit("-", 1)[0])
                short_reason = (
                    f"STOCKOUT ({reason})"
                    if ("exhausted" in reason_lower or "stockout" in reason_lower or "does not have enough" in reason_lower)
                    else reason
                )
                if burst_idx == 0:
                    log(f"  [MISS] {zone} ({count}x {tier['machine_type']}): {short_reason}")
                else:
                    log(f"  [HOT-ZONE DONE] {zone} follow-up ended: {short_reason}")
                return

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(zones))) as pool:
        futures = [pool.submit(worker, z) for z in zones]
        for fut in concurrent.futures.as_completed(futures):
            fut.result()

    if dead_zones:
        tier["zones"] = [z for z in tier["zones"] if z not in dead_zones]
        log(f"  [Auto-Prune] Dropped {len(dead_zones)} zero-quota/unsupported zone(s): {', '.join(sorted(dead_zones))}")

    if quota_full_regions:
        before_cnt = len(tier["zones"])
        tier["zones"] = [z for z in tier["zones"] if z.rsplit("-", 1)[0] not in quota_full_regions]
        dropped_cnt = before_cnt - len(tier["zones"])
        if dropped_cnt > 0:
            log(f"  [Quota-Full] Regional quota reached in {', '.join(sorted(quota_full_regions))}; continuing in remaining regions.")

    successes.sort(key=lambda item: zone_rank.get(item[0], 999))
    return successes, rate_limited


def parse_args():
    global PROJECT_ID, SHARE_WITH_PROJECT, TARGET_VM_COUNT, PER_RESERVATION_VM_COUNT
    global DEFAULT_MACHINE_TYPES, DEFAULT_ACCELERATOR_TYPE, DEFAULT_ACCELERATOR_COUNT
    global DEFAULT_REGIONS, CUSTOM_ZONES
    global MAX_WAVES, WAVE_INTERVAL_SECONDS, MAX_HUNT_HOURS
    parser = argparse.ArgumentParser(description="Ultra-Fast 1-VM Sniper & Multi-Zone Parallel GPU Hunter")
    parser.add_argument("--project", default=PROJECT_ID, help="Target GCP Host Project ID (default: prj-d-gai-launchpad)")
    parser.add_argument("--share-with", default=SHARE_WITH_PROJECT, help="Project ID to share reservation with (default: prj-d-gai-aipgaisvc-1)")
    parser.add_argument(
        "--machine-types",
        default=DEFAULT_MACHINE_TYPES,
        help="Comma-separated GPU machine types to hunt in priority order (default: a3-highgpu-8g; e.g. a3-highgpu-8g,a3-megagpu-8g)",
    )
    parser.add_argument(
        "--accelerator-type",
        default=DEFAULT_ACCELERATOR_TYPE,
        help="Optional override for accelerator type (default: auto-resolved from machine type, e.g. nvidia-h100-80gb)",
    )
    parser.add_argument(
        "--accelerator-count",
        type=int,
        default=DEFAULT_ACCELERATOR_COUNT,
        help="Optional override for accelerator count per VM (default: auto-resolved from machine type, e.g. 8)",
    )
    parser.add_argument(
        "--regions",
        default=DEFAULT_REGIONS,
        help="Comma-separated GCP regions to hunt across (default: us-east4,us-central1)",
    )
    parser.add_argument(
        "--zones",
        default=CUSTOM_ZONES,
        help="Optional comma-separated explicit zone list (overrides --regions)",
    )
    parser.add_argument(
        "--target-vms",
        type=int,
        default=TARGET_VM_COUNT,
        help="Total new VMs to reserve (default: 9, i.e. 12 desired minus 3 already secured; every 1-VM hit is kept immediately)",
    )
    parser.add_argument(
        "--per-reservation-vms",
        type=int,
        default=PER_RESERVATION_VM_COUNT,
        help="VMs per individual reservation request (default: 1 so every single available node is grabbed immediately)",
    )
    parser.add_argument("--max-waves", type=int, default=MAX_WAVES, help="Stop after N waves (0 = run until target met)")
    parser.add_argument("--max-hunt-hours", type=float, default=MAX_HUNT_HOURS, help="Max hours to keep hunting (default: 24)")
    parser.add_argument("--wave-interval", type=float, default=WAVE_INTERVAL_SECONDS, help="Seconds between waves (default: 20)")
    args, _ = parser.parse_known_args()

    PROJECT_ID = args.project
    SHARE_WITH_PROJECT = args.share_with
    DEFAULT_MACHINE_TYPES = args.machine_types
    DEFAULT_ACCELERATOR_TYPE = args.accelerator_type
    DEFAULT_ACCELERATOR_COUNT = args.accelerator_count
    DEFAULT_REGIONS = args.regions
    CUSTOM_ZONES = args.zones
    TARGET_VM_COUNT = args.target_vms
    PER_RESERVATION_VM_COUNT = args.per_reservation_vms
    MAX_WAVES = args.max_waves
    MAX_HUNT_HOURS = args.max_hunt_hours
    WAVE_INTERVAL_SECONDS = args.wave_interval


def print_summary(secured_reservations: list, secured_vms: int, interrupted: bool = False):
    log("\n" + "=" * 78)
    if secured_vms >= TARGET_VM_COUNT:
        log(f"SUCCESS! Secured all {secured_vms}/{TARGET_VM_COUNT} requested GPU VMs:")
    elif interrupted:
        log(f"STOPPED BY USER (Ctrl+C): Kept {secured_vms}/{TARGET_VM_COUNT} GPU VMs grabbed so far:")
    else:
        log(f"HUNTING ENDED: Kept {secured_vms}/{TARGET_VM_COUNT} GPU VMs grabbed so far:")

    if secured_reservations:
        for r in secured_reservations:
            log(f"  - ACTIVE Reservation: {r['name']} | Zone: {r['zone']} | {r['vm_count']}x {r['machine_type']}")
    else:
        log("  - (No new reservations secured yet)")
    log("=" * 78)


def main():
    parse_args()
    token_mgr = TokenManager()
    tiers = build_tiers(
        DEFAULT_MACHINE_TYPES,
        DEFAULT_REGIONS,
        CUSTOM_ZONES,
        DEFAULT_ACCELERATOR_TYPE,
        DEFAULT_ACCELERATOR_COUNT,
    )

    log("=" * 78)
    log(f"Starting Ultra-Fast 1-VM Sniper & Multi-Zone Parallel GPU Hunter")
    log(f"  Host Project:        {PROJECT_ID}")
    log(f"  Shared With:         {SHARE_WITH_PROJECT or '(None - single project)'} (ALLOW_ALL)")
    log(f"  Machine Types:       {DEFAULT_MACHINE_TYPES}")
    log(f"  Target Regions:      {CUSTOM_ZONES if CUSTOM_ZONES else DEFAULT_REGIONS}")
    log(f"  Target Quantity:     Up to {TARGET_VM_COUNT} new VMs (grabbing {PER_RESERVATION_VM_COUNT} VM at a time — incremental lock-in)")
    log(f"  Incremental Grab:    Every 1-VM hit is immediately ACTIVE & KEPT (safe to Ctrl+C anytime)")
    log(f"  Hot-Zone Double-Tap: ENABLED (in-thread 0s burst without waiting for slow zones)")
    log(f"  Wave Interval:       {WAVE_INTERVAL_SECONDS}s (+0..{JITTER_SECONDS}s jitter) | Max Hunt: {MAX_HUNT_HOURS}h")
    for t in tiers:
        log(f"  {t['name']} ({t['accelerator_count']}x {t['accelerator_type']}): {', '.join(t['zones'])}")
    log("=" * 78)

    try:
        token_mgr.get_token()
        log("Authentication verified. Starting parallel hunting waves...")
    except Exception as e:
        log(f"FATAL: {e}")
        sys.exit(1)

    secured_reservations = []
    secured_vms = 0
    wave_num = 0
    backoff_multiplier = 1.0
    start_time = time.time()
    deadline = start_time + (MAX_HUNT_HOURS * 3600) if MAX_HUNT_HOURS > 0 else float("inf")

    stop_requested = False

    def handle_sig(signum, frame):
        nonlocal stop_requested
        stop_requested = True
        log("\nSignal received — finishing current in-flight check and keeping all reservations grabbed so far...")

    signal.signal(signal.SIGINT, handle_sig)
    signal.signal(signal.SIGTERM, handle_sig)

    try:
        while secured_vms < TARGET_VM_COUNT and time.time() < deadline and not stop_requested:
            wave_num += 1
            remaining = TARGET_VM_COUNT - secured_vms
            log(f"\n--- Wave #{wave_num} | Grabbed {secured_vms}/{TARGET_VM_COUNT} new VMs so far (hunting up to {remaining} more) ---")

            any_rate_limit = False
            active_tiers = [t for t in tiers if t["zones"]]
            if not active_tiers:
                log("STOPPED: All candidate zones reached quota limit or were pruned.")
                break

            for tier in active_tiers:
                if secured_vms >= TARGET_VM_COUNT or stop_requested:
                    break

                needed = TARGET_VM_COUNT - secured_vms
                req_count = min(PER_RESERVATION_VM_COUNT, needed)
                log(f"Probing {tier['name']} ({req_count}x {tier['machine_type']} per zone) across {len(tier['zones'])} zones in parallel...")
                hits, rl = run_parallel_wave(tier, req_count, needed, secured_vms, token_mgr)
                if rl:
                    any_rate_limit = True

                for zone, res_name, count in hits:
                    if secured_vms + count <= TARGET_VM_COUNT:
                        secured_vms += count
                        secured_reservations.append({
                            "zone": zone,
                            "name": res_name,
                            "machine_type": tier["machine_type"],
                            "vm_count": count,
                        })
                    else:
                        delete_reservation(zone, res_name, token_mgr.get_token())

            if secured_vms >= TARGET_VM_COUNT or stop_requested:
                break
            if MAX_WAVES > 0 and wave_num >= MAX_WAVES:
                break

            if any_rate_limit:
                backoff_multiplier = min(backoff_multiplier * 1.8, 4.0)
                wait_secs = (WAVE_INTERVAL_SECONDS * backoff_multiplier) + random.uniform(1, 5)
                log(f"Rate limit detected — backing off for {wait_secs:.1f}s...")
            else:
                backoff_multiplier = max(1.0, backoff_multiplier * 0.8)
                wait_secs = WAVE_INTERVAL_SECONDS + random.uniform(0, JITTER_SECONDS)
                log(f"Wave #{wave_num} complete ({secured_vms}/{TARGET_VM_COUNT} grabbed so far). Sleeping {wait_secs:.1f}s before next wave...")

            for _ in range(int(wait_secs * 2)):
                if stop_requested:
                    break
                time.sleep(0.5)
    except KeyboardInterrupt:
        stop_requested = True

    print_summary(secured_reservations, secured_vms, interrupted=stop_requested)
    sys.exit(0 if secured_vms > 0 else 1)


if __name__ == "__main__":
    main()
