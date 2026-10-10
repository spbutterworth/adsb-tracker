#!/usr/bin/env python3
"""
================================================================================
ACARS Collector Auto-Restart Wrapper
================================================================================
Version:        1.0.0
Last Updated:   2026-03-05
Description:    Wrapper script that automatically restarts the ACARS collector
                if it crashes. Similar to adsb_collector_restart.py.

Usage:
    python3 acars_collector_restart.py
    
    Or run in background:
    nohup python3 acars_collector_restart.py > acars_restart.log 2>&1 &

Features:
    - Automatically restarts collector on crash
    - Configurable restart delay
    - Maximum restart attempts with cooldown
    - Logging of restart events
================================================================================
"""

import subprocess
import time
import sys
from datetime import datetime


# Configuration
COLLECTOR_SCRIPT = 'acars_collector.py'
RESTART_DELAY = 10           # Seconds to wait before restarting
MAX_RESTARTS = 10            # Max restarts within cooldown period
COOLDOWN_PERIOD = 3600       # Reset restart counter after this many seconds (1 hour)
PYTHON_EXECUTABLE = sys.executable  # Use same Python as this script


def log(message):
    """Print timestamped log message"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] {message}")


def run_collector():
    """Run the ACARS collector and return exit code"""
    log(f"Starting {COLLECTOR_SCRIPT}...")
    
    try:
        process = subprocess.run(
            [PYTHON_EXECUTABLE, COLLECTOR_SCRIPT],
            check=False
        )
        return process.returncode
    except FileNotFoundError:
        log(f"ERROR: {COLLECTOR_SCRIPT} not found!")
        return -1
    except Exception as e:
        log(f"ERROR: {e}")
        return -1


def main():
    """Main restart loop"""
    log("=" * 60)
    log("ACARS Collector Auto-Restart Wrapper")
    log("=" * 60)
    log(f"Collector script: {COLLECTOR_SCRIPT}")
    log(f"Restart delay: {RESTART_DELAY} seconds")
    log(f"Max restarts: {MAX_RESTARTS} per {COOLDOWN_PERIOD} seconds")
    log("=" * 60)
    
    restart_count = 0
    last_restart_reset = time.time()
    
    while True:
        # Reset restart counter after cooldown period
        if time.time() - last_restart_reset > COOLDOWN_PERIOD:
            restart_count = 0
            last_restart_reset = time.time()
            log("Restart counter reset after cooldown period")
        
        # Check if we've exceeded max restarts
        if restart_count >= MAX_RESTARTS:
            log(f"ERROR: Maximum restarts ({MAX_RESTARTS}) exceeded!")
            log(f"Waiting {COOLDOWN_PERIOD} seconds before retry...")
            time.sleep(COOLDOWN_PERIOD)
            restart_count = 0
            last_restart_reset = time.time()
            continue
        
        # Run the collector
        exit_code = run_collector()
        
        if exit_code == 0:
            log("Collector exited normally (code 0)")
        else:
            log(f"Collector exited with code {exit_code}")
        
        restart_count += 1
        log(f"Restart count: {restart_count}/{MAX_RESTARTS}")
        log(f"Restarting in {RESTART_DELAY} seconds...")
        
        try:
            time.sleep(RESTART_DELAY)
        except KeyboardInterrupt:
            log("\nShutdown requested. Exiting restart wrapper.")
            sys.exit(0)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        log("\nShutdown requested. Exiting.")
        sys.exit(0)
