"""
================================================================================
ACARS Data Collector with ADS-B Integration
================================================================================
Version:        1.0.0
Last Updated:   2026-03-05
Author:         ADS-B Flight Tracker - ACARS Extension
Description:    Collects ACARS messages from acarsdec decoder, parses messages,
                stores in Oracle database, and links to ADS-B flight data.

Features:
    - Connects to acarsdec JSON output via UDP or network
    - Parses ACARS message content
    - Stores all messages in Oracle database
    - Automatic classification by message type
    - Links ACARS messages to ADS-B flights by registration/ICAO
    - Extracts position data from position reports
    - Real-time console output for interesting messages

Dependencies:
    - oracledb: Oracle database connectivity
    - acarsdec: Must be running and outputting JSON (acarsdec -j <ip>:<port>)

Configuration:
    - ACARS_HOST: IP to listen on for acarsdec JSON (default: 0.0.0.0)
    - ACARS_PORT: UDP port for acarsdec output (default: 5555)
    - DB_USER, DB_PASSWORD, DB_DSN: Oracle connection settings

Setup with acarsdec:
    acarsdec -v -o 4 -g 42 -r 0 129.125 130.025 130.450 131.550 -j 127.0.0.1:5555
    
    Options:
      -v: Verbose output to console
      -o 4: Output format (JSON)
      -g 42: RTL-SDR gain (adjust for your setup)
      -r 0: Use RTL-SDR device 0
      129.125 130.025 130.450 131.550: Common ACARS frequencies
      -j 127.0.0.1:5555: Send JSON to this collector

Version History:
    1.0.0 (2026-03-05) - Initial release
        - Basic ACARS collection from acarsdec
        - Oracle database storage
        - Message classification
        - ADS-B flight linking
================================================================================
"""

import socket
import json
import oracledb
from datetime import datetime
import time
import sys
import re
import signal

# Configuration
ACARS_HOST = '0.0.0.0'  # Listen on all interfaces
ACARS_PORT = 5555       # UDP port for acarsdec JSON output

# Oracle Database Configuration - UPDATE THESE TO MATCH YOUR SETUP
DB_USER = 'adsb_user'
DB_PASSWORD = 'oracle'
DB_DSN = '192.168.10.23:1521/ORA26PDB'  # For Oracle 23ai Free in Docker

# Message classification patterns
OOOI_LABELS = {'H1', 'H2', 'H3', 'H4', '10', '12', '13', '14'}
POSITION_LABELS = {'15', '16', '17'}
WEATHER_LABELS = {'20', '21', '22', '5U', '5Z', 'SA'}
MAINTENANCE_LABELS = {'44', '45'}

# Statistics
stats = {
    'total_messages': 0,
    'oooi_count': 0,
    'position_count': 0,
    'weather_count': 0,
    'maintenance_count': 0,
    'freetext_count': 0,
    'linked_count': 0,
    'unique_aircraft': set()
}

# Aircraft cache for linking
aircraft_cache = {}  # registration -> icao_address

# Graceful shutdown flag
running = True


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    global running
    print("\nShutdown signal received...")
    running = False


def connect_to_database():
    """Connect to Oracle database"""
    print(f"Connecting to Oracle: {DB_USER}@{DB_DSN}")
    try:
        conn = oracledb.connect(user=DB_USER, password=DB_PASSWORD, dsn=DB_DSN)
        print("Connected to Oracle database")
        return conn
    except Exception as e:
        print(f"Database connection failed: {e}")
        sys.exit(1)


def load_aircraft_cache(cursor):
    """Load aircraft registration to ICAO mapping from database"""
    global aircraft_cache
    try:
        cursor.execute("""
            SELECT registration, icao_address 
            FROM aircraft 
            WHERE registration IS NOT NULL
        """)
        for reg, icao in cursor.fetchall():
            aircraft_cache[reg.upper().strip()] = icao.upper()
        print(f"Loaded {len(aircraft_cache)} aircraft registrations")
    except Exception as e:
        print(f"Could not load aircraft cache: {e}")


def classify_message(label, message_text):
    """Classify ACARS message by label and content"""
    if label in OOOI_LABELS:
        return 'OOOI'
    elif label in POSITION_LABELS:
        return 'POSITION'
    elif label in WEATHER_LABELS:
        return 'WEATHER'
    elif label in MAINTENANCE_LABELS:
        return 'MAINTENANCE'
    
    # Check message content for additional classification
    if message_text:
        text_upper = message_text.upper()
        if any(x in text_upper for x in ['ETA', 'ARR', 'DEP', 'GATE']):
            return 'OPERATIONAL'
        if any(x in text_upper for x in ['FUEL', 'FOB', 'BURN']):
            return 'FUEL'
        if any(x in text_upper for x in ['WX', 'WIND', 'TURB', 'ICE', 'METAR', 'TAF']):
            return 'WEATHER'
        if any(x in text_upper for x in ['FAULT', 'MAINT', 'ENG', 'ERROR']):
            return 'MAINTENANCE'
    
    return 'FREETEXT'


def extract_position(message_text):
    """Try to extract lat/lon from message content"""
    if not message_text:
        return None, None
    
    lat, lon = None, None
    
    # Common position formats in ACARS:
    # N32.897W097.038 or N3253.8W09702.3 or 32.897N 097.038W
    
    # Format: N32.897W097.038 or S32.897E097.038 (with decimal)
    pattern1 = r'([NS])(\d{1,2}\.\d+)[,\s]*([EW])(\d{1,3}\.\d+)'
    match = re.search(pattern1, message_text.upper())
    if match:
        lat = float(match.group(2))
        if match.group(1) == 'S':
            lat = -lat
        lon = float(match.group(4))
        if match.group(3) == 'W':
            lon = -lon
        # Validate ranges
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon
    
    # Format: 32.897N 097.038W (with decimal)
    pattern2 = r'(\d{1,2}\.\d+)\s*([NS])[,\s]*(\d{1,3}\.\d+)\s*([EW])'
    match = re.search(pattern2, message_text.upper())
    if match:
        lat = float(match.group(1))
        if match.group(2) == 'S':
            lat = -lat
        lon = float(match.group(3))
        if match.group(4) == 'W':
            lon = -lon
        # Validate ranges
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon
    
    # Format: N3253.8W09702.3 (degrees + decimal minutes)
    # DDMM.M format - need to convert to decimal degrees
    pattern3 = r'([NS])(\d{2})(\d{2}\.\d+)[,\s]*([EW])(\d{2,3})(\d{2}\.\d+)'
    match = re.search(pattern3, message_text.upper())
    if match:
        lat_deg = int(match.group(2))
        lat_min = float(match.group(3))
        lat = lat_deg + (lat_min / 60.0)
        if match.group(1) == 'S':
            lat = -lat
        
        lon_deg = int(match.group(5))
        lon_min = float(match.group(6))
        lon = lon_deg + (lon_min / 60.0)
        if match.group(4) == 'W':
            lon = -lon
        
        # Validate ranges
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon
    
    return None, None


def extract_altitude(message_text):
    """Try to extract altitude from message content"""
    if not message_text:
        return None
    
    # Look for FL (flight level) - FL350 = 35000 feet
    fl_match = re.search(r'FL\s*(\d{2,3})', message_text.upper())
    if fl_match:
        return int(fl_match.group(1)) * 100
    
    # Look for altitude patterns: ALT 35000 or A35000
    alt_match = re.search(r'(?:ALT|A)\s*(\d{4,5})', message_text.upper())
    if alt_match:
        return int(alt_match.group(1))
    
    return None


def find_linked_flight(cursor, registration, icao_address):
    """Find matching ADS-B flight for this ACARS message"""
    
    # Try by ICAO address first (most reliable)
    if icao_address:
        cursor.execute("""
            SELECT flight_id FROM flights
            WHERE icao_address = :icao
              AND last_contact > SYSDATE - (2/24)
            ORDER BY last_contact DESC
            FETCH FIRST 1 ROWS ONLY
        """, icao=icao_address.upper())
        row = cursor.fetchone()
        if row:
            return row[0]
    
    # Try by registration via aircraft table
    if registration:
        reg_clean = registration.upper().strip().replace('-', '')
        
        # Check cache first
        if reg_clean in aircraft_cache:
            icao = aircraft_cache[reg_clean]
            cursor.execute("""
                SELECT flight_id FROM flights
                WHERE icao_address = :icao
                  AND last_contact > SYSDATE - (2/24)
                ORDER BY last_contact DESC
                FETCH FIRST 1 ROWS ONLY
            """, icao=icao)
            row = cursor.fetchone()
            if row:
                return row[0]
        
        # Try database lookup
        cursor.execute("""
            SELECT f.flight_id 
            FROM flights f
            JOIN aircraft a ON f.icao_address = a.icao_address
            WHERE UPPER(REPLACE(a.registration, '-', '')) = :reg
              AND f.last_contact > SYSDATE - (2/24)
            ORDER BY f.last_contact DESC
            FETCH FIRST 1 ROWS ONLY
        """, reg=reg_clean)
        row = cursor.fetchone()
        if row:
            return row[0]
    
    return None


def parse_acarsdec_json(json_data):
    """
    Parse JSON output from acarsdec
    
    Example acarsdec JSON format:
    {
        "timestamp": 1709654321.123,
        "channel": 0,
        "freq": 129.125,
        "level": -28,
        "error": 0,
        "mode": "2",
        "label": "H1",
        "block_id": "1",
        "ack": "N",
        "tail": "N12345",
        "flight": "AA1234",
        "msgno": "M01A",
        "text": "OOOI DATA HERE"
    }
    """
    try:
        data = json.loads(json_data)
        
        # Extract fields with defaults
        message = {
            'timestamp': data.get('timestamp'),
            'frequency': data.get('freq'),
            'channel': str(data.get('channel', '')),
            'registration': data.get('tail', '').strip() or None,
            'flight_id': data.get('flight', '').strip() or None,
            'mode': data.get('mode', ''),
            'label': data.get('label', ''),
            'block_id': data.get('block_id', ''),
            'ack': data.get('ack', ''),
            'message_number': data.get('msgno', ''),
            'message_text': data.get('text', '').strip() or None,
            'level': data.get('level'),
            'error': data.get('error', 0)
        }
        
        return message
        
    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}")
        return None


def store_acars_message(cursor, message):
    """Store ACARS message in Oracle database"""
    global stats
    
    # Classify the message
    message_type = classify_message(message['label'], message['message_text'])
    
    # Extract position if present
    lat, lon = extract_position(message['message_text'])
    alt = extract_altitude(message['message_text'])
    
    # Try to find linked ADS-B flight
    linked_flight_id = find_linked_flight(
        cursor, 
        message['registration'], 
        None  # ACARS doesn't typically have ICAO address
    )
    
    try:
        cursor.execute("""
            INSERT INTO acars_messages (
                frequency, channel, registration, flight_id,
                acars_mode, label, block_id, ack, message_number,
                message_text, latitude, longitude, altitude,
                message_type, linked_flight_id
            ) VALUES (
                :freq, :chan, :reg, :fid,
                :amode, :label, :block, :ack, :msgno,
                :text, :lat, :lon, :alt,
                :mtype, :linked
            )
        """,
            freq=message['frequency'],
            chan=message['channel'],
            reg=message['registration'],
            fid=message['flight_id'],
            amode=message['mode'],
            label=message['label'],
            block=message['block_id'],
            ack=message['ack'],
            msgno=message['message_number'],
            text=message['message_text'][:4000] if message['message_text'] else None,
            lat=lat,
            lon=lon,
            alt=alt,
            mtype=message_type,
            linked=linked_flight_id
        )
        
        # Update statistics
        stats['total_messages'] += 1
        if message['registration']:
            stats['unique_aircraft'].add(message['registration'])
        
        if message_type == 'OOOI':
            stats['oooi_count'] += 1
        elif message_type == 'POSITION':
            stats['position_count'] += 1
        elif message_type == 'WEATHER':
            stats['weather_count'] += 1
        elif message_type == 'MAINTENANCE':
            stats['maintenance_count'] += 1
        else:
            stats['freetext_count'] += 1
        
        if linked_flight_id:
            stats['linked_count'] += 1
        
        return True
        
    except Exception as e:
        print(f"Error storing message: {e}")
        return False


def print_interesting_message(message, message_type, linked):
    """Print interesting messages to console"""
    
    # Always print OOOI and maintenance messages
    if message_type in ('OOOI', 'MAINTENANCE', 'WEATHER'):
        link_str = " [LINKED to ADS-B]" if linked else ""
        print(f"\n{'='*60}")
        print(f"[{message_type}]{link_str}")
        print(f"  Aircraft: {message['registration'] or 'Unknown'}")
        print(f"  Flight:   {message['flight_id'] or 'Unknown'}")
        print(f"  Label:    {message['label']}")
        print(f"  Freq:     {message['frequency']} MHz")
        if message['message_text']:
            # Truncate long messages
            text = message['message_text'][:200]
            if len(message['message_text']) > 200:
                text += '...'
            print(f"  Message:  {text}")
        print(f"{'='*60}")


def print_stats():
    """Print current statistics"""
    print(f"\n--- ACARS Statistics ---")
    print(f"  Total messages:    {stats['total_messages']}")
    print(f"  OOOI events:       {stats['oooi_count']}")
    print(f"  Position reports:  {stats['position_count']}")
    print(f"  Weather reports:   {stats['weather_count']}")
    print(f"  Maintenance:       {stats['maintenance_count']}")
    print(f"  Free text:         {stats['freetext_count']}")
    print(f"  Unique aircraft:   {len(stats['unique_aircraft'])}")
    print(f"  Linked to ADS-B:   {stats['linked_count']}")
    print(f"------------------------\n")


def main():
    """Main collector loop using UDP"""
    global running
    
    print("=" * 60)
    print("ACARS Data Collector starting...")
    print("=" * 60)
    
    # Set up signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Connect to database
    db_conn = connect_to_database()
    cursor = db_conn.cursor()
    
    # Load aircraft cache for linking
    load_aircraft_cache(cursor)
    
    # Create UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(1.0)  # 1 second timeout for graceful shutdown
    
    try:
        sock.bind((ACARS_HOST, ACARS_PORT))
        print(f"Listening for ACARS JSON on UDP {ACARS_HOST}:{ACARS_PORT}")
        print("\nWaiting for messages from acarsdec...")
        print("(Start acarsdec with: acarsdec -v -o 4 -g 42 -r 0 129.125 130.025 130.450 -j 127.0.0.1:5555)\n")
    except Exception as e:
        print(f"Failed to bind UDP socket: {e}")
        sys.exit(1)
    
    last_commit_time = time.time()
    last_stats_time = time.time()
    commit_interval = 10  # Commit every 10 seconds
    stats_interval = 60   # Print stats every minute
    
    while running:
        try:
            # Receive UDP packet
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                # Check for pending commits
                current_time = time.time()
                if current_time - last_commit_time > commit_interval:
                    db_conn.commit()
                    last_commit_time = current_time
                continue
            
            # Decode and parse JSON
            json_str = data.decode('utf-8', errors='ignore').strip()
            if not json_str:
                continue
            
            # Parse the message
            message = parse_acarsdec_json(json_str)
            if not message:
                continue
            
            # Skip messages with errors
            if message.get('error', 0) > 2:
                continue
            
            # Store in database
            message_type = classify_message(message['label'], message['message_text'])
            linked = find_linked_flight(cursor, message['registration'], None)
            
            if store_acars_message(cursor, message):
                print_interesting_message(message, message_type, linked)
            
            # Periodic commit
            current_time = time.time()
            if current_time - last_commit_time > commit_interval:
                db_conn.commit()
                last_commit_time = current_time
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                print(f"[{timestamp}] Committed. Total: {stats['total_messages']} messages")
            
            # Periodic stats
            if current_time - last_stats_time > stats_interval:
                print_stats()
                last_stats_time = current_time
            
        except Exception as e:
            print(f"Error in main loop: {e}")
            time.sleep(1)
    
    # Cleanup
    print("\nShutting down...")
    db_conn.commit()
    print_stats()
    cursor.close()
    db_conn.close()
    sock.close()
    print("ACARS collector stopped.")


def main_tcp():
    """
    Alternative main loop using TCP (for acarsdec with -n option)
    Use this if your decoder outputs to TCP instead of UDP
    """
    global running
    
    print("=" * 60)
    print("ACARS Data Collector starting (TCP mode)...")
    print("=" * 60)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    db_conn = connect_to_database()
    cursor = db_conn.cursor()
    load_aircraft_cache(cursor)
    
    last_commit_time = time.time()
    commit_interval = 10
    
    while running:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            sock.connect((ACARS_HOST, ACARS_PORT))
            print(f"Connected to ACARS decoder at {ACARS_HOST}:{ACARS_PORT}")
            
            buffer = ""
            
            while running:
                try:
                    data = sock.recv(4096).decode('utf-8', errors='ignore')
                    if not data:
                        print("Connection closed by remote host")
                        break
                    
                    buffer += data
                    
                    while '\n' in buffer:
                        line, buffer = buffer.split('\n', 1)
                        line = line.strip()
                        if not line:
                            continue
                        
                        message = parse_acarsdec_json(line)
                        if message and message.get('error', 0) <= 2:
                            message_type = classify_message(message['label'], message['message_text'])
                            linked = find_linked_flight(cursor, message['registration'], None)
                            if store_acars_message(cursor, message):
                                print_interesting_message(message, message_type, linked)
                    
                    current_time = time.time()
                    if current_time - last_commit_time > commit_interval:
                        db_conn.commit()
                        last_commit_time = current_time
                        
                except socket.timeout:
                    continue
            
            sock.close()
            
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Connection error: {e}")
            print("Reconnecting in 5 seconds...")
            time.sleep(5)
    
    db_conn.commit()
    cursor.close()
    db_conn.close()
    print("ACARS collector stopped.")


if __name__ == '__main__':
    # Use UDP mode by default (most common for acarsdec)
    # Change to main_tcp() if using TCP output
    main()
