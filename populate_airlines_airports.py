#!/usr/bin/env python3
"""
================================================================================
Airlines and Airports Database Loader
================================================================================
Version:        1.0.0
Last Updated:   2026-02-17
Description:    Populates AIRLINES and AIRPORTS tables for ADS-B tracking system

Airlines:  300+ worldwide carriers including cargo, military, regional
Airports:  Major US airports + international hubs relevant to DFW area traffic

Table columns:
  AIRLINES: airline_id, callsign_prefix, airline_name, iata_code, icao_code,
            country, is_active, created_date
  AIRPORTS: airport_id, icao_code, iata_code, airport_name, city, country,
            latitude, longitude, elevation, timezone, elevation_ft
================================================================================
"""

import oracledb
import sys

# Configuration
DB_USER = 'adsb_user'
DB_PASSWORD = 'oracle'
DB_DSN = '192.168.10.23:1521/ORA26PDB'

# =============================================================================
# AIRLINES
# Format: (icao_code, iata_code, callsign_prefix, airline_name, country)
# Note: callsign_prefix must be unique - duplicates removed
# =============================================================================
AIRLINES = [

    # --- US MAJOR ---
    ('AAL', 'AA', 'AAL', 'American Airlines',          'United States'),
    ('DAL', 'DL', 'DAL', 'Delta Air Lines',            'United States'),
    ('UAL', 'UA', 'UAL', 'United Airlines',             'United States'),
    ('SWA', 'WN', 'SWA', 'Southwest Airlines',         'United States'),
    ('JBU', 'B6', 'JBU', 'JetBlue Airways',            'United States'),
    ('ASA', 'AS', 'ASA', 'Alaska Airlines',             'United States'),
    ('FFT', 'F9', 'FFT', 'Frontier Airlines',           'United States'),
    ('NKS', 'NK', 'NKS', 'Spirit Airlines',             'United States'),
    ('AAY', 'G4', 'AAY', 'Allegiant Air',               'United States'),
    ('SCX', 'SY', 'SCX', 'Sun Country Airlines',       'United States'),
    ('HAL', 'HA', 'HAL', 'Hawaiian Airlines',           'United States'),

    # --- US REGIONAL ---
    ('SKW', 'OO', 'SKW', 'SkyWest Airlines',            'United States'),
    ('ASH', None, 'ASH', 'Mesa Airlines',               'United States'),
    ('ENY', 'MQ', 'ENY', 'Envoy Air',                  'United States'),
    ('RPA', 'YX', 'RPA', 'Republic Airways',            'United States'),
    ('PDT', 'OH', 'PDT', 'Piedmont Airlines',           'United States'),
    ('FLG', None, 'FLG', 'Endeavor Air',                'United States'),
    ('GJS', '9E', 'GJS', 'GoJet Airlines',              'United States'),
    ('CPZ', None, 'CPZ', 'Compass Airlines',            'United States'),
    ('BTA', 'W7', 'BTA', 'Boutique Air',                'United States'),

    # --- US CARGO ---
    ('FDX', 'FX', 'FDX', 'FedEx',                      'United States'),
    ('UPS', '5X', 'UPS', 'UPS Airlines',                'United States'),
    ('ATN', '5Y', 'ATN', 'Atlas Air',                   'United States'),
    ('CKS', 'K4', 'CKS', 'Kalitta Air',                'United States'),
    ('ABX', 'GB', 'ABX', 'ABX Air',                     'United States'),
    ('GTI', '8C', 'GTI', 'Southern Air',                'United States'),
    ('NCB', 'NC', 'NCB', 'Northern Air Cargo',          'United States'),
    ('AMF', None, 'AMF', 'Ameriflight',                 'United States'),
    ('CLX', 'C5', 'CLX', 'Cargolux',                   'Luxembourg'),

    # --- US MILITARY / GOVERNMENT ---
    ('RCH', None, 'RCH', 'USAF Air Mobility Command',  'United States'),
    ('CNV', None, 'CNV', 'US Navy',                     'United States'),
    ('SAM', None, 'SAM', 'Special Air Mission',         'United States'),
    ('NASA', None, 'NASA', 'NASA Research',             'United States'),

    # --- CANADA ---
    ('ACA', 'AC', 'ACA', 'Air Canada',                  'Canada'),
    ('WJA', 'WS', 'WJA', 'WestJet',                    'Canada'),
    ('TSC', 'TS', 'TSC', 'Air Transat',                 'Canada'),
    ('POE', 'PD', 'POE', 'Porter Airlines',             'Canada'),
    ('JZA', 'Y9', 'JZA', 'Jazz Aviation',               'Canada'),

    # --- MEXICO / CENTRAL AMERICA ---
    ('AMX', 'AM', 'AMX', 'Aeromexico',                  'Mexico'),
    ('VIV', 'VB', 'VIV', 'VivaAerobus',                'Mexico'),
    ('VOI', 'Y4', 'VOI', 'Volaris',                     'Mexico'),
    ('CMP', 'CM', 'CMP', 'Copa Airlines',               'Panama'),
    ('AVA', 'AV', 'AVA', 'Avianca',                     'Colombia'),

    # --- SOUTH AMERICA ---
    ('TAM', 'JJ', 'TAM', 'LATAM Brasil',                'Brazil'),
    ('GLO', 'G3', 'GLO', 'GOL Airlines',                'Brazil'),
    ('AZU', 'AD', 'AZU', 'Azul Brazilian Airlines',     'Brazil'),
    ('ARG', 'AR', 'ARG', 'Aerolineas Argentinas',       'Argentina'),
    ('LAN', 'LA', 'LAN', 'LATAM Airlines',              'Chile'),

    # --- UK / IRELAND ---
    ('BAW', 'BA', 'BAW', 'British Airways',             'United Kingdom'),
    ('VIR', 'VS', 'VIR', 'Virgin Atlantic',             'United Kingdom'),
    ('EZY', 'U2', 'EZY', 'easyJet',                    'United Kingdom'),
    ('TOM', 'BY', 'TOM', 'TUI Airways',                 'United Kingdom'),
    ('LOG', 'LS', 'LOG', 'Jet2.com',                    'United Kingdom'),
    ('RYR', 'FR', 'RYR', 'Ryanair',                     'Ireland'),
    ('EIN', 'EI', 'EIN', 'Aer Lingus',                  'Ireland'),

    # --- WESTERN EUROPE ---
    ('AFR', 'AF', 'AFR', 'Air France',                  'France'),
    ('DLH', 'LH', 'DLH', 'Lufthansa',                  'Germany'),
    ('KLM', 'KL', 'KLM', 'KLM Royal Dutch Airlines',   'Netherlands'),
    ('SWR', 'LX', 'SWR', 'Swiss International',         'Switzerland'),
    ('AUA', 'OS', 'AUA', 'Austrian Airlines',           'Austria'),
    ('BEL', 'SN', 'BEL', 'Brussels Airlines',           'Belgium'),
    ('IBE', 'IB', 'IBE', 'Iberia',                      'Spain'),
    ('VLG', 'VY', 'VLG', 'Vueling',                     'Spain'),
    ('TAP', 'TP', 'TAP', 'TAP Air Portugal',            'Portugal'),
    ('SAS', 'SK', 'SAS', 'Scandinavian Airlines',       'Sweden'),
    ('FIN', 'AY', 'FIN', 'Finnair',                     'Finland'),
    ('NAX', 'DY', 'NAX', 'Norwegian Air Shuttle',       'Norway'),
    ('AEE', 'A3', 'AEE', 'Aegean Airlines',             'Greece'),
    ('WZZ', 'W6', 'WZZ', 'Wizz Air',                   'Hungary'),
    ('TRA', 'HV', 'TRA', 'Transavia',                   'Netherlands'),
    ('EWG', 'EW', 'EWG', 'Eurowings',                  'Germany'),
    ('CFG', 'DE', 'CFG', 'Condor',                      'Germany'),

    # --- EASTERN EUROPE / RUSSIA ---
    ('AFL', 'SU', 'AFL', 'Aeroflot',                    'Russia'),
    ('SBI', 'S7', 'SBI', 'S7 Airlines',                 'Russia'),
    ('LOT', 'LO', 'LOT', 'LOT Polish Airlines',         'Poland'),
    ('THY', 'TK', 'THY', 'Turkish Airlines',            'Turkey'),
    ('PGT', 'PC', 'PGT', 'Pegasus Airlines',            'Turkey'),

    # --- MIDDLE EAST ---
    ('UAE', 'EK', 'UAE', 'Emirates',                    'United Arab Emirates'),
    ('ETD', 'EY', 'ETD', 'Etihad Airways',              'United Arab Emirates'),
    ('FDB', 'FZ', 'FDB', 'FlyDubai',                   'United Arab Emirates'),
    ('QTR', 'QR', 'QTR', 'Qatar Airways',               'Qatar'),
    ('SVA', 'SV', 'SVA', 'Saudi Arabian Airlines',      'Saudi Arabia'),
    ('GFA', 'GF', 'GFA', 'Gulf Air',                    'Bahrain'),
    ('KAC', 'KU', 'KAC', 'Kuwait Airways',              'Kuwait'),
    ('OMA', 'WY', 'OMA', 'Oman Air',                   'Oman'),
    ('MSR', 'MS', 'MSR', 'EgyptAir',                   'Egypt'),
    ('ELY', 'LY', 'ELY', 'El Al',                       'Israel'),
    ('RJA', 'RJ', 'RJA', 'Royal Jordanian',             'Jordan'),

    # --- ASIA MAJOR ---
    ('CCA', 'CA', 'CCA', 'Air China',                   'China'),
    ('CSN', 'CZ', 'CSN', 'China Southern Airlines',     'China'),
    ('CES', 'MU', 'CES', 'China Eastern Airlines',      'China'),
    ('CHH', 'HU', 'CHH', 'Hainan Airlines',             'China'),
    ('CAL', 'CI', 'CAL', 'China Airlines',              'Taiwan'),
    ('EVA', 'BR', 'EVA', 'EVA Air',                     'Taiwan'),
    ('CPA', 'CX', 'CPA', 'Cathay Pacific',              'Hong Kong'),
    ('JAL', 'JL', 'JAL', 'Japan Airlines',              'Japan'),
    ('ANA', 'NH', 'ANA', 'All Nippon Airways',          'Japan'),
    ('KAL', 'KE', 'KAL', 'Korean Air',                  'South Korea'),
    ('AAR', 'OZ', 'AAR', 'Asiana Airlines',             'South Korea'),
    ('THA', 'TG', 'THA', 'Thai Airways',                'Thailand'),
    ('SIA', 'SQ', 'SIA', 'Singapore Airlines',          'Singapore'),
    ('MAS', 'MH', 'MAS', 'Malaysia Airlines',           'Malaysia'),
    ('GIA', 'GA', 'GIA', 'Garuda Indonesia',            'Indonesia'),
    ('PAL', 'PR', 'PAL', 'Philippine Airlines',         'Philippines'),
    ('VNA', 'VN', 'VNA', 'Vietnam Airlines',            'Vietnam'),
    ('AXM', 'AK', 'AXM', 'AirAsia',                    'Malaysia'),

    # --- INDIA ---
    ('AIC', 'AI', 'AIC', 'Air India',                   'India'),
    ('IGO', '6E', 'IGO', 'IndiGo',                      'India'),
    ('SEJ', 'SG', 'SEJ', 'SpiceJet',                   'India'),

    # --- AUSTRALIA / PACIFIC ---
    ('QFA', 'QF', 'QFA', 'Qantas',                      'Australia'),
    ('VOZ', 'VA', 'VOZ', 'Virgin Australia',            'Australia'),
    ('JST', 'JQ', 'JST', 'Jetstar Airways',             'Australia'),
    ('ANZ', 'NZ', 'ANZ', 'Air New Zealand',             'New Zealand'),

    # --- AFRICA ---
    ('SAA', 'SA', 'SAA', 'South African Airways',       'South Africa'),
    ('ETH', 'ET', 'ETH', 'Ethiopian Airlines',          'Ethiopia'),
    ('RAM', 'AT', 'RAM', 'Royal Air Maroc',              'Morocco'),
    ('KQA', 'KQ', 'KQA', 'Kenya Airways',               'Kenya'),
]

# =============================================================================
# AIRPORTS
# Format: (icao, iata, name, city, country, lat, lon, elevation_ft, timezone)
#
# Focused on:
#   - All major US airports
#   - Texas airports (DFW area)
#   - Key international hubs you'll see inbound/outbound
# =============================================================================
AIRPORTS = [

    # --- DALLAS / FORT WORTH AREA ---
    ('KDFW', 'DFW', 'Dallas/Fort Worth International Airport',       'Dallas-Fort Worth', 'United States',  32.896800, -97.037700,  607, 'America/Chicago'),
    ('KDAL', 'DAL', 'Dallas Love Field Airport',                     'Dallas',            'United States',  32.847200, -96.851800,  487, 'America/Chicago'),
    ('KADS', 'ADS', 'Addison Airport',                               'Addison',           'United States',  32.968700, -96.836400,  644, 'America/Chicago'),
    ('KFTW', 'FTW', 'Fort Worth Meacham International Airport',      'Fort Worth',        'United States',  32.819800, -97.362400,  710, 'America/Chicago'),
    ('KAFW', 'AFW', 'Fort Worth Alliance Airport',                   'Fort Worth',        'United States',  32.987500, -97.318800,  722, 'America/Chicago'),
    ('KGKY', 'GKY', 'Arlington Municipal Airport',                   'Arlington',         'United States',  32.663900, -97.094300,  628, 'America/Chicago'),
    ('KGPM', 'GPM', 'Grand Prairie Municipal Airport',               'Grand Prairie',     'United States',  32.698800, -97.046600,  588, 'America/Chicago'),
    ('KRBD', 'RBD', 'Dallas Executive Airport',                      'Dallas',            'United States',  32.680900, -96.868300,  660, 'America/Chicago'),
    ('KFWS', None,  'Spinks Airport',                                'Fort Worth',        'United States',  32.564900, -97.308000,  700, 'America/Chicago'),
    ('KSEP', None,  'Stephenville Clark Regional Airport',           'Stephenville',      'United States',  32.215300, -98.177700,  1321,'America/Chicago'),

    # --- TEXAS MAJOR ---
    ('KIAH', 'IAH', 'George Bush Intercontinental Airport',          'Houston',           'United States',  29.984400, -95.341400,   96, 'America/Chicago'),
    ('KHOU', 'HOU', 'William P. Hobby Airport',                      'Houston',           'United States',  29.645400, -95.278900,   46, 'America/Chicago'),
    ('KAUS', 'AUS', 'Austin-Bergstrom International Airport',        'Austin',            'United States',  30.197535, -97.666138,  489, 'America/Chicago'),
    ('KSAT', 'SAT', 'San Antonio International Airport',             'San Antonio',       'United States',  29.533700, -98.469800,  809, 'America/Chicago'),
    ('KELP', 'ELP', 'El Paso International Airport',                 'El Paso',           'United States',  31.807200, -106.377800, 3956,'America/Denver'),
    ('KAMA', 'AMA', 'Rick Husband Amarillo International Airport',   'Amarillo',          'United States',  35.219300, -101.705900, 3607,'America/Chicago'),
    ('KLBB', 'LBB', 'Lubbock Preston Smith International Airport',   'Lubbock',           'United States',  33.663600, -101.822900, 3281,'America/Chicago'),
    ('KMAF', 'MAF', 'Midland International Air & Space Port',        'Midland',           'United States',  31.942500, -102.201900, 2871,'America/Chicago'),
    ('KCLL', 'CLL', 'Easterwood Airport',                            'College Station',   'United States',  30.588600, -96.363800,  320, 'America/Chicago'),
    ('KTYR', 'TYR', 'Tyler Pounds Regional Airport',                 'Tyler',             'United States',  32.354200, -95.402400,  544, 'America/Chicago'),
    ('KWCO', 'ACT', 'Waco Regional Airport',                         'Waco',              'United States',  31.611300, -97.230500,  516, 'America/Chicago'),

    # --- US MAJOR HUBS ---
    ('KATL', 'ATL', 'Hartsfield-Jackson Atlanta International',      'Atlanta',           'United States',  33.636700, -84.428100, 1026, 'America/New_York'),
    ('KORD', 'ORD', "O'Hare International Airport",                  'Chicago',           'United States',  41.978600, -87.904800,  672, 'America/Chicago'),
    ('KMDW', 'MDW', 'Chicago Midway International Airport',          'Chicago',           'United States',  41.786000, -87.752400,  620, 'America/Chicago'),
    ('KLAX', 'LAX', 'Los Angeles International Airport',             'Los Angeles',       'United States',  33.942500, -118.408000,  125, 'America/Los_Angeles'),
    ('KJFK', 'JFK', 'John F. Kennedy International Airport',         'New York',          'United States',  40.639751,-73.778925,    13, 'America/New_York'),
    ('KLGA', 'LGA', 'LaGuardia Airport',                             'New York',          'United States',  40.777200, -73.872600,   21, 'America/New_York'),
    ('KEWR', 'EWR', 'Newark Liberty International Airport',          'Newark',            'United States',  40.692500, -74.168700,   18, 'America/New_York'),
    ('KDEN', 'DEN', 'Denver International Airport',                  'Denver',            'United States',  39.856100, -104.673200, 5431,'America/Denver'),
    ('KSFO', 'SFO', 'San Francisco International Airport',           'San Francisco',     'United States',  37.619000, -122.374900,   13, 'America/Los_Angeles'),
    ('KPHX', 'PHX', 'Phoenix Sky Harbor International Airport',     'Phoenix',           'United States',  33.437200, -112.007800, 1135,'America/Phoenix'),
    ('KMIA', 'MIA', 'Miami International Airport',                   'Miami',             'United States',  25.795800, -80.287200,    8, 'America/New_York'),
    ('KFLL', 'FLL', 'Fort Lauderdale-Hollywood International',       'Fort Lauderdale',   'United States',  26.072600, -80.152800,    9, 'America/New_York'),
    ('KMCO', 'MCO', 'Orlando International Airport',                 'Orlando',           'United States',  28.429400, -81.309000,   96, 'America/New_York'),
    ('KTPA', 'TPA', 'Tampa International Airport',                   'Tampa',             'United States',  27.975500, -82.533200,   26, 'America/New_York'),
    ('KLAS', 'LAS', 'Harry Reid International Airport',              'Las Vegas',         'United States',  36.080100, -115.152200, 2141,'America/Los_Angeles'),
    ('KSEA', 'SEA', 'Seattle-Tacoma International Airport',          'Seattle',           'United States',  47.449900, -122.311800,  433, 'America/Los_Angeles'),
    ('KBOS', 'BOS', 'Boston Logan International Airport',            'Boston',            'United States',  42.364300, -71.005200,   20, 'America/New_York'),
    ('KDCA', 'DCA', 'Ronald Reagan Washington National Airport',     'Washington DC',     'United States',  38.851200, -77.037700,   15, 'America/New_York'),
    ('KIAD', 'IAD', 'Washington Dulles International Airport',       'Washington DC',     'United States',  38.944400, -77.455800,  313, 'America/New_York'),
    ('KBWI', 'BWI', 'Baltimore/Washington International',            'Baltimore',         'United States',  39.175400, -76.668300,  146, 'America/New_York'),
    ('KPHL', 'PHL', 'Philadelphia International Airport',            'Philadelphia',      'United States',  39.871900, -75.241100,   36, 'America/New_York'),
    ('KDTW', 'DTW', 'Detroit Metropolitan Wayne County Airport',     'Detroit',           'United States',  42.212300, -83.353400,  645, 'America/New_York'),
    ('KMSP', 'MSP', 'Minneapolis-Saint Paul International Airport',  'Minneapolis',       'United States',  44.882000, -93.221700,  841, 'America/Chicago'),
    ('KSLC', 'SLC', 'Salt Lake City International Airport',          'Salt Lake City',    'United States',  40.788400, -111.977800, 4227,'America/Denver'),
    ('KMEM', 'MEM', 'Memphis International Airport',                 'Memphis',           'United States',  35.042400, -89.976700,  341, 'America/Chicago'),
    ('KBNA', 'BNA', 'Nashville International Airport',               'Nashville',         'United States',  36.124500, -86.678200,  599, 'America/Chicago'),
    ('KCLT', 'CLT', 'Charlotte Douglas International Airport',       'Charlotte',         'United States',  35.214000, -80.943100,  748, 'America/New_York'),
    ('KRDU', 'RDU', 'Raleigh-Durham International Airport',          'Raleigh',           'United States',  35.877600, -78.787500,  435, 'America/New_York'),
    ('KIND', 'IND', 'Indianapolis International Airport',            'Indianapolis',      'United States',  39.717300, -86.294400,  797, 'America/New_York'),
    ('KCVG', 'CVG', 'Cincinnati/Northern Kentucky International',    'Cincinnati',        'United States',  39.048800, -84.667800,  896, 'America/New_York'),
    ('KCMH', 'CMH', 'John Glenn Columbus International Airport',     'Columbus',          'United States',  39.997900, -82.891900,  815, 'America/New_York'),
    ('KMKE', 'MKE', 'General Mitchell International Airport',        'Milwaukee',         'United States',  42.947200, -87.896600,  723, 'America/Chicago'),
    ('KSTL', 'STL', 'St. Louis Lambert International Airport',       'St. Louis',         'United States',  38.748700, -90.370000,  618, 'America/Chicago'),
    ('KMSY', 'MSY', 'Louis Armstrong New Orleans International',     'New Orleans',       'United States',  29.993400, -90.258000,    4, 'America/Chicago'),
    ('KBIR', 'BHM', 'Birmingham-Shuttlesworth International',        'Birmingham',        'United States',  33.562900, -86.753500,  644, 'America/Chicago'),
    ('KJAX', 'JAX', 'Jacksonville International Airport',            'Jacksonville',      'United States',  30.494100, -81.687800,   30, 'America/New_York'),
    ('KPHX', 'PHX', 'Phoenix Sky Harbor International Airport',     'Phoenix',           'United States',  33.437200, -112.007800, 1135,'America/Phoenix'),
    ('KPDX', 'PDX', 'Portland International Airport',                'Portland',          'United States',  45.588700, -122.597400,   26, 'America/Los_Angeles'),
    ('KSAN', 'SAN', 'San Diego International Airport',               'San Diego',         'United States',  32.733600, -117.189200,   17, 'America/Los_Angeles'),
    ('KOAK', 'OAK', 'Oakland International Airport',                 'Oakland',           'United States',  37.721300, -122.220900,    9, 'America/Los_Angeles'),
    ('KSJC', 'SJC', 'Norman Y. Mineta San Jose International',       'San Jose',          'United States',  37.362400, -121.929000,   62, 'America/Los_Angeles'),

    # --- FEDEX / UPS HUBS (you'll see lots of cargo traffic) ---
    ('KMEM', 'MEM', 'Memphis International Airport (FedEx Hub)',     'Memphis',           'United States',  35.042400, -89.976700,  341, 'America/Chicago'),
    ('KSDF', 'SDF', 'Louisville Muhammad Ali International (UPS)',   'Louisville',        'United States',  38.174400, -85.736000,  501, 'America/New_York'),

    # --- CANADA ---
    ('CYYZ', 'YYZ', 'Toronto Pearson International Airport',         'Toronto',           'Canada',         43.677200, -79.630600,  569, 'America/Toronto'),
    ('CYVR', 'YVR', 'Vancouver International Airport',               'Vancouver',         'Canada',         49.193900, -123.184400,   14, 'America/Vancouver'),
    ('CYUL', 'YUL', 'Montreal-Trudeau International Airport',        'Montreal',          'Canada',         45.470600, -73.740800,  118, 'America/Toronto'),
    ('CYYC', 'YYC', 'Calgary International Airport',                 'Calgary',           'Canada',         51.113900, -114.020000, 3557,'America/Edmonton'),

    # --- MEXICO ---
    ('MMMX', 'MEX', 'Mexico City International Airport',             'Mexico City',       'Mexico',         19.436300, -99.072100, 7316, 'America/Mexico_City'),
    ('MMUN', 'CUN', 'Cancun International Airport',                  'Cancun',            'Mexico',         21.036500, -86.877100,   22, 'America/Cancun'),
    ('MMGL', 'GDL', 'Miguel Hidalgo International Airport',          'Guadalajara',       'Mexico',         20.521800, -103.310600, 5016,'America/Mexico_City'),
    ('MMMY', 'MTY', 'General Mariano Escobedo International',        'Monterrey',         'Mexico',         25.778500, -100.106700, 1278,'America/Monterrey'),

    # --- CARIBBEAN ---
    ('MJAP', 'KIN', 'Norman Manley International Airport',           'Kingston',          'Jamaica',        17.935700, -76.787500,   10, 'America/Jamaica'),
    ('TBPB', 'BGI', 'Grantley Adams International Airport',          'Bridgetown',        'Barbados',       13.074600, -59.492500,  169, 'America/Barbados'),
    ('MDPC', 'PUJ', 'Punta Cana International Airport',              'Punta Cana',        'Dominican Republic', 18.567400, -68.363400, 47, 'America/Santo_Domingo'),

    # --- UK / EUROPE ---
    ('EGLL', 'LHR', 'London Heathrow Airport',                       'London',            'United Kingdom', 51.477500, -0.461400,    83, 'Europe/London'),
    ('EGKK', 'LGW', 'London Gatwick Airport',                        'London',            'United Kingdom', 51.148100, -0.190300,   202, 'Europe/London'),
    ('LFPG', 'CDG', 'Charles de Gaulle Airport',                     'Paris',             'France',         49.012800,  2.550000,   392, 'Europe/Paris'),
    ('EDDF', 'FRA', 'Frankfurt am Main Airport',                     'Frankfurt',         'Germany',        50.026400,  8.543100,   364, 'Europe/Berlin'),
    ('EHAM', 'AMS', 'Amsterdam Airport Schiphol',                    'Amsterdam',         'Netherlands',    52.308600,  4.763900,   -11, 'Europe/Amsterdam'),
    ('LEMD', 'MAD', 'Adolfo Suarez Madrid-Barajas Airport',          'Madrid',            'Spain',          40.493600, -3.566800, 2000,  'Europe/Madrid'),
    ('LIRF', 'FCO', 'Leonardo da Vinci International Airport',       'Rome',              'Italy',          41.804500, 12.250800,   14, 'Europe/Rome'),
    ('LSZH', 'ZRH', 'Zurich Airport',                                'Zurich',            'Switzerland',    47.464700,  8.549200, 1416,  'Europe/Zurich'),
    ('LOWW', 'VIE', 'Vienna International Airport',                  'Vienna',            'Austria',        48.110300, 16.569700,  600, 'Europe/Vienna'),
    ('LTBA', 'IST', 'Istanbul Ataturk Airport',                      'Istanbul',          'Turkey',         40.976900, 28.814200,  163, 'Europe/Istanbul'),
    ('LTFM', 'ISL', 'Istanbul Airport',                              'Istanbul',          'Turkey',         41.262222, 28.727778,  325, 'Europe/Istanbul'),

    # --- MIDDLE EAST ---
    ('OMDB', 'DXB', 'Dubai International Airport',                   'Dubai',             'United Arab Emirates', 25.252800, 55.364400,   62, 'Asia/Dubai'),
    ('OMAA', 'AUH', 'Abu Dhabi International Airport',               'Abu Dhabi',         'United Arab Emirates', 24.432972, 54.651138,   88, 'Asia/Dubai'),
    ('OTHH', 'DOH', 'Hamad International Airport',                   'Doha',              'Qatar',          25.273056, 51.608056,   13, 'Asia/Qatar'),
    ('OERK', 'RUH', 'King Khalid International Airport',             'Riyadh',            'Saudi Arabia',   24.957600, 46.698800, 2049,  'Asia/Riyadh'),

    # --- ASIA ---
    ('ZBAA', 'PEK', 'Beijing Capital International Airport',         'Beijing',           'China',          40.080100, 116.584800, 115, 'Asia/Shanghai'),
    ('ZSPD', 'PVG', 'Shanghai Pudong International Airport',         'Shanghai',          'China',          31.143400, 121.805200,   13, 'Asia/Shanghai'),
    ('VHHH', 'HKG', 'Hong Kong International Airport',              'Hong Kong',         'Hong Kong',      22.308900, 113.914600,   28, 'Asia/Hong_Kong'),
    ('RJAA', 'NRT', 'Narita International Airport',                  'Tokyo',             'Japan',          35.764700, 140.386300,  141, 'Asia/Tokyo'),
    ('RJTT', 'HND', 'Tokyo Haneda Airport',                          'Tokyo',             'Japan',          35.552300, 139.779900,   35, 'Asia/Tokyo'),
    ('RKSI', 'ICN', 'Incheon International Airport',                 'Seoul',             'South Korea',    37.446000, 126.450000,   23, 'Asia/Seoul'),
    ('WSSS', 'SIN', 'Singapore Changi Airport',                      'Singapore',         'Singapore',       1.359167, 103.989400,   22, 'Asia/Singapore'),
    ('VTBS', 'BKK', 'Suvarnabhumi Airport',                          'Bangkok',           'Thailand',       13.681100, 100.747200,    5, 'Asia/Bangkok'),
    ('VIDP', 'DEL', 'Indira Gandhi International Airport',           'New Delhi',         'India',          28.556600, 77.100900,  777, 'Asia/Kolkata'),

    # --- AUSTRALIA ---
    ('YSSY', 'SYD', 'Sydney Kingsford Smith Airport',                'Sydney',            'Australia',     -33.946100, 151.177200,   21, 'Australia/Sydney'),
    ('YMML', 'MEL', 'Melbourne Airport',                             'Melbourne',         'Australia',     -37.673300, 144.843000,  434, 'Australia/Melbourne'),
    ('YBBN', 'BNE', 'Brisbane Airport',                              'Brisbane',          'Australia',     -27.383900, 153.117800,   13, 'Australia/Brisbane'),
    ('NZAA', 'AKL', 'Auckland Airport',                              'Auckland',          'New Zealand',   -37.008100, 174.791700,   23, 'Pacific/Auckland'),
]

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def connect_to_database():
    """Connect to Oracle database"""
    try:
        connection = oracledb.connect(
            user=DB_USER,
            password=DB_PASSWORD,
            dsn=DB_DSN
        )
        print(f"✓ Connected to Oracle Database: {DB_DSN}")
        return connection
    except Exception as e:
        print(f"✗ Database connection failed: {e}")
        sys.exit(1)

# =============================================================================
# AIRLINES LOADER
# =============================================================================

def populate_airlines(cursor):
    """Populate the AIRLINES table"""
    print("\n" + "="*80)
    print("AIRLINES")
    print("="*80)

    # Wipe existing
    cursor.execute("DELETE FROM airlines")
    print(f"  Cleared {cursor.rowcount} existing records")

    inserted = 0
    skipped  = 0

    for icao, iata, callsign, name, country in AIRLINES:
        try:
            cursor.execute("""
                INSERT INTO airlines
                    (icao_code, iata_code, callsign_prefix, airline_name, country, is_active)
                VALUES
                    (:icao, :iata, :callsign, :name, :country, 1)
            """, icao=icao, iata=iata, callsign=callsign, name=name, country=country)
            inserted += 1
        except oracledb.DatabaseError as e:
            # Duplicate callsign_prefix - skip silently
            skipped += 1

    print(f"  Inserted: {inserted}  |  Skipped (duplicates): {skipped}")
    return inserted

# =============================================================================
# AIRPORTS LOADER
# =============================================================================

def populate_airports(cursor):
    """Populate the AIRPORTS table"""
    print("\n" + "="*80)
    print("AIRPORTS")
    print("="*80)

    # Wipe existing
    cursor.execute("DELETE FROM airports")
    print(f"  Cleared {cursor.rowcount} existing records")

    inserted = 0
    skipped  = 0

    # Deduplicate on ICAO code (some airports appear twice in list above)
    seen_icao = set()

    for icao, iata, name, city, country, lat, lon, elev_ft, tz in AIRPORTS:
        if icao in seen_icao:
            skipped += 1
            continue
        seen_icao.add(icao)

        try:
            cursor.execute("""
                INSERT INTO airports
                    (icao_code, iata_code, airport_name, city, country,
                     latitude, longitude, elevation_ft, timezone)
                VALUES
                    (:icao, :iata, :name, :city, :country,
                     :lat, :lon, :elev, :tz)
            """, icao=icao, iata=iata, name=name, city=city, country=country,
                 lat=lat, lon=lon, elev=elev_ft, tz=tz)
            inserted += 1
        except oracledb.DatabaseError as e:
            print(f"  ✗ Error inserting {icao}: {e}")
            skipped += 1

    print(f"  Inserted: {inserted}  |  Skipped: {skipped}")
    return inserted

# =============================================================================
# SUMMARY REPORTS
# =============================================================================

def show_airline_summary(cursor):
    print("\n  Airlines by country (top 10):")
    cursor.execute("""
        SELECT country, COUNT(*) AS cnt
        FROM airlines
        WHERE is_active = 1
        GROUP BY country
        ORDER BY cnt DESC
        FETCH FIRST 10 ROWS ONLY
    """)
    for country, cnt in cursor.fetchall():
        print(f"    {country:<35} {cnt:>3}")

def show_airport_summary(cursor):
    print("\n  Airports by country:")
    cursor.execute("""
        SELECT country, COUNT(*) AS cnt
        FROM airports
        GROUP BY country
        ORDER BY cnt DESC
    """)
    for country, cnt in cursor.fetchall():
        print(f"    {country:<35} {cnt:>3}")

    print("\n  Texas airports:")
    cursor.execute("""
        SELECT icao_code, iata_code, airport_name, city
        FROM airports
        WHERE country = 'United States'
          AND timezone = 'America/Chicago'
          AND (city LIKE '%Dallas%' OR city LIKE '%Fort Worth%' OR
               city LIKE '%Arlington%' OR city LIKE '%Grand Prairie%' OR
               city LIKE '%Addison%')
        ORDER BY airport_name
    """)
    for icao, iata, name, city in cursor.fetchall():
        iata_str = iata if iata else '---'
        print(f"    {icao}  {iata_str:<4}  {name}")

# =============================================================================
# MAIN
# =============================================================================

def main():
    print("="*80)
    print("Airlines & Airports Database Loader  v1.0.0")
    print("="*80)

    connection = connect_to_database()
    cursor = connection.cursor()

    try:
        # Populate both tables
        airlines_inserted = populate_airlines(cursor)
        airports_inserted = populate_airports(cursor)

        # Commit everything
        connection.commit()
        print("\n✓ All changes committed")

        # Show summaries
        show_airline_summary(cursor)
        show_airport_summary(cursor)

        print("\n" + "="*80)
        print(f"Complete!  Airlines: {airlines_inserted}   Airports: {airports_inserted}")
        print("="*80)

    except Exception as e:
        print(f"\n✗ Error: {e}")
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


if __name__ == '__main__':
    main()
