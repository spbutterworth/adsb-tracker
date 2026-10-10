#!/usr/bin/env python3
"""
Oracle 23ai Free Docker Setup Script
Creates and configures an Oracle 23ai Free container with persistent storage

Version: 1.0.0
Author: Claude
Last Updated: 2026-01-30
"""

__version__ = "1.0.0"

import subprocess
import sys
import time

def run_command(command, description, check=True):
    """Execute a shell command with progress feedback"""
    print(f"\n{'='*60}")
    print(f"🔄 {description}")
    print(f"{'='*60}")
    
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            check=check
        )
        
        if result.stdout:
            print(result.stdout)
        
        if result.returncode == 0:
            print(f"✅ Success: {description}")
        else:
            print(f"⚠️  Warning: {description} completed with code {result.returncode}")
            if result.stderr:
                print(f"Error output: {result.stderr}")
        
        return result
    
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed: {description}")
        print(f"Error: {e.stderr}")
        if check:
            raise
        return e

def check_docker():
    """Verify Docker is installed and running"""
    print("\n🔍 Checking Docker installation...")
    
    try:
        result = subprocess.run(
            "docker --version",
            shell=True,
            capture_output=True,
            text=True,
            check=True
        )
        print(f"✅ Docker found: {result.stdout.strip()}")
        
        # Check if Docker daemon is running
        subprocess.run(
            "docker ps",
            shell=True,
            capture_output=True,
            check=True
        )
        print("✅ Docker daemon is running")
        return True
        
    except subprocess.CalledProcessError:
        print("❌ Docker is not installed or not running")
        print("Please install Docker and ensure the Docker daemon is running")
        return False

def cleanup_existing(container_name):
    """Remove existing container if it exists"""
    print(f"\n🧹 Checking for existing container '{container_name}'...")
    
    check_cmd = f"docker ps -a --filter name={container_name} --format '{{{{.Names}}}}'"
    result = subprocess.run(check_cmd, shell=True, capture_output=True, text=True)
    
    if container_name in result.stdout:
        print(f"Found existing container '{container_name}', removing it...")
        run_command(
            f"docker stop {container_name}",
            f"Stopping container {container_name}",
            check=False
        )
        run_command(
            f"docker rm {container_name}",
            f"Removing container {container_name}",
            check=False
        )
    else:
        print(f"No existing container named '{container_name}' found")

def main():
    """Main setup function"""
    print(f"""
╔════════════════════════════════════════════════════════════╗
║         Oracle 23ai Free Docker Setup Script              ║
║                    Version {__version__}                         ║
║                                                            ║
║  This script will:                                         ║
║  • Create a Docker volume for data persistence            ║
║  • Pull the Oracle 23ai Free image                        ║
║  • Launch the container with proper configuration         ║
╚════════════════════════════════════════════════════════════╝
    """)
    
    # Configuration
    CONTAINER_NAME = "ora23c"
    VOLUME_NAME = "oracle23ai_data"
    IMAGE = "container-registry.oracle.com/database/free:latest"
    ORACLE_PWD = "oracle"
    
    # Step 1: Check Docker
    if not check_docker():
        sys.exit(1)
    
    # Step 2: Clean up existing container
    cleanup_existing(CONTAINER_NAME)
    
    # Step 3: Create Docker volume
    run_command(
        f"docker volume create {VOLUME_NAME}",
        f"Creating Docker volume '{VOLUME_NAME}' for data persistence"
    )
    
    # Step 4: Pull Oracle image
    print("\n⏬ Pulling Oracle 23ai Free image (this may take several minutes)...")
    run_command(
        f"docker pull {IMAGE}",
        "Downloading Oracle 23ai Free Docker image"
    )
    
    # Step 5: Run the container
    docker_run_cmd = f"""docker run -d \
        --name {CONTAINER_NAME} \
        -p 1521:1521 \
        -p 5501:5501 \
        -e ORACLE_PWD={ORACLE_PWD} \
        -v {VOLUME_NAME}:/opt/oracle/oradata \
        {IMAGE}"""
    
    run_command(
        docker_run_cmd,
        f"Starting Oracle 23ai Free container '{CONTAINER_NAME}'"
    )
    
    # Success message
    print(f"""
╔════════════════════════════════════════════════════════════╗
║                    🎉 SETUP COMPLETE! 🎉                   ║
╚════════════════════════════════════════════════════════════╝

📋 Container Details:
   • Container Name: {CONTAINER_NAME}
   • Image: {IMAGE}
   • Data Volume: {VOLUME_NAME}

🔌 Connection Information:
   • SQL*Net Port: 1521
   • EM Express: http://localhost:5501/em
   • Password: {ORACLE_PWD}
   • Service Name: FREEPDB1
   • SYS/SYSTEM Password: {ORACLE_PWD}

⏳ IMPORTANT: First Startup Information
   The database is now initializing. This process takes 5-10 minutes.
   The container is running, but the database won't be ready immediately.

📊 Monitor Startup Progress:
   docker logs -f {CONTAINER_NAME}

   Watch for this message: "DATABASE IS READY TO USE!"

🔍 Useful Commands:
   • Check container status:
     docker ps -a --filter name={CONTAINER_NAME}
   
   • View logs:
     docker logs {CONTAINER_NAME}
   
   • Follow logs in real-time:
     docker logs -f {CONTAINER_NAME}
   
   • Connect to container shell:
     docker exec -it {CONTAINER_NAME} bash
   
   • Stop container:
     docker stop {CONTAINER_NAME}
   
   • Start container:
     docker start {CONTAINER_NAME}
   
   • Connect with SQL*Plus (after database is ready):
     docker exec -it {CONTAINER_NAME} sqlplus sys/{ORACLE_PWD}@FREEPDB1 as sysdba

💡 Tips:
   • Be patient during first startup - database initialization takes time
   • Data persists in volume '{VOLUME_NAME}' even if container is removed
   • Use 'docker logs' to monitor progress and troubleshoot issues
   • EM Express will be available after database initialization completes

🚀 Next Steps:
   1. Wait for "DATABASE IS READY TO USE!" message in logs
   2. Test connection with SQL*Plus or your preferred Oracle client
   3. Access EM Express at http://localhost:5501/em
   4. Start building your Oracle applications!

════════════════════════════════════════════════════════════
""")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Setup interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Setup failed with error: {e}")
        sys.exit(1)

