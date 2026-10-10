"""
================================================================================
ACARS Web Interface Routes
================================================================================
Version:        1.1.0
Last Updated:   2026-03-10
Description:    Flask routes to add ACARS viewing to the ADS-B web application.
                Features sortable columns, filtering by type, and search.

Integration:
    Add this import at the top of adsb_webapp.py:
        from acars_routes import register_acars_routes
    
    Then after creating the Flask app, call:
        register_acars_routes(app, get_db_connection_function)

Or simply copy the routes into your existing webapp.
================================================================================
"""

from flask import render_template_string, jsonify, request
from datetime import datetime


# HTML template for ACARS messages page
ACARS_HTML = '''
<!DOCTYPE html>
<html>
<head>
    <title>ACARS Messages - ADS-B Tracker</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #1a1a2e; 
            color: #eee;
            padding: 20px;
        }
        h1 { color: #00d4ff; margin-bottom: 10px; }
        h2 { color: #00d4ff; margin: 20px 0 10px 0; font-size: 1.2em; }
        .nav { margin-bottom: 20px; }
        .nav a { 
            color: #00d4ff; 
            text-decoration: none; 
            margin-right: 20px;
            padding: 8px 15px;
            background: #16213e;
            border-radius: 5px;
        }
        .nav a:hover { background: #1f4068; }
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 15px;
            margin-bottom: 30px;
        }
        .stat-box {
            background: #16213e;
            padding: 20px;
            border-radius: 10px;
            text-align: center;
        }
        .stat-box .number { 
            font-size: 2em; 
            color: #00d4ff; 
            font-weight: bold;
        }
        .stat-box .label { 
            color: #888; 
            font-size: 0.9em;
            margin-top: 5px;
        }
        
        /* Search and Filter Controls */
        .controls {
            display: flex;
            gap: 15px;
            margin-bottom: 20px;
            flex-wrap: wrap;
            align-items: center;
        }
        .search-box {
            flex: 1;
            min-width: 250px;
            max-width: 400px;
        }
        .search-box input {
            width: 100%;
            padding: 10px 15px;
            border: none;
            border-radius: 5px;
            background: #16213e;
            color: #eee;
            font-size: 1em;
        }
        .search-box input::placeholder { color: #666; }
        .search-box input:focus { 
            outline: 2px solid #00d4ff;
            background: #1f4068;
        }
        .filter-buttons {
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }
        .filter-btn {
            padding: 8px 15px;
            background: #16213e;
            border: none;
            color: #eee;
            border-radius: 5px;
            cursor: pointer;
            font-size: 0.9em;
            transition: all 0.2s;
        }
        .filter-btn:hover { background: #1f4068; }
        .filter-btn.active {
            background: #00d4ff;
            color: #1a1a2e;
        }
        .refresh-btn {
            padding: 8px 15px;
            background: #28a745;
            border: none;
            color: white;
            border-radius: 5px;
            cursor: pointer;
            font-size: 0.9em;
        }
        .refresh-btn:hover { background: #218838; }
        
        /* Table Styles */
        .table-container {
            overflow-x: auto;
            margin-bottom: 30px;
        }
        table { 
            width: 100%; 
            border-collapse: collapse; 
            background: #16213e;
            border-radius: 10px;
            overflow: hidden;
            min-width: 800px;
        }
        th { 
            background: #1f4068; 
            padding: 12px 8px; 
            text-align: left;
            color: #00d4ff;
            font-weight: 500;
            cursor: pointer;
            user-select: none;
            white-space: nowrap;
            position: relative;
        }
        th:hover { background: #2a5080; }
        th.sort-asc::after { content: ' ▲'; font-size: 0.7em; }
        th.sort-desc::after { content: ' ▼'; font-size: 0.7em; }
        th.no-sort { cursor: default; }
        th.no-sort:hover { background: #1f4068; }
        td { 
            padding: 10px 8px; 
            border-bottom: 1px solid #2a2a4a;
        }
        tr:hover { background: #1f4068; }
        tr.hidden { display: none; }
        
        /* Badges */
        .badge {
            display: inline-block;
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 0.8em;
            font-weight: 500;
        }
        .badge-oooi { background: #28a745; color: white; }
        .badge-weather { background: #17a2b8; color: white; }
        .badge-position { background: #007bff; color: white; }
        .badge-maintenance { background: #dc3545; color: white; }
        .badge-operational { background: #fd7e14; color: white; }
        .badge-fuel { background: #e83e8c; color: white; }
        .badge-freetext { background: #6c757d; color: white; }
        .badge-other { background: #6c757d; color: white; }
        .badge-linked { background: #ffc107; color: black; }
        
        /* Message Text */
        .message-text {
            font-family: 'Courier New', monospace;
            font-size: 0.85em;
            max-width: 400px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .message-text:hover {
            white-space: normal;
            word-break: break-all;
        }
        .timestamp { color: #888; font-size: 0.9em; }
        .empty-message { 
            text-align: center; 
            padding: 40px; 
            color: #666;
            background: #16213e;
            border-radius: 10px;
        }
        
        /* Results count */
        .results-count {
            color: #888;
            font-size: 0.9em;
            margin-bottom: 10px;
        }
        
        /* Footer */
        .footer {
            margin-top: 30px;
            color: #666;
            font-size: 0.9em;
        }
    </style>
</head>
<body>
    <h1>ACARS Messages</h1>
    
    <div class="nav">
        <a href="/">Live Aircraft</a>
        <a href="/flights">Flights</a>
        <a href="/routes">Routes</a>
        <a href="/stats">Statistics</a>
        <a href="/acars">ACARS</a>
    </div>
    
    <div class="stats-grid">
        <div class="stat-box">
            <div class="number">{{ stats.total_today }}</div>
            <div class="label">Messages Today</div>
        </div>
        <div class="stat-box">
            <div class="number">{{ stats.oooi_today }}</div>
            <div class="label">OOOI Events</div>
        </div>
        <div class="stat-box">
            <div class="number">{{ stats.weather_today }}</div>
            <div class="label">Weather Reports</div>
        </div>
        <div class="stat-box">
            <div class="number">{{ stats.unique_aircraft }}</div>
            <div class="label">Unique Aircraft</div>
        </div>
        <div class="stat-box">
            <div class="number">{{ stats.linked_adsb }}</div>
            <div class="label">Linked to ADS-B</div>
        </div>
        <div class="stat-box">
            <div class="number">{{ stats.last_hour }}</div>
            <div class="label">Last Hour</div>
        </div>
    </div>
    
    <!-- Search and Filter Controls -->
    <div class="controls">
        <div class="search-box">
            <input type="text" id="searchInput" placeholder="Search aircraft, flight, message..." onkeyup="filterTable()">
        </div>
        <div class="filter-buttons">
            <button class="filter-btn active" data-filter="all" onclick="setFilter('all')">All</button>
            <button class="filter-btn" data-filter="OOOI" onclick="setFilter('OOOI')">OOOI</button>
            <button class="filter-btn" data-filter="WEATHER" onclick="setFilter('WEATHER')">Weather</button>
            <button class="filter-btn" data-filter="POSITION" onclick="setFilter('POSITION')">Position</button>
            <button class="filter-btn" data-filter="MAINTENANCE" onclick="setFilter('MAINTENANCE')">Maintenance</button>
            <button class="filter-btn" data-filter="LINKED" onclick="setFilter('LINKED')">Linked</button>
        </div>
        <button class="refresh-btn" onclick="location.reload()">&#x21bb; Refresh</button>
    </div>
    
    <h2>Recent OOOI Events (Out/Off/On/In)</h2>
    <div class="results-count" id="oooiCount"></div>
    {% if oooi_messages %}
    <div class="table-container">
        <table id="oooiTable">
            <thead>
                <tr>
                    <th data-col="0" onclick="sortTable('oooiTable', 0)">Time</th>
                    <th data-col="1" onclick="sortTable('oooiTable', 1)">Aircraft</th>
                    <th data-col="2" onclick="sortTable('oooiTable', 2)">Flight</th>
                    <th data-col="3" onclick="sortTable('oooiTable', 3)">Event</th>
                    <th data-col="4" class="no-sort">Message</th>
                    <th data-col="5" onclick="sortTable('oooiTable', 5)">ADS-B</th>
                </tr>
            </thead>
            <tbody>
                {% for msg in oooi_messages %}
                <tr data-type="OOOI" data-linked="{{ 'yes' if msg.linked_flight_id else 'no' }}">
                    <td class="timestamp" data-sort="{{ msg.received_time.timestamp() if msg.received_time else 0 }}">{{ msg.received_time.strftime('%H:%M:%S') if msg.received_time else '' }}</td>
                    <td>{{ msg.registration or '-' }}</td>
                    <td>{{ msg.acars_flight_id or '-' }}</td>
                    <td>{{ msg.event_type }}</td>
                    <td class="message-text">{{ msg.message_text or '-' }}</td>
                    <td>{% if msg.linked_flight_id %}<span class="badge badge-linked">Linked</span>{% else %}-{% endif %}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
    {% else %}
    <div class="empty-message">No OOOI events in the last 24 hours</div>
    {% endif %}
    
    <h2>All Recent Messages (Last Hour)</h2>
    <div class="results-count" id="recentCount"></div>
    {% if recent_messages %}
    <div class="table-container">
        <table id="recentTable">
            <thead>
                <tr>
                    <th data-col="0" onclick="sortTable('recentTable', 0)">Time</th>
                    <th data-col="1" onclick="sortTable('recentTable', 1)">Aircraft</th>
                    <th data-col="2" onclick="sortTable('recentTable', 2)">Flight</th>
                    <th data-col="3" onclick="sortTable('recentTable', 3)">Type</th>
                    <th data-col="4" onclick="sortTable('recentTable', 4)">Label</th>
                    <th data-col="5" onclick="sortTable('recentTable', 5)">Freq</th>
                    <th data-col="6" class="no-sort">Message</th>
                    <th data-col="7" onclick="sortTable('recentTable', 7)">ADS-B</th>
                </tr>
            </thead>
            <tbody>
                {% for msg in recent_messages %}
                <tr data-type="{{ msg.category or 'OTHER' }}" data-linked="{{ 'yes' if msg.linked_flight_id else 'no' }}">
                    <td class="timestamp" data-sort="{{ msg.received_time.timestamp() if msg.received_time else 0 }}">{{ msg.received_time.strftime('%H:%M:%S') if msg.received_time else '' }}</td>
                    <td>{{ msg.registration or '-' }}</td>
                    <td>{{ msg.acars_flight_id or '-' }}</td>
                    <td>
                        <span class="badge badge-{{ (msg.category or 'other')|lower }}">
                            {{ msg.category or 'OTHER' }}
                        </span>
                    </td>
                    <td>{{ msg.label }}</td>
                    <td>{{ '%.3f'|format(msg.frequency) if msg.frequency else '-' }}</td>
                    <td class="message-text">{{ msg.message_text or '-' }}</td>
                    <td>{% if msg.linked_flight_id %}<span class="badge badge-linked">Linked</span>{% else %}-{% endif %}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
    {% else %}
    <div class="empty-message">No ACARS messages received in the last hour. Make sure acarsdec is running.</div>
    {% endif %}
    
    <h2>Message Types (Last 24 Hours)</h2>
    {% if categories %}
    <div class="table-container">
        <table id="categoriesTable" style="max-width: 500px; min-width: 400px;">
            <thead>
                <tr>
                    <th data-col="0" onclick="sortTable('categoriesTable', 0)">Category</th>
                    <th data-col="1" onclick="sortTable('categoriesTable', 1)">Count</th>
                    <th data-col="2" onclick="sortTable('categoriesTable', 2)">Aircraft</th>
                </tr>
            </thead>
            <tbody>
                {% for cat in categories %}
                <tr>
                    <td>
                        <span class="badge badge-{{ cat.category|lower }}">{{ cat.category }}</span>
                    </td>
                    <td data-sort="{{ cat.message_count }}">{{ cat.message_count }}</td>
                    <td data-sort="{{ cat.unique_aircraft }}">{{ cat.unique_aircraft }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
    {% endif %}
    
    <div class="footer">
        Last updated: {{ now.strftime('%Y-%m-%d %H:%M:%S') }}
    </div>
    
    <script>
        // Current filter and sort state
        let currentFilter = 'all';
        let sortStates = {};
        
        // Filter by message type
        function setFilter(filterType) {
            currentFilter = filterType;
            
            // Update button states
            document.querySelectorAll('.filter-btn').forEach(btn => {
                btn.classList.remove('active');
                if (btn.dataset.filter === filterType) {
                    btn.classList.add('active');
                }
            });
            
            filterTable();
        }
        
        // Combined search and filter
        function filterTable() {
            const searchText = document.getElementById('searchInput').value.toLowerCase();
            
            // Filter both tables
            ['oooiTable', 'recentTable'].forEach(tableId => {
                const table = document.getElementById(tableId);
                if (!table) return;
                
                const rows = table.querySelectorAll('tbody tr');
                let visibleCount = 0;
                
                rows.forEach(row => {
                    const rowType = row.dataset.type;
                    const isLinked = row.dataset.linked === 'yes';
                    const rowText = row.textContent.toLowerCase();
                    
                    // Check filter
                    let passesFilter = false;
                    if (currentFilter === 'all') {
                        passesFilter = true;
                    } else if (currentFilter === 'LINKED') {
                        passesFilter = isLinked;
                    } else {
                        passesFilter = rowType === currentFilter;
                    }
                    
                    // Check search
                    const passesSearch = searchText === '' || rowText.includes(searchText);
                    
                    // Show/hide row
                    if (passesFilter && passesSearch) {
                        row.classList.remove('hidden');
                        visibleCount++;
                    } else {
                        row.classList.add('hidden');
                    }
                });
                
                // Update count display
                const countId = tableId === 'oooiTable' ? 'oooiCount' : 'recentCount';
                const countEl = document.getElementById(countId);
                if (countEl) {
                    const totalRows = rows.length;
                    if (searchText || currentFilter !== 'all') {
                        countEl.textContent = `Showing ${visibleCount} of ${totalRows} messages`;
                    } else {
                        countEl.textContent = `${totalRows} messages`;
                    }
                }
            });
        }
        
        // Sort table by column
        function sortTable(tableId, colIndex) {
            const table = document.getElementById(tableId);
            if (!table) return;
            
            const th = table.querySelector(`th[data-col="${colIndex}"]`);
            if (th.classList.contains('no-sort')) return;
            
            const tbody = table.querySelector('tbody');
            const rows = Array.from(tbody.querySelectorAll('tr'));
            
            // Determine sort direction
            const stateKey = `${tableId}-${colIndex}`;
            const currentDir = sortStates[stateKey] || 'none';
            const newDir = currentDir === 'asc' ? 'desc' : 'asc';
            sortStates[stateKey] = newDir;
            
            // Clear all sort indicators in this table
            table.querySelectorAll('th').forEach(header => {
                header.classList.remove('sort-asc', 'sort-desc');
            });
            
            // Add sort indicator to current column
            th.classList.add(newDir === 'asc' ? 'sort-asc' : 'sort-desc');
            
            // Sort rows
            rows.sort((a, b) => {
                const aCell = a.cells[colIndex];
                const bCell = b.cells[colIndex];
                
                // Use data-sort attribute if present, otherwise use text content
                let aVal = aCell.dataset.sort !== undefined ? aCell.dataset.sort : aCell.textContent.trim();
                let bVal = bCell.dataset.sort !== undefined ? bCell.dataset.sort : bCell.textContent.trim();
                
                // Try numeric comparison
                const aNum = parseFloat(aVal);
                const bNum = parseFloat(bVal);
                
                if (!isNaN(aNum) && !isNaN(bNum)) {
                    return newDir === 'asc' ? aNum - bNum : bNum - aNum;
                }
                
                // String comparison
                aVal = aVal.toLowerCase();
                bVal = bVal.toLowerCase();
                
                if (aVal < bVal) return newDir === 'asc' ? -1 : 1;
                if (aVal > bVal) return newDir === 'asc' ? 1 : -1;
                return 0;
            });
            
            // Re-append sorted rows
            rows.forEach(row => tbody.appendChild(row));
        }
        
        // Initialize counts on page load
        document.addEventListener('DOMContentLoaded', function() {
            filterTable();
        });
        
        // Auto-refresh every 30 seconds
        setTimeout(function() {
            location.reload();
        }, 30000);
    </script>
</body>
</html>
'''


def register_acars_routes(app, get_db_connection):
    """
    Register ACARS routes with the Flask app
    
    Parameters:
        app: Flask application instance
        get_db_connection: Function that returns an Oracle database connection
    """
    
    @app.route('/acars')
    def acars_page():
        """Main ACARS messages page"""
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get statistics
        stats = {}
        try:
            # Total messages today
            cursor.execute("""
                SELECT COUNT(*) FROM acars_messages 
                WHERE received_time > TRUNC(SYSDATE)
            """)
            stats['total_today'] = cursor.fetchone()[0]
            
            # OOOI events today
            cursor.execute("""
                SELECT COUNT(*) FROM acars_messages 
                WHERE received_time > TRUNC(SYSDATE)
                  AND message_type = 'OOOI'
            """)
            stats['oooi_today'] = cursor.fetchone()[0]
            
            # Weather reports today
            cursor.execute("""
                SELECT COUNT(*) FROM acars_messages 
                WHERE received_time > TRUNC(SYSDATE)
                  AND message_type = 'WEATHER'
            """)
            stats['weather_today'] = cursor.fetchone()[0]
            
            # Unique aircraft today
            cursor.execute("""
                SELECT COUNT(DISTINCT registration) FROM acars_messages 
                WHERE received_time > TRUNC(SYSDATE)
                  AND registration IS NOT NULL
            """)
            stats['unique_aircraft'] = cursor.fetchone()[0]
            
            # Linked to ADS-B
            cursor.execute("""
                SELECT COUNT(*) FROM acars_messages 
                WHERE received_time > TRUNC(SYSDATE)
                  AND linked_flight_id IS NOT NULL
            """)
            stats['linked_adsb'] = cursor.fetchone()[0]
            
            # Last hour count
            cursor.execute("""
                SELECT COUNT(*) FROM acars_messages 
                WHERE received_time > SYSDATE - (1/24)
            """)
            stats['last_hour'] = cursor.fetchone()[0]
            
        except Exception as e:
            print(f"Stats query error: {e}")
            stats = {
                'total_today': 0, 'oooi_today': 0, 'weather_today': 0,
                'unique_aircraft': 0, 'linked_adsb': 0, 'last_hour': 0
            }
        
        # Get OOOI events (last 24 hours)
        oooi_messages = []
        try:
            cursor.execute("""
                SELECT 
                    registration,
                    flight_id as acars_flight_id,
                    message_text,
                    received_time,
                    linked_flight_id,
                    CASE label
                        WHEN 'H1' THEN 'OUT (Left Gate)'
                        WHEN 'H2' THEN 'OFF (Takeoff)'
                        WHEN 'H3' THEN 'ON (Landing)'
                        WHEN 'H4' THEN 'IN (At Gate)'
                        WHEN '12' THEN 'DEPARTURE'
                        WHEN '13' THEN 'ARRIVAL'
                        ELSE label
                    END as event_type
                FROM acars_messages
                WHERE label IN ('H1', 'H2', 'H3', 'H4', '10', '12', '13', '14')
                    AND received_time > SYSDATE - 1
                ORDER BY received_time DESC
                FETCH FIRST 100 ROWS ONLY
            """)
            cols = [c[0].lower() for c in cursor.description]
            oooi_messages = [dict(zip(cols, row)) for row in cursor.fetchall()]
        except Exception as e:
            print(f"OOOI query error: {e}")
        
        # Get recent messages (last hour, more rows for filtering)
        recent_messages = []
        try:
            cursor.execute("""
                SELECT 
                    m.received_time,
                    m.registration,
                    m.flight_id as acars_flight_id,
                    m.label,
                    m.frequency,
                    m.message_text,
                    m.message_type as category,
                    m.linked_flight_id
                FROM acars_messages m
                WHERE m.received_time > SYSDATE - (1/24)
                ORDER BY m.received_time DESC
                FETCH FIRST 200 ROWS ONLY
            """)
            cols = [c[0].lower() for c in cursor.description]
            recent_messages = [dict(zip(cols, row)) for row in cursor.fetchall()]
        except Exception as e:
            print(f"Recent messages query error: {e}")
        
        # Get category breakdown
        categories = []
        try:
            cursor.execute("""
                SELECT 
                    NVL(message_type, 'OTHER') as category,
                    COUNT(*) as message_count,
                    COUNT(DISTINCT registration) as unique_aircraft
                FROM acars_messages
                WHERE received_time > SYSDATE - 1
                GROUP BY NVL(message_type, 'OTHER')
                ORDER BY message_count DESC
            """)
            cols = [c[0].lower() for c in cursor.description]
            categories = [dict(zip(cols, row)) for row in cursor.fetchall()]
        except Exception as e:
            print(f"Categories query error: {e}")
        
        cursor.close()
        conn.close()
        
        return render_template_string(
            ACARS_HTML,
            stats=stats,
            oooi_messages=oooi_messages,
            recent_messages=recent_messages,
            categories=categories,
            now=datetime.now()
        )
    
    @app.route('/api/acars/recent')
    def api_acars_recent():
        """API endpoint for recent ACARS messages with optional filtering"""
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get query parameters
        msg_type = request.args.get('type', None)
        registration = request.args.get('registration', None)
        flight = request.args.get('flight', None)
        hours = request.args.get('hours', 1, type=float)
        limit = request.args.get('limit', 100, type=int)
        
        try:
            # Build query with filters
            query = """
                SELECT 
                    message_id,
                    received_time,
                    registration,
                    flight_id,
                    label,
                    frequency,
                    message_text,
                    message_type,
                    latitude,
                    longitude,
                    altitude,
                    linked_flight_id
                FROM acars_messages
                WHERE received_time > SYSDATE - (:hours/24)
            """
            params = {'hours': hours}
            
            if msg_type:
                query += " AND message_type = :msg_type"
                params['msg_type'] = msg_type.upper()
            
            if registration:
                query += " AND UPPER(registration) LIKE :reg"
                params['reg'] = f'%{registration.upper()}%'
            
            if flight:
                query += " AND UPPER(flight_id) LIKE :flt"
                params['flt'] = f'%{flight.upper()}%'
            
            query += " ORDER BY received_time DESC FETCH FIRST :limit ROWS ONLY"
            params['limit'] = min(limit, 500)  # Cap at 500
            
            cursor.execute(query, params)
            cols = [c[0].lower() for c in cursor.description]
            messages = []
            for row in cursor.fetchall():
                msg = dict(zip(cols, row))
                if msg['received_time']:
                    msg['received_time'] = msg['received_time'].isoformat()
                messages.append(msg)
            
            return jsonify({'messages': messages, 'count': len(messages)})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
        finally:
            cursor.close()
            conn.close()
    
    @app.route('/api/acars/stats')
    def api_acars_stats():
        """API endpoint for ACARS statistics"""
        conn = get_db_connection()
        cursor = conn.cursor()
        
        try:
            stats = {}
            
            cursor.execute("""
                SELECT 
                    COUNT(*) as total,
                    COUNT(DISTINCT registration) as unique_aircraft,
                    SUM(CASE WHEN message_type = 'OOOI' THEN 1 ELSE 0 END) as oooi_count,
                    SUM(CASE WHEN message_type = 'WEATHER' THEN 1 ELSE 0 END) as weather_count,
                    SUM(CASE WHEN message_type = 'POSITION' THEN 1 ELSE 0 END) as position_count,
                    SUM(CASE WHEN linked_flight_id IS NOT NULL THEN 1 ELSE 0 END) as linked_count
                FROM acars_messages
                WHERE received_time > TRUNC(SYSDATE)
            """)
            row = cursor.fetchone()
            stats = {
                'total': row[0],
                'unique_aircraft': row[1],
                'oooi_count': row[2],
                'weather_count': row[3],
                'position_count': row[4],
                'linked_count': row[5]
            }
            
            return jsonify(stats)
        except Exception as e:
            return jsonify({'error': str(e)}), 500
        finally:
            cursor.close()
            conn.close()
    
    @app.route('/api/acars/search')
    def api_acars_search():
        """API endpoint to search ACARS messages"""
        conn = get_db_connection()
        cursor = conn.cursor()
        
        query_text = request.args.get('q', '')
        hours = request.args.get('hours', 24, type=float)
        limit = request.args.get('limit', 100, type=int)
        
        if not query_text:
            return jsonify({'error': 'Missing search query parameter: q'}), 400
        
        try:
            cursor.execute("""
                SELECT 
                    message_id,
                    received_time,
                    registration,
                    flight_id,
                    label,
                    frequency,
                    message_text,
                    message_type,
                    linked_flight_id
                FROM acars_messages
                WHERE received_time > SYSDATE - (:hours/24)
                  AND (
                      UPPER(registration) LIKE :search
                      OR UPPER(flight_id) LIKE :search
                      OR UPPER(message_text) LIKE :search
                      OR UPPER(label) LIKE :search
                  )
                ORDER BY received_time DESC
                FETCH FIRST :limit ROWS ONLY
            """, {
                'hours': hours,
                'search': f'%{query_text.upper()}%',
                'limit': min(limit, 500)
            })
            
            cols = [c[0].lower() for c in cursor.description]
            messages = []
            for row in cursor.fetchall():
                msg = dict(zip(cols, row))
                if msg['received_time']:
                    msg['received_time'] = msg['received_time'].isoformat()
                messages.append(msg)
            
            return jsonify({
                'query': query_text,
                'messages': messages,
                'count': len(messages)
            })
        except Exception as e:
            return jsonify({'error': str(e)}), 500
        finally:
            cursor.close()
            conn.close()


# Standalone integration instructions
if __name__ == '__main__':
    print("""
================================================================================
ACARS Web Interface Integration
================================================================================

To add ACARS viewing to your existing adsb_webapp.py:

Option 1: Import and register routes
----------------------------------------
Add these lines to adsb_webapp.py:

    from acars_routes import register_acars_routes
    
    # After creating the Flask app and defining get_db_connection:
    register_acars_routes(app, get_db_connection)

Option 2: Copy routes directly
----------------------------------------
Copy the register_acars_routes function contents directly into your
adsb_webapp.py file. Make sure to also copy the ACARS_HTML template.

The ACARS page will be available at: http://localhost:5001/acars

API endpoints:
    - /api/acars/recent?type=OOOI&hours=2&limit=50
    - /api/acars/stats
    - /api/acars/search?q=AA1234&hours=24

================================================================================
""")
