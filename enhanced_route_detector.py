#!/usr/bin/env python3
"""
================================================================================
Enhanced Route Detector with API Integration
================================================================================
Version:        2.0.0
Date:           2026-01-21
Description:    Enhanced route detection that combines local pattern detection
                with external API lookups for better accuracy.

Strategy:
    1. Try local pattern detection first (free, instant)
    2. If no local detection, query external APIs for commercial flights
    3. Cache API results to minimize requests
    4. Update database with confirmed routes

New Features:
    - FlightAware API integration
    - AviationStack API integration  
    - Smart caching to reduce API calls
    - Fallback chain: Local -> FlightAware -> AviationStack -> FR24
    - Commercial flight prioritization

Dependencies:
    - flight_route_lookup module (must be in same directory)
    - requests library: pip install requests

Configuration:
    Set API keys via environment variables:
    export FLIGHTAWARE_API_KEY=your_key
    export AVIATIONSTACK_API_KEY=your_key (optional)
================================================================================
"""

import math
from datetime import datetime, timedelta
import os

# Try to import the API lookup module
try:
    from flight_route_lookup import FlightRouteLookup
    API_AVAILABLE = True
except ImportError:
    API_AVAILABLE = False
    print("Warning: flight_route_lookup module not found - API lookups disabled")

class EnhancedRouteDetector:
    """
    Detects flight routes using both local analysis and external APIs
    """
    
    def __init__(self, cursor, use_api=True):
        self.cursor = cursor
        self.airport_cache = {}
        self.load_airports()
        
        # API integration
        self.use_api = use_api and API_AVAILABLE
        self.api_lookup = None
        
        if self.use_api:
            # Initialize API lookup with keys from environment
            self.api_lookup = FlightRouteLookup()
            print("API route lookup enabled")
        else:
            print("API route lookup disabled (using local detection only)")
        
        # Statistics
        self.stats = {
            'local_detections': 0,
            'api_lookups': 0,
            'api_successes': 0,
            'total_routes': 0
        }
    
    def load_airports(self):
        """Load airports from database into memory for fast lookups"""
        try:
            # Check if elevation_ft column exists
            self.cursor.execute("""
                SELECT column_name 
                FROM user_tab_columns 
                WHERE table_name = 'AIRPORTS' 
                  AND column_name = 'ELEVATION_FT'
            """)
            has_elevation = self.cursor.fetchone() is not None
            
            # Build query based on whether elevation_ft exists
            if has_elevation:
                query = """
                    SELECT 
                        airport_id,
                        icao_code, 
                        iata_code, 
                        airport_name,
                        latitude, 
                        longitude,
                        elevation_ft
                    FROM airports
                    WHERE latitude IS NOT NULL 
                      AND longitude IS NOT NULL
                """
            else:
                query = """
                    SELECT 
                        airport_id,
                        icao_code, 
                        iata_code, 
                        airport_name,
                        latitude, 
                        longitude,
                        0 as elevation_ft
                    FROM airports
                    WHERE latitude IS NOT NULL 
                      AND longitude IS NOT NULL
                """
            
            self.cursor.execute(query)
            
            for row in self.cursor.fetchall():
                airport = {
                    'id': row[0],
                    'icao': row[1],
                    'iata': row[2],
                    'name': row[3],
                    'lat': float(row[4]),
                    'lon': float(row[5]),
                    'elevation': row[6] if row[6] else 0
                }
                
                # Index by both ICAO and IATA codes
                if airport['icao']:
                    self.airport_cache[airport['icao']] = airport
                if airport['iata']:
                    self.airport_cache[airport['iata']] = airport
            
            print(f"Loaded {len(self.airport_cache)} airport codes for route detection")
            
        except Exception as e:
            print(f"Could not load airports: {e}")
    
    def haversine_distance(self, lat1, lon1, lat2, lon2):
        """Calculate distance between two points on Earth (in nautical miles)"""
        R = 3440.065  # Earth radius in nautical miles
        
        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2)
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        
        a = math.sin(dlat/2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon/2)**2
        c = 2 * math.asin(math.sqrt(a))
        
        return R * c
    
    def find_nearest_airport(self, lat, lon, max_distance_nm=30):
        """Find the nearest airport to given coordinates"""
        nearest = None
        min_distance = float('inf')
        
        for code, airport in self.airport_cache.items():
            distance = self.haversine_distance(lat, lon, airport['lat'], airport['lon'])
            
            if distance < min_distance and distance <= max_distance_nm:
                min_distance = distance
                nearest = airport.copy()
                nearest['distance_nm'] = distance
        
        return nearest
    
    def detect_origin_from_positions(self, flight_id, icao_address):
        """Detect origin airport by analyzing early flight positions"""
        try:
            self.cursor.execute("""
                SELECT 
                    latitude,
                    longitude,
                    altitude,
                    received_time,
                    vertical_rate
                FROM positions
                WHERE flight_id = :fid
                  AND latitude IS NOT NULL
                  AND longitude IS NOT NULL
                ORDER BY received_time ASC
                FETCH FIRST 20 ROWS ONLY
            """, fid=flight_id)
            
            positions = self.cursor.fetchall()
            
            if not positions:
                return None, None
            
            # Strategy 1: Find first position with low altitude near an airport
            for lat, lon, alt, time, vrate in positions:
                if alt and alt < 10000:  # Below 10,000 feet
                    airport = self.find_nearest_airport(lat, lon, max_distance_nm=30)
                    if airport:
                        # Check if climbing (positive vertical rate indicates takeoff)
                        if vrate and vrate > 500:
                            return airport, time
            
            # Strategy 2: Just use first position if near an airport
            lat, lon, alt, time, vrate = positions[0]
            airport = self.find_nearest_airport(lat, lon, max_distance_nm=50)
            if airport:
                return airport, time
            
            return None, None
            
        except Exception as e:
            print(f"Error detecting origin for flight {flight_id}: {e}")
            return None, None
    
    def detect_destination_from_positions(self, flight_id, icao_address):
        """Detect destination airport by analyzing recent positions"""
        try:
            self.cursor.execute("""
                SELECT 
                    latitude,
                    longitude,
                    altitude,
                    received_time,
                    vertical_rate
                FROM positions
                WHERE flight_id = :fid
                  AND latitude IS NOT NULL
                  AND longitude IS NOT NULL
                ORDER BY received_time DESC
                FETCH FIRST 20 ROWS ONLY
            """, fid=flight_id)
            
            positions = self.cursor.fetchall()
            
            if not positions:
                return None
            
            # Strategy 1: Find low altitude position near airport with negative vertical rate
            for lat, lon, alt, time, vrate in positions:
                if alt and alt < 5000:  # Below 5,000 feet
                    airport = self.find_nearest_airport(lat, lon, max_distance_nm=20)
                    if airport:
                        # Descending pattern indicates landing
                        if vrate and vrate < -500:
                            return airport
            
            # Strategy 2: Check if last position is near an airport
            lat, lon, alt, time, vrate = positions[0]  # Most recent
            if alt and alt < 3000:  # Very low altitude
                airport = self.find_nearest_airport(lat, lon, max_distance_nm=30)
                if airport:
                    return airport
            
            return None
            
        except Exception as e:
            print(f"Error detecting destination for flight {flight_id}: {e}")
            return None
    
    def lookup_route_from_api(self, callsign):
        """
        Look up route from external APIs
        Returns tuple: (origin_airport_dict, dest_airport_dict) or (None, None)
        """
        if not self.use_api or not self.api_lookup or not callsign:
            return None, None
        
        self.stats['api_lookups'] += 1
        
        try:
            # Query the API
            route_info = self.api_lookup.get_route_by_callsign(callsign)
            
            if route_info and route_info.get('origin') and route_info.get('destination'):
                self.stats['api_successes'] += 1
                
                # Look up airports in our database
                origin = None
                dest = None
                
                origin_code = route_info['origin']
                dest_code = route_info['destination']
                
                # Try to find in our cache
                if origin_code in self.airport_cache:
                    origin = self.airport_cache[origin_code]
                
                if dest_code in self.airport_cache:
                    dest = self.airport_cache[dest_code]
                
                # If not in cache, might need to add to database
                if not origin and origin_code:
                    print(f"  Note: Origin airport {origin_code} not in database")
                
                if not dest and dest_code:
                    print(f"  Note: Destination airport {dest_code} not in database")
                
                return origin, dest
                
        except Exception as e:
            print(f"Error during API lookup: {e}")
        
        return None, None
    
    def estimate_arrival_time(self, current_lat, current_lon, dest_lat, dest_lon, ground_speed):
        """Estimate arrival time based on current position, destination, and speed"""
        if not all([current_lat, current_lon, dest_lat, dest_lon, ground_speed]):
            return None
        
        if ground_speed < 50:  # Aircraft not moving or speed too slow
            return None
        
        # Calculate distance to destination
        distance_nm = self.haversine_distance(current_lat, current_lon, dest_lat, dest_lon)
        
        # Calculate time in hours
        time_hours = distance_nm / ground_speed
        
        # Add current time
        eta = datetime.now() + timedelta(hours=time_hours)
        
        return eta
    
    def update_flight_route(self, flight_id, icao_address, callsign):
        """
        Main function to detect and update route information for a flight
        Uses both local detection and API lookup
        """
        try:
            # Check if route already exists
            self.cursor.execute("""
                SELECT route_id, origin_airport_id, dest_airport_id
                FROM flight_routes
                WHERE flight_id = :fid
            """, fid=flight_id)
            
            existing = self.cursor.fetchone()
            
            # If we already have a complete route, skip
            if existing and existing[1] and existing[2]:
                return
            
            # Try local detection first
            origin_airport = None
            origin_time = None
            dest_airport = None
            
            if not existing or not existing[1]:
                origin_airport, origin_time = self.detect_origin_from_positions(flight_id, icao_address)
                if origin_airport:
                    self.stats['local_detections'] += 1
            
            if not existing or not existing[2]:
                dest_airport = self.detect_destination_from_positions(flight_id, icao_address)
                if dest_airport:
                    self.stats['local_detections'] += 1
            
            # If local detection failed and we have a callsign, try API
            if (not origin_airport or not dest_airport) and callsign:
                print(f"  Trying API lookup for {callsign}...")
                api_origin, api_dest = self.lookup_route_from_api(callsign)
                
                if not origin_airport and api_origin:
                    origin_airport = api_origin
                    print(f"  Found origin via API: {origin_airport['icao']}")
                
                if not dest_airport and api_dest:
                    dest_airport = api_dest
                    print(f"  Found destination via API: {dest_airport['icao']}")
            
            # Get current position for ETA calculation
            self.cursor.execute("""
                SELECT latitude, longitude, ground_speed
                FROM positions
                WHERE flight_id = :fid
                  AND latitude IS NOT NULL
                  AND longitude IS NOT NULL
                ORDER BY received_time DESC
                FETCH FIRST 1 ROWS ONLY
            """, fid=flight_id)
            
            current_pos = self.cursor.fetchone()
            
            eta = None
            if current_pos and dest_airport:
                lat, lon, speed = current_pos
                eta = self.estimate_arrival_time(
                    lat, lon, 
                    dest_airport['lat'], dest_airport['lon'],
                    speed
                )
            
            # Insert or update route
            if existing:
                # Update existing route
                if origin_airport:
                    self.cursor.execute("""
                        UPDATE flight_routes
                        SET origin_airport_id = :origin_id,
                            actual_departure = :dept_time
                        WHERE route_id = :rid
                    """, 
                        origin_id=origin_airport['id'],
                        dept_time=origin_time,
                        rid=existing[0]
                    )
                
                if dest_airport:
                    self.cursor.execute("""
                        UPDATE flight_routes
                        SET dest_airport_id = :dest_id,
                            estimated_arrival = :eta
                        WHERE route_id = :rid
                    """,
                        dest_id=dest_airport['id'],
                        eta=eta,
                        rid=existing[0]
                    )
            else:
                # Insert new route
                if origin_airport or dest_airport:
                    self.cursor.execute("""
                        INSERT INTO flight_routes (
                            flight_id,
                            origin_airport_id,
                            dest_airport_id,
                            actual_departure,
                            estimated_arrival
                        ) VALUES (
                            :fid,
                            :origin_id,
                            :dest_id,
                            :dept,
                            :eta
                        )
                    """,
                        fid=flight_id,
                        origin_id=origin_airport['id'] if origin_airport else None,
                        dest_id=dest_airport['id'] if dest_airport else None,
                        dept=origin_time,
                        eta=eta
                    )
                    
                    self.stats['total_routes'] += 1
                    
                    # Print detected route
                    origin_code = origin_airport['icao'] or origin_airport['iata'] if origin_airport else '????'
                    dest_code = dest_airport['icao'] or dest_airport['iata'] if dest_airport else '????'
                    
                    if origin_airport or dest_airport:
                        print(f"Route detected for {callsign or icao_address}: {origin_code} → {dest_code}")
        
        except Exception as e:
            print(f"Error updating flight route: {e}")
    
    def print_stats(self):
        """Print statistics about route detection"""
        print("\n" + "="*60)
        print("Route Detection Statistics")
        print("="*60)
        print(f"Total routes detected: {self.stats['total_routes']}")
        print(f"Local detections: {self.stats['local_detections']}")
        print(f"API lookups attempted: {self.stats['api_lookups']}")
        print(f"API lookups successful: {self.stats['api_successes']}")
        if self.stats['api_lookups'] > 0:
            success_rate = (self.stats['api_successes'] / self.stats['api_lookups']) * 100
            print(f"API success rate: {success_rate:.1f}%")
        print("="*60)
