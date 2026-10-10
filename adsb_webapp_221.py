#!/usr/bin/env python3
"""
================================================================================
ADS-B Flight Tracker Web Application with Route Display
================================================================================
Version:        2.2.1
Last Updated:   2026-10-10
Author:         ADS-B Flight Tracker Route Detection System
Description:    Flask-based web application for visualizing ADS-B flight data
                with automatic route detection and display.

Features:
    - Current Aircraft page: Real-time aircraft list with routes
    - Live Map page: Interactive Leaflet map with aircraft positions
    - Routes page: Active routes, popular routes, airport traffic stats
    - Flight History page: Historical flight data with statistics
    - Statistics page: Database stats and hourly traffic charts
    - ACARS page: Real-time ACARS messages with search/filter/sort
    - Auto-refresh every 10 seconds
    - Route display: Origin &rarr; Destination with departure/ETA times

Pages:
    /           - Current aircraft with route information
    /map        - Interactive map with real-time positions
    /routes     - Route analysis (active, popular, airport traffic)
    /history    - Flight history with statistics
    /stats      - Database statistics and charts
    /acars      - ACARS messages with filtering and search
    /api/aircraft - JSON API for map data

Dependencies:
    - Flask: Web framework
    - oracledb: Oracle database connectivity
    - Leaflet.js: Interactive maps (CDN)

Configuration:
    - DB_USER, DB_PASSWORD, DB_DSN: Oracle connection settings
    - RECEIVER_LAT, RECEIVER_LON: Your receiver location
    - Port: 5001 (default)

Version History:
    2.2.1 (2026-10-10) - Live map shows last 30 minutes only
        - /api/aircraft now returns aircraft whose latest position is
          within MAP_WINDOW_MINUTES (30) instead of a 370-minute window
        - Time filters now compare against CURRENT_DATE (the session's
          America/Chicago clock) instead of SYSDATE (the DB server's OS
          clock, UTC in Docker). The old 370/390-minute values were
          hand-made UTC offsets that silently changed with daylight saving
        - Latest-position subquery restricted to the time window so it no
          longer scans every row in POSITIONS on each 5-second map refresh
        - Removed the debug COUNT(*) of all positions run on every refresh

    2.2.0 (2026-03-10) - ACARS integration
        - Added ACARS page with real-time message display
        - Sortable, filterable tables with search functionality
        - ACARS flight numbers enrich ADS-B flight data
        - OOOI events populate actual departure/arrival times
        - Added ACARS link to navigation on all pages

    2.1.0 (2026-02-25) - Operator data enhancement
        - Added Operator column to Current Aircraft page
        - Added Registration, Type, and Operator columns to Flight History page
        - Joined aircraft table in history query to pull operator data

    2.0.0 (2026-01-20) - Route detection enhancement
        - Added /routes page with active routes display
        - Origin &rarr; Destination display on main page
        - Departure time and ETA display
        - Popular routes analysis (last 30 days)
        - Airport traffic statistics (last 7 days)
        - Fixed squawk column error in v_current_aircraft query
        - Updated array indices after removing squawk
    
    1.0.0 (Earlier) - Initial release
        - Basic aircraft display
        - Interactive map
        - Flight history
        - Statistics page
================================================================================
"""

from flask import Flask, render_template_string, jsonify
import oracledb
from datetime import datetime

app = Flask(__name__)

# Oracle Database Configuration - UPDATE THESE
DB_USER = 'adsb_user'
DB_PASSWORD = 'oracle'
DB_DSN = '192.168.10.51:1521/PDB1'  

# Your receiver location (update with your actual location)
RECEIVER_LAT = 32.7357  # Arlington, TX
RECEIVER_LON = -97.1081

# Live map shows aircraft whose latest position is newer than this
MAP_WINDOW_MINUTES = 30

def get_db_connection():
    """Create database connection"""
    conn = oracledb.connect(
        user=DB_USER,
        password=DB_PASSWORD,
        dsn=DB_DSN
    )
    
    # Set session timezone to Central Time
    cursor = conn.cursor()
    cursor.execute("ALTER SESSION SET TIME_ZONE = 'America/Chicago'")
    cursor.close()
    
    return conn

# Register ACARS routes (must be after get_db_connection is defined)
from acars_routes import register_acars_routes
register_acars_routes(app, get_db_connection)

# HTML Template
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>ADS-B Flight Tracker</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f5f5f5;
        }
        .container {
            max-width: 98vw;
            margin: 0 auto;
            background-color: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        h1 {
            color: #333;
            border-bottom: 3px solid #4CAF50;
            padding-bottom: 10px;
        }
        .stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin: 20px 0;
        }
        .stat-box {
            background-color: #4CAF50;
            color: white;
            padding: 20px;
            border-radius: 5px;
            text-align: center;
        }
        .stat-box h3 {
            margin: 0;
            font-size: 2em;
        }
        .stat-box p {
            margin: 5px 0 0 0;
            opacity: 0.9;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 20px;
        }
        th {
            background-color: #4CAF50;
            color: white;
            padding: 12px;
            text-align: left;
            position: sticky;
            top: 0;
        }
        td {
            padding: 10px;
            border-bottom: 1px solid #ddd;
        }
        tr:hover {
            background-color: #f5f5f5;
        }
        .refresh-info {
            color: #666;
            font-size: 0.9em;
            margin-top: 10px;
        }
        .active {
            color: #4CAF50;
            font-weight: bold;
        }
        .altitude {
            text-align: right;
        }
        .speed {
            text-align: right;
        }
        .col-type {
            min-width: 180px;
            white-space: nowrap;
        }
        .col-operator {
            min-width: 220px;
        }
        button {
            background-color: #4CAF50;
            color: white;
            padding: 10px 20px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 16px;
            margin: 10px 5px;
        }
        button:hover {
            background-color: #45a049;
        }
        .nav {
            margin: 20px 0;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>&#9992;&#65039; ADS-B Flight Tracker</h1>
        
        <div class="nav">
            <button onclick="location.href='/'">Current Aircraft</button>
            <button onclick="location.href='/map'">Live Map</button>
            <button onclick="location.href='/routes'">Routes</button>
            <button onclick="location.href='/history'">Flight History</button>
            <button onclick="location.href='/stats'">Statistics</button>
            <button onclick="location.href='/acars'">ACARS</button>
        </div>
        
        {% if page == 'current' %}
        <div class="stats">
            <div class="stat-box">
                <h3>{{ stats.active_aircraft }}</h3>
                <p>Active Aircraft</p>
            </div>
            <div class="stat-box">
                <h3>{{ stats.total_aircraft }}</h3>
                <p>Total Aircraft Seen</p>
            </div>
            <div class="stat-box">
                <h3>{{ stats.total_positions }}</h3>
                <p>Position Reports</p>
            </div>
            <div class="stat-box">
                <h3>{{ stats.total_flights }}</h3>
                <p>Total Flights</p>
            </div>
        </div>
        
        <h2>Current Aircraft (Last 30 Minutes)</h2>
        <table>
            <thead>
                <tr>
                    <th>ICAO</th>
                    <th>Registration</th>
                    <th class="col-type">Type</th>
                    <th class="col-operator">Operator</th>
                    <th>Callsign</th>
                    <th>Route</th>
                    <th class="altitude">Altitude (ft)</th>
                    <th class="speed">Speed (kts)</th>
                    <th>Track</th>
                    <th>Position</th>
                    <th>Last Seen</th>
                    <th>Photo</th>
                </tr>
            </thead>
            <tbody>
                {% for aircraft in current_aircraft %}
                <tr>
                    <td>{{ aircraft[0] }}</td>
                    <td>{{ aircraft[1] or 'N/A' }}</td>
                    <td class="col-type">{{ aircraft[2] or 'N/A' }}</td>
                    <td class="col-operator">{{ aircraft[18] or 'N/A' }}</td>
                    <td><strong>{{ aircraft[3] or 'N/A' }}</strong></td>
                    <td>
                        {% if aircraft[10] or aircraft[13] %}
                            <strong>{{ aircraft[10] or aircraft[11] or '????' }}</strong> &rarr; <strong>{{ aircraft[13] or aircraft[14] or '????' }}</strong>
                            {% if aircraft[16] %}
                                <br><small>Dep: {{ aircraft[16].strftime('%H:%M') }}</small>
                            {% endif %}
                            {% if aircraft[17] %}
                                <br><small>ETA: {{ aircraft[17].strftime('%H:%M') }}</small>
                            {% endif %}
                        {% else %}
                            <span style="color: #999;">Unknown</span>
                        {% endif %}
                    </td>
                    <td class="altitude">{{ aircraft[4] or 'N/A' }}</td>
                    <td class="speed">{{ aircraft[5] or 'N/A' }}</td>
                    <td>{{ aircraft[6] or 'N/A' }}&deg;</td>
                    <td>
                        {% if aircraft[7] and aircraft[8] %}
                            {{ "%.4f"|format(aircraft[7]) }}, {{ "%.4f"|format(aircraft[8]) }}
                        {% else %}
                            N/A
                        {% endif %}
                    </td>
                    <td>
                        <span class="{% if aircraft[9] < 5 %}active{% endif %}">
                            {{ "%.1f"|format(aircraft[9]) }} min ago
                        </span>
                    </td>
                    <td>
                        {% if aircraft[1] %}
                            <a href="https://www.jetphotos.com/registration/{{ aircraft[1] }}" target="_blank" style="color: #4CAF50; text-decoration: none;">Photo</a>
                        {% else %}
                            N/A
                        {% endif %}
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        
        {% elif page == 'history' %}
        <h2>Recent Flights</h2>
        <table>
            <thead>
                <tr>
                    <th>ICAO</th>
                    <th>Registration</th>
                    <th class="col-type">Type</th>
                    <th class="col-operator">Operator</th>
                    <th>Callsign</th>
                    <th>First Contact</th>
                    <th>Last Contact</th>
                    <th>Duration</th>
                    <th>Positions</th>
                    <th>Max Alt (ft)</th>
                    <th>Avg Speed (kts)</th>
                </tr>
            </thead>
            <tbody>
                {% for flight in flights %}
                <tr>
                    <td>{{ flight[0] }}</td>
                    <td>{{ flight[7] or 'N/A' }}</td>
                    <td class="col-type">{{ flight[8] or 'N/A' }}</td>
                    <td class="col-operator">{{ flight[9] or 'N/A' }}</td>
                    <td><strong>{{ flight[1] or 'N/A' }}</strong></td>
                    <td>{{ flight[2].strftime('%Y-%m-%d %H:%M:%S') if flight[2] else 'N/A' }}</td>
                    <td>{{ flight[3].strftime('%Y-%m-%d %H:%M:%S') if flight[3] else 'N/A' }}</td>
                    <td>
                        {% if flight[2] and flight[3] %}
                            {{ "%.1f"|format((flight[3] - flight[2]).total_seconds() / 60) }} min
                        {% else %}
                            N/A
                        {% endif %}
                    </td>
                    <td>{{ flight[4] }}</td>
                    <td class="altitude">{{ flight[5] or 'N/A' }}</td>
                    <td class="speed">{{ "%.0f"|format(flight[6]) if flight[6] else 'N/A' }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        
        {% elif page == 'routes' %}
        <h2>Active Routes (Last Hour)</h2>
        <table>
            <thead>
                <tr>
                    <th>Callsign</th>
                    <th>Aircraft</th>
                    <th>Origin</th>
                    <th>Destination</th>
                    <th>Distance (nm)</th>
                    <th>Departed</th>
                    <th>ETA</th>
                    <th>Max Alt (ft)</th>
                    <th>Avg Speed (kts)</th>
                </tr>
            </thead>
            <tbody>
                {% for route in current_routes %}
                <tr>
                    <td><strong>{{ route[1] or route[0] }}</strong></td>
                    <td>{{ route[2] or 'N/A' }}<br><small>{{ route[3] or '' }}</small></td>
                    <td>
                        {% if route[4] or route[5] %}
                            <strong>{{ route[4] or route[5] }}</strong><br>
                            <small>{{ route[6] or '' }}{% if route[7] %}, {{ route[7] }}{% endif %}</small>
                        {% else %}
                            <span style="color: #999;">Unknown</span>
                        {% endif %}
                    </td>
                    <td>
                        {% if route[8] or route[9] %}
                            <strong>{{ route[8] or route[9] }}</strong><br>
                            <small>{{ route[10] or '' }}{% if route[11] %}, {{ route[11] }}{% endif %}</small>
                        {% else %}
                            <span style="color: #999;">Unknown</span>
                        {% endif %}
                    </td>
                    <td class="altitude">{{ route[14] or 'N/A' }}</td>
                    <td>{{ route[12].strftime('%H:%M') if route[12] else 'N/A' }}</td>
                    <td>{{ route[13].strftime('%H:%M') if route[13] else 'N/A' }}</td>
                    <td class="altitude">{{ "{:,}".format(route[15]) if route[15] else 'N/A' }}</td>
                    <td class="speed">{{ "%.0f"|format(route[16]) if route[16] else 'N/A' }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        
        <h2>Popular Routes (Last 30 Days)</h2>
        <table>
            <thead>
                <tr>
                    <th>Route</th>
                    <th>Origin</th>
                    <th>Destination</th>
                    <th>Flights</th>
                    <th>Aircraft</th>
                    <th>Distance (nm)</th>
                    <th>First Seen</th>
                    <th>Last Seen</th>
                </tr>
            </thead>
            <tbody>
                {% for route in popular_routes %}
                <tr>
                    <td><strong>{{ route[0] }}</strong></td>
                    <td>
                        <strong>{{ route[1] or route[2] }}</strong><br>
                        <small>{{ route[3] }}{% if route[4] %}, {{ route[4] }}{% endif %}</small>
                    </td>
                    <td>
                        <strong>{{ route[5] or route[6] }}</strong><br>
                        <small>{{ route[7] }}{% if route[8] %}, {{ route[8] }}{% endif %}</small>
                    </td>
                    <td>{{ route[9] }}</td>
                    <td>{{ route[10] }}</td>
                    <td class="altitude">{{ route[11] or 'N/A' }}</td>
                    <td>{{ route[12].strftime('%Y-%m-%d') if route[12] else 'N/A' }}</td>
                    <td>{{ route[13].strftime('%Y-%m-%d') if route[13] else 'N/A' }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        
        <h2>Airport Traffic (Last 7 Days)</h2>
        <table>
            <thead>
                <tr>
                    <th>Airport</th>
                    <th>Location</th>
                    <th>Departures</th>
                    <th>Arrivals</th>
                    <th>Total Traffic</th>
                </tr>
            </thead>
            <tbody>
                {% for airport in airport_traffic %}
                <tr>
                    <td><strong>{{ airport[1] or airport[2] }}</strong><br><small>{{ airport[3] }}</small></td>
                    <td>{{ airport[4] }}, {{ airport[5] }}</td>
                    <td>{{ airport[6] }}</td>
                    <td>{{ airport[7] }}</td>
                    <td><strong>{{ airport[8] }}</strong></td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        
        {% elif page == 'stats' %}
        <h2>Database Statistics</h2>
        <div class="stats">
            <div class="stat-box">
                <h3>{{ stats.total_aircraft }}</h3>
                <p>Unique Aircraft</p>
            </div>
            <div class="stat-box">
                <h3>{{ stats.total_flights }}</h3>
                <p>Total Flights</p>
            </div>
            <div class="stat-box">
                <h3>{{ stats.total_positions }}</h3>
                <p>Position Reports</p>
            </div>
            <div class="stat-box">
                <h3>{{ stats.active_aircraft }}</h3>
                <p>Active (30 min)</p>
            </div>
        </div>
        
        <h3>Recent Activity</h3>
        <table>
            <thead>
                <tr>
                    <th>Hour</th>
                    <th>Aircraft Count</th>
                    <th>Position Reports</th>
                </tr>
            </thead>
            <tbody>
                {% for hour in hourly_stats %}
                <tr>
                    <td>{{ hour[0].strftime('%Y-%m-%d %H:00') if hour[0] else 'N/A' }}</td>
                    <td>{{ hour[1] }}</td>
                    <td>{{ hour[2] }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        {% endif %}
        
        <p class="refresh-info">Page auto-refreshes every 10 seconds</p>
    </div>
    
    <script>
        // Auto-refresh every 10 seconds
        setTimeout(function() {
            location.reload();
        }, 10000);
    </script>
</body>
</html>
"""

# Map Template
MAP_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>ADS-B Live Map</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 0;
        }
        .header {
            background-color: #4CAF50;
            color: white;
            padding: 10px 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .nav {
            display: flex;
            gap: 10px;
        }
        button {
            background-color: white;
            color: #4CAF50;
            padding: 8px 16px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 14px;
        }
        button:hover {
            background-color: #f0f0f0;
        }
        #map {
            height: calc(100vh - 120px);
            width: 100%;
        }
        .stats {
            background-color: #f5f5f5;
            padding: 10px 20px;
            display: flex;
            gap: 30px;
            border-top: 1px solid #ddd;
        }
        .stat-item {
            font-size: 14px;
        }
        .stat-value {
            font-weight: bold;
            color: #4CAF50;
            font-size: 18px;
        }
        .aircraft-icon {
            font-size: 20px;
            transition: transform 0.3s;
        }
        .popup-content {
            min-width: 200px;
        }
        .popup-content h3 {
            margin: 0 0 10px 0;
            color: #4CAF50;
        }
        .popup-content table {
            width: 100%;
            font-size: 12px;
        }
        .popup-content td {
            padding: 3px 5px;
        }
        .popup-content td:first-child {
            font-weight: bold;
            width: 40%;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>&#9992;&#65039; ADS-B Live Map</h1>
        <div class="nav">
            <button onclick="location.href='/'">Current Aircraft</button>
            <button onclick="location.href='/map'">Live Map</button>
            <button onclick="location.href='/history'">Flight History</button>
            <button onclick="location.href='/stats'">Statistics</button>
            <button onclick="location.href='/acars'">ACARS</button>
            <button onclick="toggleTrails()" id="trail-toggle">Hide Trails</button>
        </div>
    </div>
    
    <div id="map"></div>
    
    <div class="stats">
        <div class="stat-item">
            <div class="stat-value" id="aircraft-count">0</div>
            <div>Aircraft Tracked</div>
        </div>
        <div class="stat-item">
            <div class="stat-value" id="update-time">--:--:--</div>
            <div>Last Update</div>
        </div>
        <div class="stat-item">
            <div class="stat-value" id="altitude-range">0 - 0 ft</div>
            <div>Altitude Range</div>
        </div>
    </div>
    
    <script>
        // Initialize map centered on your receiver location
        const RECEIVER_LAT = """ + str(RECEIVER_LAT) + """;
        const RECEIVER_LON = """ + str(RECEIVER_LON) + """;
        
        const map = L.map('map').setView([RECEIVER_LAT, RECEIVER_LON], 10);
        
        // Add OpenStreetMap tiles
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '&copy; OpenStreetMap contributors',
            maxZoom: 19
        }).addTo(map);
        
        // Add receiver marker
        const receiverIcon = L.divIcon({
            html: '<div style="font-size: 24px;">&#128161;</div>',
            className: 'receiver-icon',
            iconSize: [30, 30],
            iconAnchor: [15, 15]
        });
        
        L.marker([RECEIVER_LAT, RECEIVER_LON], {icon: receiverIcon})
            .bindPopup('<b>ADS-B Receiver</b><br>Arlington, TX')
            .addTo(map);
        
        // Store aircraft markers and trails
        let aircraftMarkers = {};
        let aircraftTrails = {};
        let showTrails = true;  // Toggle for showing/hiding trails
        
        // Function to get color based on altitude
        function getAltitudeColor(altitude) {
            if (!altitude) return '#808080';
            if (altitude < 5000) return '#ff4444';
            if (altitude < 15000) return '#ff8800';
            if (altitude < 25000) return '#ffcc00';
            if (altitude < 35000) return '#88cc00';
            return '#0088ff';
        }
        
        // Function to create aircraft icon with rotation
        function createAircraftIcon(track, altitude) {
            const color = getAltitudeColor(altitude);
            const rotation = track || 0;
            return L.divIcon({
                html: `<div style="transform: rotate(${rotation}deg); font-size: 34px; color: ${color};">&#9992;</div>`,
                className: 'aircraft-icon',
                iconSize: [24, 24],
                iconAnchor: [12, 12]
            });
        }
        
        // Function to create popup content
        function createPopup(aircraft) {
            let html = '<div class="popup-content">';
            html += `<h3>${aircraft.callsign || aircraft.icao}</h3>`;
            html += '<table>';
            if (aircraft.registration) html += `<tr><td>Registration:</td><td>${aircraft.registration}</td></tr>`;
            if (aircraft.type) html += `<tr><td>Type:</td><td>${aircraft.type}</td></tr>`;
            if (aircraft.operator) html += `<tr><td>Operator:</td><td>${aircraft.operator}</td></tr>`;
            html += `<tr><td>ICAO:</td><td>${aircraft.icao}</td></tr>`;
            if (aircraft.altitude) html += `<tr><td>Altitude:</td><td>${aircraft.altitude.toLocaleString()} ft</td></tr>`;
            if (aircraft.speed) html += `<tr><td>Speed:</td><td>${aircraft.speed} kts</td></tr>`;
            if (aircraft.track) html += `<tr><td>Track:</td><td>${aircraft.track}&deg;</td></tr>`;
            if (aircraft.squawk) html += `<tr><td>Squawk:</td><td>${aircraft.squawk}</td></tr>`;
            html += '</table></div>';
            return html;
        }
        
        // Function to fetch and draw flight trail
        function updateTrail(aircraft) {
            if (!showTrails || !aircraft.flight_id) return;
            
            fetch(`/api/trail/${aircraft.flight_id}`)
                .then(response => response.json())
                .then(trail => {
                    if (trail.length < 2) return;  // Need at least 2 points for a line
                    
                    // Remove old trail if exists
                    if (aircraftTrails[aircraft.icao]) {
                        map.removeLayer(aircraftTrails[aircraft.icao]);
                    }
                    
                    // Create array of lat/lon pairs
                    const coords = trail.map(point => [point.lat, point.lon]);
                    
                    // Create polyline with color based on altitude
                    const color = getAltitudeColor(aircraft.altitude);
                    const polyline = L.polyline(coords, {
                        color: color,
                        weight: 2,
                        opacity: 0.6,
                        smoothFactor: 1
                    }).addTo(map);
                    
                    // Store trail
                    aircraftTrails[aircraft.icao] = polyline;
                    
                    console.log(`Drew trail for ${aircraft.icao}: ${trail.length} points`);
                })
                .catch(error => console.error(`Error fetching trail for ${aircraft.icao}:`, error));
        }
        
        // Function to update aircraft on map
        function updateAircraft() {
            fetch('/api/aircraft')
                .then(response => response.json())
                .then(data => {
                    console.log('Received aircraft data:', data.length, 'aircraft');
                    
                    const currentIcaos = new Set();
                    let minAlt = Infinity;
                    let maxAlt = -Infinity;
                    
                    // Update or create markers for each aircraft
                    data.forEach(aircraft => {
                        console.log('Processing aircraft:', aircraft.icao, 'Lat:', aircraft.lat, 'Lon:', aircraft.lon);
                        
                        if (!aircraft.lat || !aircraft.lon) {
                            console.warn('Aircraft missing coordinates:', aircraft.icao);
                            return;
                        }
                        
                        currentIcaos.add(aircraft.icao);
                        
                        // Track altitude range
                        if (aircraft.altitude) {
                            minAlt = Math.min(minAlt, aircraft.altitude);
                            maxAlt = Math.max(maxAlt, aircraft.altitude);
                        }
                        
                        const icon = createAircraftIcon(aircraft.track, aircraft.altitude);
                        
                        if (aircraftMarkers[aircraft.icao]) {
                            // Update existing marker
                            const marker = aircraftMarkers[aircraft.icao];
                            marker.setLatLng([aircraft.lat, aircraft.lon]);
                            marker.setIcon(icon);
                            marker.setPopupContent(createPopup(aircraft));
                            console.log('Updated marker for:', aircraft.icao);
                        } else {
                            // Create new marker
                            const marker = L.marker([aircraft.lat, aircraft.lon], {icon: icon})
                                .bindPopup(createPopup(aircraft))
                                .addTo(map);
                            aircraftMarkers[aircraft.icao] = marker;
                            console.log('Created new marker for:', aircraft.icao, 'at', aircraft.lat, aircraft.lon);
                            
                            // Fetch and draw trail for new aircraft
                            updateTrail(aircraft);
                        }
                    });
                    
                    console.log('Total markers on map:', Object.keys(aircraftMarkers).length);
                    
                    // Remove markers for aircraft no longer present
                    Object.keys(aircraftMarkers).forEach(icao => {
                        if (!currentIcaos.has(icao)) {
                            map.removeLayer(aircraftMarkers[icao]);
                            delete aircraftMarkers[icao];
                            
                            // Also remove trail
                            if (aircraftTrails[icao]) {
                                map.removeLayer(aircraftTrails[icao]);
                                delete aircraftTrails[icao];
                            }
                            
                            console.log('Removed marker for:', icao);
                        }
                    });
                    
                    // Update statistics
                    document.getElementById('aircraft-count').textContent = data.length;
                    document.getElementById('update-time').textContent = new Date().toLocaleTimeString();
                    
                    if (minAlt !== Infinity && maxAlt !== -Infinity) {
                        document.getElementById('altitude-range').textContent = 
                            `${minAlt.toLocaleString()} - ${maxAlt.toLocaleString()} ft`;
                    }
                })
                .catch(error => {
                    console.error('Error fetching aircraft:', error);
                });
        }
        
        // Toggle trails on/off
        function toggleTrails() {
            showTrails = !showTrails;
            const button = document.getElementById('trail-toggle');
            
            if (showTrails) {
                button.textContent = 'Hide Trails';
                // Reload all trails
                fetch('/api/aircraft')
                    .then(response => response.json())
                    .then(data => {
                        data.forEach(aircraft => {
                            if (aircraft.lat && aircraft.lon) {
                                updateTrail(aircraft);
                            }
                        });
                    });
            } else {
                button.textContent = 'Show Trails';
                // Remove all trails
                Object.values(aircraftTrails).forEach(trail => {
                    map.removeLayer(trail);
                });
                aircraftTrails = {};
            }
        }
        
        // Initial update
        updateAircraft();
        
        // Update every 5 seconds
        setInterval(updateAircraft, 5000);
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    """Display current aircraft"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get current aircraft from view with route information
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
            fr.estimated_arrival,
            a.operator
        FROM v_current_aircraft ca
        LEFT JOIN flights f ON ca.icao_address = f.icao_address 
            AND ca.callsign = f.callsign
        LEFT JOIN flight_routes fr ON f.flight_id = fr.flight_id
        LEFT JOIN airports origin ON fr.origin_airport_id = origin.airport_id
        LEFT JOIN airports dest ON fr.dest_airport_id = dest.airport_id
        LEFT JOIN aircraft a ON ca.icao_address = a.icao_address
        ORDER BY ca.minutes_ago ASC
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

@app.route('/routes')
def routes():
    """Display detected routes"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get current active routes
    cursor.execute("""
        SELECT 
            icao_address,
            callsign,
            registration,
            aircraft_type,
            origin_icao,
            origin_iata,
            origin_name,
            origin_city,
            dest_icao,
            dest_iata,
            dest_name,
            dest_city,
            actual_departure,
            estimated_arrival,
            route_distance_nm,
            max_altitude,
            avg_speed
        FROM v_flight_routes
        WHERE (origin_icao IS NOT NULL OR dest_icao IS NOT NULL)
          AND last_contact > CURRENT_DATE - (60/1440)
        ORDER BY last_contact DESC
    """)
    current_routes = cursor.fetchall()
    
    # Get popular routes
    cursor.execute("""
        SELECT * FROM v_popular_routes_enhanced
        FETCH FIRST 20 ROWS ONLY
    """)
    popular_routes = cursor.fetchall()
    
    # Get airport traffic
    cursor.execute("""
        SELECT * FROM v_airport_traffic
        WHERE total_traffic_7days > 0
        ORDER BY total_traffic_7days DESC
        FETCH FIRST 15 ROWS ONLY
    """)
    airport_traffic = cursor.fetchall()
    
    cursor.close()
    conn.close()
    
    return render_template_string(HTML_TEMPLATE,
                                 page='routes',
                                 current_routes=current_routes,
                                 popular_routes=popular_routes,
                                 airport_traffic=airport_traffic)

@app.route('/history')
def history():
    """Display flight history"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get recent flights with stats
    cursor.execute("""
        SELECT 
            f.icao_address,
            f.callsign,
            MIN(p.received_time) as first_contact,
            MAX(p.received_time) as last_contact,
            COUNT(p.position_id) as position_count,
            MAX(p.altitude) as max_altitude,
            AVG(p.ground_speed) as avg_speed,
            a.registration,
            a.aircraft_type,
            a.operator
        FROM flights f
        LEFT JOIN positions p ON f.flight_id = p.flight_id
        LEFT JOIN aircraft a ON f.icao_address = a.icao_address
        GROUP BY f.icao_address, f.callsign, a.registration, a.aircraft_type, a.operator
        ORDER BY last_contact DESC
        FETCH FIRST 100 ROWS ONLY
    """)
    flights = cursor.fetchall()
    
    cursor.close()
    conn.close()
    
    return render_template_string(HTML_TEMPLATE, 
                                 page='history',
                                 flights=flights)

@app.route('/map')
def map_view():
    """Display interactive map with current aircraft"""
    return render_template_string(MAP_TEMPLATE)

@app.route('/api/aircraft')
def api_aircraft():
    """API endpoint for current aircraft data (JSON)"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Latest position per flight, only for flights heard from in the last
    # MAP_WINDOW_MINUTES. CURRENT_DATE uses the session time zone
    # (America/Chicago, set in get_db_connection), matching received_time.
    cursor.execute("""
        SELECT 
            a.icao_address,
            a.registration,
            a.aircraft_type,
            f.callsign,
            p.altitude,
            p.ground_speed,
            p.track,
            p.latitude,
            p.longitude,
            p.received_time,
            p.squawk,
            a.operator
        FROM aircraft a
        JOIN flights f ON a.icao_address = f.icao_address
        JOIN positions p ON f.flight_id = p.flight_id
        WHERE p.position_id IN (
            SELECT MAX(position_id) 
            FROM positions 
            WHERE latitude IS NOT NULL 
              AND longitude IS NOT NULL
              AND received_time > CURRENT_DATE - (:win / 1440)
            GROUP BY flight_id
        )
        AND p.received_time > CURRENT_DATE - (:win / 1440)
        AND p.latitude IS NOT NULL
        AND p.longitude IS NOT NULL
        ORDER BY p.received_time DESC
    """, win=MAP_WINDOW_MINUTES)
    
    aircraft_list = []
    for row in cursor.fetchall():
        aircraft_list.append({
            'icao': row[0],
            'registration': row[1],
            'type': row[2],
            'callsign': row[3],
            'altitude': row[4],
            'speed': row[5],
            'track': row[6],
            'lat': float(row[7]) if row[7] else None,
            'lon': float(row[8]) if row[8] else None,
            'time': row[9].isoformat() if row[9] else None,
            'squawk': row[10],
            'operator': row[11]
        })
    
    print(f"Returning {len(aircraft_list)} aircraft to map")
    
    cursor.close()
    conn.close()
    
    return jsonify(aircraft_list)

@app.route('/stats')
def stats():
    """Display statistics"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get overall stats
    cursor.execute("SELECT COUNT(*) FROM aircraft")
    total_aircraft = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM flights")
    total_flights = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM positions")
    total_positions = cursor.fetchone()[0]
    
    cursor.execute("""
        SELECT COUNT(DISTINCT icao_address) 
        FROM positions 
        WHERE received_time > CURRENT_DATE - INTERVAL '30' MINUTE
    """)
    active_aircraft = cursor.fetchone()[0]
    
    stats_data = {
        'total_aircraft': total_aircraft,
        'total_flights': total_flights,
        'total_positions': total_positions,
        'active_aircraft': active_aircraft
    }
    
    # Get hourly statistics for last 24 hours
    cursor.execute("""
        SELECT 
            TRUNC(received_time, 'HH') as hour,
            COUNT(DISTINCT icao_address) as aircraft_count,
            COUNT(*) as position_count
        FROM positions
        WHERE received_time > CURRENT_DATE - 1
        GROUP BY TRUNC(received_time, 'HH')
        ORDER BY hour DESC
    """)
    hourly_stats = cursor.fetchall()
    
    cursor.close()
    conn.close()
    
    return render_template_string(HTML_TEMPLATE, 
                                 page='stats',
                                 stats=stats_data,
                                 hourly_stats=hourly_stats)

if __name__ == '__main__':
    print("Starting ADS-B Web Application v2.2.1...")
    print("Open your browser to: http://localhost:5001")
    app.run(host='0.0.0.0', port=5001, debug=True)

