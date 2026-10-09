# GCP On-Demand GPU Reservation Hunter (`fast_gpu_hunter`)

Ultra-fast **1-VM sniper** & **multi-zone parallel** On-Demand GPU reservation hunter for Google Cloud (`a3-highgpu-8g`, `a3-megagpu-8g`, `a3-edgegpu-8g`, `a2-ultragpu-8g`, `g4-standard-*`, `g2-standard-*`).

- **Default Project:** `prj-d-gai-launchpad` (shared with `prj-d-gai-aipgaisvc-1`, `ALLOW_ALL`)
- **Default Chip:** `a3-highgpu-8g` (`8x nvidia-h100-80gb`)
- **Default Regions:** `us-east4,us-central1` (automatically probes all valid zones in parallel: `us-east4-a, us-central1-a, us-central1-c, us-east4-b`)
- **Default Target:** Up to **`9` new VMs** (`12` desired total minus `3` already secured; override with `--target-vms 12`), hunted **`1` VM at a time (`--vm-count=1`)** with **instant incremental lock-in** (every single 1-VM reservation hit is immediately active and kept, even if stopped early with `Ctrl+C`)

---

## Quick Start (1-Line Run)

```bash
# Option A: 1-Line Download & Run (Python Direct REST Edition — Recommended):
curl -sSLO https://raw.githubusercontent.com/MG-Cafe/gcp-gpu-hunter/main/fast_gpu_hunter.py && python3 fast_gpu_hunter.py
```

```bash
# Option B: git clone & Run:
git clone https://github.com/MG-Cafe/gcp-gpu-hunter.git
cd gcp-gpu-hunter
gcloud auth login
python3 fast_gpu_hunter.py
```

---

## Custom Runs (Chips, Regions, Zones, Target VMs)

```bash
# Hunt up to 9 more VMs (or pass --target-vms 12) across both H100 Standard & H100 Mega:
python3 fast_gpu_hunter.py --machine-types a3-highgpu-8g,a3-megagpu-8g --regions us-east4,us-central1 --target-vms 9

# Specify exact zones:
python3 fast_gpu_hunter.py --machine-types a3-highgpu-8g --zones us-east4-a,us-central1-a,us-central1-c --target-vms 9

# Run in background (survives terminal disconnect; every 1-VM hit is immediately kept):
nohup python3 fast_gpu_hunter.py > gpu_hunter.log 2>&1 &
tail -f gpu_hunter.log
```

---

## Bash `gcloud` Edition

```bash
chmod +x fast_gpu_hunter.sh
./fast_gpu_hunter.sh

# Or custom chips / regions / target VMs:
./fast_gpu_hunter.sh --machine-types a3-highgpu-8g,a3-megagpu-8g --regions us-east4,us-central1 --target-vms 9
```
