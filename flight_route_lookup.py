#!/usr/bin/env python3
"""
================================================================================
Flight Route Lookup Module
================================================================================
Version:        1.0.0
Date:           2026-01-21
Description:    Look up flight routes from FlightAware and FlightRadar24 APIs
                to supplement local route detection.

APIs Available:
    1. FlightAware AeroAPI (Recommended)
       - Most comprehensive data
       - Requires API key (free tier: 1,000 requests/month)
       - https://www.flightaware.com/commercial/aeroapi/

    2. FlightRadar24 API (Limited Free)
       - Some endpoints don't require authentication
       - Rate limited
       - May require API key for full access

    3. AviationStack (Alternative)
       - Free tier: 500 requests/month
       - Good for commercial flights
       - https://aviationstack.com/

Usage:
    from flight_route_lookup import FlightRouteLookup
    
    lookup = FlightRouteLookup(flightaware_key='your_api_key')
    route = lookup.get_route_by_callsign('UAL123')
    
    if route:
        print(f"Origin: {route['origin']} -> Dest: {route['destination']}")

Dependencies:
    pip install requests

Configuration:
    Set environment variables or pass keys directly:
    - FLIGHTAWARE_API_KEY
    - FR24_API_KEY (if you have one)
    - AVIATIONSTACK_API_KEY (alternative)
================================================================================
"""

import requests
import json
import time
from datetime import datetime
import os

class FlightRouteLookup:
    """Look up flight routes from various flight tracking APIs"""
    
    def __init__(self, flightaware_key=None, fr24_key=None, aviationstack_key=None):
        """
        Initialize with API keys
        Keys can also be set via environment variables
        """
        self.flightaware_key = flightaware_key or os.getenv('FLIGHTAWARE_API_KEY')
        self.fr24_key = fr24_key or os.getenv('FR24_API_KEY')
        self.aviationstack_key = aviationstack_key or os.getenv('AVIATIONSTACK_API_KEY')
        
        # Rate limiting
        self.last_request_time = {}
        self.min_request_interval = 1.0  # Seconds between requests
        
        # Cache
        self.route_cache = {}
        self.cache_duration = 3600  # Cache for 1 hour
    
    def _rate_limit(self, api_name):
        """Enforce rate limiting"""
        now = time.time()
        if api_name in self.last_request_time:
            elapsed = now - self.last_request_time[api_name]
            if elapsed < self.min_request_interval:
                time.sleep(self.min_request_interval - elapsed)
        self.last_request_time[api_name] = time.time()
    
    def _check_cache(self, key):
        """Check if route is in cache and still valid"""
        if key in self.route_cache:
            cached_data, timestamp = self.route_cache[key]
            if time.time() - timestamp < self.cache_duration:
                return cached_data
        return None
    
    def _cache_route(self, key, data):
        """Cache route data"""
        self.route_cache[key] = (data, time.time())
    
    def get_route_by_callsign(self, callsign):
        """
        Look up route by callsign (tries multiple sources)
        
        Returns dict with:
            {
                'callsign': 'UAL123',
                'origin': 'KSFO',
                'origin_name': 'San Francisco International',
                'destination': 'KJFK',
                'destination_name': 'John F Kennedy International',
                'aircraft_type': 'B77W',
                'departure_time': datetime,
                'arrival_time': datetime,
                'status': 'En Route',
                'source': 'flightaware'
            }
        """
        # Check cache first
        cached = self._check_cache(callsign)
        if cached:
            return cached
        
        # Try FlightAware first (best data)
        if self.flightaware_key:
            route = self._lookup_flightaware(callsign)
            if route:
                self._cache_route(callsign, route)
                return route
        
        # Try AviationStack (good alternative)
        if self.aviationstack_key:
            route = self._lookup_aviationstack(callsign)
            if route:
                self._cache_route(callsign, route)
                return route
        
        # Try FlightRadar24 (limited free access)
        route = self._lookup_fr24_public(callsign)
        if route:
            self._cache_route(callsign, route)
            return route
        
        return None
    
    def get_route_by_icao(self, icao_address):
        """
        Look up route by ICAO hex address
        Note: Most APIs don't support direct ICAO lookup, so this is limited
        """
        # FlightAware supports tail number lookup if we have it
        # For now, this is a placeholder
        print(f"Direct ICAO lookup not widely supported by APIs")
        return None
    
    def _lookup_flightaware(self, callsign):
        """
        Look up route using FlightAware AeroAPI
        Documentation: https://www.flightaware.com/commercial/aeroapi/
        """
        if not self.flightaware_key:
            return None
        
        self._rate_limit('flightaware')
        
        try:
            # FlightAware API v4
            url = f"https://aeroapi.flightaware.com/aeroapi/flights/{callsign}"
            
            headers = {
                'x-apikey': self.flightaware_key
            }
            
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                
                # API returns array of flights, get the most recent
                if 'flights' in data and len(data['flights']) > 0:
                    flight = data['flights'][0]
                    
                    return {
                        'callsign': callsign,
                        'origin': flight.get('origin', {}).get('code'),
                        'origin_name': flight.get('origin', {}).get('name'),
                        'destination': flight.get('destination', {}).get('code'),
                        'destination_name': flight.get('destination', {}).get('name'),
                        'aircraft_type': flight.get('aircraft_type'),
                        'departure_time': self._parse_time(flight.get('scheduled_off')),
                        'arrival_time': self._parse_time(flight.get('scheduled_on')),
                        'status': flight.get('status'),
                        'source': 'flightaware'
                    }
            elif response.status_code == 401:
                print(f"FlightAware API authentication failed - check your API key")
            elif response.status_code == 404:
                print(f"Flight {callsign} not found in FlightAware")
            else:
                print(f"FlightAware API error: {response.status_code}")
                
        except Exception as e:
            print(f"Error calling FlightAware API: {e}")
        
        return None
    
    def _lookup_aviationstack(self, callsign):
        """
        Look up route using AviationStack API
        Documentation: https://aviationstack.com/documentation
        """
        if not self.aviationstack_key:
            return None
        
        self._rate_limit('aviationstack')
        
        try:
            url = "http://api.aviationstack.com/v1/flights"
            
            params = {
                'access_key': self.aviationstack_key,
                'flight_iata': callsign  # Try IATA format first
            }
            
            response = requests.get(url, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                
                if 'data' in data and len(data['data']) > 0:
                    flight = data['data'][0]
                    
                    return {
                        'callsign': callsign,
                        'origin': flight.get('departure', {}).get('icao'),
                        'origin_name': flight.get('departure', {}).get('airport'),
                        'destination': flight.get('arrival', {}).get('icao'),
                        'destination_name': flight.get('arrival', {}).get('airport'),
                        'aircraft_type': flight.get('aircraft', {}).get('icao'),
                        'departure_time': self._parse_time(flight.get('departure', {}).get('scheduled')),
                        'arrival_time': self._parse_time(flight.get('arrival', {}).get('scheduled')),
                        'status': flight.get('flight_status'),
                        'source': 'aviationstack'
                    }
                    
        except Exception as e:
            print(f"Error calling AviationStack API: {e}")
        
        return None
    
    def _lookup_fr24_public(self, callsign):
        """
        Try to look up route using FlightRadar24 public/unofficial endpoints
        Note: This may not work reliably without an API key
        """
        self._rate_limit('fr24')
        
        try:
            # Try the public search endpoint (may be rate limited)
            url = f"https://api.flightradar24.com/common/v1/search.json"
            
            params = {
                'query': callsign,
                'limit': 1
            }
            
            headers = {
                'User-Agent': 'Mozilla/5.0'
            }
            
            response = requests.get(url, params=params, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                
                # Parse results (structure varies)
                if 'results' in data and len(data['results']) > 0:
                    result = data['results'][0]
                    
                    # This is a simplified parser - FR24 structure is complex
                    if result.get('type') == 'flight':
                        flight_id = result.get('id')
                        
                        # Try to get flight details
                        detail_url = f"https://api.flightradar24.com/common/v1/flight-playback.json"
                        detail_params = {'flightId': flight_id}
                        
                        detail_response = requests.get(
                            detail_url, 
                            params=detail_params, 
                            headers=headers, 
                            timeout=10
                        )
                        
                        if detail_response.status_code == 200:
                            detail_data = detail_response.json()
                            
                            return {
                                'callsign': callsign,
                                'origin': detail_data.get('airport', {}).get('origin', {}).get('code', {}).get('icao'),
                                'origin_name': detail_data.get('airport', {}).get('origin', {}).get('name'),
                                'destination': detail_data.get('airport', {}).get('destination', {}).get('code', {}).get('icao'),
                                'destination_name': detail_data.get('airport', {}).get('destination', {}).get('name'),
                                'aircraft_type': detail_data.get('aircraft', {}).get('model', {}).get('code'),
                                'status': detail_data.get('status', {}).get('text'),
                                'source': 'fr24_public'
                            }
                            
        except Exception as e:
            print(f"Error calling FR24 API: {e}")
        
        return None
    
    def _parse_time(self, time_str):
        """Parse various time formats to datetime"""
        if not time_str:
            return None
        
        try:
            # Try ISO format
            return datetime.fromisoformat(time_str.replace('Z', '+00:00'))
        except:
            try:
                # Try other common formats
                return datetime.strptime(time_str, "%Y-%m-%dT%H:%M:%S")
            except:
                return None


def example_usage():
    """Example of how to use the FlightRouteLookup class"""
    
    # Initialize with your API key
    lookup = FlightRouteLookup(
        flightaware_key='YOUR_FLIGHTAWARE_KEY_HERE'
        # Or set environment variable: export FLIGHTAWARE_API_KEY=your_key
    )
    
    # Look up a flight by callsign
    callsigns = ['UAL123', 'DAL456', 'SWA789']
    
    for callsign in callsigns:
        print(f"\nLooking up {callsign}...")
        route = lookup.get_route_by_callsign(callsign)
        
        if route:
            print(f"  Found via {route['source']}")
            print(f"  Route: {route['origin']} ({route.get('origin_name', 'N/A')})")
            print(f"      -> {route['destination']} ({route.get('destination_name', 'N/A')})")
            print(f"  Status: {route.get('status', 'Unknown')}")
            if route.get('aircraft_type'):
                print(f"  Aircraft: {route['aircraft_type']}")
        else:
            print(f"  Route not found")


if __name__ == '__main__':
    print("Flight Route Lookup Module")
    print("=" * 80)
    print("\nTo use this module:")
    print("1. Get API key from FlightAware (recommended): https://www.flightaware.com/commercial/aeroapi/")
    print("2. Or AviationStack: https://aviationstack.com/")
    print("3. Set environment variable: export FLIGHTAWARE_API_KEY=your_key")
    print("4. Import and use in your code")
    print("\nExample:")
    print("  from flight_route_lookup import FlightRouteLookup")
    print("  lookup = FlightRouteLookup()")
    print("  route = lookup.get_route_by_callsign('UAL123')")
    print("\n" + "=" * 80)
