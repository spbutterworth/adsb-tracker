#!/usr/bin/env python3
"""
================================================================================
ADS-B Data Collector with Flight Lifecycle Management
================================================================================
Version:        2.7.0
Last Updated:   2026-10-10
Author:         ADS-B Flight Tracker Route Detection System
Description:    Enhanced ADS-B data collector with macOS sleep resilience,
                proper flight lifecycle management, intelligent position
                throttling, and auto-restart on "too many open files" error.

Features:
    - Connects to ADS-B receiver via network (BaseStation format)
    - TCP keepalive to survive macOS display sleep
    - Socket timeout detection for frozen connections
    - Stores aircraft, flights, and position data in Oracle
    - Proper flight lifecycle (creation, updates, ending)
    - Automatic cleanup of stale flights
    - Downloads and caches FAA aircraft registry database
    - Real-time alert system (ICAO, callsign, altitude, squawk)
    - Automatic route detection every 10 seconds
    - INTELLIGENT POSITION THROTTLING (~85% storage reduction)
    - Auto-restart after 50,000 messages to prevent memory bloat
    - Auto-restart on "too many open files" error (errno 24)
    - Safe per-record validation, bounded receive buffering, atomic FAA refreshes,
      alert cooldowns, and deferred throttle-state updates after DB success

Version History:
    2.7.0 (2026-10-10) - Route lookup via adsbdb.com
        - Origin/destination now come from the adsbdb callsign API
          (https://api.adsbdb.com/v0/callsign/<CALLSIGN>), looked up once
          per flight when the callsign is first known
        - adsbdb routes overwrite any guessed route; the position-based
          RouteDetector is kept only as a fallback (unknown callsigns,
          tail-number callsigns, API unavailable)
        - Airports missing from the AIRPORTS table are added from the API
          response
        - route_distance_nm filled from the two airports; estimated_arrival
          refreshed each route cycle from the latest position and speed
        - Results cached in memory (24h found / 6h not found), max 5 API
          calls per 10-second route cycle, 5-minute pause after repeated
          network errors or HTTP 429, so the feed is never held up for long

    2.6.4 (2026-10-10) - Backfill callsign on every stored row
        - Only MSG,1 (identification) messages carry the callsign, so most
          stored position rows had a NULL callsign and CALLSIGN alerts only
          fired on the occasional MSG,1 message
        - Added 'callsign' to the merge_last_known() cache so each row gets
          the aircraft's most recently broadcast callsign

    2.6.3 (2026-09-15) - Fix busy-loop reconnect on remote close
        - When the ADS-B feed (Pi) closed the TCP connection cleanly
          (sock.recv() returning empty), the collector broke out and
          reconnected immediately with no delay - the only disconnect
          path in the whole loop missing one. If the Pi was flapping
          (mid-reboot, feeder restarting, single-client feeder already
          holding a session) this hammered it with reconnect attempts
          in a tight loop instead of backing off.
        - Now calls get_reconnect_delay() and sleeps before reconnecting,
          same as the socket-timeout and connection-refused paths

    2.6.2 (2026-09-15) - Fix missing speed/track data
        - BaseStation format splits position (MSG,3: altitude+lat/lon) and
          velocity (MSG,4: ground_speed+track) across separate messages,
          so a single stored row was very often missing speed/track even
          though the aircraft had just reported it
        - Added last_known_state cache + merge_last_known() to backfill
          altitude/ground_speed/track/vertical_rate/squawk/is_on_ground
          from the most recent value seen for that aircraft before insert
        - Cache is cleaned up alongside the other stale-flight cleanup

    2.6.1 (2026-09-15) - Database connection resilience
        - Fixed "'NoneType' object has no attribute 'send'" crash caused by
          a dead Oracle connection (network drop / Mac sleep-wake / idle
          session kill) being reused after its socket was already torn down
        - Added is_connection_healthy() / reconnect_database() helpers
        - Main loop now detects a dead DB connection and reconnects
          (recreating the cursor and RouteDetector) instead of endlessly
          retrying against a broken connection
        - Added expire_time=2 to the Oracle connect() call so dead
          connections are detected proactively via keepalive probes

    2.6.0 (2026-02-23) - Intelligent Position Throttling
        - Added hybrid time + change-based position filtering
        - Configurable thresholds for storage decisions
        - Always stores: squawk changes, ground transitions, emergencies
        - Stores on significant: heading change, altitude change, distance
        - Expected ~85% reduction in stored positions
        - Added throttling statistics to periodic status output

    2.5.0 (2026-02-23) - FAA Aircraft Registry Integration
        - Replaced ADS-B Exchange JSON with official FAA database
        - Downloads from https://registry.faa.gov/database/ReleasableAircraft.zip
        - Parses MASTER.txt and ACFTREF.txt for US aircraft data
        - Maps Mode S hex codes to N-number registrations
        - Note: FAA data covers US-registered aircraft only

    2.4.3 (2026-02-17) - Fix missing cleanup_resources function
    2.4.2 (2026-02-17) - Raspberry Pi reboot awareness
    2.4.1 (2026-02-17) - Fix 403 aircraft database download
    2.4.0 (2026-02-13) - Auto-restart on file handle errors
    2.3.0 (2026-02-09) - macOS sleep resilience
    2.2.0 (2026-01-21) - Flight lifecycle management
    2.1.0 (2026-01-20) - Auto-restart enhancement
    2.0.0 (2026-01-20) - Route detection enhancement
================================================================================
"""

import socket
import oracledb
from datetime import datetime, timedelta, timezone
import time
import sys
import os
import platform
import math
from collections import defaultdict
import json
import csv
import zipfile
import tempfile
import shutil
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
import errno
import re
import logging
import signal
try:
    from route_detector import RouteDetector
except ImportError as exc:
    raise ImportError(
        "route_detector.py must be in the same directory as this collector "
        "or otherwise available on PYTHONPATH."
    ) from exc

# =============================================================================
# Configuration
# =============================================================================

# ADS-B Receiver Configuration
ADSB_HOST = '192.168.10.139'
ADSB_PORT = 30003

# Oracle Database Configuration - UPDATE THESE
DB_USER = 'adsb_user'
DB_PASSWORD = 'oracle'
DB_DSN = '192.168.10.51:1521/PDB1'  # For Oracle 26ai                 

# Auto-restart configuration
MAX_MESSAGES_BEFORE_RESTART = 50000  # Restart after this many messages
FILE_ERROR_RESTART_DELAY = 300  # 5 minutes before restart on file errors

# Network configuration
SOCKET_TIMEOUT = 300  # 5 minutes - detect frozen connections
KEEPALIVE_INTERVAL = 60  # Send keepalive every 60 seconds
RECONNECT_DELAY = 5  # Wait 5 seconds before reconnecting

# Raspberry Pi scheduled reboot configuration (daily at 21:00 UTC)
PI_REBOOT_HOUR_UTC = 21         # Reboot hour in UTC
PI_REBOOT_WINDOW_MINUTES = 5    # How long the reboot window lasts
PI_REBOOT_RECONNECT_DELAY = 30  # Slower retry during reboot window

# Flight lifecycle configuration
FLIGHT_TIMEOUT_MINUTES = 60  # Mark flight as ended after this many minutes of no updates
CLEANUP_INTERVAL_SECONDS = 600  # Run cleanup every 10 minutes

# FAA Database configuration
FAA_DOWNLOAD_URL = "https://registry.faa.gov/database/ReleasableAircraft.zip"
FAA_CACHE_DIR = Path.home() / '.adsb_tracker' / 'faa_cache'
FAA_CACHE_MAX_AGE_HOURS = 24  # Re-download if cache is older than this

# BaseStation timestamps from this receiver are local Central time. Keep these
# values naive: the target `reported_time` column is a local wall-clock time,
# not a TIMESTAMP WITH TIME ZONE value. Binding an aware UTC datetime here
# shifts displayed flights by five hours during daylight-saving time.
DATABASE_TIME_ZONE = 'America/Chicago'

# Protect the long-running process from a corrupt or hostile feed.
MAX_RECEIVE_BUFFER_BYTES = 1_048_576  # 1 MiB without a line terminator
MAX_TRACKED_CALLSIGNS = 20_000

# A condition that remains true for a flight should not create an alert row for
# every received message. Set to 0 to preserve the former behaviour.
ALERT_COOLDOWN_SECONDS = 300

# =============================================================================
# Position Throttling Configuration
# =============================================================================
# These settings control when positions are stored to reduce database volume
# while maintaining accurate flight tracking. Expected ~85% reduction.

THROTTLE_ENABLED = True  # Set to False to store every position (not recommended)

# Time-based thresholds
THROTTLE_MIN_INTERVAL_SEC = 5      # Minimum seconds between stored positions
THROTTLE_MAX_INTERVAL_SEC = 60     # Maximum seconds - always store after this long

# Change-based thresholds (store if ANY of these change significantly)
THROTTLE_MIN_DISTANCE_NM = 0.5     # Nautical miles moved (~3000 ft)
THROTTLE_MIN_ALT_CHANGE_FT = 300   # Altitude change in feet
THROTTLE_MIN_TRACK_CHANGE_DEG = 15 # Heading change in degrees
THROTTLE_MIN_SPEED_CHANGE_KT = 50  # Ground speed change in knots

# Always store positions when these conditions occur (safety-critical)
THROTTLE_ALWAYS_STORE_SQUAWK_CHANGE = True    # Squawk code changed
THROTTLE_ALWAYS_STORE_GROUND_CHANGE = True    # Takeoff or landing detected
THROTTLE_ALWAYS_STORE_EMERGENCY = True        # Emergency flag set
THROTTLE_ALWAYS_STORE_FIRST_POSITION = True   # First position with lat/lon

# Emergency squawk codes - always store immediately
EMERGENCY_SQUAWKS = {'7500', '7600', '7700'}  # Hijack, Radio Fail, Emergency

# =============================================================================
# adsbdb.com Route Lookup Configuration
# =============================================================================
ADSBDB_ENABLED = True
ADSBDB_URL = "https://api.adsbdb.com/v0/callsign/{}"
ADSBDB_TIMEOUT_SEC = 5               # Per-request timeout
ADSBDB_CACHE_HOURS = 24              # Re-use a found route this long
ADSBDB_NOT_FOUND_CACHE_HOURS = 6     # Don't re-ask about unknown callsigns
ADSBDB_MAX_LOOKUPS_PER_CYCLE = 5     # API calls per 10-second route cycle
ADSBDB_FAILURES_BEFORE_PAUSE = 3     # Consecutive network errors...
ADSBDB_PAUSE_SECONDS = 300           # ...pause lookups this long

# Airline-style callsigns only: 3-letter ICAO airline code, then a digit
# (AAL3102, SWA1234, UAL55X). Tail numbers like N123AB are skipped.
ADSBDB_CALLSIGN_PATTERN = re.compile(r'^[A-Z]{3}[0-9][0-9A-Z]{0,4}$')

# =============================================================================
# Global State
# =============================================================================

# Track active flights to avoid creating duplicates
active_flights = {}  # key: icao_address, value: {'flight_id': X, 'last_seen': datetime}

# Position throttling state - tracks last stored position per aircraft
# key: icao_address, value: {time, lat, lon, alt, track, speed, squawk, on_ground}
last_stored_positions = {}

# Per-aircraft "last known values" cache. BaseStation format splits data
# across message types - a MSG,3 (position) carries altitude/lat/lon but
# NOT speed/track, while a MSG,4 (velocity) carries speed/track but NOT
# position - so a single parsed message is very often missing fields the
# aircraft actually has. This cache lets us backfill those gaps with the
# most recent value seen for that aircraft before a row is inserted.
# key: icao_address, value: {field: value, ..., '_updated': epoch_seconds}
last_known_state = {}

# Throttling statistics
throttle_stats = {
    'received': 0,       # Total positions received
    'stored': 0,         # Positions actually stored
    'skipped_time': 0,   # Skipped due to time threshold
    'skipped_nochange': 0,  # Skipped due to no significant change
    'forced_change': 0,  # Stored due to significant change
    'forced_safety': 0,  # Stored due to safety conditions
    'forced_maxtime': 0, # Stored due to max interval reached
}

# Aircraft database cache
aircraft_db = {}

# FAA aircraft reference (manufacturer/model lookup)
aircraft_ref = {}

# Airline database cache
airline_db = {}

# Alert rules cache
alert_rules = []

# Local performance and alert-deduplication caches.
known_aircraft = set()
last_aircraft_touch = {}
last_alert_times = {}
route_detection_pending = set()

# adsbdb lookup state
adsbdb_cache = {}          # callsign -> (expires_epoch, route_dict or None)
adsbdb_flight_dest = {}    # flight_id -> (dest_lat, dest_lon) for ETA refresh
adsbdb_done_flights = set()  # flight_ids already resolved (found or not found)
airport_id_cache = {}      # icao_code -> airport_id
adsbdb_state = {'failures': 0, 'paused_until': 0.0, 'calls_this_cycle': 0}
adsbdb_stats = {'calls': 0, 'found': 0, 'not_found': 0, 'errors': 0, 'cache_hits': 0}

# Route detector instance
route_detector = None

# Track last cleanup time
last_cleanup_time = time.time()

# Track last data received time for heartbeat monitoring
last_data_time = time.time()

# =============================================================================
# FAA Aircraft Type and Engine Code Mappings
# =============================================================================

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

# =============================================================================
# Position Throttling Functions
# =============================================================================

def haversine_nm(lat1, lon1, lat2, lon2):
    """
    Calculate the great-circle distance between two points in nautical miles.
    Returns None if any coordinate is None.
    """
    if None in (lat1, lon1, lat2, lon2):
        return None
    
    # Convert to radians
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    
    # Haversine formula
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a))
    
    # Earth radius in nautical miles
    r_nm = 3440.065
    
    return c * r_nm


def normalize_track(track):
    """Normalize track to 0-360 range."""
    if track is None:
        return None
    return track % 360


def track_difference(track1, track2):
    """
    Calculate the absolute difference between two headings (0-180).
    Handles wraparound (e.g., 350° to 10° = 20°).
    """
    if track1 is None or track2 is None:
        return None
    
    t1 = normalize_track(track1)
    t2 = normalize_track(track2)
    
    diff = abs(t1 - t2)
    if diff > 180:
        diff = 360 - diff
    
    return diff


def should_store_position(data):
    """
    Determine if this position should be stored based on throttling rules.
    
    Returns: (should_store: bool, reason: str)
    
    Reasons:
        'first'      - First position for this aircraft
        'time_min'   - Minimum time not yet elapsed (skip)
        'time_max'   - Maximum time elapsed (force store)
        'distance'   - Significant distance traveled
        'altitude'   - Significant altitude change
        'track'      - Significant heading change
        'speed'      - Significant speed change
        'squawk'     - Squawk code changed
        'ground'     - Ground state changed (takeoff/landing)
        'emergency'  - Emergency condition
        'no_change'  - No significant change (skip)
        'no_position'- No lat/lon in this message (skip storing, but process)
    """
    global throttle_stats
    
    if not THROTTLE_ENABLED:
        return True, 'disabled'
    
    icao = data['icao_address']
    now = time.time()
    
    throttle_stats['received'] += 1
    
    # Get current position data
    lat = data.get('latitude')
    lon = data.get('longitude')
    alt = data.get('altitude')
    track = data.get('track')
    speed = data.get('ground_speed')
    squawk = data.get('squawk')
    on_ground = data.get('is_on_ground')
    emergency = data.get('emergency')
    
    # Check if we have a previous position for this aircraft
    if icao not in last_stored_positions:
        # First time seeing this aircraft - store if we have position
        if lat is not None and lon is not None:
            return True, 'first'
        else:
            # No position yet - still process but don't count as stored
            return False, 'no_position'
    
    last = last_stored_positions[icao]
    elapsed = now - last['time']
    
    # --- Safety-critical conditions: ALWAYS store ---
    
    # Emergency squawk or flag
    if THROTTLE_ALWAYS_STORE_EMERGENCY:
        if emergency or (squawk in EMERGENCY_SQUAWKS):
            return True, 'emergency'
    
    # Squawk change
    if THROTTLE_ALWAYS_STORE_SQUAWK_CHANGE:
        if squawk and last.get('squawk') and squawk != last['squawk']:
            return True, 'squawk'
    
    # Ground state change (takeoff/landing)
    if THROTTLE_ALWAYS_STORE_GROUND_CHANGE:
        if on_ground is not None and last.get('on_ground') is not None:
            if on_ground != last['on_ground']:
                return True, 'ground'
    
    # First position with coordinates
    if THROTTLE_ALWAYS_STORE_FIRST_POSITION:
        if lat is not None and lon is not None:
            if last.get('lat') is None or last.get('lon') is None:
                return True, 'first_pos'
    
    # --- Time-based checks ---
    
    # Minimum interval not elapsed - skip unless there's a significant change
    if elapsed < THROTTLE_MIN_INTERVAL_SEC:
        # Even within min interval, check for very significant changes
        # (emergency already handled above)
        throttle_stats['skipped_time'] += 1
        return False, 'time_min'
    
    # Maximum interval elapsed - always store
    if elapsed >= THROTTLE_MAX_INTERVAL_SEC:
        return True, 'time_max'
    
    # --- Change-based checks (only if we have position data) ---
    
    if lat is None or lon is None:
        # No position in this message - skip
        return False, 'no_position'
    
    # Distance check
    if last.get('lat') is not None and last.get('lon') is not None:
        dist = haversine_nm(last['lat'], last['lon'], lat, lon)
        if dist is not None and dist >= THROTTLE_MIN_DISTANCE_NM:
            return True, 'distance'
    
    # Altitude check
    if alt is not None and last.get('alt') is not None:
        alt_change = abs(alt - last['alt'])
        if alt_change >= THROTTLE_MIN_ALT_CHANGE_FT:
            return True, 'altitude'
    
    # Track/heading check
    if track is not None and last.get('track') is not None:
        track_change = track_difference(track, last['track'])
        if track_change is not None and track_change >= THROTTLE_MIN_TRACK_CHANGE_DEG:
            return True, 'track'
    
    # Speed check
    if speed is not None and last.get('speed') is not None:
        speed_change = abs(speed - last['speed'])
        if speed_change >= THROTTLE_MIN_SPEED_CHANGE_KT:
            return True, 'speed'
    
    # No significant change detected
    throttle_stats['skipped_nochange'] += 1
    return False, 'no_change'


def merge_last_known(data):
    """
    Backfill None fields in `data` (callsign, altitude, ground_speed, track,
    vertical_rate, squawk, is_on_ground) from the most recently seen value
    for this aircraft, and record any new values this message provided.

    Why this is needed: BaseStation/SBS format spreads an aircraft's state
    across several message "transmission types" instead of sending it all
    at once - a MSG,3 (airborne position) gives you altitude + lat/lon but
    leaves ground_speed/track blank, while a MSG,4 (airborne velocity)
    gives you ground_speed/track/vertical_rate but leaves lat/lon blank.
    Only messages with lat/lon ever get inserted as a position row (see
    should_store_position), so without this merge, most stored rows would
    have NULL speed/track even though the aircraft reported it moments
    earlier in a separate message.
    """
    icao = data['icao_address']
    state = last_known_state.setdefault(icao, {})
    fields = ('callsign', 'altitude', 'ground_speed', 'track', 'vertical_rate', 'squawk', 'is_on_ground')

    for field in fields:
        if data.get(field) is None:
            if field in state:
                data[field] = state[field]
        else:
            state[field] = data[field]

    state['_updated'] = time.time()
    return data


def cleanup_stale_last_known_state():
    """Remove stale entries from the last-known-state cache."""
    global last_known_state

    now = time.time()
    stale_threshold = FLIGHT_TIMEOUT_MINUTES * 60  # Same as flight timeout

    stale_icaos = [
        icao for icao, state in last_known_state.items()
        if now - state.get('_updated', 0) > stale_threshold
    ]

    for icao in stale_icaos:
        del last_known_state[icao]

    if stale_icaos:
        print(f"Cleaned up {len(stale_icaos)} stale entries from last-known-state cache")

    # Alert keys contain flight IDs, so expire them too rather than allowing a
    # long-running collector to retain every historic flight indefinitely.
    cutoff = now - max(ALERT_COOLDOWN_SECONDS, FLIGHT_TIMEOUT_MINUTES * 60)
    for key, triggered_at in list(last_alert_times.items()):
        if triggered_at < cutoff:
            del last_alert_times[key]


def _update_last_stored(icao, timestamp, data):
    """Update the last stored position cache for an aircraft."""
    last_stored_positions[icao] = {
        'time': timestamp,
        'lat': data.get('latitude'),
        'lon': data.get('longitude'),
        'alt': data.get('altitude'),
        'track': data.get('track'),
        'speed': data.get('ground_speed'),
        'squawk': data.get('squawk'),
        'on_ground': data.get('is_on_ground')
    }


def record_stored_position(data, reason):
    """Record throttle state only after its INSERT has succeeded."""
    _update_last_stored(data['icao_address'], time.time(), data)
    throttle_stats['stored'] += 1
    if reason in {'emergency', 'squawk', 'ground', 'first_pos'}:
        throttle_stats['forced_safety'] += 1
    elif reason == 'time_max':
        throttle_stats['forced_maxtime'] += 1
    elif reason in {'distance', 'altitude', 'track', 'speed'}:
        throttle_stats['forced_change'] += 1


def get_throttle_stats_summary():
    """Get a formatted summary of throttling statistics."""
    stats = throttle_stats
    received = stats['received']
    stored = stats['stored']
    
    if received == 0:
        return "Throttle: No positions received yet"
    
    pct_stored = (stored / received) * 100
    pct_reduction = 100 - pct_stored
    
    return (
        f"Throttle: {stored:,}/{received:,} stored ({pct_stored:.1f}%), "
        f"reduction: {pct_reduction:.1f}% | "
        f"time:{stats['skipped_time']:,} nochange:{stats['skipped_nochange']:,} | "
        f"change:{stats['forced_change']:,} safety:{stats['forced_safety']:,} maxtime:{stats['forced_maxtime']:,}"
    )


def cleanup_stale_throttle_cache():
    """Remove stale entries from the throttle position cache."""
    global last_stored_positions
    
    now = time.time()
    stale_threshold = FLIGHT_TIMEOUT_MINUTES * 60  # Same as flight timeout
    
    stale_icaos = [
        icao for icao, pos in last_stored_positions.items()
        if now - pos['time'] > stale_threshold
    ]
    
    for icao in stale_icaos:
        del last_stored_positions[icao]
    
    if stale_icaos:
        print(f"Cleaned up {len(stale_icaos)} stale entries from throttle cache")


# =============================================================================
# Utility Functions
# =============================================================================

def restart_after_delay(delay_seconds=FILE_ERROR_RESTART_DELAY, reason="scheduled restart"):
    """
    Perform clean shutdown and restart after a delay
    """
    print(f"\n{'='*80}")
    print(f"RESTART INITIATED: {reason}")
    print(f"Current time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Delay: {delay_seconds} seconds ({delay_seconds//60} minutes)")
    restart_time = datetime.now() + timedelta(seconds=delay_seconds)
    print(f"Restart scheduled for: {restart_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*80}\n")
    
    # Wait for the delay period
    print(f"Waiting {delay_seconds} seconds before restart...")
    
    # Show countdown for last 60 seconds
    if delay_seconds > 60:
        time.sleep(delay_seconds - 60)
        delay_seconds = 60
    
    # Countdown last 60 seconds in 10-second intervals
    while delay_seconds > 0:
        if delay_seconds <= 60:
            print(f"Restarting in {delay_seconds} seconds...")
        time.sleep(min(10, delay_seconds))
        delay_seconds -= 10
    
    print(f"\n{'='*80}")
    print("Restarting collector process now...")
    print(f"{'='*80}\n")
    
    # Restart the process
    os.execv(sys.executable, [sys.executable] + sys.argv)


def in_pi_reboot_window():
    """
    Check if we are currently in the Pi's scheduled reboot window.
    Returns (bool: in_window, int: seconds_until_reboot)
    """
    now_utc = datetime.now(timezone.utc)
    reboot_today = now_utc.replace(
        hour=PI_REBOOT_HOUR_UTC,
        minute=0,
        second=0,
        microsecond=0
    )

    # Seconds since the reboot time today
    seconds_since_reboot = (now_utc - reboot_today).total_seconds()

    # We're inside the reboot window if within 0 - PI_REBOOT_WINDOW_MINUTES
    in_window = 0 <= seconds_since_reboot <= (PI_REBOOT_WINDOW_MINUTES * 60)

    # Seconds until next reboot (for informational logging)
    if seconds_since_reboot < 0:
        seconds_until = -seconds_since_reboot
    else:
        seconds_until = (24 * 3600) - seconds_since_reboot

    return in_window, int(seconds_until)


def get_reconnect_delay():
    """
    Return the appropriate reconnect delay depending on whether we are in the
    Pi reboot window or not.
    """
    in_window, seconds_until = in_pi_reboot_window()

    if in_window:
        print(f"  ℹ️  Pi reboot window active (21:00 UTC ±{PI_REBOOT_WINDOW_MINUTES} min)")
        print(f"  Waiting {PI_REBOOT_RECONNECT_DELAY}s for Pi to come back up...")
        return PI_REBOOT_RECONNECT_DELAY
    else:
        # Warn if reboot is coming soon (within 2 minutes)
        if seconds_until <= 120:
            print(f"  ⚠️  Pi reboot in ~{seconds_until}s (21:00 UTC) - connection may drop shortly")
        return RECONNECT_DELAY


# =============================================================================
# FAA Database Download and Parsing Functions
# =============================================================================

def get_faa_cache_path():
    """Get path to FAA cache directory, creating if needed."""
    FAA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return FAA_CACHE_DIR


def is_cache_valid():
    """Check if cached FAA data exists and is recent enough."""
    cache_dir = get_faa_cache_path()
    master_file = cache_dir / 'MASTER.txt'
    acftref_file = cache_dir / 'ACFTREF.txt'
    
    if not master_file.exists() or not acftref_file.exists():
        return False
    
    # Check age
    cache_age = datetime.now() - datetime.fromtimestamp(master_file.stat().st_mtime)
    if cache_age > timedelta(hours=FAA_CACHE_MAX_AGE_HOURS):
        print(f"FAA cache is {cache_age.total_seconds()/3600:.1f} hours old, will refresh")
        return False
    
    print(f"Using cached FAA data ({cache_age.total_seconds()/3600:.1f} hours old)")
    return True


def download_faa_database():
    """
    Download the FAA Releasable Aircraft Database ZIP file.
    Returns True on success, False on failure.
    """
    cache_dir = get_faa_cache_path()
    
    print(f"Downloading FAA database from {FAA_DOWNLOAD_URL}")
    print("This is approximately 60MB and may take a few minutes...")
    
    temp_dir = Path(tempfile.mkdtemp(prefix='faa-refresh-', dir=cache_dir))
    zip_path = temp_dir / 'ReleasableAircraft.zip'
    try:
        # Create request with User-Agent
        request = Request(
            FAA_DOWNLOAD_URL,
            headers={
                'User-Agent': 'ADS-B-Tracker/2.6.0 (Aircraft Database Sync)',
                'Accept': '*/*'
            }
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
                        # Update progress every ~5%
                        if downloaded % (total_size // 20 + 1) < 8192:
                            print(f"  Downloading: {downloaded:,} / {total_size:,} bytes ({pct:.0f}%)")
        
        print(f"Download complete: {downloaded:,} bytes")
        
        # Validate and extract into a temporary directory. Existing cached data
        # remains usable if the download or archive is incomplete/corrupt.
        print("Extracting FAA data files...")
        with zipfile.ZipFile(zip_path, 'r') as zf:
            bad_member = zf.testzip()
            if bad_member:
                raise zipfile.BadZipFile(f"CRC check failed for {bad_member}")
            for filename in ['MASTER.txt', 'ACFTREF.txt']:
                # Handle potential case variations
                matching = [n for n in zf.namelist() if n.upper().endswith(filename.upper())]
                if matching:
                    with zf.open(matching[0]) as src, open(temp_dir / filename, 'wb') as dst:
                        shutil.copyfileobj(src, dst)
                    print(f"  Extracted {filename}")
                else:
                    raise ValueError(f"{filename} not found in FAA archive")

        for filename in ['MASTER.txt', 'ACFTREF.txt']:
            extracted = temp_dir / filename
            if not extracted.exists() or extracted.stat().st_size == 0:
                raise ValueError(f"Extracted {filename} is missing or empty")
            os.replace(extracted, cache_dir / filename)
        
        return True
        
    except HTTPError as e:
        print(f"HTTP Error downloading FAA database: {e.code} - {e.reason}")
        return False
    except URLError as e:
        print(f"URL Error downloading FAA database: {e.reason}")
        return False
    except Exception as e:
        print(f"Error downloading FAA database: {e}")
        return False
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def parse_acftref_file():
    """
    Parse the ACFTREF.txt aircraft reference file.
    Maps manufacturer/model codes to actual names.
    """
    global aircraft_ref
    aircraft_ref = {}
    
    acftref_path = get_faa_cache_path() / 'ACFTREF.txt'
    if not acftref_path.exists():
        print("ACFTREF.txt not found")
        return
    
    print(f"Parsing aircraft reference file...")
    line_count = 0
    
    with open(acftref_path, 'r', encoding='utf-8', errors='replace') as f:
        reader = csv.reader(f)
        
        for row in reader:
            line_count += 1
            
            try:
                if len(row) < 4:
                    continue
                
                code = row[0].strip()
                if not code:
                    continue
                
                manufacturer = row[1].strip() if len(row) > 1 else ''
                model = row[2].strip() if len(row) > 2 else ''
                type_aircraft = row[3].strip() if len(row) > 3 else ''
                type_engine = row[4].strip() if len(row) > 4 else ''
                
                aircraft_ref[code] = {
                    'manufacturer': manufacturer,
                    'model': model,
                    'type_code': type_aircraft,
                    'type_name': AIRCRAFT_TYPE_CODES.get(type_aircraft, 'Unknown'),
                    'engine_type': ENGINE_TYPE_CODES.get(type_engine, 'Unknown')
                }
                
            except Exception as e:
                pass  # Skip malformed lines
    
    print(f"  Loaded {len(aircraft_ref):,} aircraft type references")


def parse_master_file():
    """
    Parse the MASTER.txt aircraft registration master file.
    Populates the global aircraft_db dictionary keyed by Mode S hex code.
    """
    global aircraft_db
    aircraft_db = {}
    
    master_path = get_faa_cache_path() / 'MASTER.txt'
    if not master_path.exists():
        print("MASTER.txt not found")
        return
    
    print(f"Parsing master registration file...")
    line_count = 0
    valid_count = 0
    
    with open(master_path, 'r', encoding='utf-8', errors='replace') as f:
        reader = csv.reader(f)
        
        for row in reader:
            line_count += 1
            
            try:
                if len(row) < 10:
                    continue
                
                # N-Number (registration without N prefix)
                n_number = row[0].strip()
                if not n_number:
                    continue
                
                registration = f"N{n_number}"
                
                # Aircraft Mfr/Model/Series Code (column index 2)
                mfr_code = row[2].strip() if len(row) > 2 else ''
                
                # Registrant name (owner/operator) - usually column 6
                registrant_name = row[6].strip() if len(row) > 6 else ''
                
                # Mode S Code Hex - search in last columns
                mode_s_hex = ''
                for idx in range(len(row) - 1, max(len(row) - 10, 25), -1):
                    candidate = row[idx].strip().upper()
                    # Valid Mode S hex is up to 6 characters, all hex digits
                    if candidate and len(candidate) <= 6 and all(c in '0123456789ABCDEF' for c in candidate):
                        mode_s_hex = candidate.zfill(6)  # Pad to 6 chars
                        break
                
                if not mode_s_hex:
                    continue
                
                # Look up manufacturer/model from reference
                ref_data = aircraft_ref.get(mfr_code, {})
                manufacturer = ref_data.get('manufacturer', '')
                model = ref_data.get('model', '')
                type_name = ref_data.get('type_name', '')
                
                # Build aircraft type string (matches ADS-B Exchange format)
                if manufacturer and model:
                    aircraft_type = f"{manufacturer} {model}".strip()
                elif type_name:
                    aircraft_type = type_name
                else:
                    aircraft_type = ''
                
                # Store in format compatible with existing code
                # Keys match what ensure_aircraft_exists() expects
                aircraft_db[mode_s_hex] = {
                    'registration': registration,
                    'aircraft_type': aircraft_type[:50] if aircraft_type else '',
                    'manufacturer': manufacturer[:100] if manufacturer else '',
                    'model': model[:100] if model else '',
                    'ownop': registrant_name[:200] if registrant_name else ''
                }
                valid_count += 1
                
            except Exception as e:
                pass  # Skip malformed lines
    
    print(f"  Parsed {line_count:,} total records")
    print(f"  Loaded {valid_count:,} aircraft with Mode S hex codes")


def load_aircraft_database():
    """
    Load aircraft database from FAA Releasable Aircraft Registry.
    Downloads fresh data if cache is missing or expired.
    """
    global aircraft_db, aircraft_ref
    
    print("\n" + "="*60)
    print("Loading FAA Aircraft Registry Database")
    print("="*60)
    
    # Check if we need to download
    if not is_cache_valid():
        success = download_faa_database()
        if not success:
            # Try to use existing cache even if expired
            cache_dir = get_faa_cache_path()
            if (cache_dir / 'MASTER.txt').exists():
                print("Download failed, using existing cached data")
            else:
                print("Warning: No FAA aircraft database available")
                print("Aircraft lookups will not have registration data")
                return
    
    # Parse the reference file first (needed for manufacturer/model lookup)
    parse_acftref_file()
    
    # Parse the master file
    parse_master_file()
    
    # Show sample data
    if aircraft_db:
        sample_icao = list(aircraft_db.keys())[0]
        sample_data = aircraft_db[sample_icao]
        print(f"\nSample aircraft - ICAO: {sample_icao}")
        print(f"  Registration: {sample_data['registration']}")
        print(f"  Type: {sample_data['aircraft_type']}")
        print(f"  Manufacturer: {sample_data['manufacturer']}")
        print(f"  Owner: {sample_data['ownop'][:50]}...")
    
    print("\nNote: FAA database contains US-registered aircraft only (N-numbers)")
    print("="*60 + "\n")


# =============================================================================
# Database and Airline Loading
# =============================================================================

def load_airline_database(cursor):
    """Load airline/callsign prefix database from Oracle"""
    global airline_db
    try:
        cursor.execute("SELECT callsign_prefix, airline_name FROM airlines WHERE is_active = 1")
        for prefix, name in cursor.fetchall():
            airline_db[prefix.upper()] = name
        print(f"Loaded {len(airline_db)} airline prefixes")
    except Exception as e:
        print(f"Could not load airlines (table may not exist yet): {e}")


def load_alert_rules(cursor):
    """Load active alert rules from database"""
    global alert_rules
    try:
        cursor.execute("""
            SELECT alert_id, alert_type, alert_value 
            FROM alerts 
            WHERE is_active = 1
        """)
        alert_rules = cursor.fetchall()
        print(f"Loaded {len(alert_rules)} active alert rules")
    except Exception as e:
        print(f"Could not load alerts (table may not exist yet): {e}")


def cleanup_stale_flights(cursor):
    """
    End flights that haven't been updated in FLIGHT_TIMEOUT_MINUTES
    Also clean up stale entries from active_flights cache
    """
    try:
        # Call the database procedure to end stale flights
        cursor.callproc('end_stale_flights')
        
        # Clean up local cache - remove flights we haven't seen recently
        current_time = datetime.now()
        stale_icaos = []
        
        for icao, flight_info in active_flights.items():
            if current_time - flight_info['last_seen'] > timedelta(minutes=FLIGHT_TIMEOUT_MINUTES):
                stale_icaos.append(icao)
        
        for icao in stale_icaos:
            del active_flights[icao]
        
        if stale_icaos:
            print(f"Cleaned up {len(stale_icaos)} stale flights from cache")
        
        # Also clean up throttle cache and last-known-state cache
        cleanup_stale_throttle_cache()
        cleanup_stale_last_known_state()
        cleanup_adsbdb_state()
            
    except Exception as e:
        print(f"Error during flight cleanup: {e}")


def check_alerts(cursor, data, flight_id):
    """Check if this position triggers any alerts"""
    if not alert_rules:
        return
    
    for alert_id, alert_type, alert_value in alert_rules:
        alert_key = (alert_id, flight_id)
        last_triggered = last_alert_times.get(alert_key, 0)
        if ALERT_COOLDOWN_SECONDS and time.time() - last_triggered < ALERT_COOLDOWN_SECONDS:
            continue
        triggered = False
        
        if alert_type == 'ICAO' and data['icao_address'] == alert_value.upper():
            triggered = True
        elif alert_type == 'CALLSIGN' and data['callsign'] == alert_value.upper():
            triggered = True
        elif alert_type == 'SQUAWK' and data['squawk'] == alert_value:
            triggered = True
        elif alert_type == 'ALTITUDE' and data['altitude']:
            # Format: ">35000" or "<10000"
            try:
                if alert_value.startswith('>') and data['altitude'] > int(alert_value[1:]):
                    triggered = True
                elif alert_value.startswith('<') and data['altitude'] < int(alert_value[1:]):
                    triggered = True
            except (TypeError, ValueError):
                continue
        
        if triggered:
            try:
                cursor.execute("""
                    INSERT INTO alert_history 
                    (alert_id, flight_id, icao_address, callsign, altitude, latitude, longitude)
                    VALUES (:aid, :fid, :icao, :call, :alt, :lat, :lon)
                """,
                    aid=alert_id,
                    fid=flight_id,
                    icao=data['icao_address'],
                    call=data['callsign'],
                    alt=data['altitude'],
                    lat=data['latitude'],
                    lon=data['longitude']
                )
                
                # Update last_triggered timestamp
                cursor.execute("""
                    UPDATE alerts 
                    SET last_triggered = CURRENT_TIMESTAMP 
                    WHERE alert_id = :aid
                """, aid=alert_id)
                last_alert_times[alert_key] = time.time()
                
                print(f"ALERT TRIGGERED: {alert_type}={alert_value} for {data['icao_address']} / {data['callsign']}")
            except Exception as e:
                print(f"Error recording alert: {e}")


# =============================================================================
# Database Connection and Socket Functions
# =============================================================================

def connect_to_database():
    """Connect to Oracle database"""
    try:
        connection = oracledb.connect(
            user=DB_USER,
            password=DB_PASSWORD,
            dsn=DB_DSN,
            expire_time=2  # Send a keepalive probe every 2 min so a dead
                           # connection (network drop, Mac sleep/wake, DB-side
                           # idle kill) is detected instead of silently
                           # leaving the socket in a broken state
        )
        print(f"Connected to Oracle Database: {DB_DSN}")

        # The receiver and existing database data use Central local time.
        cursor = connection.cursor()
        cursor.execute(f"ALTER SESSION SET TIME_ZONE = '{DATABASE_TIME_ZONE}'")
        cursor.close()
        print(f"Session timezone set to {DATABASE_TIME_ZONE}")

        return connection
    except Exception as e:
        print(f"Database connection failed: {e}")
        raise


def is_connection_healthy(connection):
    """
    Check whether the Oracle connection is still usable.
    A connection can go dead (network blip, Mac sleep/wake, idle session
    killed on the DB side) without Python noticing until the next
    cursor.execute() blows up - sometimes with a confusing low-level error
    like "'NoneType' object has no attribute 'send'" instead of a clean
    Oracle error, because the driver's internal socket was already torn
    down but the connection object itself wasn't invalidated.
    """
    if connection is None:
        return False
    try:
        return connection.is_healthy()
    except Exception:
        return False


def reconnect_database(old_cursor, old_conn):
    """
    Close out a dead connection/cursor (best-effort - they may already be
    broken) and open a fresh one. Returns (new_connection, new_cursor).
    """
    print("\n⚠️  Database connection appears dead — reconnecting...")

    try:
        if old_cursor:
            old_cursor.close()
    except Exception:
        pass

    try:
        if old_conn:
            old_conn.close()
    except Exception:
        pass

    new_conn = connect_to_database()
    new_cursor = new_conn.cursor()
    return new_conn, new_cursor


def create_resilient_socket():
    """
    Create a socket with TCP keepalive and timeout settings
    to survive macOS display sleep and detect frozen connections
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    
    # Enable TCP keepalive to detect dead connections
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    
    # Platform-specific keepalive settings
    system = platform.system()
    
    if system == 'Darwin':  # macOS
        # TCP_KEEPALIVE is the macOS constant for keepalive time
        TCP_KEEPALIVE = 0x10
        try:
            # Send keepalive probes after 60 seconds of idle time
            sock.setsockopt(socket.IPPROTO_TCP, TCP_KEEPALIVE, KEEPALIVE_INTERVAL)
            print(f"macOS TCP keepalive enabled: {KEEPALIVE_INTERVAL}s interval")
        except (OSError, AttributeError) as e:
            print(f"Warning: Could not set macOS keepalive options: {e}")
    
    elif system == 'Linux':
        try:
            # Linux uses different constants
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, KEEPALIVE_INTERVAL)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)  # Probe interval
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)  # Number of probes
            print(f"Linux TCP keepalive enabled: {KEEPALIVE_INTERVAL}s interval")
        except (OSError, AttributeError) as e:
            print(f"Warning: Could not set Linux keepalive options: {e}")
    
    # Set socket timeout to detect frozen connections
    sock.settimeout(SOCKET_TIMEOUT)
    print(f"Socket timeout set to {SOCKET_TIMEOUT} seconds")
    
    return sock


# =============================================================================
# Aircraft and Flight Management
# =============================================================================

def ensure_aircraft_exists(cursor, icao_address):
    """Make sure aircraft exists in database, create if not"""
    # A full SELECT/UPDATE for every raw ADS-B record becomes expensive quickly.
    # Touch known aircraft at most once per minute while retaining new-aircraft
    # detection and metadata enrichment.
    now = time.monotonic()
    if icao_address in known_aircraft:
        if now - last_aircraft_touch.get(icao_address, 0) >= 60:
            cursor.execute(
                "UPDATE aircraft SET last_seen = CURRENT_TIMESTAMP WHERE icao_address = :icao",
                icao=icao_address
            )
            last_aircraft_touch[icao_address] = now
        return

    cursor.execute(
        "SELECT icao_address FROM aircraft WHERE icao_address = :icao",
        icao=icao_address
    )
    if cursor.fetchone() is None:
        # Look up aircraft info from database
        aircraft_info = aircraft_db.get(icao_address.upper(), {})
        registration = aircraft_info.get('registration')
        aircraft_type = aircraft_info.get('aircraft_type')
        manufacturer = aircraft_info.get('manufacturer')
        model = aircraft_info.get('model')
        operator = aircraft_info.get('ownop')
        
        # Build photo URL if we have registration
        photo_url = None
        if registration:
            photo_url = f"https://www.jetphotos.com/registration/{registration}"
        
        # Debug: Log what we found
        if registration or aircraft_type:
            print(f"Adding new aircraft {icao_address}: Reg={registration}, Type={aircraft_type}, Op={operator}")
        else:
            print(f"Adding new aircraft {icao_address}: No registration data (may be non-US aircraft)")
        
        cursor.execute(
            """INSERT INTO aircraft (icao_address, registration, aircraft_type, manufacturer, model, operator, photo_url) 
               VALUES (:icao, :reg, :type, :mfr, :mdl, :op, :photo)""",
            icao=icao_address,
            reg=registration,
            type=aircraft_type,
            mfr=manufacturer,
            mdl=model,
            op=operator,
            photo=photo_url
        )
    else:
        # Update last_seen timestamp and photo_url if we now have registration
        aircraft_info = aircraft_db.get(icao_address.upper(), {})
        registration = aircraft_info.get('registration')
        
        if registration:
            photo_url = f"https://www.jetphotos.com/registration/{registration}"
            cursor.execute(
                """UPDATE aircraft 
                   SET last_seen = CURRENT_TIMESTAMP,
                       photo_url = :photo
                   WHERE icao_address = :icao 
                     AND (photo_url IS NULL OR registration IS NULL)""",
                photo=photo_url,
                icao=icao_address
            )
        else:
            cursor.execute(
                "UPDATE aircraft SET last_seen = CURRENT_TIMESTAMP WHERE icao_address = :icao",
                icao=icao_address
            )
    known_aircraft.add(icao_address)
    last_aircraft_touch[icao_address] = now


def get_or_create_flight(cursor, icao_address, callsign):
    """Get existing flight_id or create new flight"""
    current_time = datetime.now()
    
    # Check if we have an active flight for this aircraft in our cache
    if icao_address in active_flights:
        flight_info = active_flights[icao_address]
        flight_id = flight_info['flight_id']
        
        # Update last_seen in cache
        active_flights[icao_address]['last_seen'] = current_time
        
        # Update last_contact and callsign in database
        if callsign:
            cursor.execute(
                """UPDATE flights 
                   SET last_contact = CURRENT_TIMESTAMP,
                       callsign = :call
                   WHERE flight_id = :fid""",
                call=callsign,
                fid=flight_id
            )
        else:
            cursor.execute(
                "UPDATE flights SET last_contact = CURRENT_TIMESTAMP WHERE flight_id = :fid",
                fid=flight_id
            )
        return flight_id
    
    # Check database for active flight
    cursor.execute(
        """SELECT flight_id FROM flights 
           WHERE icao_address = :icao 
             AND is_active = 1
           ORDER BY first_contact DESC
           FETCH FIRST 1 ROWS ONLY""",
        icao=icao_address
    )
    
    existing = cursor.fetchone()
    if existing:
        flight_id = existing[0]
        
        # Add to cache
        active_flights[icao_address] = {
            'flight_id': flight_id,
            'last_seen': current_time
        }
        
        # Update last_contact
        if callsign:
            cursor.execute(
                """UPDATE flights 
                   SET last_contact = CURRENT_TIMESTAMP,
                       callsign = :call
                   WHERE flight_id = :fid""",
                call=callsign,
                fid=flight_id
            )
        else:
            cursor.execute(
                "UPDATE flights SET last_contact = CURRENT_TIMESTAMP WHERE flight_id = :fid",
                fid=flight_id
            )
        
        return flight_id
    
    # Create new flight
    flight_id_var = cursor.var(int)
    cursor.execute(
        """INSERT INTO flights (icao_address, callsign, is_active) 
           VALUES (:icao, :call, 1) 
           RETURNING flight_id INTO :fid""",
        icao=icao_address,
        call=callsign if callsign else None,
        fid=flight_id_var
    )
    flight_id = flight_id_var.getvalue()[0]
    
    # Add to cache
    active_flights[icao_address] = {
        'flight_id': flight_id,
        'last_seen': current_time
    }
    
    return flight_id


# =============================================================================
# Message Parsing and Storage
# =============================================================================

def _optional_int(value):
    """Parse an SBS numeric field without letting one bad record kill the feed."""
    try:
        return int(float(value)) if value else None
    except (TypeError, ValueError):
        return None


def _optional_float(value):
    try:
        return float(value) if value else None
    except (TypeError, ValueError):
        return None


def parse_basestation_message(line):
    """Parse BaseStation format message from port 30003"""
    fields = line.strip().split(',')
    
    if len(fields) < 22:
        return None
    
    msg_type = fields[0]
    
    # We're mainly interested in MSG types (ADS-B messages)
    if msg_type != 'MSG':
        return None
    
    # Extract callsign and clean it up
    callsign = fields[10].strip() if len(fields) > 10 and fields[10] else None
    # Remove any whitespace and make uppercase
    if callsign:
        callsign = callsign.strip().upper()
        # Only keep if it's not empty after stripping
        if not callsign:
            callsign = None
    
    icao_address = fields[4].strip().upper() if fields[4] else None
    if not icao_address or len(icao_address) != 6 or any(c not in '0123456789ABCDEF' for c in icao_address):
        return None

    latitude = _optional_float(fields[14])
    longitude = _optional_float(fields[15])
    if latitude is not None and not -90 <= latitude <= 90:
        return None
    if longitude is not None and not -180 <= longitude <= 180:
        return None

    data = {
        'msg_type': msg_type,
        'transmission_type': _optional_int(fields[1]),
        'icao_address': icao_address,
        'callsign': callsign,
        'altitude': _optional_int(fields[11]),
        'ground_speed': _optional_float(fields[12]),
        'track': _optional_float(fields[13]),
        'latitude': latitude,
        'longitude': longitude,
        'vertical_rate': _optional_int(fields[16]),
        'squawk': fields[17].strip() if fields[17] else None,
        'alert': _optional_int(fields[18]),
        'emergency': _optional_int(fields[19]),
        'spi': _optional_int(fields[20]),
        'is_on_ground': _optional_int(fields[21]),
    }
    
    # Parse timestamp if available
    if fields[6] and fields[7]:
        try:
            date_str = fields[6]
            time_str = fields[7]
            data['reported_time'] = datetime.strptime(
                f"{date_str} {time_str}", 
                "%Y/%m/%d %H:%M:%S.%f"
            )
        except ValueError:
            data['reported_time'] = None
    else:
        data['reported_time'] = None
    
    return data


def store_position(cursor, data):
    """
    Store position data in Oracle database.
    Uses throttling to reduce storage volume while maintaining tracking quality.
    """
    if not data or not data['icao_address']:
        return False  # Return False to indicate nothing stored

    try:
        # Backfill altitude/speed/track/etc from this aircraft's last known
        # values - BaseStation splits position and velocity across
        # different message types, so this message alone is often missing
        # fields the aircraft has actually reported (see merge_last_known).
        data = merge_last_known(data)

        # Ensure aircraft exists (always do this for flight tracking)
        ensure_aircraft_exists(cursor, data['icao_address'])
        
        # Get or create flight (always do this for flight tracking)
        flight_id = get_or_create_flight(cursor, data['icao_address'], data['callsign'])
        
        # Check if this triggers any alerts (always check)
        check_alerts(cursor, data, flight_id)
        
        # Apply throttling logic to determine if we should store this position
        should_store, reason = should_store_position(data)
        
        if not should_store:
            return False  # Position was throttled
        
        # Insert position
        cursor.execute(
            """INSERT INTO positions (
                flight_id, icao_address, msg_type, transmission_type,
                reported_time, callsign, altitude, ground_speed, track,
                latitude, longitude, vertical_rate, squawk,
                alert, emergency, spi, is_on_ground
            ) VALUES (
                :fid, :icao, :msg, :trans,
                :rtime, :call, :alt, :speed, :track,
                :lat, :lon, :vrate, :squawk,
                :alert, :emerg, :spi, :ground
            )""",
            fid=flight_id,
            icao=data['icao_address'],
            msg=data['msg_type'],
            trans=data['transmission_type'],
            rtime=data['reported_time'],
            call=data['callsign'],
            alt=data['altitude'],
            speed=data['ground_speed'],
            track=data['track'],
            lat=data['latitude'],
            lon=data['longitude'],
            vrate=data['vertical_rate'],
            squawk=data['squawk'],
            alert=data['alert'],
            emerg=data['emergency'],
            spi=data['spi'],
            ground=data['is_on_ground']
        )
        # Do this only after Oracle accepted the INSERT. Otherwise a failed
        # write could suppress later records as though it had been stored.
        record_stored_position(data, reason)
        route_detection_pending.add((flight_id, data['icao_address'], data['callsign']))
        return True  # Position was stored
        
    except Exception as e:
        print(f"Error storing position: {e}")
        raise


# =============================================================================
# adsbdb.com Route Lookup
# =============================================================================

def _adsbdb_airport(raw):
    """Pull the fields we need out of an adsbdb origin/destination block."""
    if not isinstance(raw, dict):
        return None
    icao = (raw.get('icao_code') or '').strip().upper()
    if not icao:
        return None
    return {
        'icao': icao,
        'iata': (raw.get('iata_code') or '').strip().upper() or None,
        'name': raw.get('name'),
        'city': raw.get('municipality'),
        'country': raw.get('country_name'),
        'lat': raw.get('latitude'),
        'lon': raw.get('longitude'),
        'elevation': raw.get('elevation'),
    }


def fetch_adsbdb_route(callsign):
    """
    Look up a callsign on adsbdb.com.

    Returns (status, route):
        ('found', {'origin': {...}, 'destination': {...}})
        ('not_found', None)  - adsbdb doesn't know this callsign
        ('deferred', None)   - not tried this cycle (budget used, paused,
                               or network error); the flight is retried on
                               a later cycle when it stores another position
    """
    now = time.time()

    cached = adsbdb_cache.get(callsign)
    if cached and cached[0] > now:
        adsbdb_stats['cache_hits'] += 1
        return ('found', cached[1]) if cached[1] else ('not_found', None)

    if now < adsbdb_state['paused_until']:
        return 'deferred', None
    if adsbdb_state['calls_this_cycle'] >= ADSBDB_MAX_LOOKUPS_PER_CYCLE:
        return 'deferred', None

    adsbdb_state['calls_this_cycle'] += 1
    adsbdb_stats['calls'] += 1
    request = Request(
        ADSBDB_URL.format(callsign),
        headers={'User-Agent': 'ADS-B-Tracker/2.7.0 (hobbyist route lookup)',
                 'Accept': 'application/json'}
    )

    try:
        with urlopen(request, timeout=ADSBDB_TIMEOUT_SEC) as response:
            payload = json.loads(response.read().decode('utf-8'))
    except HTTPError as e:
        if e.code == 404:
            # adsbdb answers "unknown callsign" with a 404
            adsbdb_state['failures'] = 0
            adsbdb_stats['not_found'] += 1
            adsbdb_cache[callsign] = (now + ADSBDB_NOT_FOUND_CACHE_HOURS * 3600, None)
            return 'not_found', None
        if e.code == 429:
            print("adsbdb rate limit reached (HTTP 429) - pausing route lookups "
                  f"for {ADSBDB_PAUSE_SECONDS}s")
            adsbdb_state['paused_until'] = now + ADSBDB_PAUSE_SECONDS
            adsbdb_stats['errors'] += 1
            return 'deferred', None
        print(f"adsbdb HTTP error for {callsign}: {e.code} {e.reason}")
        return _adsbdb_network_failure(now)
    except (URLError, socket.timeout, TimeoutError, ConnectionError) as e:
        print(f"adsbdb unreachable for {callsign}: {e}")
        return _adsbdb_network_failure(now)
    except ValueError as e:  # bad JSON
        print(f"adsbdb returned unreadable data for {callsign}: {e}")
        return _adsbdb_network_failure(now)

    adsbdb_state['failures'] = 0
    response_block = payload.get('response') if isinstance(payload, dict) else None
    flightroute = response_block.get('flightroute') if isinstance(response_block, dict) else None
    origin = _adsbdb_airport(flightroute.get('origin')) if isinstance(flightroute, dict) else None
    destination = _adsbdb_airport(flightroute.get('destination')) if isinstance(flightroute, dict) else None

    if not origin or not destination:
        adsbdb_stats['not_found'] += 1
        adsbdb_cache[callsign] = (now + ADSBDB_NOT_FOUND_CACHE_HOURS * 3600, None)
        return 'not_found', None

    route = {'origin': origin, 'destination': destination}
    adsbdb_stats['found'] += 1
    adsbdb_cache[callsign] = (now + ADSBDB_CACHE_HOURS * 3600, route)
    return 'found', route


def _adsbdb_network_failure(now):
    """Count a failed call; pause lookups after several in a row."""
    adsbdb_stats['errors'] += 1
    adsbdb_state['failures'] += 1
    if adsbdb_state['failures'] >= ADSBDB_FAILURES_BEFORE_PAUSE:
        print(f"adsbdb failed {adsbdb_state['failures']} times in a row - "
              f"pausing route lookups for {ADSBDB_PAUSE_SECONDS}s")
        adsbdb_state['paused_until'] = now + ADSBDB_PAUSE_SECONDS
        adsbdb_state['failures'] = 0
    return 'deferred', None


def get_or_create_airport(cursor, airport):
    """Return AIRPORTS.airport_id for an adsbdb airport, inserting it if missing."""
    icao = airport['icao']
    if icao in airport_id_cache:
        return airport_id_cache[icao]

    cursor.execute(
        "SELECT airport_id FROM airports WHERE icao_code = :icao FETCH FIRST 1 ROWS ONLY",
        icao=icao
    )
    row = cursor.fetchone()
    if row:
        airport_id_cache[icao] = row[0]
        return row[0]

    if len(icao) > 4:  # Won't fit AIRPORTS.icao_code VARCHAR2(4)
        return None

    id_var = cursor.var(int)
    cursor.execute(
        """INSERT INTO airports (icao_code, iata_code, airport_name, city, country,
                                 latitude, longitude, elevation_ft)
           VALUES (:icao, :iata, :name, :city, :country, :lat, :lon, :elev)
           RETURNING airport_id INTO :aid""",
        icao=icao,
        iata=airport['iata'][:3] if airport['iata'] else None,
        name=(airport['name'] or '')[:200] or None,
        city=(airport['city'] or '')[:100] or None,
        country=(airport['country'] or '')[:50] or None,
        lat=airport['lat'],
        lon=airport['lon'],
        elev=airport['elevation'],
        aid=id_var
    )
    airport_id = id_var.getvalue()[0]
    airport_id_cache[icao] = airport_id
    print(f"  Added airport {icao} ({airport['name']}) from adsbdb")
    return airport_id


def _adsbdb_eta(icao_address, dest_lat, dest_lon):
    """ETA from the aircraft's last stored position and speed, or None."""
    last = last_stored_positions.get(icao_address)
    if not last or dest_lat is None or dest_lon is None:
        return None
    speed = last.get('speed')
    if not speed or speed < 50:
        return None
    distance = haversine_nm(last.get('lat'), last.get('lon'), dest_lat, dest_lon)
    if distance is None:
        return None
    return datetime.now() + timedelta(hours=distance / speed)


def apply_adsbdb_route(cursor, flight_id, icao_address, callsign):
    """
    Set the route for this flight from adsbdb. Returns True when the flight
    has an adsbdb route (so the position-based detector can be skipped).
    """
    if not ADSBDB_ENABLED:
        return False

    # Already resolved: just refresh the ETA
    if flight_id in adsbdb_flight_dest:
        dest_lat, dest_lon = adsbdb_flight_dest[flight_id]
        eta = _adsbdb_eta(icao_address, dest_lat, dest_lon)
        if eta:
            cursor.execute(
                """UPDATE flight_routes
                   SET estimated_arrival = :eta, last_updated = CURRENT_TIMESTAMP
                   WHERE flight_id = :fid""",
                eta=eta, fid=flight_id
            )
        return True
    if flight_id in adsbdb_done_flights:
        return False  # adsbdb didn't know it; leave it to the detector

    if not callsign or not ADSBDB_CALLSIGN_PATTERN.match(callsign):
        return False

    status, route = fetch_adsbdb_route(callsign)
    if status == 'deferred':
        return False
    adsbdb_done_flights.add(flight_id)
    if status != 'found':
        return False

    origin, dest = route['origin'], route['destination']
    origin_id = get_or_create_airport(cursor, origin)
    dest_id = get_or_create_airport(cursor, dest)
    if not origin_id or not dest_id:
        return False

    distance = haversine_nm(origin['lat'], origin['lon'], dest['lat'], dest['lon'])
    eta = _adsbdb_eta(icao_address, dest['lat'], dest['lon'])

    # adsbdb wins over any route the detector guessed earlier
    cursor.execute(
        """MERGE INTO flight_routes fr
           USING (SELECT :fid AS flight_id FROM dual) src
           ON (fr.flight_id = src.flight_id)
           WHEN MATCHED THEN UPDATE SET
                fr.origin_airport_id = :origin_id,
                fr.dest_airport_id   = :dest_id,
                fr.route_distance_nm = :dist,
                fr.estimated_arrival = NVL(:eta, fr.estimated_arrival),
                fr.last_updated      = CURRENT_TIMESTAMP
           WHEN NOT MATCHED THEN INSERT
                (flight_id, origin_airport_id, dest_airport_id, route_distance_nm, estimated_arrival)
                VALUES (:fid, :origin_id, :dest_id, :dist, :eta)""",
        fid=flight_id,
        origin_id=origin_id,
        dest_id=dest_id,
        dist=round(distance) if distance is not None else None,
        eta=eta
    )

    adsbdb_flight_dest[flight_id] = (dest['lat'], dest['lon'])
    print(f"Route (adsbdb) for {callsign}: {origin['icao']} -> {dest['icao']}")
    return True


def get_adsbdb_stats_summary():
    s = adsbdb_stats
    return (f"adsbdb: {s['calls']} calls, {s['found']} found, {s['not_found']} not found, "
            f"{s['errors']} errors, {s['cache_hits']} cache hits")


def cleanup_adsbdb_state():
    """Forget ended flights and expired cache entries."""
    active_ids = {info['flight_id'] for info in active_flights.values()}
    for fid in list(adsbdb_flight_dest):
        if fid not in active_ids:
            del adsbdb_flight_dest[fid]
    adsbdb_done_flights.intersection_update(active_ids)
    now = time.time()
    for callsign, (expires, _route) in list(adsbdb_cache.items()):
        if expires < now:
            del adsbdb_cache[callsign]


# =============================================================================
# Resource Cleanup
# =============================================================================

def cleanup_resources(cursor=None, db_conn=None, sock=None):
    """
    Clean up all resources gracefully before restart or shutdown.
    Closes socket, commits pending DB work, closes cursor and connection.
    """
    print("\nCleaning up resources...")

    # Close socket
    if sock:
        try:
            sock.shutdown(2)   # SHUT_RDWR
            sock.close()
            print("  ✓ Socket closed")
        except Exception:
            pass

    # Commit any pending database work
    if db_conn:
        try:
            db_conn.commit()
            print("  ✓ Database committed")
        except Exception:
            pass

    # Close cursor
    if cursor:
        try:
            cursor.close()
            print("  ✓ Cursor closed")
        except Exception:
            pass

    # Close connection
    if db_conn:
        try:
            db_conn.close()
            print("  ✓ Database connection closed")
        except Exception:
            pass


# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    """Main collector loop"""
    global last_data_time

    # Let service managers terminate the collector without losing the pending
    # transaction or leaving the socket open.
    signal.signal(signal.SIGTERM, lambda _signum, _frame: (_ for _ in ()).throw(KeyboardInterrupt()))
    
    print("="*80)
    print("ADS-B Data Collector v2.7.0 starting...")
    print(f"Platform: {platform.system()}")
    print("="*80)
    print("\nConfiguration:")
    print(f"  Auto-restart: After {MAX_MESSAGES_BEFORE_RESTART:,} messages")
    print(f"  Socket timeout: {SOCKET_TIMEOUT} seconds")
    print(f"  TCP keepalive: {KEEPALIVE_INTERVAL} seconds")
    print(f"  Flight timeout: {FLIGHT_TIMEOUT_MINUTES} minutes")
    print(f"  Pi reboot: Daily at {PI_REBOOT_HOUR_UTC:02d}:00 UTC")
    print(f"  FAA cache: {FAA_CACHE_DIR}")
    print("\nPosition Throttling:")
    print(f"  Enabled: {THROTTLE_ENABLED}")
    if THROTTLE_ENABLED:
        print(f"  Min interval: {THROTTLE_MIN_INTERVAL_SEC}s")
        print(f"  Max interval: {THROTTLE_MAX_INTERVAL_SEC}s")
        print(f"  Distance threshold: {THROTTLE_MIN_DISTANCE_NM} NM")
        print(f"  Altitude threshold: {THROTTLE_MIN_ALT_CHANGE_FT} ft")
        print(f"  Heading threshold: {THROTTLE_MIN_TRACK_CHANGE_DEG}°")
        print(f"  Speed threshold: {THROTTLE_MIN_SPEED_CHANGE_KT} kt")
        print(f"  Always store: squawk={THROTTLE_ALWAYS_STORE_SQUAWK_CHANGE}, "
              f"ground={THROTTLE_ALWAYS_STORE_GROUND_CHANGE}, "
              f"emergency={THROTTLE_ALWAYS_STORE_EMERGENCY}")
    print("="*80)
    
    # Load aircraft database (FAA Registry)
    load_aircraft_database()
    
    print(f"Connecting to {ADSB_HOST}:{ADSB_PORT}")
    
    # Connect to database
    db_conn = None
    cursor = None
    sock = None
    
    try:
        db_conn = connect_to_database()
        cursor = db_conn.cursor()
        
        # Load airline and alert data
        load_airline_database(cursor)
        load_alert_rules(cursor)
        
        # Initialize route detector
        global route_detector
        route_detector = RouteDetector(cursor)
        print("Route detector initialized")
        
        # Run initial cleanup
        print("Running initial flight cleanup...")
        cleanup_stale_flights(cursor)
        db_conn.commit()
        
        # Statistics
        message_count = 0
        positions_stored = 0
        last_commit_time = time.time()
        last_cleanup_time = time.time()
        last_stats_time = time.time()
        commit_interval = 10  # Commit every 10 seconds
        stats_interval = 60   # Show throttle stats every 60 seconds
        callsigns_seen = set()  # Track unique callsigns for debugging
        connection_attempts = 0
        
        while True:
            try:
                connection_attempts += 1
                
                # Create socket with keepalive and timeout
                sock = create_resilient_socket()
                
                try:
                    sock.connect((ADSB_HOST, ADSB_PORT))
                    print(f"Connected to ADS-B feed at {ADSB_HOST}:{ADSB_PORT} (attempt #{connection_attempts})")
                    last_data_time = time.time()
                    
                    buffer = ""
                    
                    while True:
                        try:
                            data = sock.recv(4096).decode('utf-8', errors='ignore')
                            
                            if not data:
                                print("Connection closed by remote host")
                                delay = get_reconnect_delay()
                                print(f"Reconnecting in {delay} seconds...")
                                time.sleep(delay)
                                break
                            
                            # Update heartbeat
                            last_data_time = time.time()
                            
                            buffer += data
                            if len(buffer) > MAX_RECEIVE_BUFFER_BYTES:
                                print(f"Discarding oversized unterminated feed buffer ({len(buffer):,} bytes)")
                                buffer = ""
                                continue
                            
                            # Process complete lines
                            while '\n' in buffer:
                                line, buffer = buffer.split('\n', 1)
                                
                                # Parse and store message
                                parsed = parse_basestation_message(line)
                                if parsed:
                                    # Debug: Track callsigns
                                    if (parsed['callsign'] and parsed['callsign'] not in callsigns_seen
                                            and len(callsigns_seen) < MAX_TRACKED_CALLSIGNS):
                                        callsigns_seen.add(parsed['callsign'])
                                        print(f"New callsign detected: {parsed['callsign']} (ICAO: {parsed['icao_address']})")
                                    
                                    # Store position (with throttling)
                                    stored = store_position(cursor, parsed)
                                    message_count += 1
                                    if stored:
                                        positions_stored += 1
                                    
                                    # Print progress
                                    if message_count % 1000 == 0:
                                        print(f"Processed {message_count} messages, stored {positions_stored} positions, "
                                              f"{len(callsigns_seen)} callsigns, {len(active_flights)} active flights")
                            
                            # Commit periodically
                            current_time = time.time()
                            if current_time - last_commit_time > commit_interval:
                                db_conn.commit()
                                last_commit_time = current_time
                                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                                
                                # Calculate time since last data
                                time_since_data = current_time - last_data_time
                                print(f"[{timestamp}] Committed. Messages: {message_count}, "
                                      f"Stored: {positions_stored}, Last data: {time_since_data:.1f}s ago")
                                
                                # Show throttle stats periodically
                                if current_time - last_stats_time > stats_interval:
                                    print(f"  {get_throttle_stats_summary()}")
                                    print(f"  {get_adsbdb_stats_summary()}")
                                    last_stats_time = current_time
                                
                                # Check if we've reached the restart threshold
                                if message_count >= MAX_MESSAGES_BEFORE_RESTART:
                                    cleanup_resources(cursor, db_conn, sock)
                                    restart_after_delay(0, f"reached {MAX_MESSAGES_BEFORE_RESTART} messages")
                                
                                # Only recalculate routes that received a newly
                                # persisted position since the previous commit.
                                if route_detector and route_detection_pending:
                                    try:
                                        pending_routes = tuple(route_detection_pending)
                                        adsbdb_state['calls_this_cycle'] = 0
                                        for fid, icao, callsign in pending_routes:
                                            # adsbdb first; position-based guess only as fallback
                                            if not apply_adsbdb_route(cursor, fid, icao, callsign):
                                                route_detector.update_flight_route(fid, icao, callsign)
                                        db_conn.commit()
                                        route_detection_pending.difference_update(pending_routes)
                                    except Exception as e:
                                        print(f"Route detection error: {e}")
                            
                            # Run cleanup periodically
                            if current_time - last_cleanup_time > CLEANUP_INTERVAL_SECONDS:
                                print("\nRunning flight cleanup...")
                                cleanup_stale_flights(cursor)
                                db_conn.commit()
                                last_cleanup_time = current_time
                                print(f"Cleanup complete. Active flights: {len(active_flights)}, "
                                      f"Throttle cache: {len(last_stored_positions)}\n")
                        
                        except socket.timeout:
                            # Socket timeout - connection is frozen
                            elapsed = time.time() - last_data_time
                            print(f"\n⚠️  Socket timeout after {elapsed:.1f}s with no data")
                            print("Connection appears frozen - will reconnect...")
                            break
                
                finally:
                    # Always close the socket
                    try:
                        sock.close()
                    except:
                        pass
            
            except KeyboardInterrupt:
                print("\n\nShutting down...")
                print(f"\nFinal {get_throttle_stats_summary()}")
                cleanup_resources(cursor, db_conn, sock)
                sys.exit(0)
            
            except socket.timeout:
                print(f"\n⚠️  Connection timeout - no data received for {SOCKET_TIMEOUT} seconds")
                delay = get_reconnect_delay()
                print(f"Reconnecting in {delay} seconds...")
                time.sleep(delay)

            except ConnectionRefusedError:
                print(f"\n⚠️  Connection refused by {ADSB_HOST}:{ADSB_PORT}")
                delay = get_reconnect_delay()
                print(f"Is the ADS-B receiver running? Retrying in {delay} seconds...")
                time.sleep(delay)

            # NOTE: this must come AFTER the socket.timeout / ConnectionRefusedError
            # handlers above, not before. Both of those are subclasses of OSError,
            # so if this handler were listed first it would intercept them, find
            # an errno that isn't EMFILE, and hit the `raise` below — which skips
            # the more specific handlers entirely (a `raise` inside an except
            # block propagates straight out of the whole try/except chain, it
            # does not get re-matched against sibling except clauses). That
            # escaped exception was what was reaching the outer "FATAL ERROR
            # during initialization" handler and killing the process instead of
            # reconnecting after a delay.
            except OSError as e:
                if e.errno == errno.EMFILE:  # errno 24: Too many open files
                    print(f"\n{'!'*80}")
                    print(f"ERROR: Too many open files detected (errno 24)")
                    print(f"Error details: {e}")
                    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                    print(f"This is likely caused by iCloud Drive file watchers")
                    print(f"{'!'*80}\n")

                    # Clean up resources
                    cleanup_resources(cursor, db_conn, sock)

                    # Restart after delay
                    restart_after_delay(FILE_ERROR_RESTART_DELAY, "too many open files error")
                else:
                    # Any other OS/network-level error (host unreachable, network
                    # down, broken pipe, etc.) — retry with a delay instead of
                    # letting it escape to the fatal handler and exit the process.
                    print(f"\n⚠️  Network/OS error (errno {e.errno}): {e}")
                    delay = get_reconnect_delay()
                    print(f"Reconnecting in {delay} seconds...")
                    time.sleep(delay)

            except Exception as e:
                print(f"\n⚠️  Unexpected error: {e}")
                import traceback
                traceback.print_exc()

                # If this was the Oracle connection dying underneath us
                # (network blip, Mac sleep/wake, idle-killed session), the
                # cursor/connection are now unusable and every subsequent
                # message will hit the exact same error forever unless we
                # get a fresh connection here.
                if not is_connection_healthy(db_conn):
                    try:
                        db_conn, cursor = reconnect_database(cursor, db_conn)
                        route_detector = RouteDetector(cursor)
                        print("  ✓ Database reconnected")
                    except Exception as reconnect_error:
                        print(f"  ✗ Database reconnect failed: {reconnect_error}")
                else:
                    # Clear failed DML before continuing with a healthy Oracle
                    # session; a later commit must not accidentally include it.
                    try:
                        db_conn.rollback()
                    except Exception:
                        pass

                delay = get_reconnect_delay()
                print(f"Reconnecting in {delay} seconds...")
                time.sleep(delay)
    
    except Exception as e:
        # Catch any errors during initialization
        print(f"\n{'!'*80}")
        print(f"FATAL ERROR during initialization: {e}")
        print(f"{'!'*80}\n")
        import traceback
        traceback.print_exc()
        
        # Clean up what we can
        cleanup_resources(cursor, db_conn, sock)
        
        # Exit with error code
        sys.exit(1)


if __name__ == '__main__':
    main()
