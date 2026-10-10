#!/usr/bin/env python3
"""
populate_faa_aircraft.py - FAA Aircraft Registry Database Loader

Downloads and parses the official FAA Releasable Aircraft Database to populate
the AIRCRAFT table in the ADS-B tracking system. This replaces the previous
approach of using the ADS-B Exchange basic-ac-db.json file.

Data Source: https://registry.faa.gov/database/ReleasableAircraft.zip
Documentation: https://registry.faa.gov/database/ardata.pdf

Files used from the FAA download:
  - MASTER.txt: Aircraft registration master file (N-number, Mode S hex, mfr codes)
  - ACFTREF.txt: Aircraft reference file (manufacturer/model code -> names)

Usage:
    python populate_faa_aircraft.py [--full-refresh] [--download-only] [--dry-run]

Options:
    --full-refresh  Clear existing data and reload everything
    --download-only Download and extract files only, skip database operations
    --dry-run       Parse files and show statistics without database changes
    --keep-files    Don't delete downloaded files after processing

Author: ADS-B Tracking System
Version: 1.0.0
Date: 2026-02-23
"""

import os
import sys
import csv
import zipfile
import tempfile
import shutil
import logging
import argparse
from datetime import datetime
from pathlib import Path
from io import StringIO
from typing import Dict, Optional, Tuple
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

# Oracle database connectivity
try:
    import oracledb
    HAS_ORACLE = True
except ImportError:
    try:
        import cx_Oracle as oracledb
        HAS_ORACLE = True
    except ImportError:
        HAS_ORACLE = False

# =============================================================================
# Configuration
# =============================================================================

FAA_DOWNLOAD_URL = "https://registry.faa.gov/database/ReleasableAircraft.zip"
FAA_ARCHIVE_NAME = "ReleasableAircraft.zip"

# Database connection settings - adjust as needed
DB_CONFIG = {
    'user': os.environ.get('ADSB_DB_USER', 'adsb_user'),
    'password': os.environ.get('ADSB_DB_PASSWORD', 'oracle'),
    'dsn': os.environ.get('ADSB_DB_DSN', '192.168.10.23:1521/ORA26PDB')
}

# Batch size for database operations
BATCH_SIZE = 5000

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


# =============================================================================
# FAA Data Structures
# =============================================================================

# Aircraft type codes from FAA documentation
AIRCRAFT_TYPE_CODES = {
    '1': 'Glider',
    '2': 'Balloon',
    '3': 'Blimp/Dirigible',
    '4': 'Fixed Wing Single Engine',
    '5': 'Fixed Wing Multi Engine',
    '6': 'Rotorcraft',
    '7': 'Weight-Shift-Control',
    '8': 'Powered Parachute',
    '9': 'Gyroplane',
    'H': 'Hybrid Lift',
    'O': 'Other'
}

# Engine type codes
ENGINE_TYPE_CODES = {
    '0': 'None',
    '1': 'Reciprocating',
    '2': 'Turbo-Prop',
    '3': 'Turbo-Shaft',
    '4': 'Turbo-Jet',
    '5': 'Turbo-Fan',
    '6': 'Ramjet',
    '7': '2-Cycle',
    '8': '4-Cycle',
    '9': 'Unknown',
    '10': 'Electric',
    '11': 'Rotary'
}

# Status codes indicating valid registrations
VALID_STATUS_CODES = {'V', 'A', 'S', 'T', 'M', 'N'}


# =============================================================================
# Download Functions
# =============================================================================

def download_faa_database(target_dir: Path, force: bool = False) -> Path:
    """
    Download the FAA Releasable Aircraft Database ZIP file.
    
    Args:
        target_dir: Directory to save the downloaded file
        force: If True, download even if file exists
        
    Returns:
        Path to the downloaded ZIP file
    """
    zip_path = target_dir / FAA_ARCHIVE_NAME
    
    if zip_path.exists() and not force:
        logger.info(f"Using existing download: {zip_path}")
        return zip_path
    
    logger.info(f"Downloading FAA database from {FAA_DOWNLOAD_URL}")
    logger.info("This is approximately 60MB and may take a few minutes...")
    
    try:
        # Create request with User-Agent to avoid potential blocking
        request = Request(
            FAA_DOWNLOAD_URL,
            headers={'User-Agent': 'ADS-B-Tracker/1.0 (Aircraft Database Sync)'}
        )
        
        with urlopen(request, timeout=300) as response:
            total_size = int(response.headers.get('Content-Length', 0))
            downloaded = 0
            
            with open(zip_path, 'wb') as f:
                while True:
                    chunk = response.read(8192)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    
                    if total_size > 0:
                        pct = (downloaded / total_size) * 100
                        print(f"\rDownloading: {downloaded:,} / {total_size:,} bytes ({pct:.1f}%)", 
                              end='', flush=True)
                              
            print()  # Newline after progress
            
        logger.info(f"Download complete: {zip_path} ({downloaded:,} bytes)")
        return zip_path
        
    except HTTPError as e:
        logger.error(f"HTTP Error downloading FAA database: {e.code} - {e.reason}")
        raise
    except URLError as e:
        logger.error(f"URL Error downloading FAA database: {e.reason}")
        raise
    except Exception as e:
        logger.error(f"Error downloading FAA database: {e}")
        raise


def extract_faa_files(zip_path: Path, target_dir: Path) -> Dict[str, Path]:
    """
    Extract required files from the FAA ZIP archive.
    
    Args:
        zip_path: Path to the downloaded ZIP file
        target_dir: Directory to extract files to
        
    Returns:
        Dictionary mapping file type to extracted file path
    """
    required_files = ['MASTER.txt', 'ACFTREF.txt', 'ENGINE.txt']
    extracted = {}
    
    logger.info(f"Extracting files from {zip_path}")
    
    with zipfile.ZipFile(zip_path, 'r') as zf:
        # List contents for debugging
        contents = zf.namelist()
        logger.debug(f"ZIP contents: {contents}")
        
        for filename in required_files:
            # Handle potential case variations or subdirectories
            matching_files = [n for n in contents if n.upper().endswith(filename.upper())]
            
            if not matching_files:
                if filename == 'ENGINE.txt':
                    logger.warning(f"Optional file {filename} not found in archive")
                    continue
                else:
                    raise FileNotFoundError(f"Required file {filename} not found in FAA archive")
            
            source_name = matching_files[0]
            target_path = target_dir / filename
            
            with zf.open(source_name) as source, open(target_path, 'wb') as target:
                shutil.copyfileobj(source, target)
            
            extracted[filename.replace('.txt', '')] = target_path
            file_size = target_path.stat().st_size
            logger.info(f"  Extracted {filename}: {file_size:,} bytes")
    
    return extracted


# =============================================================================
# Parsing Functions
# =============================================================================

def parse_acftref(filepath: Path) -> Dict[str, Dict]:
    """
    Parse the ACFTREF.txt aircraft reference file.
    
    This file maps manufacturer/model/series codes to actual names.
    
    File format (comma-delimited):
        Positions 1-7:   Aircraft Mfr/Model/Series Code
        Positions 9-38:  Manufacturer Name
        Positions 40-59: Model Name
        Position 61:     Type Aircraft (1-9, H, O)
        Positions 63-64: Type Engine
        Position 66:     Category Code (1=Land, 2=Sea, 3=Amphibian)
        Position 68:     Builder Certification Code
        Positions 70-71: Number of Engines
        Positions 73-75: Number of Seats
        Positions 77-83: Aircraft Weight Class
        Positions 85-88: Cruising Speed
        
    Returns:
        Dictionary mapping 7-char code to aircraft details
    """
    aircraft_ref = {}
    line_count = 0
    error_count = 0
    
    logger.info(f"Parsing aircraft reference file: {filepath}")
    
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        reader = csv.reader(f)
        
        for row in reader:
            line_count += 1
            
            try:
                if len(row) < 4:
                    continue
                    
                code = row[0].strip()
                if not code or len(code) != 7:
                    continue
                
                manufacturer = row[1].strip() if len(row) > 1 else ''
                model = row[2].strip() if len(row) > 2 else ''
                type_aircraft = row[3].strip() if len(row) > 3 else ''
                type_engine = row[4].strip() if len(row) > 4 else ''
                num_engines = row[6].strip() if len(row) > 6 else ''
                num_seats = row[7].strip() if len(row) > 7 else ''
                
                aircraft_ref[code] = {
                    'manufacturer': manufacturer,
                    'model': model,
                    'type_code': type_aircraft,
                    'type_name': AIRCRAFT_TYPE_CODES.get(type_aircraft, 'Unknown'),
                    'engine_type': ENGINE_TYPE_CODES.get(type_engine, 'Unknown'),
                    'num_engines': num_engines,
                    'num_seats': num_seats
                }
                
            except Exception as e:
                error_count += 1
                if error_count <= 5:
                    logger.warning(f"Error parsing ACFTREF line {line_count}: {e}")
    
    logger.info(f"  Parsed {len(aircraft_ref):,} aircraft type references ({error_count} errors)")
    return aircraft_ref


def parse_master(filepath: Path, aircraft_ref: Dict[str, Dict]) -> Dict[str, Dict]:
    """
    Parse the MASTER.txt aircraft registration master file.
    
    File format (comma-delimited, 612 characters per record):
        Positions 1-5:     N-Number (registration without 'N' prefix)
        Positions 7-36:    Serial Number
        Positions 38-44:   Aircraft Mfr/Model/Series Code (links to ACFTREF)
        Positions 46-50:   Engine Mfr/Model Code
        Positions 52-55:   Year Manufactured
        Position 57:       Type Registrant (1-9)
        Positions 59-108:  Registrant's Name
        ...
        Positions 254-255: Status Code
        Positions 257-264: Mode S Code (octal)
        ...
        Positions 602-611: Mode S Code Hex (THIS IS WHAT WE NEED FOR ICAO ADDRESS)
        
    Returns:
        Dictionary mapping ICAO hex address to aircraft details
    """
    aircraft = {}
    line_count = 0
    error_count = 0
    skipped_no_hex = 0
    skipped_invalid_status = 0
    
    logger.info(f"Parsing master registration file: {filepath}")
    
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        reader = csv.reader(f)
        
        for row in reader:
            line_count += 1
            
            try:
                # Need enough columns for Mode S Hex (should be around column index 33+)
                if len(row) < 34:
                    error_count += 1
                    continue
                
                # N-Number (registration)
                n_number = row[0].strip()
                if not n_number:
                    continue
                
                # Add 'N' prefix for US registrations
                registration = f"N{n_number}"
                
                # Aircraft Mfr/Model/Series Code
                mfr_code = row[2].strip() if len(row) > 2 else ''
                
                # Year manufactured
                year_mfr = row[4].strip() if len(row) > 4 else ''
                
                # Registrant name (owner/operator)
                registrant_name = row[6].strip() if len(row) > 6 else ''
                
                # Status code - varies by position, usually around index 17-18
                # We'll look for valid status indicators
                status_code = ''
                for idx in [17, 18, 19]:
                    if len(row) > idx:
                        val = row[idx].strip()
                        if val in VALID_STATUS_CODES or val.isdigit():
                            status_code = val
                            break
                
                # Mode S Code Hex - should be in one of the last columns
                # The exact position varies, so we'll search for it
                mode_s_hex = ''
                
                # Try the expected position first (around index 33 for column 602-611)
                for idx in range(len(row) - 1, max(len(row) - 10, 25), -1):
                    candidate = row[idx].strip().upper()
                    # Valid Mode S hex is 6 characters, all hex digits
                    if len(candidate) == 6 and all(c in '0123456789ABCDEF' for c in candidate):
                        mode_s_hex = candidate
                        break
                    # Some entries may have leading zeros stripped or other formats
                    elif len(candidate) > 0 and len(candidate) <= 6 and all(c in '0123456789ABCDEF' for c in candidate):
                        mode_s_hex = candidate.zfill(6)
                        break
                
                if not mode_s_hex:
                    skipped_no_hex += 1
                    continue
                
                # Look up manufacturer/model from reference
                ref_data = aircraft_ref.get(mfr_code, {})
                manufacturer = ref_data.get('manufacturer', '')
                model = ref_data.get('model', '')
                type_name = ref_data.get('type_name', '')
                
                # Build aircraft type string
                if manufacturer and model:
                    aircraft_type = f"{manufacturer} {model}".strip()
                elif type_name:
                    aircraft_type = type_name
                else:
                    aircraft_type = ''
                
                # Store with ICAO hex as key
                aircraft[mode_s_hex] = {
                    'icao_address': mode_s_hex,
                    'registration': registration,
                    'aircraft_type': aircraft_type[:50] if aircraft_type else None,
                    'manufacturer': manufacturer[:100] if manufacturer else None,
                    'model': model[:100] if model else None,
                    'operator': registrant_name[:200] if registrant_name else None,
                    'year_mfr': year_mfr,
                    'status_code': status_code
                }
                
            except Exception as e:
                error_count += 1
                if error_count <= 10:
                    logger.warning(f"Error parsing MASTER line {line_count}: {e}")
    
    logger.info(f"  Parsed {line_count:,} total records")
    logger.info(f"  Valid aircraft with Mode S hex: {len(aircraft):,}")
    logger.info(f"  Skipped (no Mode S hex): {skipped_no_hex:,}")
    logger.info(f"  Parse errors: {error_count:,}")
    
    return aircraft


# =============================================================================
# Database Functions
# =============================================================================

def get_db_connection():
    """Create a database connection."""
    if not HAS_ORACLE:
        raise ImportError("Oracle database driver not found. Install with: pip install oracledb")
    
    logger.info(f"Connecting to database as {DB_CONFIG['user']}@{DB_CONFIG['dsn']}")
    return oracledb.connect(**DB_CONFIG)


def get_existing_aircraft(conn) -> set:
    """Get set of ICAO addresses already in the database."""
    cursor = conn.cursor()
    cursor.execute("SELECT ICAO_ADDRESS FROM AIRCRAFT")
    existing = {row[0] for row in cursor}
    cursor.close()
    logger.info(f"Found {len(existing):,} existing aircraft in database")
    return existing


def insert_aircraft_batch(conn, aircraft_list: list, batch_num: int, total_batches: int):
    """Insert a batch of aircraft records."""
    cursor = conn.cursor()
    
    insert_sql = """
        INSERT INTO AIRCRAFT (
            ICAO_ADDRESS, REGISTRATION, AIRCRAFT_TYPE, 
            MANUFACTURER, MODEL, OPERATOR,
            FIRST_SEEN, LAST_SEEN
        ) VALUES (
            :icao_address, :registration, :aircraft_type,
            :manufacturer, :model, :operator,
            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        )
    """
    
    data = [
        {
            'icao_address': ac['icao_address'],
            'registration': ac['registration'],
            'aircraft_type': ac['aircraft_type'],
            'manufacturer': ac['manufacturer'],
            'model': ac['model'],
            'operator': ac['operator']
        }
        for ac in aircraft_list
    ]
    
    cursor.executemany(insert_sql, data)
    cursor.close()
    
    logger.info(f"  Inserted batch {batch_num}/{total_batches}: {len(aircraft_list):,} records")


def update_aircraft_batch(conn, aircraft_list: list, batch_num: int, total_batches: int):
    """Update existing aircraft records with FAA data."""
    cursor = conn.cursor()
    
    # Only update fields that are currently NULL or update all registration info
    update_sql = """
        UPDATE AIRCRAFT SET
            REGISTRATION = NVL(:registration, REGISTRATION),
            AIRCRAFT_TYPE = NVL(:aircraft_type, AIRCRAFT_TYPE),
            MANUFACTURER = NVL(:manufacturer, MANUFACTURER),
            MODEL = NVL(:model, MODEL),
            OPERATOR = NVL(:operator, OPERATOR),
            LAST_SEEN = GREATEST(LAST_SEEN, CURRENT_TIMESTAMP)
        WHERE ICAO_ADDRESS = :icao_address
    """
    
    data = [
        {
            'icao_address': ac['icao_address'],
            'registration': ac['registration'],
            'aircraft_type': ac['aircraft_type'],
            'manufacturer': ac['manufacturer'],
            'model': ac['model'],
            'operator': ac['operator']
        }
        for ac in aircraft_list
    ]
    
    cursor.executemany(update_sql, data)
    cursor.close()
    
    logger.info(f"  Updated batch {batch_num}/{total_batches}: {len(aircraft_list):,} records")


def clear_aircraft_table(conn):
    """Clear all records from the AIRCRAFT table."""
    logger.warning("Clearing all records from AIRCRAFT table...")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM AIRCRAFT")
    deleted = cursor.rowcount
    cursor.close()
    conn.commit()
    logger.info(f"  Deleted {deleted:,} records")


def load_to_database(aircraft: Dict[str, Dict], full_refresh: bool = False):
    """Load parsed aircraft data into the database."""
    conn = get_db_connection()
    
    try:
        if full_refresh:
            # Note: This may fail if there are FK constraints from FLIGHTS table
            # Consider using MERGE or handling existing data differently
            try:
                clear_aircraft_table(conn)
            except Exception as e:
                logger.warning(f"Could not clear table (FK constraints?): {e}")
                logger.info("Will perform MERGE operation instead")
        
        existing = get_existing_aircraft(conn)
        
        # Separate into inserts and updates
        to_insert = []
        to_update = []
        
        for icao, data in aircraft.items():
            if icao in existing:
                to_update.append(data)
            else:
                to_insert.append(data)
        
        logger.info(f"To insert: {len(to_insert):,}, To update: {len(to_update):,}")
        
        # Process inserts in batches
        if to_insert:
            logger.info("Inserting new aircraft records...")
            total_batches = (len(to_insert) + BATCH_SIZE - 1) // BATCH_SIZE
            
            for i in range(0, len(to_insert), BATCH_SIZE):
                batch = to_insert[i:i + BATCH_SIZE]
                batch_num = (i // BATCH_SIZE) + 1
                insert_aircraft_batch(conn, batch, batch_num, total_batches)
                
                # Commit periodically
                if batch_num % 10 == 0:
                    conn.commit()
            
            conn.commit()
            logger.info(f"Inserted {len(to_insert):,} new aircraft records")
        
        # Process updates in batches
        if to_update:
            logger.info("Updating existing aircraft records...")
            total_batches = (len(to_update) + BATCH_SIZE - 1) // BATCH_SIZE
            
            for i in range(0, len(to_update), BATCH_SIZE):
                batch = to_update[i:i + BATCH_SIZE]
                batch_num = (i // BATCH_SIZE) + 1
                update_aircraft_batch(conn, batch, batch_num, total_batches)
                
                if batch_num % 10 == 0:
                    conn.commit()
            
            conn.commit()
            logger.info(f"Updated {len(to_update):,} existing aircraft records")
        
    finally:
        conn.close()


# =============================================================================
# Statistics and Reporting
# =============================================================================

def print_statistics(aircraft: Dict[str, Dict]):
    """Print statistics about the parsed data."""
    print("\n" + "=" * 60)
    print("FAA Aircraft Database Statistics")
    print("=" * 60)
    
    print(f"\nTotal aircraft with Mode S hex: {len(aircraft):,}")
    
    # Count by manufacturer
    mfr_counts = {}
    for ac in aircraft.values():
        mfr = ac.get('manufacturer') or 'Unknown'
        mfr_counts[mfr] = mfr_counts.get(mfr, 0) + 1
    
    print(f"\nUnique manufacturers: {len(mfr_counts):,}")
    print("\nTop 15 manufacturers:")
    for mfr, count in sorted(mfr_counts.items(), key=lambda x: -x[1])[:15]:
        print(f"  {mfr:30s} {count:>8,}")
    
    # Count by aircraft type
    type_counts = {}
    for ac in aircraft.values():
        atype = ac.get('aircraft_type') or 'Unknown'
        type_counts[atype] = type_counts.get(atype, 0) + 1
    
    print(f"\nUnique aircraft types: {len(type_counts):,}")
    print("\nTop 20 aircraft types:")
    for atype, count in sorted(type_counts.items(), key=lambda x: -x[1])[:20]:
        print(f"  {atype:45s} {count:>8,}")
    
    # Sample records
    print("\nSample records:")
    for i, (icao, ac) in enumerate(list(aircraft.items())[:5]):
        print(f"\n  ICAO: {icao}")
        print(f"    Registration: {ac['registration']}")
        print(f"    Type: {ac['aircraft_type']}")
        print(f"    Manufacturer: {ac['manufacturer']}")
        print(f"    Model: {ac['model']}")
        print(f"    Operator: {ac['operator']}")
    
    print("\n" + "=" * 60)


# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='Populate AIRCRAFT table from FAA Releasable Aircraft Database',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Standard incremental update
    python populate_faa_aircraft.py
    
    # Full refresh (clear and reload all)
    python populate_faa_aircraft.py --full-refresh
    
    # Just download and show statistics
    python populate_faa_aircraft.py --dry-run
    
    # Download only (for manual inspection)
    python populate_faa_aircraft.py --download-only --keep-files
    
Environment Variables:
    ADSB_DB_USER     - Database username (default: adsb_user)
    ADSB_DB_PASSWORD - Database password (default: adsb_password)
    ADSB_DB_DSN      - Database connection string (default: 192.168.10.23:1521/ORA26PDB)
        """
    )
    
    parser.add_argument('--full-refresh', action='store_true',
                        help='Clear existing data and reload everything')
    parser.add_argument('--download-only', action='store_true',
                        help='Download and extract files only, skip database operations')
    parser.add_argument('--dry-run', action='store_true',
                        help='Parse files and show statistics without database changes')
    parser.add_argument('--keep-files', action='store_true',
                        help="Don't delete downloaded files after processing")
    parser.add_argument('--work-dir', type=str, default=None,
                        help='Working directory for downloads (default: temp directory)')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Enable verbose logging')
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Create working directory
    if args.work_dir:
        work_dir = Path(args.work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        cleanup_dir = False
    else:
        work_dir = Path(tempfile.mkdtemp(prefix='faa_aircraft_'))
        cleanup_dir = not args.keep_files
    
    logger.info(f"Working directory: {work_dir}")
    
    try:
        start_time = datetime.now()
        
        # Step 1: Download
        zip_path = download_faa_database(work_dir)
        
        # Step 2: Extract
        files = extract_faa_files(zip_path, work_dir)
        
        if args.download_only:
            logger.info("Download complete. Files available in: " + str(work_dir))
            for name, path in files.items():
                print(f"  {name}: {path}")
            return 0
        
        # Step 3: Parse reference file
        aircraft_ref = parse_acftref(files['ACFTREF'])
        
        # Step 4: Parse master file
        aircraft = parse_master(files['MASTER'], aircraft_ref)
        
        # Step 5: Show statistics
        print_statistics(aircraft)
        
        if args.dry_run:
            logger.info("Dry run complete. No database changes made.")
            return 0
        
        # Step 6: Load to database
        if not HAS_ORACLE:
            logger.error("Oracle database driver not available. Cannot load data.")
            logger.info("Install with: pip install oracledb --break-system-packages")
            return 1
        
        load_to_database(aircraft, full_refresh=args.full_refresh)
        
        elapsed = datetime.now() - start_time
        logger.info(f"Completed in {elapsed}")
        
        return 0
        
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=args.verbose)
        return 1
        
    finally:
        if cleanup_dir and work_dir.exists():
            logger.info(f"Cleaning up working directory: {work_dir}")
            shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
