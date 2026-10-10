#!/usr/bin/env python3
"""
Drop Old Partitions from ADSB_USER.POSITIONS Table
Removes partitions older than 5 days to save disk space
"""

import oracledb
from datetime import datetime, timedelta
import sys

# Configuration
DB_USER = 'adsb_user'
DB_PASSWORD = 'oracle'
DB_DSN = '192.168.10.23:1521/ORA26PDB'

# Days to retain
RETENTION_DAYS = 2

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

def get_partitions_to_drop(cursor):
    """Get list of partitions older than RETENTION_DAYS"""
    
    cutoff_date = datetime.now() - timedelta(days=RETENTION_DAYS)
    
    print(f"\nSearching for partitions older than {RETENTION_DAYS} days...")
    print(f"Cutoff date: {cutoff_date.strftime('%Y-%m-%d')}")
    print()
    
    # Get partition information
    cursor.execute("""
        SELECT 
            partition_name,
            high_value,
            num_rows,
            blocks,
            TO_CHAR(last_analyzed, 'YYYY-MM-DD HH24:MI:SS') as last_analyzed
        FROM dba_tab_partitions
        WHERE table_owner = 'ADSB_USER'
          AND table_name = 'POSITIONS'
        ORDER BY partition_position
    """)
    
    partitions_to_drop = []
    partitions_to_keep = []
    
    for row in cursor.fetchall():
        partition_name, high_value, num_rows, blocks, last_analyzed = row
        
        # Extract date from high_value
        # Format is typically: TIMESTAMP' 2026-02-10 00:00:00'
        try:
            # Parse the high_value to extract the date
            # High value format: TIMESTAMP' YYYY-MM-DD HH:MI:SS'
            date_str = high_value.split("'")[1].strip().split()[0]
            partition_date = datetime.strptime(date_str, '%Y-%m-%d')
            
            partition_info = {
                'name': partition_name,
                'date': partition_date,
                'num_rows': num_rows or 0,
                'blocks': blocks or 0,
                'last_analyzed': last_analyzed
            }
            
            if partition_date < cutoff_date:
                partitions_to_drop.append(partition_info)
            else:
                partitions_to_keep.append(partition_info)
                
        except Exception as e:
            print(f"Warning: Could not parse date for partition {partition_name}: {e}")
            print(f"  High value: {high_value}")
            continue
    
    return partitions_to_drop, partitions_to_keep

def display_partition_summary(partitions_to_drop, partitions_to_keep):
    """Display summary of partitions"""
    
    print("="*100)
    print("PARTITION SUMMARY")
    print("="*100)
    
    if partitions_to_drop:
        print(f"\n{len(partitions_to_drop)} partition(s) to DROP (older than {RETENTION_DAYS} days):")
        print("-"*100)
        print(f"{'Partition Name':<30} {'Date':<12} {'Rows':<15} {'Blocks':<10} {'Last Analyzed':<20}")
        print("-"*100)
        
        total_rows = 0
        total_blocks = 0
        
        for p in partitions_to_drop:
            total_rows += p['num_rows']
            total_blocks += p['blocks']
            analyzed = p['last_analyzed'] or 'Never'
            print(f"{p['name']:<30} {p['date'].strftime('%Y-%m-%d'):<12} {p['num_rows']:<15,} {p['blocks']:<10,} {analyzed:<20}")
        
        print("-"*100)
        print(f"{'TOTAL TO DROP:':<30} {'':<12} {total_rows:<15,} {total_blocks:<10,}")
        print()
    else:
        print(f"\n✓ No partitions older than {RETENTION_DAYS} days found")
    
    if partitions_to_keep:
        print(f"\n{len(partitions_to_keep)} partition(s) to KEEP (within {RETENTION_DAYS} days):")
        print("-"*100)
        print(f"{'Partition Name':<30} {'Date':<12} {'Rows':<15} {'Blocks':<10} {'Last Analyzed':<20}")
        print("-"*100)
        
        total_rows = 0
        total_blocks = 0
        
        for p in partitions_to_keep:
            total_rows += p['num_rows']
            total_blocks += p['blocks']
            analyzed = p['last_analyzed'] or 'Never'
            print(f"{p['name']:<30} {p['date'].strftime('%Y-%m-%d'):<12} {p['num_rows']:<15,} {p['blocks']:<10,} {analyzed:<20}")
        
        print("-"*100)
        print(f"{'TOTAL TO KEEP:':<30} {'':<12} {total_rows:<15,} {total_blocks:<10,}")
    
    print()

def drop_partitions(cursor, partitions):
    """Drop the specified partitions"""
    
    if not partitions:
        print("No partitions to drop")
        return
    
    print(f"\nDropping {len(partitions)} partition(s)...")
    print()
    
    dropped_count = 0
    failed_count = 0
    
    for partition in partitions:
        partition_name = partition['name']
        
        try:
            print(f"  Dropping {partition_name}...", end=' ')
            
            cursor.execute(f"""
                ALTER TABLE ADSB_USER.POSITIONS 
                DROP PARTITION {partition_name}
            """)
            
            print("✓")
            dropped_count += 1
            
        except Exception as e:
            print(f"✗ Error: {e}")
            failed_count += 1
    
    print()
    print(f"Results: {dropped_count} dropped, {failed_count} failed")
    
    return dropped_count, failed_count

def main():
    """Main function"""
    print("="*100)
    print("Drop Old Partitions - ADSB_USER.POSITIONS Table")
    print("="*100)
    print(f"Retention period: {RETENTION_DAYS} days")
    print()
    
    # Connect to database
    connection = connect_to_database()
    cursor = connection.cursor()
    
    # Get partitions to drop
    partitions_to_drop, partitions_to_keep = get_partitions_to_drop(cursor)
    
    # Display summary
    display_partition_summary(partitions_to_drop, partitions_to_keep)
    
    # If no partitions to drop, exit
    if not partitions_to_drop:
        print("Nothing to do!")
        cursor.close()
        connection.close()
        return
    
    # Calculate space to be freed
    total_rows = sum(p['num_rows'] for p in partitions_to_drop)
    total_blocks = sum(p['blocks'] for p in partitions_to_drop)
    approx_mb = (total_blocks * 8) / 1024  # Oracle block size is typically 8KB
    
    print("="*100)
    print("IMPACT SUMMARY")
    print("="*100)
    print(f"Partitions to drop:  {len(partitions_to_drop)}")
    print(f"Rows to delete:      {total_rows:,}")
    print(f"Approx space freed:  {approx_mb:,.2f} MB")
    print()
    
    # Confirm with user
    print("⚠️  WARNING: This operation cannot be undone!")
    print()
    response = input("Do you want to proceed? (yes/no): ").strip().lower()
    
    if response != 'yes':
        print("\nOperation cancelled by user")
        cursor.close()
        connection.close()
        return
    
    print()
    
    # Drop partitions
    dropped, failed = drop_partitions(cursor, partitions_to_drop)
    
    # Commit changes
    if dropped > 0:
        print("\nCommitting changes...")
        connection.commit()
        print("✓ Changes committed")
        
        # Show final partition count
        cursor.execute("""
            SELECT COUNT(*) 
            FROM dba_tab_partitions 
            WHERE table_owner = 'ADSB_USER' 
              AND table_name = 'POSITIONS'
        """)
        
        remaining = cursor.fetchone()[0]
        
        print()
        print("="*100)
        print("FINAL STATUS")
        print("="*100)
        print(f"Partitions dropped:   {dropped}")
        print(f"Failed operations:    {failed}")
        print(f"Remaining partitions: {remaining}")
        print()
    
    # Close connection
    cursor.close()
    connection.close()
    
    print("="*100)
    print("Operation complete!")
    print("="*100)

if __name__ == '__main__':
    main()
