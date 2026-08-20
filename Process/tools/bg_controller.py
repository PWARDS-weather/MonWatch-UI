#!/usr/bin/env python3
"""
MONWATCH  --  CYCLONE  V3.5.0
bg_controller.py  --  Intelligent parallel controller for bg_to_nc.py

Detects CPU topology (logical cores, physical cores, SMT threads per core),
distributes datetime folders across parallel bg_to_nc.py subprocesses,
and aggregates results.

CPU formula:
    logical = os.cpu_count()
    physical = psutil.cpu_count(logical=False)
    threads_per_core = logical / physical
    use half the physical cores
    num_procs = active_physical * threads_per_core   (subprocess count)
    each proc gets --workers = threads_per_core        (intra-process threads)
"""
import sys
import os
import argparse
import subprocess
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False

from bg_to_nc import find_datetime_folders


def detect_optimal_parallelism() -> tuple:
    """Detect CPU topology and compute optimal process/thread distribution.

    Returns (num_procs, workers_per_proc):
        num_procs       - number of parallel bg_to_nc.py subprocesses
        workers_per_proc - --workers N passed to each subprocess
    """
    logical = os.cpu_count() or 4

    if _HAS_PSUTIL:
        physical = psutil.cpu_count(logical=False)
        if physical and physical > 0:
            threads_per_core = max(1, logical // physical)
        else:
            physical = logical // 2
            threads_per_core = 2
    else:
        physical = logical // 2
        threads_per_core = 2

    active_physical = max(1, physical // 2)
    num_procs = active_physical * threads_per_core
    workers_per_proc = threads_per_core

    return num_procs, workers_per_proc


def worker_task(
    worker_id: int,
    assigned_folders: list,
    bg_to_nc_path: Path,
    no_ads: bool,
    keep_dat: bool,
    fast: bool,
    workers_per_proc: int,
    output_lock: threading.Lock,
    results: list,
):
    """Run bg_to_nc.py on each assigned folder sequentially.

    Prefixes output with [W{n}] for traceability.
    Collects per-folder "Processed: X" lines and appends
    (worker_id, folder_count, total_bands) to results.
    """
    prefix = f"[W{worker_id}]"
    folder_count = len(assigned_folders)
    total_bands = 0
    total_failed = 0

    for folder in assigned_folders:
        cmd = [sys.executable, str(bg_to_nc_path), "-i", str(folder),
               "--workers", str(workers_per_proc)]
        if no_ads:
            cmd.append("--no-ads")
        if keep_dat:
            cmd.append("--keep")
        if fast:
            cmd.append("--fast")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            )

            with output_lock:
                for line in result.stdout.split('\n'):
                    line = line.strip()
                    if not line:
                        continue
                    if "STATISTICS_OUTPUT" in line or line.startswith("==="):
                        continue
                    if "Failed:" in line:
                        try:
                            n = int(line.split(':')[1].strip())
                            total_failed += n
                        except (ValueError, IndexError):
                            print(f"{prefix} {line}")
                    elif "Processed:" in line and "RGB" not in line and "products" not in line:
                        try:
                            n = int(line.split(':')[1].strip())
                            total_bands += n
                        except (ValueError, IndexError):
                            print(f"{prefix} {line}")
                    else:
                        print(f"{prefix} {line}")

                if result.stderr.strip():
                    for line in result.stderr.split('\n'):
                        line = line.strip()
                        if line:
                            print(f"{prefix} STDERR: {line}")

        except Exception as e:
            with output_lock:
                print(f"{prefix} ERROR processing {folder.name}: {e}")

    results.append((worker_id, folder_count, total_bands, total_failed))


def main():
    parser = argparse.ArgumentParser(
        description="Cyclone V3.5.0 - bg_controller: parallel bg_to_nc.py dispatcher"
    )
    parser.add_argument("-i", "--input", required=True,
                        help="Directory containing AHI-L1b-* datetime folders")
    parser.add_argument("--no-ads", action="store_true",
                        help="Skip ADS sidecar generation (passed to bg_to_nc.py)")
    parser.add_argument("--keep", action="store_true",
                        help="Keep .dat files after conversion (passed to bg_to_nc.py)")
    parser.add_argument("--fast", action="store_true",
                        help="Fast mode: pass --fast (complevel=2) to each bg_to_nc.py")
    parser.add_argument("--low-io", action="store_true",
                        help="Low I/O mode: run ONE folder at a time with ONE band worker each "
                             "(sequential, prevents 100% disk I/O — slow but safe)")
    parser.add_argument("--workers", type=int, default=None,
                        help="Override total subprocess count (auto-detected if omitted)")
    args = parser.parse_args()

    input_dir = Path(args.input)
    if not input_dir.exists():
        print(f"[ERROR] Directory not found: {input_dir}")
        sys.exit(1)

    folders = find_datetime_folders(input_dir)
    if not folders:
        print(f"[ERROR] No supported AHI-L1b-* folders found in {input_dir}")
        sys.exit(1)

    # -- Detect parallelism --
    auto_procs, workers_per_proc = detect_optimal_parallelism()
    num_procs = args.workers if args.workers is not None else auto_procs
    num_procs = max(1, min(num_procs, len(folders)))

    # -- Low I/O mode: fully sequential (1 folder x 1 band worker). Disk-friendly. --
    if args.low_io:
        num_procs = 1
        workers_per_proc = 1
        print("[Low I/O] Enabled — 1 process / 1 worker (sequential, disk-friendly). Slow but won't max out disk I/O.")

    log_cpu = f"[CPU] Logical: {os.cpu_count() or '?'}"
    if _HAS_PSUTIL:
        phys = psutil.cpu_count(logical=False)
        log_cpu += f"  Physical: {phys or '?'}"
        if phys:
            log_cpu += f"  SMT: {os.cpu_count() // phys}"
    log_cpu += f"  Procs: {num_procs}  --workers: {workers_per_proc}"
    print(log_cpu)
    print(f"[FOLDERS] {len(folders)} found  ->  distributing across {num_procs} worker(s)")

    # -- Round-robin distribution --
    distributed = [[] for _ in range(num_procs)]
    for i, folder in enumerate(folders):
        distributed[i % num_procs].append(folder)

    for wid, assigned in enumerate(distributed):
        if assigned:
            names = "  ".join(f.name for f in assigned)
            print(f"[W{wid}] {len(assigned)} folder(s): {names}")

    bg_to_nc_path = Path(__file__).resolve().parent / "bg_to_nc.py"
    output_lock = threading.Lock()
    results = []

    # -- Launch workers --
    with ThreadPoolExecutor(max_workers=num_procs) as pool:
        futures = [
            pool.submit(
                worker_task, wid, assigned, bg_to_nc_path,
                args.no_ads, args.keep, args.fast, workers_per_proc,
                output_lock, results
            )
            for wid, assigned in enumerate(distributed) if assigned
        ]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                print(f"[ERROR] Worker crashed: {e}")

    # -- Aggregate --
    total_processed = sum(r[2] for r in results)
    total_folders = sum(r[1] for r in results)
    total_failed = sum(r[3] for r in results)

    print()
    print("=" * 70)
    print(f"CONTROLLER SUMMARY: {len(results)} workers, "
          f"{total_folders} folders, {total_processed} bands processed, {total_failed} failed")
    print("=" * 70)
    print("STATISTICS_OUTPUT:")
    print(f"Processed: {total_processed}")
    print(f"Failed: {total_failed}")


if __name__ == "__main__":
    main()
