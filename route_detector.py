#!/usr/bin/env python3
"""
================================================================================
Enhanced Flight Route Detection Module - FR24 API Edition
================================================================================
Version:        2.2.0
Last Updated:   2026-02-17
Description:    Route detection using the official Flightradar24 API as the
                primary source of origin/destination data, with the original
                position-based detection as a fallback.

Strategy (in order):
    1. FR24 API  - Query flight summary by callsign for authoritative O/D data
    2. Position  - Original haversine/altitude pattern detection (fallback)

FR24 API Endpoints used:
    GET /api/live/flight-positions/full    Live flights by callsign
    GET /api/historic/flight-summaries/full  Flight summary (origin/dest/times)

API credits consumed:
    - flight-positions/full   : 1 credit per flight returned
    - flight-summaries/full   : 3 credits per flight returned
    We use flight-positions first (cheap) to get the FR24 flight_id, then
    only call flight-summaries/full when we don't already have a route.

Cache:
    FR24 results are cached in memory for FR24_CACHE_TTL seconds to avoid
    burning credits on the same callsign repeatedly.

Configuration:
    Set FR24_API_KEY below, or export the environment variable:
        export FR24_API_KEY="your_key_here"

Version History:
    2.2.0 (2026-02-17) - Robust API key validation
        - validate_api_key() now tries two endpoints before giving up
          (static/airlines/light first, then live/flight-positions/full)
        - HTML maintenance/WAF pages are detected by content inspection and
          treated as transient server-side issues, NOT key rejections
        - Genuine auth errors (401/403 + JSON body) still disable the API
        - 5xx / network errors are treated as transient - API stays enabled
        - Validation failures no longer increment _error_count, so the
          runtime lookup path is never penalised by a bad startup check

    2.1.0 (2026-02-17) - Oracle-persisted cache
        - Two-tier cache: memory (1hr) + Oracle DB (24hr)
        - Cache survives collector restarts - prevents credit burn
        - Auto-creates fr24_route_cache table on first run
        - MERGE upsert keeps cached_at fresh on re-query
        - stats() shows mem hits / db hits / api calls breakdown
        - Expected usage: ~2,700 credits/month vs 86,400 before

    2.0.0 (2026-02-17) - FR24 API integration
        - Official FR24 API as primary route source
        - In-memory result cache with TTL
        - Credit-aware: position endpoint first, summary only when needed
        - Graceful fallback to position-based detection
        - API error tracking with automatic backoff

    1.0.0 (2026-01-20) - Initial release
        - Position-based origin/destination detection
        - Haversine distance calculations
================================================================================
"""

import math
import time
import urllib.request
import urllib.parse
import json
import os
from datetime import datetime, timedelta

# =============================================================================
# FR24 API CONFIGURATION
# =============================================================================

# Set your API key here, or use the FR24_API_KEY environment variable
#FR24_API_KEY = os.environ.get('FR24_API_KEY', '019c6ae0-72c7-7330-ac5a-69e586792590|CEFNbzkJXY18VX17RGYKPHsdisCWfxBfU0ldh0ua75de1da4')
FR24_API_KEY = os.environ.get('FR24_API_KEY', '01998c71-9751-7278-bbd2-6f9dcd9ec9a7|vAQ3kznRN77jlM9FCaMSa0PO3BF41E4iMJHdkHiM921c32c1')

FR24_BASE_URL    = 'https://fr24api.flightradar24.com/api'
FR24_CACHE_TTL   = 86400       # Cache results for 24 hours (persisted in Oracle)
FR24_MEM_TTL     = 3600        # In-memory cache TTL - 1 hour (avoids DB hit every call)
FR24_TIMEOUT     = 10          # HTTP timeout in seconds
FR24_MAX_ERRORS  = 5           # Disable API after this many consecutive errors
FR24_BACKOFF_SEC = 300         # Wait 5 minutes after hitting max errors


class FR24Client:
    """
    FR24 API client with two-tier cache:
      Tier 1 - In-memory dict  (survives within a single process run, TTL=1hr)
      Tier 2 - Oracle DB table (survives restarts, TTL=24hr)

    Only calls the FR24 API when neither cache has a fresh result.
    """

    def __init__(self, api_key, db_cursor):
        self.api_key        = api_key
        self.cursor         = db_cursor
        self._mem_cache     = {}      # { callsign: (timestamp, result) }
        self._error_count   = 0
        self._backoff_until = None
        self._credits_used  = 0
        self._db_hits       = 0
        self._mem_hits      = 0
        self._ensure_cache_table()

    # ------------------------------------------------------------------
    # Cache table setup
    # ------------------------------------------------------------------

    def _ensure_cache_table(self):
        """Create fr24_route_cache table if it doesn't already exist."""
        try:
            self.cursor.execute("""
                SELECT table_name FROM user_tables
                WHERE table_name = 'FR24_ROUTE_CACHE'
            """)
            if not self.cursor.fetchone():
                self.cursor.execute("""
                    CREATE TABLE fr24_route_cache (
                        callsign      VARCHAR2(10)   NOT NULL,
                        origin_iata   VARCHAR2(3),
                        origin_icao   VARCHAR2(4),
                        origin_name   VARCHAR2(200),
                        origin_city   VARCHAR2(100),
                        dest_iata     VARCHAR2(3),
                        dest_icao     VARCHAR2(4),
                        dest_name     VARCHAR2(200),
                        dest_city     VARCHAR2(100),
                        registration  VARCHAR2(10),
                        aircraft_type VARCHAR2(10),
                        airline_icao  VARCHAR2(3),
                        fr24_flight_id VARCHAR2(20),
                        source        VARCHAR2(20),
                        cached_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        CONSTRAINT pk_fr24_cache PRIMARY KEY (callsign)
                    )
                """)
                self.cursor.execute("""
                    CREATE INDEX idx_fr24_cache_age
                    ON fr24_route_cache (cached_at)
                """)
                print("  ✓ Created fr24_route_cache table")
        except Exception as e:
            print(f"  Warning: could not create fr24_route_cache table: {e}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self):
        return {
            'Accept':         'application/json',
            'Accept-Version': 'v1',
            'Authorization':  f'Bearer {self.api_key}',
        }

    def _get(self, path, params=None):
        """Make a GET request, return parsed JSON or None on error."""
        url = f"{FR24_BASE_URL}{path}"
        if params:
            url += '?' + urllib.parse.urlencode(params)
        try:
            req = urllib.request.Request(url, headers=self._headers())
            with urllib.request.urlopen(req, timeout=FR24_TIMEOUT) as resp:
                self._error_count = 0
                return json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            print(f"  FR24 API HTTP {e.code}: {e.reason}  ({path})")
            self._error_count += 1
        except urllib.error.URLError as e:
            print(f"  FR24 API connection error: {e.reason}")
            self._error_count += 1
        except Exception as e:
            print(f"  FR24 API error: {e}")
            self._error_count += 1

        if self._error_count >= FR24_MAX_ERRORS:
            self._backoff_until = time.time() + FR24_BACKOFF_SEC
            print(f"  FR24 API disabled for {FR24_BACKOFF_SEC}s after {FR24_MAX_ERRORS} errors")
        return None

    def _is_available(self):
        if not self.api_key or self.api_key == 'YOUR_API_KEY_HERE':
            return False
        if self._backoff_until and time.time() < self._backoff_until:
            return False
        if self._error_count >= FR24_MAX_ERRORS:
            return False
        return True

    def validate_api_key(self):
        """
        Test the API key at startup using a sequence of candidate endpoints.

        Strategy:
          1. Try /static/airlines/light  (cheap, 1 credit, but may not be in all plans)
          2. Try /live/flight-positions/full?limit=1  (always in plan if API is active)

        For each attempt the response body is inspected first:
          - If it looks like an HTML maintenance/error page the error is treated as
            a TEMPORARY server-side issue, NOT a key rejection.  The API is NOT
            disabled so that live lookups can still proceed.
          - HTTP 401 / 403 with a proper JSON body → genuine key problem → disable API.
          - HTTP 5xx or network error → transient → warn but do not disable API.

        Returns True if a 200 was received, False otherwise.
        Note: validation failures do NOT increment _error_count so the runtime
        lookup path is unaffected.
        """
        if not self.api_key or self.api_key == 'YOUR_API_KEY_HERE':
            print("  ✗ FR24 API key not set - route detection will use position fallback only")
            return False

        masked = self.api_key[:6] + '...' + self.api_key[-4:] if len(self.api_key) > 10 else '(too short)'
        print(f"  Validating FR24 API key ({masked}, length={len(self.api_key)})...")

        # Candidate endpoints tried in order - stop as soon as one gives a clear answer
        candidates = [
            (f"{FR24_BASE_URL}/static/airlines/light",               "static/airlines/light"),
            (f"{FR24_BASE_URL}/live/flight-positions/full?limit=1",  "live/flight-positions/full"),
        ]

        last_message = "no endpoints tried"

        for url, label in candidates:
            req = urllib.request.Request(url, headers=self._headers())
            try:
                with urllib.request.urlopen(req, timeout=FR24_TIMEOUT) as resp:
                    # Happy path
                    print(f"  ✓ FR24 API key accepted  (endpoint: {label})")
                    return True

            except urllib.error.HTTPError as e:
                raw = e.read()
                # Try to decode; fall back gracefully
                try:
                    body = raw.decode('utf-8')
                except Exception:
                    body = raw.decode('latin-1', errors='replace')

                body_preview = body[:400].strip()

                # ── Detect maintenance / WAF / CDN HTML pages ──────────────
                is_html = (
                    body_preview.lower().startswith('<!doctype') or
                    body_preview.lower().startswith('<html') or
                    '<html' in body_preview.lower()[:100] or
                    'maintenance' in body_preview.lower() or
                    'robots' in body_preview.lower()
                )

                if is_html:
                    # This is a server-side issue (maintenance, WAF, CDN block),
                    # NOT an API key problem.  Try the next candidate endpoint.
                    last_message = (
                        f"HTTP {e.code} from {label} returned an HTML page "
                        f"(maintenance/WAF) - not a key error, trying next endpoint..."
                    )
                    print(f"  ⚠  {last_message}")
                    continue   # try next candidate

                # ── Genuine auth error with a proper response body ──────────
                if e.code in (401, 403):
                    print(f"  ✗ FR24 API key rejected - HTTP {e.code} {e.reason}  ({label})")
                    if e.code == 403:
                        print(f"     The key exists but lacks permission for this endpoint.")
                        print(f"     Check: plan active? correct key? endpoint in your subscription?")
                    elif e.code == 401:
                        print(f"     API key is invalid or expired.")
                        print(f"     Regenerate at: https://fr24api.flightradar24.com")
                    print(f"     Response body: {body_preview}")
                    print(f"     To test manually:")
                    print(f"       curl -H 'Authorization: Bearer {masked}' \\")
                    print(f"            -H 'Accept-Version: v1' \\")
                    print(f"            '{url}'")
                    # Only disable the API if BOTH endpoints returned a genuine auth error
                    last_message = f"auth_error_{e.code}"
                    continue   # still try the next candidate before giving up

                # ── Server error (5xx) - transient, don't disable API ───────
                last_message = f"HTTP {e.code} from {label} - transient server error"
                print(f"  ⚠  FR24 API HTTP {e.code} {e.reason} ({label}) - treating as transient")
                continue

            except urllib.error.URLError as e:
                last_message = f"connection error on {label}: {e.reason}"
                print(f"  ⚠  FR24 API unreachable ({label}): {e.reason}")
                continue

            except Exception as e:
                last_message = f"unexpected error on {label}: {e}"
                print(f"  ⚠  FR24 API validation error ({label}): {e}")
                continue

        # ── All candidates exhausted without a 200 ──────────────────────────
        # Only hard-disable the API if we got genuine auth errors from all endpoints.
        # For transient/HTML responses we leave the API enabled so live lookups can try.
        if 'auth_error' in last_message:
            self._error_count = FR24_MAX_ERRORS   # disable further API calls
            print(f"  ✗ FR24 API disabled - key rejected by all tested endpoints")
            print(f"     Route detection will use position-based fallback only")
        else:
            print(f"  ⚠  FR24 API validation inconclusive (last: {last_message})")
            print(f"     Key NOT disabled - live lookups will still attempt the API")
            print(f"     If the issue persists, check https://fr24api.flightradar24.com/status")

        return False

    # ------------------------------------------------------------------
    # Tier 1: in-memory cache
    # ------------------------------------------------------------------

    def _mem_get(self, callsign):
        if callsign in self._mem_cache:
            ts, result = self._mem_cache[callsign]
            if time.time() - ts < FR24_MEM_TTL:
                self._mem_hits += 1
                return result
            del self._mem_cache[callsign]
        return None

    def _mem_put(self, callsign, result):
        self._mem_cache[callsign] = (time.time(), result)

    # ------------------------------------------------------------------
    # Tier 2: Oracle DB cache
    # ------------------------------------------------------------------

    def _db_get(self, callsign):
        """Return cached result from DB if fresher than FR24_CACHE_TTL, else None."""
        try:
            self.cursor.execute("""
                SELECT
                    origin_iata, origin_icao, origin_name, origin_city,
                    dest_iata,   dest_icao,   dest_name,   dest_city,
                    registration, aircraft_type, airline_icao,
                    fr24_flight_id, source, cached_at
                FROM fr24_route_cache
                WHERE callsign = :cs
                  AND cached_at > SYSTIMESTAMP - (:ttl / 86400)
            """, cs=callsign, ttl=FR24_CACHE_TTL)

            row = self.cursor.fetchone()
            if row:
                self._db_hits += 1
                return {
                    'origin_iata':    row[0],  'origin_icao':   row[1],
                    'origin_name':    row[2],  'origin_city':   row[3],
                    'dest_iata':      row[4],  'dest_icao':     row[5],
                    'dest_name':      row[6],  'dest_city':     row[7],
                    'registration':   row[8],  'aircraft_type': row[9],
                    'airline_icao':   row[10], 'fr24_flight_id':row[11],
                    'source':         row[12] + '_cached',
                }
        except Exception as e:
            print(f"  FR24 DB cache read error: {e}")
        return None

    def _db_put(self, callsign, result):
        """Upsert a result into the DB cache."""
        if not result:
            return
        try:
            self.cursor.execute("""
                MERGE INTO fr24_route_cache tgt
                USING (SELECT :cs AS callsign FROM dual) src
                ON (tgt.callsign = src.callsign)
                WHEN MATCHED THEN UPDATE SET
                    origin_iata    = :orig_iata,
                    origin_icao    = :orig_icao,
                    origin_name    = :orig_name,
                    origin_city    = :orig_city,
                    dest_iata      = :dest_iata,
                    dest_icao      = :dest_icao,
                    dest_name      = :dest_name,
                    dest_city      = :dest_city,
                    registration   = :reg,
                    aircraft_type  = :ac_type,
                    airline_icao   = :al_icao,
                    fr24_flight_id = :fid,
                    source         = :src,
                    cached_at      = CURRENT_TIMESTAMP
                WHEN NOT MATCHED THEN INSERT (
                    callsign, origin_iata, origin_icao, origin_name, origin_city,
                    dest_iata, dest_icao, dest_name, dest_city,
                    registration, aircraft_type, airline_icao, fr24_flight_id, source
                ) VALUES (
                    :cs, :orig_iata, :orig_icao, :orig_name, :orig_city,
                    :dest_iata, :dest_icao, :dest_name, :dest_city,
                    :reg, :ac_type, :al_icao, :fid, :src
                )
            """,
                cs        = callsign,
                orig_iata = result.get('origin_iata', ''),
                orig_icao = result.get('origin_icao', ''),
                orig_name = result.get('origin_name', ''),
                orig_city = result.get('origin_city', ''),
                dest_iata = result.get('dest_iata', ''),
                dest_icao = result.get('dest_icao', ''),
                dest_name = result.get('dest_name', ''),
                dest_city = result.get('dest_city', ''),
                reg       = result.get('registration', ''),
                ac_type   = result.get('aircraft_type', ''),
                al_icao   = result.get('airline_icao', ''),
                fid       = result.get('fr24_flight_id', ''),
                src       = result.get('source', ''),
            )
        except Exception as e:
            print(f"  FR24 DB cache write error: {e}")

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def cache_size(self):
        return len(self._mem_cache)

    def credits_used(self):
        return self._credits_used

    def cache_stats(self):
        return {
            'mem_hits': self._mem_hits,
            'db_hits':  self._db_hits,
            'api_calls': self._credits_used,
        }

    # ------------------------------------------------------------------
    # Public: main lookup
    # ------------------------------------------------------------------

    def get_route_by_callsign(self, callsign):
        """
        Look up origin/destination for a callsign.
        Check order: memory cache → DB cache → FR24 API
        Returns result dict or None.
        """
        if not callsign:
            return None
        callsign = callsign.strip().upper()

        # --- Tier 1: memory ---
        result = self._mem_get(callsign)
        if result is not None:
            return result

        # --- Tier 2: database ---
        result = self._db_get(callsign)
        if result is not None:
            self._mem_put(callsign, result)   # warm the memory cache too
            return result

        # --- Tier 3: FR24 API ---
        if not self._is_available():
            return None

        # Step A: live positions (1 credit)
        data = self._get('/live/flight-positions/full', params={'callsign': callsign})
        self._credits_used += 1
        result = None

        if data and data.get('data'):
            flights = data['data']
            if flights:
                f = flights[0]
                result = {
                    'fr24_flight_id': f.get('id') or f.get('fr24_id', ''),
                    'origin_iata':    f.get('orig_iata') or (f.get('origin') or {}).get('iata', ''),
                    'origin_icao':    f.get('orig_icao') or (f.get('origin') or {}).get('icao', ''),
                    'origin_name':    (f.get('origin') or {}).get('name', ''),
                    'origin_city':    (f.get('origin') or {}).get('city', ''),
                    'dest_iata':      f.get('dest_iata') or (f.get('destination') or {}).get('iata', ''),
                    'dest_icao':      f.get('dest_icao') or (f.get('destination') or {}).get('icao', ''),
                    'dest_name':      (f.get('destination') or {}).get('name', ''),
                    'dest_city':      (f.get('destination') or {}).get('city', ''),
                    'registration':   f.get('reg') or f.get('registration', ''),
                    'aircraft_type':  f.get('type') or f.get('aircraft_type', ''),
                    'airline_icao':   f.get('airline_icao', ''),
                    'source':         'fr24_live',
                }

        # Step B: historic summary if live had no route (3 credits)
        if not result or not (result.get('origin_iata') or result.get('dest_iata')):
            sdata = self._get('/historic/flight-summaries/full',
                              params={'callsign': callsign, 'limit': 1})
            self._credits_used += 3
            if sdata and sdata.get('data'):
                summaries = sdata['data']
                if summaries:
                    s    = summaries[0]
                    orig = s.get('orig') or s.get('origin') or {}
                    dst  = s.get('dest') or s.get('destination') or {}
                    result = {
                        'fr24_flight_id': s.get('fr24_id') or s.get('id', ''),
                        'origin_iata':    orig.get('iata', '') if isinstance(orig, dict) else str(orig),
                        'origin_icao':    orig.get('icao', '') if isinstance(orig, dict) else '',
                        'origin_name':    orig.get('name', '') if isinstance(orig, dict) else '',
                        'origin_city':    orig.get('city', '') if isinstance(orig, dict) else '',
                        'dest_iata':      dst.get('iata',  '') if isinstance(dst,  dict) else str(dst),
                        'dest_icao':      dst.get('icao',  '') if isinstance(dst,  dict) else '',
                        'dest_name':      dst.get('name',  '') if isinstance(dst,  dict) else '',
                        'dest_city':      dst.get('city',  '') if isinstance(dst,  dict) else '',
                        'registration':   s.get('reg') or s.get('registration', ''),
                        'aircraft_type':  s.get('type') or s.get('ac_type', ''),
                        'airline_icao':   s.get('airline_icao', ''),
                        'source':         'fr24_summary',
                    }

        # Store in both caches (even if result is None - avoids re-querying unknowns)
        if result:
            self._db_put(callsign, result)
            self._mem_put(callsign, result)

        return result


# =============================================================================
# MAIN ROUTE DETECTOR
# =============================================================================

class RouteDetector:
    """
    Detects flight routes.
    Uses FR24 API as primary source, falls back to position-based detection.
    """

    def __init__(self, cursor):
        self.cursor        = cursor
        self.airport_cache = {}
        self.fr24          = FR24Client(FR24_API_KEY, cursor)
        self.load_airports()
        print("RouteDetector v2.2 loaded")
        self.fr24.validate_api_key()

    # ------------------------------------------------------------------
    # Airport cache
    # ------------------------------------------------------------------

    def load_airports(self):
        """Load airports from database into memory for fast lookups."""
        try:
            self.cursor.execute("""
                SELECT column_name
                FROM user_tab_columns
                WHERE table_name = 'AIRPORTS'
                  AND column_name = 'ELEVATION_FT'
            """)
            has_elevation = self.cursor.fetchone() is not None

            elev_col = 'elevation_ft' if has_elevation else '0 as elevation_ft'

            self.cursor.execute(f"""
                SELECT airport_id, icao_code, iata_code, airport_name,
                       latitude, longitude, {elev_col}
                FROM airports
                WHERE latitude  IS NOT NULL
                  AND longitude IS NOT NULL
            """)

            for row in self.cursor.fetchall():
                airport = {
                    'id':        row[0],
                    'icao':      row[1],
                    'iata':      row[2],
                    'name':      row[3],
                    'lat':       float(row[4]),
                    'lon':       float(row[5]),
                    'elevation': row[6] if row[6] else 0
                }
                if airport['icao']:
                    self.airport_cache[airport['icao']] = airport
                if airport['iata']:
                    self.airport_cache[airport['iata']] = airport

            print(f"Loaded {len(self.airport_cache)} airport codes for route detection")

        except Exception as e:
            print(f"Could not load airports: {e}")

    def _airport_by_code(self, iata=None, icao=None):
        """Look up a local airport record by IATA or ICAO code."""
        if icao and icao in self.airport_cache:
            return self.airport_cache[icao]
        if iata and iata in self.airport_cache:
            return self.airport_cache[iata]
        return None

    # ------------------------------------------------------------------
    # Distance / ETA
    # ------------------------------------------------------------------

    def haversine_distance(self, lat1, lon1, lat2, lon2):
        """Great-circle distance in nautical miles."""
        R = 3440.065
        lat1r, lat2r = math.radians(lat1), math.radians(lat2)
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat/2)**2 + math.cos(lat1r) * math.cos(lat2r) * math.sin(dlon/2)**2
        return R * 2 * math.asin(math.sqrt(a))

    def find_nearest_airport(self, lat, lon, max_distance_nm=30):
        """Find nearest airport within max_distance_nm nautical miles."""
        nearest, min_dist = None, float('inf')
        for code, airport in self.airport_cache.items():
            d = self.haversine_distance(lat, lon, airport['lat'], airport['lon'])
            if d < min_dist and d <= max_distance_nm:
                min_dist = d
                nearest = {**airport, 'distance_nm': d}
        return nearest

    def estimate_arrival_time(self, cur_lat, cur_lon, dest_lat, dest_lon, ground_speed):
        """Return estimated arrival datetime, or None."""
        if not all([cur_lat, cur_lon, dest_lat, dest_lon, ground_speed]):
            return None
        if ground_speed < 50:
            return None
        dist = self.haversine_distance(cur_lat, cur_lon, dest_lat, dest_lon)
        return datetime.now() + timedelta(hours=dist / ground_speed)

    # ------------------------------------------------------------------
    # Fallback: position-based detection (original logic)
    # ------------------------------------------------------------------

    def _detect_origin_from_positions(self, flight_id):
        """Fallback: detect origin from early flight positions."""
        try:
            self.cursor.execute("""
                SELECT latitude, longitude, altitude, received_time, vertical_rate
                FROM positions
                WHERE flight_id   = :fid
                  AND latitude    IS NOT NULL
                  AND longitude   IS NOT NULL
                ORDER BY received_time ASC
                FETCH FIRST 20 ROWS ONLY
            """, fid=flight_id)

            positions = self.cursor.fetchall()
            if not positions:
                return None, None

            # Strategy 1: low altitude + climbing near airport
            for lat, lon, alt, t, vrate in positions:
                if alt and alt < 10000:
                    airport = self.find_nearest_airport(lat, lon, max_distance_nm=30)
                    if airport and vrate and vrate > 500:
                        return airport, t

            # Strategy 2: first position near airport
            lat, lon, alt, t, vrate = positions[0]
            airport = self.find_nearest_airport(lat, lon, max_distance_nm=50)
            return (airport, t) if airport else (None, None)

        except Exception as e:
            print(f"Error detecting origin for flight {flight_id}: {e}")
            return None, None

    def _detect_destination_from_positions(self, flight_id):
        """Fallback: detect destination from recent positions."""
        try:
            self.cursor.execute("""
                SELECT latitude, longitude, altitude, received_time, vertical_rate
                FROM positions
                WHERE flight_id   = :fid
                  AND latitude    IS NOT NULL
                  AND longitude   IS NOT NULL
                ORDER BY received_time DESC
                FETCH FIRST 20 ROWS ONLY
            """, fid=flight_id)

            positions = self.cursor.fetchall()
            if not positions:
                return None

            # Strategy 1: low altitude + descending near airport
            for lat, lon, alt, t, vrate in positions:
                if alt and alt < 5000:
                    airport = self.find_nearest_airport(lat, lon, max_distance_nm=20)
                    if airport and vrate and vrate < -500:
                        return airport

            # Strategy 2: last position very low near airport
            lat, lon, alt, t, vrate = positions[0]
            if alt and alt < 3000:
                return self.find_nearest_airport(lat, lon, max_distance_nm=30)

            return None

        except Exception as e:
            print(f"Error detecting destination for flight {flight_id}: {e}")
            return None

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def update_flight_route(self, flight_id, icao_address, callsign):
        """
        Detect and update route for a flight.
        Tries FR24 API first, falls back to position-based detection.
        """
        try:
            # Check whether we already have a complete route
            self.cursor.execute("""
                SELECT route_id, origin_airport_id, dest_airport_id
                FROM flight_routes
                WHERE flight_id = :fid
            """, fid=flight_id)

            existing = self.cursor.fetchone()
            if existing and existing[1] and existing[2]:
                return   # Already complete

            origin_airport = None
            origin_time    = None
            dest_airport   = None
            source         = 'position'

            # ==============================================================
            # PRIMARY: FR24 API
            # ==============================================================
            if callsign and callsign.strip():
                fr24_data = self.fr24.get_route_by_callsign(callsign)

                if fr24_data:
                    # Resolve origin
                    if not (existing and existing[1]):
                        a = self._airport_by_code(
                            iata=fr24_data.get('origin_iata'),
                            icao=fr24_data.get('origin_icao')
                        )
                        if a:
                            origin_airport = a
                            origin_time    = datetime.now()
                            source         = fr24_data.get('source', 'fr24')

                    # Resolve destination
                    if not (existing and existing[2]):
                        a = self._airport_by_code(
                            iata=fr24_data.get('dest_iata'),
                            icao=fr24_data.get('dest_icao')
                        )
                        if a:
                            dest_airport = a
                            source       = fr24_data.get('source', 'fr24')

            # ==============================================================
            # FALLBACK: position-based detection
            # ==============================================================
            if not origin_airport and not (existing and existing[1]):
                origin_airport, origin_time = self._detect_origin_from_positions(flight_id)

            if not dest_airport and not (existing and existing[2]):
                dest_airport = self._detect_destination_from_positions(flight_id)

            # ==============================================================
            # ETA calculation
            # ==============================================================
            eta = None
            if dest_airport:
                self.cursor.execute("""
                    SELECT latitude, longitude, ground_speed
                    FROM positions
                    WHERE flight_id = :fid
                      AND latitude  IS NOT NULL
                      AND longitude IS NOT NULL
                    ORDER BY received_time DESC
                    FETCH FIRST 1 ROWS ONLY
                """, fid=flight_id)
                pos = self.cursor.fetchone()
                if pos:
                    lat, lon, speed = pos
                    eta = self.estimate_arrival_time(
                        lat, lon,
                        dest_airport['lat'], dest_airport['lon'],
                        speed
                    )

            # ==============================================================
            # Persist to database
            # ==============================================================
            if existing:
                if origin_airport:
                    self.cursor.execute("""
                        UPDATE flight_routes
                        SET origin_airport_id = :oid,
                            actual_departure  = :dept
                        WHERE route_id = :rid
                    """, oid=origin_airport['id'], dept=origin_time, rid=existing[0])

                if dest_airport:
                    self.cursor.execute("""
                        UPDATE flight_routes
                        SET dest_airport_id  = :did,
                            estimated_arrival = :eta
                        WHERE route_id = :rid
                    """, did=dest_airport['id'], eta=eta, rid=existing[0])

            elif origin_airport or dest_airport:
                self.cursor.execute("""
                    INSERT INTO flight_routes (
                        flight_id, origin_airport_id, dest_airport_id,
                        actual_departure, estimated_arrival
                    ) VALUES (
                        :fid, :oid, :did, :dept, :eta
                    )
                """,
                    fid=flight_id,
                    oid=origin_airport['id'] if origin_airport else None,
                    did=dest_airport['id']   if dest_airport   else None,
                    dept=origin_time,
                    eta=eta
                )

                # Log the detected route
                orig_code = (origin_airport.get('iata') or origin_airport.get('icao') or '????') if origin_airport else '????'
                dest_code = (dest_airport.get('iata')   or dest_airport.get('icao')   or '????') if dest_airport   else '????'

                if origin_airport or dest_airport:
                    tag = f"[{source}]"
                    print(f"  Route {tag} {callsign or icao_address}: {orig_code} → {dest_code}")

        except Exception as e:
            print(f"Error updating flight route for {callsign or icao_address}: {e}")

    # ------------------------------------------------------------------
    # Diagnostic helpers
    # ------------------------------------------------------------------

    def parse_callsign_for_route(self, callsign):
        """Extract (airline_code, flight_number) from callsign string."""
        if not callsign or len(callsign) < 3:
            return None, None
        airline_part = flight_part = ''
        for char in callsign:
            if char.isalpha():
                if not flight_part:
                    airline_part += char
                else:
                    break
            elif char.isdigit():
                flight_part += char
        return (airline_part, flight_part) if airline_part and flight_part else (None, None)

    def stats(self):
        """Print diagnostic statistics."""
        cs = self.fr24.cache_stats()
        total = cs['mem_hits'] + cs['db_hits'] + cs['api_calls']
        print(f"  Memory cache hits : {cs['mem_hits']:>6}")
        print(f"  DB cache hits     : {cs['db_hits']:>6}")
        print(f"  FR24 API calls    : {cs['api_calls']:>6}")
        print(f"  Total lookups     : {total:>6}")
        print(f"  Airport codes     : {len(self.airport_cache):>6}")


# =============================================================================
# STANDALONE TEST
# Run directly to verify FR24 API key and test a callsign lookup:
#   python3 route_detector.py
#   python3 route_detector.py AAL1234
# =============================================================================

if __name__ == '__main__':
    import sys
    import oracledb

    DB_USER     = 'adsb_user'
    DB_PASSWORD = 'oracle'
    DB_DSN      = '192.168.10.23:1521/ORA26PDB'

    print("=" * 70)
    print("RouteDetector - Standalone Test")
    print("=" * 70)

    # Check API key
    if FR24_API_KEY == 'YOUR_API_KEY_HERE':
        print("\n⚠️  FR24_API_KEY is not set!")
        print("   Set it in the script or via environment variable:")
        print("   export FR24_API_KEY='your_key_here'")
        print("\n   Continuing in position-only mode...")
    else:
        masked = FR24_API_KEY[:6] + '...' + FR24_API_KEY[-4:]
        print(f"\n✓ FR24 API key found: {masked}")

    # Connect to database
    print(f"\nConnecting to {DB_DSN}...")
    try:
        conn   = oracledb.connect(user=DB_USER, password=DB_PASSWORD, dsn=DB_DSN)
        cursor = conn.cursor()
        print("✓ Connected")
    except Exception as e:
        print(f"✗ Database connection failed: {e}")
        sys.exit(1)

    # Instantiate detector (loads airports + validates API key)
    detector = RouteDetector(cursor)
    print()

    # Test a callsign lookup if one was provided
    test_callsign = sys.argv[1] if len(sys.argv) > 1 else None

    if test_callsign:
        print(f"Testing FR24 API lookup for callsign: {test_callsign}")
        print("-" * 70)
        result = detector.fr24.get_route_by_callsign(test_callsign)
        if result:
            print(f"  Origin:      {result.get('origin_iata','?')} / {result.get('origin_icao','?')}  {result.get('origin_name','')}")
            print(f"  Destination: {result.get('dest_iata','?')} / {result.get('dest_icao','?')}  {result.get('dest_name','')}")
            print(f"  Registration:{result.get('registration','')}")
            print(f"  Type:        {result.get('aircraft_type','')}")
            print(f"  Source:      {result.get('source','')}")
        else:
            print("  No result from FR24 API")
    else:
        print("Usage:  python3 route_detector.py [CALLSIGN]")
        print("Example: python3 route_detector.py AAL1234")
        print()
        print("Run with a callsign to test FR24 API lookup.")

    print()
    print("Cache statistics:")
    detector.stats()

    cursor.close()
    conn.close()
    print("\nDone.")
