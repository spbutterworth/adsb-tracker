--------------------------------------------------------------------------------
-- fix_route_views.sql
-- Version:  1.0.0   (2026-10-10)
-- Purpose:  Make the Popular Routes and Airport Traffic tables on /routes
--           show data again.
--
-- Why they were empty:
--   Both views filtered on FLIGHT_ROUTES.ACTUAL_DEPARTURE. That column is
--   only set when the position-based detector actually sees a takeoff, so:
--     - routes from adsbdb (collector v2.7.0+) never have it
--     - flights first heard already airborne never have it
--   Popular Routes also needs BOTH ends known and 2+ flights, which the old
--   detector rarely managed.
--   These versions date each route by FLIGHTS.FIRST_CONTACT (always set)
--   and keep the same column order, so the web app needs no changes.
--
-- Run as ADSB_USER.
--------------------------------------------------------------------------------

-- 1. (Optional) Save the current definitions first, in case you want them back
SET LONG 20000 PAGESIZE 0 LINESIZE 200
SELECT DBMS_METADATA.GET_DDL('VIEW', 'V_POPULAR_ROUTES_ENHANCED') FROM dual;
SELECT DBMS_METADATA.GET_DDL('VIEW', 'V_AIRPORT_TRAFFIC') FROM dual;
SET PAGESIZE 50

-- 2. What route data exists (before the fix)
SELECT COUNT(*)                                                  AS total_routes,
       COUNT(fr.origin_airport_id)                               AS with_origin,
       COUNT(fr.dest_airport_id)                                 AS with_dest,
       COUNT(CASE WHEN fr.origin_airport_id IS NOT NULL
                   AND fr.dest_airport_id   IS NOT NULL THEN 1 END) AS with_both,
       COUNT(fr.actual_departure)                                AS with_departure_time
FROM   flight_routes fr;

-- 3. Popular routes (last 30 days) - same columns as before
CREATE OR REPLACE VIEW v_popular_routes_enhanced AS
SELECT
    origin.icao_code || '-' || dest.icao_code   AS route_code,
    origin.icao_code                            AS origin_icao,
    origin.iata_code                            AS origin_iata,
    origin.airport_name                         AS origin_name,
    origin.city                                 AS origin_city,
    dest.icao_code                              AS dest_icao,
    dest.iata_code                              AS dest_iata,
    dest.airport_name                           AS dest_name,
    dest.city                                   AS dest_city,
    COUNT(DISTINCT fr.flight_id)                AS flight_count,
    COUNT(DISTINCT f.icao_address)              AS aircraft_count,
    ROUND(AVG(fr.route_distance_nm), 0)         AS avg_distance_nm,
    MIN(f.first_contact)                        AS first_seen,
    MAX(f.first_contact)                        AS last_seen
FROM   flight_routes fr
JOIN   flights  f      ON f.flight_id       = fr.flight_id
JOIN   airports origin ON origin.airport_id = fr.origin_airport_id
JOIN   airports dest   ON dest.airport_id   = fr.dest_airport_id
WHERE  f.first_contact > CURRENT_DATE - 30
GROUP BY
    origin.icao_code, origin.iata_code, origin.airport_name, origin.city,
    dest.icao_code,   dest.iata_code,   dest.airport_name,   dest.city
HAVING COUNT(DISTINCT fr.flight_id) >= 2
ORDER BY flight_count DESC, avg_distance_nm DESC;

-- 4. Airport traffic (last 7 days) - same columns as before
CREATE OR REPLACE VIEW v_airport_traffic AS
WITH recent AS (
    SELECT fr.flight_id, fr.origin_airport_id, fr.dest_airport_id
    FROM   flight_routes fr
    JOIN   flights f ON f.flight_id = fr.flight_id
    WHERE  f.first_contact > CURRENT_DATE - 7
),
movements AS (
    SELECT origin_airport_id AS airport_id, flight_id, 'D' AS dir
    FROM   recent WHERE origin_airport_id IS NOT NULL
    UNION ALL
    SELECT dest_airport_id, flight_id, 'A'
    FROM   recent WHERE dest_airport_id IS NOT NULL
)
SELECT
    ap.airport_id,
    ap.icao_code,
    ap.iata_code,
    ap.airport_name,
    ap.city,
    ap.country,
    COUNT(DISTINCT CASE WHEN m.dir = 'D' THEN m.flight_id END) AS departures_7days,
    COUNT(DISTINCT CASE WHEN m.dir = 'A' THEN m.flight_id END) AS arrivals_7days,
    COUNT(DISTINCT m.flight_id)                                AS total_traffic_7days
FROM   movements m
JOIN   airports  ap ON ap.airport_id = m.airport_id
GROUP BY ap.airport_id, ap.icao_code, ap.iata_code, ap.airport_name, ap.city, ap.country
ORDER BY total_traffic_7days DESC;

-- 5. Check
SELECT COUNT(*) AS popular_routes  FROM v_popular_routes_enhanced;
SELECT COUNT(*) AS airports_listed FROM v_airport_traffic;
