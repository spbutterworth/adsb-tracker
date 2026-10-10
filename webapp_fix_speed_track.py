"""
WEBAPP FIX for Missing Speed/Track Data
Replace the index() function query in adsb_webapp.py
"""

# PROBLEM: The complex join is likely causing NULLs
# The old join: LEFT JOIN flights f ON ca.icao_address = f.icao_address AND ca.callsign = f.callsign
# This creates issues when callsigns don't match exactly

# SOLUTION: Use flight_id from the view directly (we added it!)

@app.route('/')
def index():
    """Display current aircraft - FIXED VERSION"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Simplified query - use flight_id from view directly
    cursor.execute("""
        SELECT 
            ca.icao_address,
            ca.registration,
            ca.aircraft_type,
            ca.callsign,
            ca.altitude,
            ca.ground_speed,
            ca.track,
            ca.latitude,
            ca.longitude,
            ca.minutes_ago,
            origin.icao_code as origin_icao,
            origin.iata_code as origin_iata,
            origin.airport_name as origin_name,
            dest.icao_code as dest_icao,
            dest.iata_code as dest_iata,
            dest.airport_name as dest_name,
            fr.actual_departure,
            fr.estimated_arrival
        FROM v_current_aircraft ca
        LEFT JOIN flight_routes fr ON ca.flight_id = fr.flight_id
        LEFT JOIN airports origin ON fr.origin_airport_id = origin.airport_id
        LEFT JOIN airports dest ON fr.dest_airport_id = dest.airport_id
        ORDER BY ca.received_time DESC
    """)
    current_aircraft = cursor.fetchall()
    
    # Get statistics
    cursor.execute("SELECT COUNT(*) FROM aircraft")
    total_aircraft = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM flights")
    total_flights = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM positions")
    total_positions = cursor.fetchone()[0]
    
    # Active aircraft is simply the count from the view
    active_aircraft = len(current_aircraft)
    
    stats = {
        'total_aircraft': total_aircraft,
        'total_flights': total_flights,
        'total_positions': total_positions,
        'active_aircraft': active_aircraft
    }
    
    cursor.close()
    conn.close()
    
    return render_template_string(HTML_TEMPLATE, 
                                 page='current',
                                 current_aircraft=current_aircraft,
                                 stats=stats)


# KEY CHANGE:
# OLD: LEFT JOIN flights f ON ca.icao_address = f.icao_address AND ca.callsign = f.callsign
# NEW: Uses ca.flight_id directly from the view
#
# This avoids the join mismatch that was causing NULLs
