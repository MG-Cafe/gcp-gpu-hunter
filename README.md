# GCP On-Demand GPU Reservation Hunter (`fast_gpu_hunter`)

Ultra-fast **1-VM sniper** & **multi-zone parallel** On-Demand GPU reservation hunter for Google Cloud (`a3-highgpu-8g`, `a3-megagpu-8g`, `a3-edgegpu-8g`, `a2-ultragpu-8g`, `g4-standard-*`, `g2-standard-*`).

- **Default Project:** `prj-d-gai-launchpad` (shared with `prj-d-gai-aipgaisvc-1`, `ALLOW_ALL`)
- **Default Chip:** `a3-highgpu-8g` (`8x nvidia-h100-80gb`)
- **Default Regions:** `us-east4,us-central1` (automatically probes all valid zones in parallel: `us-east4-a, us-central1-a, us-central1-c, us-east4-b`)
- **Default Target:** `3` VMs total, hunted `1` VM at a time (`--vm-count=1`) with instant 0-second hot-zone double-tap

---

## Quick Start (1-Line Run)

```bash
git clone https://github.com/MG-Cafe/gcp-gpu-hunter.git
cd gcp-gpu-hunter
gcloud auth login
```

### Option 1: Run Python Direct REST Edition (Recommended)

```bash
# Run with default customer config (a3-highgpu-8g across us-east4 & us-central1, 3 VMs total):
python3 fast_gpu_hunter.py
```

```bash
# Hunt both H100 Standard (a3-highgpu-8g) and H100 Mega (a3-megagpu-8g):
python3 fast_gpu_hunter.py --machine-types a3-highgpu-8g,a3-megagpu-8g --regions us-east4,us-central1 --target-vms 3
```

```bash
# Or specify exact zones:
python3 fast_gpu_hunter.py --machine-types a3-highgpu-8g --zones us-east4-a,us-central1-a,us-central1-c --target-vms 3
```

```bash
# Run in background (survives terminal disconnect):
nohup python3 fast_gpu_hunter.py > gpu_hunter.log 2>&1 &
tail -f gpu_hunter.log
```

---

### Option 2: Run Bash `gcloud` Edition

```bash
chmod +x fast_gpu_hunter.sh
./fast_gpu_hunter.sh
```

```bash
# Custom chips or regions:
./fast_gpu_hunter.sh --machine-types a3-highgpu-8g,a3-megagpu-8g --regions us-east4,us-central1 --target-vms 3
```
