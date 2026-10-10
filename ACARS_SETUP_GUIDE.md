# ACARS Integration Setup Guide

## Overview

This guide walks you through setting up ACARS message collection to complement your existing ADS-B flight tracking system. ACARS (Aircraft Communications Addressing and Reporting System) provides text-based messages between aircraft and ground stations, including:

- **OOOI Events**: Out of gate, Off ground, On ground, Into gate times
- **Position Reports**: Aircraft location transmitted via datalink
- **Weather Reports**: Pilot reports (PIREPs) on turbulence, icing, winds
- **Maintenance Data**: Engine performance, fault codes
- **Operational Messages**: Fuel data, delays, gate assignments

## Hardware Requirements

### RTL-SDR Dongle
- **Recommended**: RTL-SDR Blog V4 or V5 (~$35)
- You need a **separate dongle** from your ADS-B receiver since ACARS uses different frequencies
- Any RTL-SDR compatible dongle will work

### VHF Antenna
Your ADS-B antenna is tuned for 1090 MHz. ACARS uses 129-131 MHz (VHF). Options:

1. **RTL-SDR Blog Dipole Kit** (~$25)
   - Telescopic elements - extend to ~22 inches for VHF
   - Screw-on assembly, no soldering required
   - Recommended for beginners

2. **Marine/Aviation VHF Mag-Mount** (~$15-20)
   - Pre-tuned for VHF frequencies
   - Easy setup

3. **DIY Quarter-Wave Ground Plane**
   - ~22 inch vertical element
   - 4 radials at 45 degrees
   - Requires SMA connector (soldering optional with screw terminals)

### ACARS Frequencies (DFW Area)
Monitor these frequencies for best coverage:
- **129.125 MHz** - Primary ACARS
- **130.025 MHz** - Secondary ACARS
- **130.450 MHz** - Additional channel
- **131.550 MHz** - Additional channel

## Software Installation

### 1. Install acarsdec

On your Raspberry Pi or Linux machine:

```bash
# Install dependencies
sudo apt-get update
sudo apt-get install -y build-essential cmake librtlsdr-dev libusb-1.0-0-dev

# Clone and build acarsdec
git clone https://github.com/TLeconte/acarsdec.git
cd acarsdec
mkdir build && cd build
cmake .. -Drtl=ON
make
sudo make install
```

### 2. Test acarsdec

```bash
# Find your RTL-SDR device (should show device 0 or 1)
rtl_test

# Test ACARS reception (adjust device number if needed)
qa  W2
```

You should start seeing messages if aircraft are transmitting nearby.

### 3. Install Oracle Database Schema

Connect to your Oracle database and run the schema script:

```bash
# Using SQLPlus
docker exec -it ora23c sqlplus adsb_user/oracle@FREEPDB1

# Then run:
@/path/to/acars_schema.sql
```

Or via Python:
```python
import oracledb
conn = oracledb.connect(user='adsb_user', password='oracle', dsn='localhost:1522/FREEPDB1')
cursor = conn.cursor()
with open('acars_schema.sql', 'r') as f:
    for statement in f.read().split(';'):
        if statement.strip():
            try:
                cursor.execute(statement)
            except Exception as e:
                print(f"Warning: {e}")
conn.commit()
```

### 4. Configure the ACARS Collector

Edit `acars_collector.py` to match your setup:

```python
# Configuration
ACARS_HOST = '0.0.0.0'  # Listen on all interfaces
ACARS_PORT = 5555       # UDP port for acarsdec JSON output

# Oracle Database Configuration - UPDATE THESE
DB_USER = 'adsb_user'
DB_PASSWORD = 'oracle'
DB_DSN = 'localhost:1522/FREEPDB1'
```

## Running the System

### Option A: Run on Same Machine as acarsdec

Terminal 1 - Start acarsdec with JSON output:
```bash
acarsdec -v -o 4 -g 42 -r 0 129.125 130.025 130.450 131.550 -j 127.0.0.1:5555
```

Terminal 2 - Start the collector:
```bash
python3 acars_collector.py
```

### Option B: Run acarsdec on Raspberry Pi, Collector Elsewhere

On Raspberry Pi:
```bash
acarsdec -v -o 4 -g 42 -r 0 129.125 130.025 130.450 131.550 -j YOUR_MAC_IP:5555
```

On your Mac/server:
```bash
python3 acars_collector.py
```

### Option C: Run with Auto-Restart

For 24/7 operation:
```bash
# Run collector with auto-restart
nohup python3 acars_collector_restart.py > acars_restart.log 2>&1 &

# Run acarsdec with auto-restart (create a similar script or use systemd)
```

### Systemd Service (for Linux)

Create `/etc/systemd/system/acars-collector.service`:
```ini
[Unit]
Description=ACARS Data Collector
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/adsb
ExecStart=/usr/bin/python3 /home/pi/adsb/acars_collector.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable acars-collector
sudo systemctl start acars-collector
```

## Web Interface Integration

### Add ACARS Page to Your Webapp

Edit your `adsb_webapp.py` and add:

```python
# At the top with other imports
from acars_routes import register_acars_routes

# After creating the Flask app (after app = Flask(__name__))
register_acars_routes(app, connect_to_database)
```

Or copy the routes directly from `acars_routes.py` into your webapp.

The ACARS page will be available at: `http://your-server:5001/acars`

## Verification

### Check Database Tables
```sql
-- Verify tables exist
SELECT table_name FROM user_tables WHERE table_name LIKE 'ACARS%';

-- Check for messages
SELECT COUNT(*) FROM acars_messages;

-- View recent messages
SELECT * FROM v_acars_current;

-- View OOOI events
SELECT * FROM v_acars_oooi;
```

### Check Collector Output
The collector prints interesting messages to console:
```
============================================================
[OOOI] [LINKED to ADS-B]
  Aircraft: N12345
  Flight:   AA1234
  Label:    H2
  Freq:     129.125 MHz
  Message:  OFF 1423 KDFW
============================================================
```

## Troubleshooting

### No Messages Received
1. Check antenna connection and frequency tuning
2. Verify acarsdec is outputting JSON: `acarsdec -v -o 4 -r 0 129.125`
3. Check UDP connectivity: `nc -ul 5555` (listen for UDP)
4. Adjust RTL-SDR gain: try values 20-50

### Database Connection Errors
1. Verify Oracle container is running: `docker ps`
2. Check connection string matches your setup
3. Test connection: `sqlplus adsb_user/oracle@localhost:1522/FREEPDB1`

### Messages Not Linking to ADS-B
1. Ensure aircraft table has registration data
2. Run the link procedure: `EXEC link_acars_to_adsb;`
3. Check that flights are recent (within 2 hours)

### High Error Count in acarsdec
1. Reduce gain: `-g 30` instead of `-g 42`
2. Check for interference sources
3. Improve antenna placement

## Database Maintenance

### Daily Statistics Update
Add to your cron:
```sql
-- Update daily stats (add to scheduled job)
INSERT INTO acars_stats (stat_date, total_messages, oooi_count, ...)
SELECT TRUNC(SYSDATE-1), COUNT(*), ...
FROM acars_messages
WHERE received_time >= TRUNC(SYSDATE-1)
  AND received_time < TRUNC(SYSDATE);
```

### Purge Old Messages
```sql
-- Delete messages older than 30 days
DELETE FROM acars_messages WHERE received_time < SYSDATE - 30;
COMMIT;
```

## Files Reference

| File | Description |
|------|-------------|
| `acars_schema.sql` | Oracle database tables, views, and procedures |
| `acars_collector.py` | Main ACARS message collector |
| `acars_collector_restart.py` | Auto-restart wrapper |
| `acars_routes.py` | Flask web interface routes |

## Next Steps

1. Monitor ACARS reception for a few days to verify coverage
2. Correlate ACARS OOOI times with ADS-B departure/arrival detection
3. Consider adding VDL2 decoding for additional messages
4. Build analytics dashboards for delay patterns, weather reports, etc.

---

*Last updated: 2026-03-05*
