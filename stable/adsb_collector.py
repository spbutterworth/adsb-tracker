#!/usr/bin/env python3
"""
ADS-B Data Collector for Flightradar24 Receiver
Connects to BaseStation format feed (port 30003) and stores data in Oracle DB
"""

import socket
import oracledb
from datetime import datetime
import time
import sys
from collections import defaultdict
import json
import gzip
import urllib.request

# Configuration
ADSB_HOST = '192.168.10.139'
ADSB_PORT = 30003

# Oracle Database Configuration - UPDATE THESE
DB_USER = 'adsb_user'
DB_PASSWORD = 'oracle'
DB_DSN = 'localhost:1522/FREEPDB1'  # For Oracle 23ai Free in Docker

# Track active flights to avoid creating duplicates
active_flights = {}  # key: icao_address, value: flight_id

# Aircraft database cache
aircraft_db = {}

# Airline database cache
airline_db = {}

# Alert rules cache
alert_rules = []

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

def check_alerts(cursor, data, flight_id):
    """Check if this position triggers any alerts"""
    if not alert_rules:
        return
    
    for alert_id, alert_type, alert_value in alert_rules:
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
            except:
                pass
        
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
                
                print(f"ALERT TRIGGERED: {alert_type}={alert_value} for {data['icao_address']} / {data['callsign']}")
            except Exception as e:
                print(f"Error recording alert: {e}")

def download_aircraft_database():
    """
    Download the latest ADS-B Exchange aircraft database
    Updated daily from government and various sources
    """
    url = 'http://downloads.adsbexchange.com/downloads/basic-ac-db.json.gz'
    print(f"Downloading aircraft database from {url}...")
    
    try:
        data = {}
        with urllib.request.urlopen(url) as response:
            with gzip.GzipFile(fileobj=response) as f:
                line_num = 0
                for line in f:
                    line_num += 1
                    line = line.decode('utf-8').strip()
                    
                    if not line:
                        continue
                    
                    try:
                        # Parse each line as a separate JSON object
                        obj = json.loads(line)
                        
                        # Each line should have an 'icao' field that's the key
                        if 'icao' in obj:
                            icao = obj['icao']
                            data[icao] = obj
                            
                    except json.JSONDecodeError:
                        continue
                
                if data:
                    print(f"Successfully downloaded {len(data)} aircraft from database")
                    return data
                else:
                    print("Failed to parse database")
                    return None
                        
    except Exception as e:
        print(f"Error downloading database: {e}")
        return None

def load_aircraft_database():
    """
    Load aircraft database from ADS-B Exchange JSON
    Format: {"icao": {"r": "registration", "t": "type", ...}, ...}
    """
    global aircraft_db
    
    # Try to download fresh database
    data = download_aircraft_database()
    
    if not data:
        # Try to load from local file if download failed
        try:
            print("Trying to load from local basic-ac-db.json...")
            with open('basic-ac-db.json', 'r', encoding='utf-8') as f:
                data = json.load(f)
        except FileNotFoundError:
            print("Warning: No aircraft database available.")
            print("Download manually from: http://downloads.adsbexchange.com/downloads/basic-ac-db.json.gz")
            return
    
    # Parse the database - handle different possible formats
    for icao, info in data.items():
        # Check if info is a dict or string
        if isinstance(info, dict):
            # The actual format uses 'reg' and 'icaotype' field names
            aircraft_db[icao.upper()] = {
                'registration': info.get('reg', '') or info.get('r', ''),
                'aircraft_type': info.get('icaotype', '') or info.get('t', ''),
                'manufacturer': info.get('manufacturer', ''),
                'model': info.get('model', ''),
                'ownop': info.get('ownop', '')
            }
        elif isinstance(info, str):
            # If it's just a string (maybe registration only?)
            aircraft_db[icao.upper()] = {
                'registration': info,
                'aircraft_type': '',
                'manufacturer': '',
                'model': '',
                'ownop': ''
            }
        else:
            # Skip if we don't understand the format
            continue
    
    print(f"Loaded {len(aircraft_db)} aircraft records from database")
    
    # Debug: Show a sample entry if we have data
    if aircraft_db:
        sample_icao = list(aircraft_db.keys())[0]
        sample_data = aircraft_db[sample_icao]
        print(f"Sample aircraft data - ICAO: {sample_icao}, Data: {sample_data}")
    
    # Save to local file for backup
    try:
        with open('basic-ac-db.json', 'w', encoding='utf-8') as f:
            json.dump(data, f)
        print("Saved aircraft database to basic-ac-db.json for backup")
    except Exception as e:
        print(f"Could not save backup file: {e}")

def connect_to_database():
    """Connect to Oracle database"""
    try:
        connection = oracledb.connect(
            user=DB_USER,
            password=DB_PASSWORD,
            dsn=DB_DSN
        )
        print(f"Connected to Oracle Database: {DB_DSN}")
        
        # Set session timezone to Central Time
        cursor = connection.cursor()
        cursor.execute("ALTER SESSION SET TIME_ZONE = 'America/Chicago'")
        cursor.close()
        print("Session timezone set to America/Chicago (Central Time)")
        
        return connection
    except Exception as e:
        print(f"Database connection failed: {e}")
        sys.exit(1)

def ensure_aircraft_exists(cursor, icao_address):
    """Make sure aircraft exists in database, create if not"""
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
            print(f"Adding new aircraft {icao_address}: No registration/type data found in database")
        
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

def get_or_create_flight(cursor, icao_address, callsign):
    """Get existing flight_id or create new flight"""
    # Check if we have an active flight for this aircraft
    if icao_address in active_flights:
        flight_id = active_flights[icao_address]
        
        # Update last_contact and callsign if we have one
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
    
    # Create new flight and get the generated flight_id
    flight_id_var = cursor.var(int)
    cursor.execute(
        """INSERT INTO flights (icao_address, callsign) 
           VALUES (:icao, :call) 
           RETURNING flight_id INTO :fid""",
        icao=icao_address,
        call=callsign if callsign else None,
        fid=flight_id_var
    )
    flight_id = flight_id_var.getvalue()[0]
    active_flights[icao_address] = flight_id
    return flight_id

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
    
    data = {
        'msg_type': msg_type,
        'transmission_type': int(fields[1]) if fields[1] else None,
        'icao_address': fields[4].strip() if fields[4] else None,
        'callsign': callsign,
        'altitude': int(fields[11]) if fields[11] else None,
        'ground_speed': int(fields[12]) if fields[12] else None,
        'track': int(fields[13]) if fields[13] else None,
        'latitude': float(fields[14]) if fields[14] else None,
        'longitude': float(fields[15]) if fields[15] else None,
        'vertical_rate': int(fields[16]) if fields[16] else None,
        'squawk': fields[17].strip() if fields[17] else None,
        'alert': int(fields[18]) if fields[18] else None,
        'emergency': int(fields[19]) if fields[19] else None,
        'spi': int(fields[20]) if fields[20] else None,
        'is_on_ground': int(fields[21]) if fields[21] else None,
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
        except:
            data['reported_time'] = None
    else:
        data['reported_time'] = None
    
    return data

def store_position(cursor, data):
    """Store position data in Oracle database"""
    if not data or not data['icao_address']:
        return
    
    try:
        # Ensure aircraft exists
        ensure_aircraft_exists(cursor, data['icao_address'])
        
        # Get or create flight
        flight_id = get_or_create_flight(cursor, data['icao_address'], data['callsign'])
        
        # Check if this triggers any alerts
        check_alerts(cursor, data, flight_id)
        
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
        
    except Exception as e:
        print(f"Error storing position: {e}")
        raise

def main():
    """Main collector loop"""
    print("ADS-B Data Collector starting...")
    
    # Load aircraft database
    load_aircraft_database()
    
    print(f"Connecting to {ADSB_HOST}:{ADSB_PORT}")
    
    # Connect to database
    db_conn = connect_to_database()
    cursor = db_conn.cursor()
    
    # Load airline and alert data
    load_airline_database(cursor)
    load_alert_rules(cursor)
    
    # Statistics
    message_count = 0
    last_commit_time = time.time()
    commit_interval = 10  # Commit every 10 seconds
    callsigns_seen = set()  # Track unique callsigns for debugging
    
    while True:
        try:
            # Connect to ADS-B feed
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((ADSB_HOST, ADSB_PORT))
            print(f"Connected to ADS-B feed at {ADSB_HOST}:{ADSB_PORT}")
            
            buffer = ""
            
            while True:
                data = sock.recv(4096).decode('utf-8', errors='ignore')
                if not data:
                    print("Connection closed by remote host")
                    break
                
                buffer += data
                
                # Process complete lines
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    
                    # Parse and store message
                    parsed = parse_basestation_message(line)
                    if parsed:
                        # Debug: Track callsigns
                        if parsed['callsign'] and parsed['callsign'] not in callsigns_seen:
                            callsigns_seen.add(parsed['callsign'])
                            print(f"New callsign detected: {parsed['callsign']} (ICAO: {parsed['icao_address']})")
                        
                        store_position(cursor, parsed)
                        message_count += 1
                        
                        # Print progress
                        if message_count % 1000 == 0:
                            print(f"Processed {message_count} messages, {len(callsigns_seen)} unique callsigns...")
                
                # Commit periodically
                current_time = time.time()
                if current_time - last_commit_time > commit_interval:
                    db_conn.commit()
                    last_commit_time = current_time
                    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    print(f"[{timestamp}] Committed {message_count} messages to database")
            
            sock.close()
            
        except KeyboardInterrupt:
            print("\nShutting down...")
            db_conn.commit()
            cursor.close()
            db_conn.close()
            sys.exit(0)
            
        except Exception as e:
            print(f"Error: {e}")
            print("Reconnecting in 5 seconds...")
            time.sleep(5)

if __name__ == '__main__':
    main()
