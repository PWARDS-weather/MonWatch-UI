#!/usr/bin/env python3
"""
Himawari AHI Satellite Data Processor - DECODER ONLY (SAFER VERSION)
Combines segmented .dat files into single GeoTIFF per band

CHANGES:
- Only deletes .dat files AFTER successful .tif creation
- Now checks if ANY .tif files exist in the folder before deleting .dat files
- Shows full errors when Satpy fails
- Fixed print statement encoding issues
"""

import sys
import re
import traceback
from pathlib import Path
from typing import Tuple, Dict, List
from collections import defaultdict

try:
    from satpy import Scene
    SATPY_AVAILABLE = True
except ImportError:
    SATPY_AVAILABLE = False
    print("[!] ERROR: satpy is required for processing. Install with: pip install satpy")
    sys.exit(1)


def find_datetime_folders(base_dir: Path) -> List[Path]:
    datetime_folders = []
    for item in base_dir.iterdir():
        if item.is_dir() and "AHI-L1b-FLDK_" in item.name:
            datetime_folders.append(item)
    
    if not datetime_folders:
        for subdir in base_dir.iterdir():
            if subdir.is_dir():
                for item in subdir.iterdir():
                    if item.is_dir() and "AHI-L1b-FLDK_" in item.name:
                        datetime_folders.append(item)
    return datetime_folders


def group_dat_files(datetime_folder: Path) -> Dict[tuple, List[Path]]:
    dat_files = []
    for ext in ['.dat', '.DAT']:
        dat_files.extend(list(datetime_folder.rglob(f"*{ext}")))
    
    if not dat_files:
        return {}

    print(f"[+] Found {len(dat_files)} .dat files in {datetime_folder.name}")

    grouped = defaultdict(list)
    for dat_file in dat_files:
        filename = dat_file.name
        # Main pattern
        pattern = r'HS_H\d{2}_(\d{8})_(\d{4})_B(\d{2})_FLDK_R\d+_S\d+\.(?:dat|DAT)'
        match = re.match(pattern, filename, re.IGNORECASE)
        if match:
            date_str = match.group(1)
            time_str = match.group(2)
            band = match.group(3)
            key = (date_str, time_str, band)
            grouped[key].append(dat_file)
            continue

        # Fallback pattern
        pattern2 = r'HS_H\d{2}_(\d{8})_(\d{4})_B(\d{2})_FLDK_R\d+\.(?:dat|DAT)'
        match2 = re.match(pattern2, filename, re.IGNORECASE)
        if match2:
            date_str = match2.group(1)
            time_str = match2.group(2)
            band = match2.group(3)
            key = (date_str, time_str, band)
            grouped[key].append(dat_file)
        else:
            print(f"[~] Could not parse filename: {filename}")

    return grouped


def has_tiff_files(folder: Path) -> bool:
    """Check if there is at least one reasonable-sized .tif file in the folder"""
    for tif in folder.glob("*.tif"):
        if tif.stat().st_size > 10000:  # ignore tiny/empty files
            return True
    return False


def process_datetime_folder(datetime_folder: Path, bands_to_process: List[str] = None, keep: bool = False) -> Tuple[int, int]:
    print(f"\n[+] Processing folder: {datetime_folder.name}")
    grouped = group_dat_files(datetime_folder)
    
    if not grouped:
        print(f"[!] No valid .dat files found in {datetime_folder.name}")
        return 0, 0

    print(f"[+] Found {len(grouped)} band/time combinations")

    success_count = 0
    deleted_files_count = 0

    for (date_str, time_str, band), files in grouped.items():
        try:
            if bands_to_process and band not in bands_to_process:
                continue

            # Sort segments by segment number
            def get_segment_num(f):
                match = re.search(r'_S(\d{4})\.', str(f))
                return int(match.group(1)) if match else 0

            files.sort(key=lambda x: get_segment_num(x.name))

            output_file = datetime_folder / f"B{band}.tif"

            # Skip if good TIFF already exists
            if output_file.exists() and output_file.stat().st_size > 10000:
                print(f"[~] Already exists: {output_file.name}")
                success_count += 1
                continue

            print(f"[+] Processing B{band} ({len(files)} segments) -> {output_file.name}")

            # === Satpy processing ===
            try:
                scn = Scene(reader='ahi_hsd', filenames=[str(f) for f in files])
                scn.load([f"B{int(band):02d}"])
                scn.save_dataset(f"B{int(band):02d}", str(output_file), writer='geotiff')
                print(f"[OK] Successfully created: {output_file.name}")
                success_count += 1
            except Exception as satpy_error:
                print(f"[!] Satpy failed for B{band}: {str(satpy_error)}")
                print("Full traceback:")
                traceback.print_exc()
                continue

        except Exception as e:
            print(f"[!] FAILED to process B{band}: {str(e)}")
            print("Full traceback:")
            traceback.print_exc()
            continue

    # ====================== DELETION LOGIC ======================
    if not keep:
        if has_tiff_files(datetime_folder):
            print(f"[~] At least one .tif file found -> Proceeding to delete .dat files")
            for dat_file in datetime_folder.rglob("*.dat"):
                try:
                    dat_file.unlink()
                    deleted_files_count += 1
                    print(f"[~] Deleted: {dat_file.name}")
                except Exception as e:
                    print(f"[~] Could not delete {dat_file.name}: {e}")
            for dat_file in datetime_folder.rglob("*.DAT"):
                try:
                    dat_file.unlink()
                    deleted_files_count += 1
                    print(f"[~] Deleted: {dat_file.name}")
                except Exception as e:
                    print(f"[~] Could not delete {dat_file.name}: {e}")
        else:
            print("[!] No valid .tif files were created -> Keeping all .dat files for safety")
    else:
        print("[~] --keep enabled: .dat files were not deleted")

    if deleted_files_count > 0:
        print(f"[~] Deleted {deleted_files_count} .dat/.DAT files from {datetime_folder.name}")

    return success_count, len(grouped)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Himawari AHI Decoder - Safer Version")
    parser.add_argument("-i", "--input", required=True, help="Input directory")
    parser.add_argument("--bands", help="Comma-separated bands (e.g. 01,02,03)")
    parser.add_argument("--keep", action="store_true", help="Do NOT delete .dat files")
    args = parser.parse_args()

    input_dir = Path(args.input)
    if not input_dir.exists():
        print(f"[!] Input directory does not exist: {input_dir}")
        sys.exit(1)

    print("\n" + "="*70)
    print("HIMAWARI AHI DECODER - SAFER VERSION")
    print("="*70)
    print(f"Input: {input_dir}")
    print(f"Keep .dat files: {args.keep}")
    print("="*70)

    datetime_folders = find_datetime_folders(input_dir)
    if not datetime_folders:
        # Check if input itself contains .dat files
        dat_files = list(input_dir.rglob("*.dat")) + list(input_dir.rglob("*.DAT"))
        if dat_files:
            datetime_folders = [input_dir]
        else:
            print("[!] No AHI-L1b-FLDK folders or .dat files found")
            sys.exit(1)

    bands_to_process = None
    if args.bands:
        bands_to_process = [b.strip().zfill(2) for b in args.bands.split(',')]

    total_success = 0
    total_groups = 0

    for folder in datetime_folders:
        success, groups = process_datetime_folder(folder, bands_to_process, keep=args.keep)
        total_success += success
        total_groups += groups

    print("\n" + "="*70)
    print("DECODING SUMMARY")
    print("="*70)
    print(f"Folders processed : {len(datetime_folders)}")
    print(f"Successful bands  : {total_success}/{total_groups}")

    if total_success == 0:
        print("\n[!] WARNING: No bands were successfully decoded!")
        print("[!] .dat files were NOT deleted (safe mode)")

    # ------------------------- ADDED FOR GUI COMPATIBILITY -------------------------
    # These lines match the exact format expected by Process_dat.py's parse_script_output()
    print(f"Successfully processed: {total_success}")
    print(f"Processed groups: {total_success}, Total groups: {total_groups}")
    # -------------------------------------------------------------------------------

    print("\nSTATISTICS_OUTPUT:")
    print(f"Processed folders: {len(datetime_folders)}")
    print(f"Processed groups: {total_success}")
    print(f"Total groups: {total_groups}")


if __name__ == "__main__":
    main()
