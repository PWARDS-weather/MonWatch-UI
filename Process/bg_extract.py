#!/usr/bin/env python3
"""
Himawari AHI Satellite Data Processor - EXTRACTION ONLY
Extracts all .bz2 files with concurrent processing (matches download concurrency)
"""

import sys
import os
import bz2
from pathlib import Path
from typing import Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed


def extract_single_file(bz2_file: Path) -> Tuple[bool, str]:
    """
    Extract a single .bz2 file and delete original
    Returns: (success, filename)
    """
    try:
        filename = bz2_file.name
        dat_path = bz2_file.with_suffix('')
        
        # Skip if already extracted
        if dat_path.exists():
            print(f"[~] Already extracted: {filename}")
            try:
                bz2_file.unlink()  # Delete original
            except:
                pass
            return True, filename
        
        print(f"[+] Extracting: {filename}")
        
        # Extract file
        with bz2.open(bz2_file, 'rb') as f_in:
            data = f_in.read()
        
        with open(dat_path, 'wb') as f_out:
            f_out.write(data)
        
        # Delete original
        bz2_file.unlink()
        
        print(f"[OK] Extracted and deleted: {filename}")
        return True, filename
        
    except Exception as e:
        print(f"[!] Failed to extract {bz2_file.name}: {str(e)}")
        return False, bz2_file.name


def extract_bz2_files(input_dir: Path, max_workers: int = 8) -> Tuple[int, int]:
    """
    Extract all .bz2 files concurrently and delete originals
    Returns: (success_count, total_count)
    """
    print(f"[+] Extracting .bz2 files (max {max_workers} concurrent)...")
    
    # Find all .bz2 files recursively
    bz2_files = list(input_dir.rglob("*.bz2"))
    
    if not bz2_files:
        print("[!] No .bz2 files found")
        return 0, 0
    
    print(f"[+] Found {len(bz2_files)} .bz2 files to extract")
    
    success_count = 0
    total_count = len(bz2_files)
    
    # Use ThreadPoolExecutor for concurrent extraction
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(extract_single_file, f): f for f in bz2_files}
        
        for future in as_completed(futures):
            success, filename = future.result()
            if success:
                success_count += 1
    
    print(f"[OK] Extraction complete: {success_count}/{total_count} files")
    return success_count, total_count


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Extract Himawari AHI .bz2 files")
    parser.add_argument("-i", "--input", required=True, help="Input directory containing .bz2 files")
    parser.add_argument("--keep", action="store_true", help="Keep original .bz2 files (don't delete)")
    parser.add_argument("--max-workers", type=int, default=8, help="Maximum concurrent extraction workers (matches download concurrency)")
    
    args = parser.parse_args()
    
    input_dir = Path(args.input)
    
    if not input_dir.exists():
        print(f"[!] Input directory does not exist: {input_dir}")
        sys.exit(1)
    
    print("\n" + "="*60)
    print("HIMAWARI AHI DATA EXTRACTOR")
    print("="*60)
    print(f"Input directory: {input_dir}")
    print(f"Max concurrent workers: {args.max_workers}")
    print("="*60)
    
    success, total = extract_bz2_files(input_dir, args.max_workers)
    
    print("\n" + "="*60)
    print("EXTRACTION SUMMARY")
    print("="*60)
    print(f"Successfully extracted: {success}/{total} files")
    
    # Output for GUI/script chaining
    print("\nSTATISTICS_OUTPUT:")
    print(f"Extracted: {success}")
    print(f"Total to extract: {total}")


if __name__ == "__main__":
    main()
