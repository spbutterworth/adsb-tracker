#!/usr/bin/env python3
"""
Oracle Schema Statistics Manager
Gathers statistics and can optionally refresh them for better query performance
"""

import oracledb
from datetime import datetime
import sys

# Configuration
DB_USER = 'system'
DB_PASSWORD = 'oracle'
DB_DSN = '192.168.10.23:1521/ORA26PDB'

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

def get_user_schemas(cursor):
    """Get list of user-created schemas (exclude system schemas)"""
    system_schemas = [
        'SYS', 'SYSTEM', 'OUTLN', 'DIP', 'ORACLE_OCM', 'DBSNMP', 'APPQOSSYS',
        'WMSYS', 'XDB', 'ANONYMOUS', 'CTXSYS', 'DVSYS', 'DVF', 'GSMADMIN_INTERNAL',
        'LBACSYS', 'MDSYS', 'OLAPSYS', 'ORDDATA', 'ORDPLUGINS', 'ORDSYS',
        'SI_INFORMTN_SCHEMA', 'APEX_PUBLIC_USER', 'FLOWS_FILES',
        'APEX_INSTANCE_ADMIN_USER', 'SPATIAL_CSW_ADMIN_USR', 'SPATIAL_WFS_ADMIN_USR',
        'XS$NULL', 'GSMCATUSER', 'MDDATA', 'SYSBACKUP', 'REMOTE_SCHEDULER_AGENT',
        'DBSFWUSER', 'DGPDB_INT', 'SYSRAC', 'GSMUSER', 'AUDSYS', 'DV_ACCTMGR',
        'DV_AUDIT_CLEANUP', 'DV_MONITOR', 'DV_OWNER', 'DV_PATCH_ADMIN',
        'DV_PUBLIC', 'DV_REALM_OWNER', 'DV_SECANALYST', 'DVF', 'GGSYS',
        'OJVMSYS', 'PDBADMIN'
    ]
    
    placeholders = ','.join([':' + str(i) for i in range(len(system_schemas))])
    
    cursor.execute(f"""
        SELECT username 
        FROM dba_users 
        WHERE username NOT IN ({placeholders})
        ORDER BY username
    """, system_schemas)
    
    return [row[0] for row in cursor.fetchall()]

def display_schema_summary(cursor, schemas):
    """Display summary statistics for each schema"""
    print("\n" + "="*100)
    print("SCHEMA SUMMARY")
    print("="*100)
    print(f"{'Schema':<20} {'Tables':<10} {'Partitions':<12} {'Indexes':<10} {'Total Rows':<20} {'Size (MB)':<15}")
    print("-"*100)
    
    for schema in schemas:
        # Get table count and total rows
        cursor.execute("""
            SELECT 
                COUNT(*) as table_count,
                NVL(SUM(num_rows), 0) as total_rows
            FROM dba_tables 
            WHERE owner = :schema
        """, schema=schema)
        
        table_count, total_rows = cursor.fetchone()
        
        # Get partition count
        cursor.execute("""
            SELECT COUNT(*) 
            FROM dba_tab_partitions 
            WHERE table_owner = :schema
        """, schema=schema)
        
        partition_count = cursor.fetchone()[0]
        
        # Get index count
        cursor.execute("""
            SELECT COUNT(*) 
            FROM dba_indexes 
            WHERE owner = :schema
        """, schema=schema)
        
        index_count = cursor.fetchone()[0]
        
        # Get total size
        cursor.execute("""
            SELECT NVL(SUM(bytes)/1024/1024, 0) 
            FROM dba_segments 
            WHERE owner = :schema
        """, schema=schema)
        
        size_mb = cursor.fetchone()[0]
        
        print(f"{schema:<20} {table_count:<10} {partition_count:<12} {index_count:<10} {total_rows:<20,} {size_mb:<15,.2f}")

def display_table_details(cursor, schema):
    """Display detailed table statistics for a schema"""
    print(f"\n{'='*120}")
    print(f"TABLE DETAILS - {schema}")
    print('='*120)
    print(f"{'Table Name':<40} {'Partitioned':<12} {'Rows':<15} {'Blocks':<10} {'Last Analyzed':<20}")
    print('-'*120)
    
    cursor.execute("""
        SELECT 
            table_name,
            partitioned,
            NVL(num_rows, 0) as num_rows,
            NVL(blocks, 0) as blocks,
            TO_CHAR(last_analyzed, 'YYYY-MM-DD HH24:MI:SS') as last_analyzed
        FROM dba_tables 
        WHERE owner = :schema
        ORDER BY num_rows DESC NULLS LAST
    """, schema=schema)
    
    for row in cursor.fetchall():
        table_name, partitioned, num_rows, blocks, last_analyzed = row
        last_analyzed = last_analyzed or 'Never'
        print(f"{table_name:<40} {partitioned:<12} {num_rows:<15,} {blocks:<10,} {last_analyzed:<20}")

def display_partition_details(cursor, schema):
    """Display partition statistics for a schema"""
    cursor.execute("""
        SELECT COUNT(*) 
        FROM dba_tab_partitions 
        WHERE table_owner = :schema
    """, schema=schema)
    
    partition_count = cursor.fetchone()[0]
    
    if partition_count == 0:
        print(f"\nNo partitioned tables in {schema}")
        return
    
    print(f"\n{'='*120}")
    print(f"PARTITION DETAILS - {schema}")
    print('='*120)
    print(f"{'Table Name':<40} {'Partition Name':<30} {'Rows':<15} {'Last Analyzed':<20}")
    print('-'*120)
    
    cursor.execute("""
        SELECT 
            table_name,
            partition_name,
            NVL(num_rows, 0) as num_rows,
            TO_CHAR(last_analyzed, 'YYYY-MM-DD HH24:MI:SS') as last_analyzed
        FROM dba_tab_partitions 
        WHERE table_owner = :schema
        ORDER BY table_name, partition_name
    """, schema=schema)
    
    for row in cursor.fetchall():
        table_name, partition_name, num_rows, last_analyzed = row
        last_analyzed = last_analyzed or 'Never'
        print(f"{table_name:<40} {partition_name:<30} {num_rows:<15,} {last_analyzed:<20}")

def display_index_details(cursor, schema):
    """Display index statistics for a schema"""
    print(f"\n{'='*120}")
    print(f"INDEX DETAILS - {schema}")
    print('='*120)
    print(f"{'Index Name':<40} {'Table Name':<30} {'Unique':<8} {'Rows':<15} {'Last Analyzed':<20}")
    print('-'*120)
    
    cursor.execute("""
        SELECT 
            index_name,
            table_name,
            uniqueness,
            NVL(num_rows, 0) as num_rows,
            TO_CHAR(last_analyzed, 'YYYY-MM-DD HH24:MI:SS') as last_analyzed
        FROM dba_indexes 
        WHERE owner = :schema
        ORDER BY table_name, index_name
    """, schema=schema)
    
    for row in cursor.fetchall():
        index_name, table_name, uniqueness, num_rows, last_analyzed = row
        last_analyzed = last_analyzed or 'Never'
        print(f"{index_name:<40} {table_name:<30} {uniqueness:<8} {num_rows:<15,} {last_analyzed:<20}")

def check_stale_statistics(cursor, schemas):
    """Check for tables with stale or missing statistics"""
    print("\n" + "="*100)
    print("STALE STATISTICS CHECK (Not analyzed in last 7 days)")
    print("="*100)
    print(f"{'Schema':<20} {'Table Name':<40} {'Days Since Analysis':<20}")
    print("-"*100)
    
    found_stale = False
    
    for schema in schemas:
        cursor.execute("""
            SELECT 
                table_name,
                CASE 
                    WHEN last_analyzed IS NULL THEN 999999
                    ELSE TRUNC(SYSDATE - last_analyzed)
                END as days_since
            FROM dba_tables 
            WHERE owner = :schema
              AND (last_analyzed IS NULL OR last_analyzed < SYSDATE - 7)
            ORDER BY days_since DESC
        """, schema=schema)
        
        for row in cursor.fetchall():
            found_stale = True
            table_name, days_since = row
            if days_since == 999999:
                days_str = "Never"
            else:
                days_str = str(int(days_since))
            print(f"{schema:<20} {table_name:<40} {days_str:<20}")
    
    if not found_stale:
        print("✓ All tables have current statistics (analyzed within 7 days)")

def gather_schema_statistics(cursor, schema):
    """Gather statistics for a schema using DBMS_STATS"""
    print(f"\nGathering statistics for schema {schema}...")
    print("This may take several minutes depending on data volume...")
    
    try:
        # Use DBMS_STATS to gather schema statistics
        # Execute as PL/SQL block with named parameters
        cursor.execute("""
            BEGIN
                DBMS_STATS.GATHER_SCHEMA_STATS(
                    ownname          => :schema,
                    estimate_percent => DBMS_STATS.AUTO_SAMPLE_SIZE,
                    method_opt       => 'FOR ALL COLUMNS SIZE AUTO',
                    degree           => DBMS_STATS.AUTO_DEGREE,
                    cascade          => TRUE,
                    options          => 'GATHER AUTO'
                );
            END;
        """, schema=schema)
        
        print(f"✓ Statistics gathered successfully for {schema}")
        return True
        
    except Exception as e:
        print(f"✗ Error gathering statistics for {schema}: {e}")
        return False

def main():
    """Main function"""
    print("="*100)
    print("Oracle Schema Statistics Manager")
    print("="*100)
    print()
    
    # Connect to database
    connection = connect_to_database()
    cursor = connection.cursor()
    
    # Get user schemas
    schemas = get_user_schemas(cursor)
    
    if not schemas:
        print("No user schemas found!")
        return
    
    print(f"\nFound {len(schemas)} user schema(s): {', '.join(schemas)}")
    
    # Display summary
    display_schema_summary(cursor, schemas)
    
    # Check for stale statistics
    check_stale_statistics(cursor, schemas)
    
    # Ask if user wants detailed stats for each schema
    print("\n" + "="*100)
    print("DETAILED STATISTICS")
    print("="*100)
    
    for schema in schemas:
        display_table_details(cursor, schema)
        display_partition_details(cursor, schema)
        display_index_details(cursor, schema)
    
    # Ask if user wants to gather statistics
    print("\n" + "="*100)
    print("GATHER STATISTICS")
    print("="*100)
    print("\nWould you like to gather fresh statistics for your schemas?")
    print("This will improve query optimizer performance.")
    print("\nOptions:")
    print("  1. Gather for all schemas")
    print("  2. Gather for specific schema")
    print("  3. Skip")
    
    choice = input("\nEnter choice (1-3): ").strip()
    
    if choice == '1':
        for schema in schemas:
            gather_schema_statistics(cursor, schema)
        connection.commit()
        print("\n✓ Statistics gathering complete for all schemas!")
        
    elif choice == '2':
        print(f"\nAvailable schemas: {', '.join(schemas)}")
        schema = input("Enter schema name: ").strip().upper()
        if schema in schemas:
            gather_schema_statistics(cursor, schema)
            connection.commit()
            print(f"\n✓ Statistics gathering complete for {schema}!")
        else:
            print(f"Invalid schema: {schema}")
    
    else:
        print("\nSkipping statistics gathering")
    
    # Close connection
    cursor.close()
    connection.close()
    
    print("\n" + "="*100)
    print("Report complete!")
    print("="*100)

if __name__ == '__main__':
    main()
