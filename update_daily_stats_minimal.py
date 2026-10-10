#!/usr/bin/env python3
"""
================================================================================
Daily Statistics Calculator - Minimal Version
================================================================================
Version:        1.1.0
Last Updated:   2026-01-20
Description:    Works with ANY daily_stats table structure by auto-detecting
                which columns exist.

This version is bulletproof and will work regardless of what columns you have.
================================================================================
"""

import oracledb
import sys
from datetime import datetime, timedelta

# Oracle Database Configuration - UPDATE THESE
DB_USER = 'adsb_user'
DB_PASSWORD = 'oracle'
DB_DSN = 'localhost:1522/FREEPDB1'

def get_table_columns(cursor):
    """Get the actual columns that exist in daily_stats table"""
    cursor.execute("""
        SELECT column_name 
        FROM user_tab_columns 
        WHERE table_name = 'DAILY_STATS'
        ORDER BY column_id
    """)
    return [row[0].upper() for row in cursor.fetchall()]

def calculate_daily_stats(conn, stat_date):
    """Calculate and store daily statistics for a given date"""
    cursor = conn.cursor()
    
    # Format date
    date_str = stat_date.strftime('%Y-%m-%d')
    print(f"\nCalculating statistics for {date_str}...")
    
    # Get existing columns
    existing_columns = get_table_columns(cursor)
    print(f"  Table has {len(existing_columns)} columns: {', '.join(existing_columns[:5])}...")
    
    # Check if stats already exist
    cursor.execute("""
        SELECT COUNT(*) FROM daily_stats WHERE stat_date = TO_DATE(:dt, 'YYYY-MM-DD')
    """, dt=date_str)
    
    if cursor.fetchone()[0] > 0:
        print(f"  Deleting existing stats for {date_str}")
        cursor.execute("""
            DELETE FROM daily_stats WHERE stat_date = TO_DATE(:dt, 'YYYY-MM-DD')
        """, dt=date_str)
    
    # Calculate statistics that we'll try to insert
    stats = {}
    
    # REQUIRED: stat_date (always present)
    stats['STAT_DATE'] = stat_date
    
    # REQUIRED: total_aircraft_seen
    if 'TOTAL_AIRCRAFT_SEEN' in existing_columns:
        cursor.execute("""
            SELECT COUNT(DISTINCT p.icao_address)
            FROM positions p
            WHERE TRUNC(p.received_time) = TO_DATE(:dt, 'YYYY-MM-DD')
        """, dt=date_str)
        stats['TOTAL_AIRCRAFT_SEEN'] = cursor.fetchone()[0] or 0
        print(f"  ✓ Aircraft seen: {stats['TOTAL_AIRCRAFT_SEEN']}")
    
    # OPTIONAL: total_flights
    if 'TOTAL_FLIGHTS' in existing_columns:
        cursor.execute("""
            SELECT COUNT(DISTINCT f.flight_id)
            FROM flights f
            JOIN positions p ON f.flight_id = p.flight_id
            WHERE TRUNC(p.received_time) = TO_DATE(:dt, 'YYYY-MM-DD')
        """, dt=date_str)
        stats['TOTAL_FLIGHTS'] = cursor.fetchone()[0] or 0
        print(f"  ✓ Total flights: {stats['TOTAL_FLIGHTS']}")
    
    # OPTIONAL: total_positions
    if 'TOTAL_POSITIONS' in existing_columns:
        cursor.execute("""
            SELECT COUNT(*)
            FROM positions
            WHERE TRUNC(received_time) = TO_DATE(:dt, 'YYYY-MM-DD')
        """, dt=date_str)
        stats['TOTAL_POSITIONS'] = cursor.fetchone()[0] or 0
        print(f"  ✓ Total positions: {stats['TOTAL_POSITIONS']}")
    
    # OPTIONAL: unique_callsigns
    if 'UNIQUE_CALLSIGNS' in existing_columns:
        cursor.execute("""
            SELECT COUNT(DISTINCT f.callsign)
            FROM flights f
            JOIN positions p ON f.flight_id = p.flight_id
            WHERE TRUNC(p.received_time) = TO_DATE(:dt, 'YYYY-MM-DD')
              AND f.callsign IS NOT NULL
        """, dt=date_str)
        stats['UNIQUE_CALLSIGNS'] = cursor.fetchone()[0] or 0
        print(f"  ✓ Unique callsigns: {stats['UNIQUE_CALLSIGNS']}")
    
    # OPTIONAL: peak_aircraft_hour and peak_aircraft_count
    if 'PEAK_AIRCRAFT_HOUR' in existing_columns or 'PEAK_AIRCRAFT_COUNT' in existing_columns:
        cursor.execute("""
            SELECT 
                TO_CHAR(received_time, 'HH24') as hour,
                COUNT(DISTINCT icao_address) as aircraft_count
            FROM positions
            WHERE TRUNC(received_time) = TO_DATE(:dt, 'YYYY-MM-DD')
              AND latitude IS NOT NULL
            GROUP BY TO_CHAR(received_time, 'HH24')
            ORDER BY aircraft_count DESC
            FETCH FIRST 1 ROWS ONLY
        """, dt=date_str)
        
        peak_result = cursor.fetchone()
        if peak_result:
            if 'PEAK_AIRCRAFT_HOUR' in existing_columns:
                stats['PEAK_AIRCRAFT_HOUR'] = peak_result[0]
            if 'PEAK_AIRCRAFT_COUNT' in existing_columns:
                stats['PEAK_AIRCRAFT_COUNT'] = peak_result[1]
                print(f"  ✓ Peak hour: {peak_result[0]}:00 ({peak_result[1]} aircraft)")
    
    # OPTIONAL: altitude statistics
    if 'AVG_ALTITUDE' in existing_columns or 'MAX_ALTITUDE' in existing_columns:
        cursor.execute("""
            SELECT 
                ROUND(AVG(altitude)) as avg_alt,
                MAX(altitude) as max_alt
            FROM positions
            WHERE TRUNC(received_time) = TO_DATE(:dt, 'YYYY-MM-DD')
              AND altitude IS NOT NULL
        """, dt=date_str)
        
        alt_result = cursor.fetchone()
        if alt_result:
            if 'AVG_ALTITUDE' in existing_columns:
                stats['AVG_ALTITUDE'] = alt_result[0]
            if 'MAX_ALTITUDE' in existing_columns:
                stats['MAX_ALTITUDE'] = alt_result[1]
            if alt_result[0]:
                print(f"  ✓ Altitude: avg {alt_result[0]}ft, max {alt_result[1]}ft")
    
    # OPTIONAL: most_common_aircraft_type
    if 'MOST_COMMON_AIRCRAFT_TYPE' in existing_columns:
        cursor.execute("""
            SELECT aircraft_type
            FROM (
                SELECT a.aircraft_type, COUNT(*) as cnt
                FROM aircraft a
                JOIN positions p ON a.icao_address = p.icao_address
                WHERE TRUNC(p.received_time) = TO_DATE(:dt, 'YYYY-MM-DD')
                  AND a.aircraft_type IS NOT NULL
                GROUP BY a.aircraft_type
                ORDER BY cnt DESC
            )
            WHERE ROWNUM = 1
        """, dt=date_str)
        
        type_result = cursor.fetchone()
        if type_result:
            stats['MOST_COMMON_AIRCRAFT_TYPE'] = type_result[0]
            print(f"  ✓ Most common type: {type_result[0]}")
    
    # OPTIONAL: total_distance_tracked
    if 'TOTAL_DISTANCE_TRACKED' in existing_columns:
        cursor.execute("""
            SELECT COUNT(*) FROM (
                SELECT flight_id
                FROM positions
                WHERE TRUNC(received_time) = TO_DATE(:dt, 'YYYY-MM-DD')
                  AND latitude IS NOT NULL
                  AND longitude IS NOT NULL
                GROUP BY flight_id
            )
        """, dt=date_str)
        
        flight_count = cursor.fetchone()[0] or 0
        stats['TOTAL_DISTANCE_TRACKED'] = flight_count * 150
        print(f"  ✓ Est. distance tracked: {stats['TOTAL_DISTANCE_TRACKED']} nm")
    
    # Build INSERT statement dynamically
    if len(stats) == 1:  # Only STAT_DATE
        print(f"  ✗ No valid columns found in daily_stats table!")
        print(f"  ✗ Please run fix_daily_stats_table.sql to add required columns")
        cursor.close()
        return
    
    # Filter stats to only include columns that exist
    insert_columns = [col for col in stats.keys() if col in existing_columns]
    
    if not insert_columns:
        print(f"  ✗ Error: Could not match calculated stats to table columns")
        cursor.close()
        return
    
    # Build INSERT SQL
    placeholders = [f":{col.lower()}" for col in insert_columns]
    insert_sql = f"""
        INSERT INTO daily_stats ({', '.join(insert_columns)})
        VALUES ({', '.join(placeholders)})
    """
    
    # Create bind parameters (lowercase keys for Oracle)
    bind_params = {col.lower(): stats[col] for col in insert_columns}
    
    # Execute insert
    try:
        cursor.execute(insert_sql, **bind_params)
        conn.commit()
        print(f"  ✓ Statistics saved successfully")
    except Exception as e:
        print(f"  ✗ Error inserting stats: {e}")
        print(f"  SQL: {insert_sql}")
        conn.rollback()
    
    cursor.close()

def main():
    """Main function"""
    print("=" * 80)
    print("Daily Statistics Calculator - Minimal Version")
    print("=" * 80)
    
    # Determine which date(s) to process
    if len(sys.argv) == 1:
        dates = [datetime.now()]
        print("Processing: Today")
    elif sys.argv[1].lower() == 'yesterday':
        dates = [datetime.now() - timedelta(days=1)]
        print("Processing: Yesterday")
    elif sys.argv[1].lower() == 'range':
        if len(sys.argv) < 3:
            print("Usage: update_daily_stats.py range <days>")
            sys.exit(1)
        days = int(sys.argv[2])
        dates = [datetime.now() - timedelta(days=i) for i in range(days, -1, -1)]
        print(f"Processing: Last {days} days")
    else:
        try:
            date_str = sys.argv[1]
            dates = [datetime.strptime(date_str, '%Y-%m-%d')]
            print(f"Processing: {date_str}")
        except ValueError:
            print("Invalid date format. Use YYYY-MM-DD")
            sys.exit(1)
    
    # Connect to database
    print("\nConnecting to database...")
    try:
        conn = oracledb.connect(
            user=DB_USER,
            password=DB_PASSWORD,
            dsn=DB_DSN
        )
        print("Connected successfully")
    except Exception as e:
        print(f"Error connecting to database: {e}")
        sys.exit(1)
    
    # Process each date
    for date in dates:
        try:
            calculate_daily_stats(conn, date)
        except Exception as e:
            print(f"\n✗ Error calculating stats for {date.strftime('%Y-%m-%d')}: {e}")
            import traceback
            traceback.print_exc()
    
    conn.close()
    
    print("\n" + "=" * 80)
    print("Statistics calculation complete!")
    print("=" * 80)
    print("\nView stats with:")
    print("  SELECT * FROM daily_stats ORDER BY stat_date DESC;")

if __name__ == '__main__':
    main()
