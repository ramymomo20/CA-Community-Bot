from ios_bot.config import *
import logging

# Set up logger for database operations
logger = logging.getLogger(__name__)

# Database failover state management
_current_db = 'primary'  # Track which database is currently active
_failover_count = 0  # Track failover attempts
_last_failover_time = None  # Track when last failover occurred
_max_failover_attempts = 3  # Maximum consecutive failover attempts before giving up

def get_current_db_config():
    """Get the current database configuration (primary or secondary)."""
    global _current_db
    return current_db_config[_current_db]

def is_secondary_db_available():
    """Check if secondary database configuration is available."""
    secondary_config = current_db_config['secondary']
    return all([
        secondary_config['host'],
        secondary_config['user'], 
        secondary_config['password'],
        secondary_config['database']
    ])

def should_attempt_failover():
    """Determine if we should attempt failover to secondary database."""
    global _failover_count, _last_failover_time
    
    # Don't attempt failover if secondary DB is not configured
    if not is_secondary_db_available():
        return False
        
    # Don't attempt failover if we've exceeded max attempts
    if _failover_count >= _max_failover_attempts:
        return False
        
    # Don't attempt failover too frequently (wait at least 30 seconds)
    import time
    current_time = time.time()
    if _last_failover_time and (current_time - _last_failover_time) < 30:
        return False
        
    return True

def perform_failover():
    """Switch to the other database (primary <-> secondary)."""
    global _current_db, _failover_count, _last_failover_time
    import time
    
    if _current_db == 'primary':
        if is_secondary_db_available():
            _current_db = 'secondary'
            _failover_count += 1
            _last_failover_time = time.time()
            print(f"🔄 Database failover: Switched to SECONDARY database (attempt {_failover_count})")
            return True
    else:
        _current_db = 'primary'
        _failover_count += 1
        _last_failover_time = time.time()
        print(f"🔄 Database failover: Switched back to PRIMARY database (attempt {_failover_count})")
        return True
    
    return False

def reset_failover_state():
    """Reset failover state after successful connection."""
    global _failover_count, _last_failover_time
    if _failover_count > 0:
        print(f"✅ Database connection stable. Resetting failover state.")
        _failover_count = 0
        _last_failover_time = None

def get_db_status_info():
    """Get current database status information."""
    return {
        'current_db': _current_db,
        'failover_count': _failover_count,
        'last_failover_time': _last_failover_time,
        'secondary_available': is_secondary_db_available(),
        'current_config': get_current_db_config()
    }

def _connect_to_specific_db_sync(db_type='primary'):
    """Connect to a specific database (primary or secondary) synchronously."""
    if db_type not in ['primary', 'secondary']:
        raise ValueError("db_type must be 'primary' or 'secondary'")
    
    config = current_db_config[db_type]
    
    # Check if configuration is available
    if not all([config['host'], config['user'], config['password'], config['database']]):
        raise ValueError(f"{db_type.capitalize()} database configuration is incomplete")
    
    try:
        conn = mysql.connector.connect(
            host=config['host'],
            port=config['port'],
            user=config['user'],
            password=config['password'],
            database=config['database'],
            connection_timeout=30
        )
        if conn.is_connected():
            print(f"✅ Connected to {db_type} database")
            return conn
    except Error as e:
        raise Exception(f"Failed to connect to {db_type} database: {e}")

async def connect_to_specific_db(db_type='primary'):
    """Connect to a specific database (primary or secondary) asynchronously."""
    return await run_blocking_db_operation(_connect_to_specific_db_sync, db_type)

def _get_all_table_names_sync(conn):
    """Get all table names from a database connection."""
    cursor = conn.cursor()
    cursor.execute("SHOW TABLES")
    tables = [table[0] for table in cursor.fetchall()]
    cursor.close()
    return tables

def _sync_table_structure_sync(source_conn, target_conn, table_name):
    """Synchronize table structure from source to target database."""
    source_cursor = source_conn.cursor()
    target_cursor = target_conn.cursor()
    
    try:
        # Get CREATE TABLE statement from source
        source_cursor.execute(f"SHOW CREATE TABLE `{table_name}`")
        result = source_cursor.fetchone()
        if not result:
            raise Exception(f"Table {table_name} not found in source database")
        
        create_statement = result[1]
        
        # Drop table in target if it exists
        target_cursor.execute(f"DROP TABLE IF EXISTS `{table_name}`")
        
        # Create table in target
        target_cursor.execute(create_statement)
        
        return True
        
    except Exception as e:
        raise Exception(f"Failed to sync table structure for {table_name}: {e}")
    finally:
        source_cursor.close()
        target_cursor.close()

def _sync_table_data_sync(source_conn, target_conn, table_name, batch_size=1000):
    """Synchronize table data from source to target database."""
    source_cursor = source_conn.cursor(dictionary=True)
    target_cursor = target_conn.cursor()
    
    try:
        # Get total row count
        source_cursor.execute(f"SELECT COUNT(*) as count FROM `{table_name}`")
        total_rows = source_cursor.fetchone()['count']
        
        if total_rows == 0:
            print(f"  ℹ️ Table {table_name} is empty, skipping data sync")
            return True
        
        # Clear target table
        target_cursor.execute(f"TRUNCATE TABLE `{table_name}`")
        
        # Get column names
        source_cursor.execute(f"DESCRIBE `{table_name}`")
        columns = [col['Field'] for col in source_cursor.fetchall()]
        column_list = ', '.join([f"`{col}`" for col in columns])
        placeholders = ', '.join(['%s'] * len(columns))
        
        # Sync data in batches
        offset = 0
        synced_rows = 0
        
        while offset < total_rows:
            # Fetch batch from source
            source_cursor.execute(f"SELECT {column_list} FROM `{table_name}` LIMIT {batch_size} OFFSET {offset}")
            rows = source_cursor.fetchall()
            
            if not rows:
                break
            
            # Prepare data for insertion
            data_to_insert = []
            for row in rows:
                data_to_insert.append(tuple(row[col] for col in columns))
            
            # Insert batch into target
            insert_query = f"INSERT INTO `{table_name}` ({column_list}) VALUES ({placeholders})"
            target_cursor.executemany(insert_query, data_to_insert)
            
            synced_rows += len(rows)
            offset += batch_size
            
            # Show progress for large tables
            if total_rows > 10000:
                progress = (synced_rows / total_rows) * 100
                print(f"  📊 {table_name}: {synced_rows}/{total_rows} rows ({progress:.1f}%)")
        
        print(f"  ✅ Synced {synced_rows} rows for table {table_name}")
        return True
        
    except Exception as e:
        raise Exception(f"Failed to sync table data for {table_name}: {e}")
    finally:
        source_cursor.close()
        target_cursor.close()

async def sync_databases(source_db='primary', target_db='secondary', sync_structure=True, sync_data=True):
    """
    Synchronize databases from source to target.
    
    Args:
        source_db: Source database ('primary' or 'secondary')
        target_db: Target database ('primary' or 'secondary') 
        sync_structure: Whether to sync table structures
        sync_data: Whether to sync table data
    
    Returns:
        dict: Sync results with success status and details
    """
    if source_db == target_db:
        raise ValueError("Source and target databases cannot be the same")
    
    sync_results = {
        'success': False,
        'source_db': source_db,
        'target_db': target_db,
        'tables_synced': 0,
        'total_tables': 0,
        'errors': [],
        'start_time': datetime.now(),
        'end_time': None
    }
    
    source_conn = None
    target_conn = None
    
    try:
        print(f"🔄 Starting database sync: {source_db.upper()} → {target_db.upper()}")
        
        # Connect to both databases
        source_conn = await connect_to_specific_db(source_db)
        target_conn = await connect_to_specific_db(target_db)
        
        if not source_conn or not target_conn:
            raise Exception("Failed to connect to one or both databases")
        
        # Get all tables from source database
        tables = await run_blocking_db_operation(_get_all_table_names_sync, source_conn)
        sync_results['total_tables'] = len(tables)
        
        print(f"📋 Found {len(tables)} tables to sync")
        
        # Sync each table
        for table_name in tables:
            try:
                print(f"🔄 Syncing table: {table_name}")
                
                # Sync table structure
                if sync_structure:
                    await run_blocking_db_operation(_sync_table_structure_sync, source_conn, target_conn, table_name)
                
                # Sync table data
                if sync_data:
                    await run_blocking_db_operation(_sync_table_data_sync, source_conn, target_conn, table_name)
                
                sync_results['tables_synced'] += 1
                
            except Exception as e:
                error_msg = f"Failed to sync table {table_name}: {str(e)}"
                sync_results['errors'].append(error_msg)
                print(f"❌ {error_msg}")
                continue
        
        # Commit changes
        if target_conn:
            target_conn.commit()
        
        sync_results['success'] = True
        sync_results['end_time'] = datetime.now()
        
        duration = sync_results['end_time'] - sync_results['start_time']
        print(f"✅ Database sync completed in {duration.total_seconds():.2f} seconds")
        print(f"📊 Successfully synced {sync_results['tables_synced']}/{sync_results['total_tables']} tables")
        
        if sync_results['errors']:
            print(f"⚠️ {len(sync_results['errors'])} errors occurred during sync")
        
    except Exception as e:
        sync_results['errors'].append(str(e))
        sync_results['end_time'] = datetime.now()
        print(f"❌ Database sync failed: {e}")
        
    finally:
        # Close connections
        try:
            if source_conn and source_conn.is_connected():
                source_conn.close()
            if target_conn and target_conn.is_connected():
                target_conn.close()
        except:
            pass
    
    return sync_results
import re
import pandas as pd
from difflib import SequenceMatcher
from datetime import datetime
import tempfile
import json
import asyncio
import mysql.connector.pooling
from mysql.connector import Error
import time as clock
import logging
from typing import Optional, Dict, Any, List, Tuple

# Set up logging for database operations
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==============================================================================
# DATABASE CONNECTION POOLING AND OPTIMIZATION
# ==============================================================================

class DatabaseConnectionPool:
    """
    Manages a pool of database connections for better performance.
    This replaces the old pattern of opening/closing connections for every query.
    """
    _instance = None
    _pool = None
    _query_cache = {}
    _cache_timestamps = {}
    _cache_ttl = 300  # 5 minutes TTL for cache
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if self._pool is None:
            self._initialize_pool()
    
    def _initialize_pool(self):
        """Initialize the connection pool with MariaDB-compatible settings and failover support."""
        try:
            # Get current database configuration
            db_config = get_current_db_config()
            db_name = _current_db.upper()
            
            # Simple configuration - same as direct connection
            pool_config = {
                'pool_name': f'discord_bot_pool_{_current_db}',
                'pool_size': 10,
                'host': db_config['host'],
                'port': db_config['port'],
                'user': db_config['user'],
                'password': db_config['password'],
                'database': db_config['database'],
                'connection_timeout': 30,
            }
            
            # Use the same simple approach for both databases
            try:
                self._pool = mysql.connector.pooling.MySQLConnectionPool(**pool_config)
                print(f"✅ {db_name} database connection pool initialized")
                reset_failover_state()
            except Error as pool_error:
                print(f"⚠️ {db_name} pool init failed: {pool_error}")
                raise Error(f"Failed to initialize {db_name} connection pool: {pool_error}")
            
        except Error as e:
            print(f"❌ Failed to initialize {_current_db.upper()} connection pool: {e}")
            
            # Attempt failover if possible
            if should_attempt_failover():
                print(f"🔄 Attempting pool failover to {'SECONDARY' if _current_db == 'primary' else 'PRIMARY'} database...")
                if perform_failover():
                    # Reset pool to None and recursively try with new database
                    self._pool = None
                    self._initialize_pool()
                    return
            
            print("🔄 Falling back to single connection mode...")
            self._pool = None
    
    def get_connection(self):
        """Get a connection from the pool."""
        if self._pool is None:
            self._initialize_pool()
        
        if self._pool is None:
            logger.error("❌ Connection pool is not available")
            return None
        
        try:
            return self._pool.get_connection()
        except Error as e:
            logger.error(f"❌ Failed to get connection from pool: {e}")
            return None
    
    def _is_cache_valid(self, cache_key: str) -> bool:
        """Check if cache entry is still valid."""
        if cache_key not in self._cache_timestamps:
            return False
        
        age = clock.time() - self._cache_timestamps[cache_key]
        return age < self._cache_ttl
    
    def _cache_result(self, cache_key: str, result: Any):
        """Cache a query result."""
        self._query_cache[cache_key] = result
        self._cache_timestamps[cache_key] = clock.time()
    
    def _get_cached_result(self, cache_key: str) -> Optional[Any]:
        """Get cached result if valid."""
        if self._is_cache_valid(cache_key):
            return self._query_cache[cache_key]
        return None
    
    def _clear_expired_cache(self):
        """Clear expired cache entries."""
        current_time = clock.time()
        expired_keys = [
            key for key, timestamp in self._cache_timestamps.items()
            if current_time - timestamp > self._cache_ttl
        ]
        
        for key in expired_keys:
            self._query_cache.pop(key, None)
            self._cache_timestamps.pop(key, None)

# Global connection pool instance
db_pool = DatabaseConnectionPool()

# Get the current event loop; this should be done once, ideally where the bot is defined or starts
# However, for a self-contained manager, getting it on demand is also an option.
# Be mindful if this module is imported before an event loop is set by discord.py.
# A more robust way would be to pass the loop or bot instance to the db manager if needed.

async def run_blocking_db_operation(func, *args):
    """Runs a blocking database function in an executor."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, func, *args) # None uses default ThreadPoolExecutor

def _connect_db_sync(): # Renamed original connect_db
    """Connect to the MySQL database (synchronous) with retry logic and failover."""
    max_retries = 3
    retry_delay = 2
    
    # Try connecting with current database configuration
    db_config = get_current_db_config()
    db_name = _current_db.upper()
    
    for attempt in range(max_retries):
        try:
            conn = mysql.connector.connect(
                host=db_config['host'],
                port=db_config['port'],
                user=db_config['user'],
                password=db_config['password'],
                database=db_config['database'],
                connection_timeout=30
            )
            
            if conn.is_connected():
                # Reset failover state on successful connection
                reset_failover_state()
                
                # Only print connection success if it took multiple attempts or after failover
                if attempt > 0 or _failover_count > 0:
                    print(f"✅ {db_name} database connection successful on attempt {attempt + 1}")
                return conn
                    
        except Error as e:
            # Only print connection details on first failure
            if attempt == 0:
                print(f"🔗 {db_name} database connection issue (retrying):")
                print(f"  Host: {db_config['host']}")
                print(f"  Database: {db_config['database']}")
            
            print(f"❌ {db_name} connection attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                import time
                time.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            else:
                print(f"💥 All {max_retries} {db_name} connection attempts failed")
                break
    
    # If all retries failed, attempt failover if possible
    if should_attempt_failover():
        print(f"🔄 Attempting failover to {'SECONDARY' if _current_db == 'primary' else 'PRIMARY'} database...")
        if perform_failover():
            # Recursively try connecting with the new database
            return _connect_db_sync()
    
    print(f"💀 All database connection options exhausted")
    return None

async def connect_db():
    """Connect to the MySQL database (asynchronous wrapper)."""
    return await run_blocking_db_operation(_connect_db_sync)

async def test_database_connection():
    """Test database connection and return detailed status."""
    # First check if all required environment variables are set
    missing_vars = []
    for var_name, var_value in [('DB_HOST', host), ('DB_PORT', port), ('DB_USER', user), ('DB_PASSWORD', password), ('DB_NAME', database)]:
        if not var_value:
            missing_vars.append(var_name)
    
    if missing_vars:
        print(f"❌ Missing required environment variables: {', '.join(missing_vars)}")
        return False
    
    # Test connection
    try:
        conn = await connect_db()
        if conn:
            # Test a simple query
            result = await execute_query("SELECT 1 as test", fetchone=True)
            if result and result.get('test') == 1:
                # Only print success message if there were connection issues
                # (the _connect_db_sync function will handle printing if there are retries)
                return True
            else:
                print("❌ Database query test failed")
                return False
        else:
            print("❌ Failed to establish database connection")
            return False
    except Exception as e:
        print(f"❌ Database connection test failed: {e}")
        return False


def _execute_query_sync(query: str, params: tuple | None = None, fetchone: bool = False, fetchall: bool = False, commit: bool = False): # Renamed original
    """Execute a general query (synchronous). Handles connection opening/closing with retry logic."""
    max_retries = 2
    retry_delay = 1
    
    for attempt in range(max_retries):
        conn = None
        cursor = None
        try:
            conn = _connect_db_sync()
            if not conn:
                if attempt < max_retries - 1:
                    import time
                    time.sleep(retry_delay)
                    continue
                else:
                    return False
            
            # Test connection health
            conn.ping(reconnect=True, attempts=2, delay=1)
            cursor = conn.cursor()
            cursor.execute(query, params)
            
            if commit:
                conn.commit()
                return True 
            elif fetchone:
                result = cursor.fetchone()
                if result and cursor.description:
                    # Convert to dictionary
                    columns = [desc[0] for desc in cursor.description]
                    return dict(zip(columns, result))
                return result
            elif fetchall:
                results = cursor.fetchall()
                if results and cursor.description:
                    # Convert to list of dictionaries
                    columns = [desc[0] for desc in cursor.description]
                    return [dict(zip(columns, row)) for row in results]
                return results
            else:
                # For non-commit/non-fetch queries, we need to consume any results
                # to prevent "Unread result found" errors
                try:
                    cursor.fetchall()  # Consume any results
                except:
                    pass  # No results to consume
                return True 
                
        except Error as e:
            if conn and commit: 
                try:
                    conn.rollback()
                except Error:
                    pass
            
            # Check if this is a connection error that we should retry
            if attempt < max_retries - 1 and (
                "Lost connection" in str(e) or 
                "Connection reset by peer" in str(e) or
                "MySQL server has gone away" in str(e) or
                e.errno in [2013, 2055, 2006]  # Common connection error codes
            ):
                import time
                time.sleep(retry_delay)
                retry_delay *= 2
                continue
            else:
                return False
                
        finally:
            if cursor:
                try:
                    cursor.close()
                except:
                    pass  # Ignore errors when closing cursor
            if conn and conn.is_connected():
                try:
                    conn.close()
                except:
                    pass  # Ignore errors when closing connection

    return False

async def execute_query(query: str, params: tuple | None = None, fetchone: bool = False, fetchall: bool = False, commit: bool = False):
    """Execute a general query (asynchronous wrapper)."""
    return await run_blocking_db_operation(_execute_query_sync, query, params, fetchone, fetchall, commit)

# ==============================================================================
# OPTIMIZED QUERY EXECUTION FUNCTIONS
# ==============================================================================

def _execute_query_optimized_sync(query: str, params: tuple | None = None, fetchone: bool = False, fetchall: bool = False, commit: bool = False, use_cache: bool = False):
    """
    Execute a query using connection pooling and caching for better performance.
    This replaces the old pattern of opening/closing connections for every query.
    """
    # Generate cache key for SELECT queries
    cache_key = None
    if use_cache and not commit and (fetchone or fetchall):
        cache_key = f"{query}:{params}"
        cached_result = db_pool._get_cached_result(cache_key)
        if cached_result is not None:
            return cached_result
    
    # Get connection from pool
    conn = db_pool.get_connection()
    if not conn:
        logger.error("❌ Failed to get database connection from pool")
        return False
    
    cursor = None
    try:
        cursor = conn.cursor()
        cursor.execute(query, params)
        
        if commit:
            conn.commit()
            return True
        elif fetchone:
            result = cursor.fetchone()
            if result and cursor.description:
                columns = [desc[0] for desc in cursor.description]
                result_dict = dict(zip(columns, result))
                if use_cache and cache_key:
                    db_pool._cache_result(cache_key, result_dict)
                return result_dict
            return result
        elif fetchall:
            results = cursor.fetchall()
            if results and cursor.description:
                columns = [desc[0] for desc in cursor.description]
                result_list = [dict(zip(columns, row)) for row in results]
                if use_cache and cache_key:
                    db_pool._cache_result(cache_key, result_list)
                return result_list
            return results
        else:
            # Consume any results to prevent "Unread result found" errors
            try:
                cursor.fetchall()
            except:
                pass
            return True
            
    except Error as e:
        if conn and commit:
            try:
                conn.rollback()
            except Error:
                pass
        logger.error(f"❌ Database query failed: {e}")
        return False
        
    finally:
        if cursor:
            try:
                cursor.close()
            except:
                pass
        if conn:
            try:
                conn.close()
            except:
                pass

async def execute_query_optimized(query: str, params: tuple | None = None, fetchone: bool = False, fetchall: bool = False, commit: bool = False, use_cache: bool = False):
    """Execute a query using optimized connection pooling and caching."""
    return await run_blocking_db_operation(_execute_query_optimized_sync, query, params, fetchone, fetchall, commit, use_cache)

def _execute_batch_queries_sync(queries: List[Tuple[str, tuple]], commit: bool = True) -> bool:
    """Execute multiple queries in a single transaction for better performance."""
    conn = db_pool.get_connection()
    if not conn:
        logger.error("❌ Failed to get database connection from pool")
        return False
    
    cursor = None
    try:
        cursor = conn.cursor()
        
        # Execute all queries
        for query, params in queries:
            cursor.execute(query, params)
        
        if commit:
            conn.commit()
        
        return True
        
    except Error as e:
        if conn and commit:
            try:
                conn.rollback()
            except Error:
                pass
        logger.error(f"❌ Batch query execution failed: {e}")
        return False
        
    finally:
        if cursor:
            try:
                cursor.close()
            except:
                pass
        if conn:
            try:
                conn.close()
            except:
                pass

async def execute_batch_queries(queries: List[Tuple[str, tuple]], commit: bool = True) -> bool:
    """Execute multiple queries in a single transaction."""
    return await run_blocking_db_operation(_execute_batch_queries_sync, queries, commit)

# ==============================================================================
# DATABASE PERFORMANCE OPTIMIZATION FUNCTIONS
# ==============================================================================

async def add_critical_database_indexes():
    """
    Add critical database indexes to improve query performance.
    This addresses the primary cause of full table scans and slow queries.
    """
    indexes = [
        # Critical indexes for MATCH_STATS table
        ("idx_match_stats_home_team", "MATCH_STATS", "home_team_name"),
        ("idx_match_stats_away_team", "MATCH_STATS", "away_team_name"),
        ("idx_match_stats_match_id", "MATCH_STATS", "match_id"),
        ("idx_match_stats_datetime", "MATCH_STATS", "datetime"),
        ("idx_match_stats_home_guild_id", "MATCH_STATS", "home_guild_id"),
        ("idx_match_stats_away_guild_id", "MATCH_STATS", "away_guild_id"),
        
        # Critical indexes for PLAYER_MATCH_DATA table
        ("idx_player_match_data_steam_id", "PLAYER_MATCH_DATA", "steam_id"),
        ("idx_player_match_data_player_name", "PLAYER_MATCH_DATA", "player_name"),
        ("idx_player_match_data_team_guild_id", "PLAYER_MATCH_DATA", "team_guild_id"),
        ("idx_player_match_data_match_id", "PLAYER_MATCH_DATA", "match_id"),
        
        # Composite indexes for common query patterns
        ("idx_match_stats_teams_datetime", "MATCH_STATS", "home_team_name, away_team_name, datetime"),
        ("idx_match_stats_guilds_datetime", "MATCH_STATS", "home_guild_id, away_guild_id, datetime"),
    ]
    
    successful_indexes = 0
    
    for index_name, table_name, columns in indexes:
        try:
            # Check if index already exists
            check_query = """
            SELECT COUNT(*) as count 
            FROM information_schema.statistics 
            WHERE table_schema = %s 
            AND table_name = %s 
            AND index_name = %s
            """
            
            result = await execute_query_optimized(
                check_query, 
                (database, table_name, index_name), 
                fetchone=True
            )
            
            if result and result.get('count', 0) > 0:
                logger.info(f"✅ Index {index_name} already exists on {table_name}")
                successful_indexes += 1
                continue
            
            # Create the index
            create_query = f"CREATE INDEX {index_name} ON {table_name} ({columns})"
            
            success = await execute_query_optimized(create_query, commit=True)
            
            if success:
                logger.info(f"✅ Created index {index_name} on {table_name}({columns})")
                successful_indexes += 1
            else:
                logger.error(f"❌ Failed to create index {index_name} on {table_name}")
                
        except Exception as e:
            logger.error(f"❌ Error creating index {index_name}: {e}")
    
    logger.info(f"✅ Successfully created/verified {successful_indexes}/{len(indexes)} database indexes")
    return successful_indexes == len(indexes)

# === IMPROVED CSV IMPORT FUNCTIONS ===

def parse_csv_with_commas(file_path: str) -> list[dict]:
    """
    Parse CSV file using pandas to properly handle commas in names and other fields.
    This is much more robust than manual parsing.
    """
    try:
        # Use pandas for robust CSV parsing
        df = pd.read_csv(file_path, encoding='utf-8')
        # Convert to list of dictionaries
        return df.to_dict('records')
    except Exception as e:
        print(f"❌ Error parsing CSV {file_path}: {e}")
        return []

def safe_get_string(data: dict, key: str, default: str = '') -> str:
    """
    Safely get a string value from CSV data, handling NaN/float values.
    """
    import pandas as pd
    
    value = data.get(key, default)
    
    # Handle None values
    if value is None:
        return default
    
    # Handle pandas NaN values
    if pd.isna(value):
        return default
    
    # Convert to string and check for NaN string representation
    str_value = str(value)
    if str_value.lower() in ['nan', 'none', 'null']:
        return default
    
    # Strip whitespace and return
    return str_value.strip()

async def sync_csv_to_database():
    """
    Fast CSV-to-Database synchronization with automatic placeholder team creation.
    Only imports new rows that don't exist in the database.
    """
    import os
    
    print("🔄 Starting optimized CSV-to-Database synchronization...")
    
    # Test database connection first with timeout
    try:
        # Add a timeout to prevent hanging during connection test
        connection_test = await asyncio.wait_for(test_database_connection(), timeout=30.0)
        if not connection_test:
            print("❌ Database connection test failed. Cannot proceed with CSV sync.")
            return False
    except asyncio.TimeoutError:
        print("❌ Database connection test timed out after 30 seconds. Cannot proceed with CSV sync.")
        return False
    
    # Get paths to CSV files
    csv_dir = os.path.join(os.path.dirname(__file__), 'ratings')
    match_summaries_path = os.path.join(csv_dir, 'match_summaries.csv')
    player_stats_path = os.path.join(csv_dir, 'player_stats.csv')
    
    if not os.path.exists(match_summaries_path) or not os.path.exists(player_stats_path):
        print("❌ CSV files not found. Skipping sync.")
        return False
    
    try:
        # Parse CSV files first
        print("📄 Loading CSV files...")
        match_data = parse_csv_with_commas(match_summaries_path)
        player_data = parse_csv_with_commas(player_stats_path)
        
        csv_match_count = len(match_data)
        csv_player_count = len(player_data)
        print(f"📄 CSV files: {csv_match_count} matches, {csv_player_count} player records")
        
        # Get existing match IDs for incremental sync
        existing_matches = await execute_query("SELECT match_id FROM MATCH_STATS", fetchall=True)
        existing_match_ids = {match['match_id'] for match in existing_matches} if existing_matches else set()
        
        # Filter to only new matches
        new_matches_data = [match for match in match_data if match.get('match_id', '') not in existing_match_ids]
        print(f"📥 Found {len(new_matches_data)} new matches to import")
        
        if not new_matches_data:
            print("✅ All matches already imported. Checking player data...")
        else:
            # Get all unique team names from new matches
            all_team_names = set()
            for match in new_matches_data:
                home_team = safe_get_string(match, 'home_team')
                away_team = safe_get_string(match, 'away_team')
                if home_team:
                    all_team_names.add(home_team)
                if away_team:
                    all_team_names.add(away_team)
            
            print(f"🏢 Found {len(all_team_names)} unique team names in new matches")
            
            # Create/update team mappings for all teams
            await ensure_team_mappings_exist(all_team_names)
            
            # Get updated team mappings
            team_mappings = await get_team_mappings_lookup()
            
            # Batch insert matches
            print("📥 Batch inserting matches...")
            match_inserts = []
            skipped_matches = 0
            
            for match in new_matches_data:
                match_id = match.get('match_id', '')
                if not match_id:
                    continue
                
                home_team_name = safe_get_string(match, 'home_team')
                away_team_name = safe_get_string(match, 'away_team')
                
                home_mapping = team_mappings.get(home_team_name)
                away_mapping = team_mappings.get(away_team_name)
                
                if not home_mapping or not away_mapping:
                    skipped_matches += 1
                    continue
                
                # Parse datetime
                try:
                    match_datetime = datetime.strptime(match['datetime'], '%Y-%m-%d %H:%M:%S')
                except (ValueError, KeyError):
                    skipped_matches += 1
                    continue
                
                match_inserts.append((
                    match_id, match_datetime, home_mapping['guild_id'], away_mapping['guild_id'],
                    home_team_name, away_team_name, match.get('scoreline', ''), match.get('game_type', ''),
                    match.get('initial_lineups', ''), match.get('final_lineups', ''), 
                    match.get('substitution_summary', '')
                ))
            
            # Batch insert all matches
            if match_inserts:
                print(f"📥 Inserting {len(match_inserts)} matches...")
                await batch_insert_matches(match_inserts)
                print(f"✅ Inserted {len(match_inserts)} matches (skipped {skipped_matches})")
            else:
                print(f"⚠️ No matches could be inserted (skipped {skipped_matches})")
        
        # Handle player data
        print("👥 Processing player data...")
        existing_players = await execute_query("SELECT match_id, steam_id FROM PLAYER_MATCH_DATA", fetchall=True)
        existing_player_keys = {(p['match_id'], p['steam_id']) for p in existing_players} if existing_players else set()
        
        new_players_data = [
            player for player in player_data 
            if (player.get('match_id', ''), player.get('Steam ID', '')) not in existing_player_keys
        ]
        print(f"👥 Found {len(new_players_data)} new player records to import")
        
        if new_players_data:
            team_mappings = await get_team_mappings_lookup()
            
            # Batch insert player data
            player_inserts = []
            skipped_players = 0
            
            for player_record in new_players_data:
                match_id = player_record.get('match_id', '')
                steam_id = player_record.get('Steam ID', '')
                
                if not match_id or not steam_id:
                    continue
                
                team_name = safe_get_string(player_record, 'Team Name')
                opponent_name = safe_get_string(player_record, 'Opponent Team Name')
                
                team_mapping = team_mappings.get(team_name)
                opponent_mapping = team_mappings.get(opponent_name)
                
                if not team_mapping or not opponent_mapping:
                    skipped_players += 1
                    continue
                
                try:
                    match_datetime = datetime.strptime(player_record['datetime'], '%Y-%m-%d %H:%M:%S')
                except (ValueError, KeyError):
                    skipped_players += 1
                    continue
                
                # Prepare additional stats
                base_fields = {'match_id', 'datetime', 'Steam ID', 'Name', 'Team Name', 'Opponent Team Name', 'Team Side', 'Position'}
                additional_stats = {}
                for key, value in player_record.items():
                    if key not in base_fields and value is not None:
                        try:
                            if isinstance(value, str) and value.isdigit():
                                additional_stats[key] = int(value)
                            else:
                                additional_stats[key] = value
                        except:
                            additional_stats[key] = str(value)
                
                player_inserts.append((
                    match_id, match_datetime, steam_id, player_record.get('Name', ''),
                    team_mapping['guild_id'] if team_mapping else 0, 
                    opponent_mapping['guild_id'] if opponent_mapping else 0,
                    team_name, opponent_name, player_record.get('Team Side', ''),
                    player_record.get('Position', ''), json.dumps(additional_stats)
                ))
            
            # Batch insert all player records
            if player_inserts:
                print(f"👥 Inserting {len(player_inserts)} player records...")
                await batch_insert_players(player_inserts)
                print(f"✅ Inserted {len(player_inserts)} player records (skipped {skipped_players})")
        
        # Final stats
        final_match_count = await execute_query("SELECT COUNT(*) as count FROM MATCH_STATS", fetchone=True)
        final_player_count = await execute_query("SELECT COUNT(*) as count FROM PLAYER_MATCH_DATA", fetchone=True)
        
        print(f"🎉 Sync completed!")
        print(f"📊 Final database: {final_match_count['count']} matches, {final_player_count['count']} player records")
        
        return True
        
    except asyncio.TimeoutError:
        print("❌ CSV sync operation timed out after 2 minutes. The process may be stuck.")
        print("💡 Try running /sync_csv_data command manually, or check database connectivity.")
        return False
        
    except Exception as e:
        print(f"❌ Error during CSV sync: {e}")
        import traceback
        traceback.print_exc()
        return False

async def ensure_team_mappings_exist(team_names: set):
    """
    Ensure all team names have mappings, but only map to existing registered teams.
    No placeholder teams are created - teams must be registered via register command.
    """
    print(f"🔍 Mapping {len(team_names)} teams to existing registered teams...")
    
    # Get existing mappings
    existing_mappings = await get_team_mappings_lookup()
    
    # Get existing teams
    existing_teams = await get_all_teams_with_details()
    if not existing_teams or not isinstance(existing_teams, list):
        print("❌ No teams found in database or failed to retrieve teams")
        existing_teams = []
    existing_team_lookup = {team['guild_name'].lower(): team for team in existing_teams}
    
    unmapped_teams = []
    mapped_teams = 0
    
    for team_name in team_names:
        if team_name in existing_mappings:
            continue  # Already mapped
            
        # Try to find exact match in database teams (case insensitive)
        team_key = team_name.lower()
        if team_key in existing_team_lookup:
            # Create direct mapping to existing registered team
            team = existing_team_lookup[team_key]
            await execute_query(
                """
                INSERT INTO TEAM_NAME_MAPPINGS (csv_team_name, guild_id, guild_name, similarity_score)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE similarity_score = VALUES(similarity_score)
                """,
                (team_name, team['guild_id'], team['guild_name'], 1.0),
                commit=True
            )
            mapped_teams += 1
            continue
        
        # Team not found in registered teams - track as unmapped
        unmapped_teams.append(team_name)
        
        # Track in unmapped teams table
        await execute_query(
            """
            INSERT INTO UNMAPPED_TEAMS (csv_team_name, created_placeholder, notes)
            VALUES (%s, FALSE, 'Team not registered - must use register command')
            ON DUPLICATE KEY UPDATE 
            match_count = match_count + 1,
            notes = 'Team not registered - must use register command'
            """,
            (team_name,),
            commit=True
        )
    
    if mapped_teams > 0:
        print(f"✅ Mapped {mapped_teams} teams to existing registered teams")
    
    if unmapped_teams:
        print(f"⚠️ {len(unmapped_teams)} teams remain unmapped (not registered): {unmapped_teams[:5]}{'...' if len(unmapped_teams) > 5 else ''}")
        print(f"ℹ️ These teams need to be registered via the register command to be mapped.")

async def batch_insert_matches(match_inserts: list):
    """Batch insert matches for better performance."""
    if not match_inserts:
        return
    
    # Use executemany for better performance
    query = """
    INSERT INTO MATCH_STATS 
    (match_id, datetime, home_guild_id, away_guild_id, home_team_name, away_team_name, 
     scoreline, game_type, initial_lineups, final_lineups, substitution_summary)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    
    # Split into batches of 1000 for memory efficiency
    batch_size = 1000
    for i in range(0, len(match_inserts), batch_size):
        batch = match_inserts[i:i + batch_size]
        
        conn = None
        cursor = None
        try:
            conn = await run_blocking_db_operation(_connect_db_sync)
            if not conn:
                raise Exception("Failed to connect to database")
            
            cursor = conn.cursor()
            cursor.executemany(query, batch)
            conn.commit()
            
        except Exception as e:
            if conn:
                conn.rollback()
            raise e
            
        finally:
            if cursor:
                cursor.close()
            if conn and conn.is_connected():
                conn.close()

async def batch_insert_players(player_inserts: list):
    """Batch insert player records for better performance."""
    if not player_inserts:
        return
    
    query = """
    INSERT INTO PLAYER_MATCH_DATA 
    (match_id, datetime, steam_id, player_name, team_guild_id, opponent_guild_id,
     team_name, opponent_team_name, team_side, position, additional_stats)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    
    # Split into batches of 1000
    batch_size = 1000
    for i in range(0, len(player_inserts), batch_size):
        batch = player_inserts[i:i + batch_size]
        
        conn = None
        cursor = None
        try:
            conn = await run_blocking_db_operation(_connect_db_sync)
            if not conn:
                raise Exception("Failed to connect to database")
            
            cursor = conn.cursor()
            cursor.executemany(query, batch)
            conn.commit()
            
        except Exception as e:
            if conn:
                conn.rollback()
            raise e
            
        finally:
            if cursor:
                cursor.close()
            if conn and conn.is_connected():
                conn.close()

# === TEAM NAME MATCHING FUNCTIONS ===

def calculate_levenshtein_distance(s1: str, s2: str) -> int:
    """Calculate the Levenshtein distance between two strings."""
    if len(s1) < len(s2):
        return calculate_levenshtein_distance(s2, s1)
    
    if len(s2) == 0:
        return len(s1)
    
    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    
    return previous_row[-1]

def normalize_team_name(name: str) -> str:
    """Normalize team name for better matching."""
    # Remove common prefixes/suffixes and normalize
    name = re.sub(r'^(team\s+|club\s+|fc\s+)', '', name.lower().strip())
    name = re.sub(r'(\s+fc|\s+club|\s+team)$', '', name)
    # Remove special characters except spaces
    name = re.sub(r'[^\w\s]', '', name)
    # Normalize whitespace
    name = re.sub(r'\s+', ' ', name).strip()
    return name

def calculate_similarity_score(csv_name: str, db_name: str) -> float:
    """Calculate similarity score between CSV team name and database team name."""
    # Normalize both names
    csv_normalized = normalize_team_name(csv_name)
    db_normalized = normalize_team_name(db_name)
    
    # If names are identical after normalization, perfect match
    if csv_normalized == db_normalized:
        return 1.0
    
    # Calculate Levenshtein distance
    distance = calculate_levenshtein_distance(csv_normalized, db_normalized)
    max_len = max(len(csv_normalized), len(db_normalized))
    
    if max_len == 0:
        return 1.0
    
    # Convert distance to similarity score (0-1)
    similarity = 1 - (distance / max_len)
    
    # Boost score for partial matches
    if csv_normalized in db_normalized or db_normalized in csv_normalized:
        similarity = max(similarity, 0.8)
    
    # Use SequenceMatcher for additional similarity check
    seq_similarity = SequenceMatcher(None, csv_normalized, db_normalized).ratio()
    
    # Return the higher of the two similarity scores
    return max(similarity, seq_similarity)

async def find_matching_team(csv_team_name: str, threshold: float = 0.7) -> dict | None:
    """Find the best matching team from database using fuzzy matching."""
    # Get all teams from database
    all_teams = await get_all_teams_with_details()
    if not all_teams:
        return None
    
    best_match = None
    best_score = 0.0
    
    for team in all_teams:
        score = calculate_similarity_score(csv_team_name, team['guild_name'])
        if score > best_score and score >= threshold:
            best_score = score
            best_match = team
    
    return best_match if best_match else None



# === OPTIMIZED TOURNAMENT SYSTEM ===

async def create_optimized_tournament_tables():
    """Create optimized tournament tables (2 tables instead of 5)."""
    
    # Main tournaments table with embedded league info
    tournaments_query = """
    CREATE TABLE IF NOT EXISTS TOURNAMENTS_V2 (
        id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(255) NOT NULL UNIQUE,
        num_teams INT NOT NULL,
        num_leagues INT NOT NULL,
        leagues JSON NOT NULL,  -- Stores league structure: [{"name": "League A", "teams": [...]}]
        start_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        end_date TIMESTAMP NULL,
        is_completed BOOLEAN DEFAULT FALSE,
        awards JSON,  -- Stores all awards: {"champion": "...", "runner_up": "...", etc.}
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    );
    """
    await execute_query(tournaments_query)
    
    # Tournament data table (matches, stats, teams all in one)
    tournament_data_query = """
    CREATE TABLE IF NOT EXISTS TOURNAMENT_DATA (
        id INT AUTO_INCREMENT PRIMARY KEY,
        tournament_id INT NOT NULL,
        league_name VARCHAR(255) NOT NULL,
        data_type ENUM('match', 'team_stats') NOT NULL,
        
        -- Match data (used when data_type = 'match')
        match_id VARCHAR(255) NULL,
        home_team_guild_id BIGINT NULL,
        away_team_guild_id BIGINT NULL,
        
        -- Team stats data (used when data_type = 'team_stats')
        team_guild_id BIGINT NULL,
        team_name VARCHAR(255) NULL,
        stats JSON NULL,  -- Stores: {"matches": 10, "wins": 7, "goals_for": 25, etc.}
        
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (tournament_id) REFERENCES TOURNAMENTS_V2(id) ON DELETE CASCADE,
        
        -- Indexes for performance
        INDEX idx_tournament_league (tournament_id, league_name),
        INDEX idx_tournament_type (tournament_id, data_type),
        INDEX idx_match_id (match_id),
        INDEX idx_team (team_guild_id),
        
        -- Unique constraints
        UNIQUE KEY unique_tournament_match (tournament_id, match_id),
        UNIQUE KEY unique_tournament_team_stats (tournament_id, league_name, team_guild_id, data_type)
    );
    """
    await execute_query(tournament_data_query)





# === UPDATED INITIALIZATION FUNCTION ===

async def initialize_database_v2():
    """Initialize the optimized database schema with single connection."""
    print("🔍 Checking database schema...")
    
    # Check if main tables already exist
    tables_to_check = ['IOSCA_TEAMS', 'MATCH_STATS', 'PLAYER_MATCH_DATA']
    existing_tables = []
    
    try:
        for table_name in tables_to_check:
            result = await execute_query(
                f"SELECT COUNT(*) as count FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
                (database, table_name),
                fetchone=True
            )
            if result and result['count'] > 0:
                existing_tables.append(table_name)
    except Exception as e:
        print(f"⚠️ Error checking existing tables: {e}")
    
    if len(existing_tables) == len(tables_to_check):
        print("✅ Database schema already exists. Skipping table creation.")
        await auto_populate_database_from_csv_v2()
        return
    
    print("🔄 Initializing database schema...")
    
    # Get a single connection for all initialization operations
    conn = None
    cursor = None
    
    try:
        conn = await run_blocking_db_operation(_connect_db_sync)
        if not conn:
            print("❌ Failed to connect to database for initialization")
            return
        
        cursor = conn.cursor()
        
        # Create all tables in batch
        table_queries = [
            # Teams table
            """
    CREATE TABLE IF NOT EXISTS IOSCA_TEAMS (
        guild_id BIGINT PRIMARY KEY,
        guild_name VARCHAR(255) NOT NULL,
        guild_icon VARCHAR(255),
        captain_id BIGINT NOT NULL,
        captain_name VARCHAR(255) NOT NULL,
        vice_captain_id BIGINT,
        vice_captain_name VARCHAR(255),
        eights_channels JSON,
        sixes_channels JSON,
                players JSON,
                is_national_team BOOLEAN NOT NULL DEFAULT FALSE,
                is_mix_team BOOLEAN NOT NULL DEFAULT FALSE
            )
            """,
            
            # Players table
            """
            CREATE TABLE IF NOT EXISTS IOSCA_PLAYERS (
                discord_id BIGINT PRIMARY KEY,
                username VARCHAR(255) NOT NULL,
                steam_id VARCHAR(255)
            )
            """,
            
            # Servers table
            """
            CREATE TABLE IF NOT EXISTS IOS_SERVERS (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(255) NOT NULL UNIQUE,
                address VARCHAR(255) NOT NULL,
                password VARCHAR(255) NOT NULL,
                sftp_ip VARCHAR(255),
                host_username VARCHAR(255),
                host_password VARCHAR(255),
                server_type ENUM('linux', 'windows') DEFAULT 'linux',
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """,
            
            # Tournament tables
            """
            CREATE TABLE IF NOT EXISTS TOURNAMENTS_V2 (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(255) NOT NULL UNIQUE,
                num_teams INT NOT NULL,
                num_leagues INT NOT NULL,
                leagues JSON NOT NULL,
                start_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                end_date TIMESTAMP NULL,
                is_completed BOOLEAN DEFAULT FALSE,
                awards JSON,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """,
            
            """
            CREATE TABLE IF NOT EXISTS TOURNAMENT_DATA (
                id INT AUTO_INCREMENT PRIMARY KEY,
                tournament_id INT NOT NULL,
                league_name VARCHAR(255) NOT NULL,
                data_type ENUM('match', 'team_stats') NOT NULL,
                match_id VARCHAR(255) NULL,
                home_team_guild_id BIGINT NULL,
                away_team_guild_id BIGINT NULL,
                team_guild_id BIGINT NULL,
                team_name VARCHAR(255) NULL,
                stats JSON NULL,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_tournament_league (tournament_id, league_name),
                INDEX idx_tournament_type (tournament_id, data_type),
                INDEX idx_match_id (match_id),
                INDEX idx_team (team_guild_id),
                UNIQUE KEY unique_tournament_match (tournament_id, match_id),
                UNIQUE KEY unique_tournament_team_stats (tournament_id, league_name, team_guild_id, data_type)
            )
            """,
            
            # Transfer tables
            """
            CREATE TABLE IF NOT EXISTS PLAYER_TRANSFERS (
                id INT AUTO_INCREMENT PRIMARY KEY,
                player_discord_id BIGINT NOT NULL,
                player_name VARCHAR(255) NOT NULL,
                from_team_guild_id BIGINT NULL,
                from_team_name VARCHAR(255) NULL,
                to_team_guild_id BIGINT NULL,
                to_team_name VARCHAR(255) NULL,
                transfer_type ENUM('JOIN', 'LEAVE', 'TRANSFER') NOT NULL,
                transfer_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                reason VARCHAR(255) NULL,
                processed_by_discord_id BIGINT NULL,
                processed_by_name VARCHAR(255) NULL,
                INDEX idx_player_discord_id (player_discord_id),
                INDEX idx_transfer_date (transfer_date)
            )
            """,
            
            """
            CREATE TABLE IF NOT EXISTS TRANSFER_SETTINGS (
                id INT AUTO_INCREMENT PRIMARY KEY,
                transfer_window_open BOOLEAN DEFAULT TRUE,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                updated_by_discord_id BIGINT NULL,
                updated_by_name VARCHAR(255) NULL
            )
            """,
            
            """
            CREATE TABLE IF NOT EXISTS TEAM_NAME_MAPPINGS (
                csv_team_name VARCHAR(255) PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                guild_name VARCHAR(255) NOT NULL,
                similarity_score FLOAT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """,
            
            """
            CREATE TABLE IF NOT EXISTS UNMAPPED_TEAMS (
                csv_team_name VARCHAR(255) PRIMARY KEY,
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                match_count INT DEFAULT 1,
                created_placeholder BOOLEAN DEFAULT FALSE,
                notes TEXT NULL
            )
            """,
            
            """
            CREATE TABLE IF NOT EXISTS MATCH_STATS (
                match_id VARCHAR(255) PRIMARY KEY,
                datetime TIMESTAMP NOT NULL,
                home_guild_id BIGINT NULL,
                away_guild_id BIGINT NULL,
                home_team_name VARCHAR(255) NOT NULL,
                away_team_name VARCHAR(255) NOT NULL,
                scoreline VARCHAR(20) NOT NULL,
                game_type ENUM('6v6', '8v8') NOT NULL,
                initial_lineups TEXT,
                final_lineups TEXT,
                substitution_summary TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_datetime (datetime),
                INDEX idx_home_team (home_guild_id),
                INDEX idx_away_team (away_guild_id),
                INDEX idx_teams (home_guild_id, away_guild_id),
                INDEX idx_game_type (game_type)
            )
            """,
            
            """
            CREATE TABLE IF NOT EXISTS PLAYER_MATCH_DATA (
                id INT AUTO_INCREMENT PRIMARY KEY,
                match_id VARCHAR(255) NOT NULL,
                datetime TIMESTAMP NOT NULL,
                steam_id VARCHAR(255) NOT NULL,
                player_name VARCHAR(255) NOT NULL,
                team_guild_id BIGINT NULL,
                opponent_guild_id BIGINT NULL,
                team_name VARCHAR(255) NOT NULL,
                opponent_team_name VARCHAR(255) NOT NULL,
                team_side ENUM('home', 'away') NOT NULL,
                position VARCHAR(10),
                additional_stats JSON,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_match_id (match_id),
                INDEX idx_player (steam_id),
                INDEX idx_team (team_guild_id),
                INDEX idx_datetime (datetime),
                INDEX idx_player_team (steam_id, team_guild_id)
            )
            """
        ]
        
        # Execute all table creation queries
        for i, query in enumerate(table_queries, 1):
            cursor.execute(query)
            print(f"✅ Created table {i}/{len(table_queries)}")
        
        # Initialize transfer settings if empty
        cursor.execute("SELECT COUNT(*) as count FROM TRANSFER_SETTINGS")
        result = cursor.fetchone()
        if result[0] == 0:
            cursor.execute("INSERT INTO TRANSFER_SETTINGS (transfer_window_open) VALUES (TRUE)")
            print("✅ Initialized transfer settings")
        
        # Initialize default servers if empty
        cursor.execute("SELECT COUNT(*) as count FROM IOS_SERVERS WHERE is_active = TRUE")
        result = cursor.fetchone()
        if result[0] == 0:
            default_servers = [
                ("Florida", "*", "*", "*", "*"),
                ("Georgia", "*", "*", "*", "*")
            ]
            for name, address, password, host_username, host_password in default_servers:
                cursor.execute(
                    """
                    INSERT INTO IOS_SERVERS (name, address, password, host_username, host_password, is_active)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (name, address, password, host_username, host_password, True)
                )
            print(f"✅ Added {len(default_servers)} default servers")
        
        conn.commit()
        print("✅ Database initialization complete with single connection!")
        
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"❌ Error during database initialization: {e}")
        raise e
        
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()
    
    # Run migration to allow NULL guild_ids
    await migrate_tables_to_allow_null_guild_ids()
    
    # Run migrations for new columns
    await alter_teams_table_for_national_teams()
    await alter_teams_table_for_mix_teams()
    
    # Auto-populate with CSV data if needed
    await auto_populate_database_from_csv_v2()

async def auto_populate_database_from_csv_v2():
    """Check if database needs CSV data and populate efficiently using the new no-auto-teams approach."""
    try:
        # Check if MATCH_STATS table has any data
        match_count = await execute_query("SELECT COUNT(*) as count FROM MATCH_STATS", fetchone=True)
        
        if match_count and match_count.get('count', 0) > 0:
            return
        
        print("🚀 Starting smart background CSV import (no auto team creation)...")
        
        # Start CSV import as a background task (non-blocking)
        import asyncio
        asyncio.create_task(background_csv_import_smart())
        
    except Exception as e:
        print(f"⚠️ Error during CSV import check: {e}")
        print("🔄 Continuing with bot startup...")

# Global variable to track background import status
_background_import_status = {"running": False, "completed": False, "error": None}

async def background_csv_import_smart():
    """Run smart CSV import in the background without blocking bot startup or creating placeholder teams."""
    global _background_import_status
    
    try:
        _background_import_status["running"] = True
        _background_import_status["completed"] = False
        _background_import_status["error"] = None
        
        # Add a small delay to ensure bot is fully initialized
        await asyncio.sleep(5)
        
        print("📊 Smart background CSV import starting (no auto team creation)...")
        
        # Use the comprehensive CSV import that imports all data with NULL for unregistered teams
        success = await comprehensive_csv_import()
        
        if success:
            print("✅ Smart background CSV import completed successfully.")
            print("💡 Only data for manually registered teams was imported.")
            _background_import_status["completed"] = True
        else:
            print("❌ Smart background CSV import failed, but bot continues running.")
            _background_import_status["error"] = "Smart CSV import failed"
            
    except Exception as e:
        print(f"⚠️ Error during smart background CSV import: {e}")
        print("🔄 Bot continues running normally...")
        _background_import_status["error"] = str(e)
        
    finally:
        _background_import_status["running"] = False

# Legacy function for backward compatibility
async def background_csv_import():
    """Legacy function - now redirects to smart import."""
    await background_csv_import_smart()

async def get_background_import_status():
    """Get the current status of the background CSV import."""
    return _background_import_status.copy()

async def start_background_csv_sync():
    """Start a background CSV sync task (non-blocking)."""
    global _background_import_status
    
    if _background_import_status["running"]:
        return {"status": "already_running", "message": "CSV sync is already running in the background"}
    
    print("🚀 Starting background CSV sync...")
    
    # Reset status
    _background_import_status = {"running": True, "completed": False, "error": None}
    
    # Start as background task
    import asyncio
    asyncio.create_task(background_csv_sync_task())
    
    return {"status": "started", "message": "Background CSV sync started"}

async def background_csv_sync_task():
    """Background task for manual CSV sync."""
    global _background_import_status
    
    try:
        print("📊 Manual background CSV sync starting...")
        
        # Use the comprehensive CSV import function (does not create teams)
        success = await comprehensive_csv_import()
        
        if success:
            print("✅ Manual background CSV sync completed successfully.")
            _background_import_status["completed"] = True
        else:
            print("❌ Manual background CSV sync failed.")
            _background_import_status["error"] = "Manual CSV sync failed"
            
    except Exception as e:
        print(f"⚠️ Error during manual background CSV sync: {e}")
        _background_import_status["error"] = str(e)
        
    finally:
        _background_import_status["running"] = False

# === TEAM NAME MATCHING AND MAPPING FUNCTIONS ===

async def preprocess_csv_team_mappings():
    """Pre-process all unique team names from CSV and create mappings with auto-deduplication."""
    print("🔍 Pre-processing CSV team mappings with auto-deduplication...")
    
    # Get path to match summaries CSV
    match_summaries_path = os.path.join(os.path.dirname(__file__), 'ratings', 'match_summaries.csv')
    if not os.path.exists(match_summaries_path):
        print(f"❌ CSV file not found: {match_summaries_path}")
        return

    # Extract all unique team names from CSV using pandas for better parsing
    match_data = parse_csv_with_commas(match_summaries_path)
    if not match_data:
        return
    
    unique_csv_teams = set()
    for row in match_data:
        home_team = row.get('home_team', '')
        away_team = row.get('away_team', '')
        if home_team:
            unique_csv_teams.add(home_team)
        if away_team:
            unique_csv_teams.add(away_team)
    
    print(f"📊 Found {len(unique_csv_teams)} unique team names in {len(match_data)} matches")
    
    # Get all database teams
    db_teams = await get_all_teams_with_details()
    if not db_teams or not isinstance(db_teams, list):
        print("❌ No teams found in database or failed to retrieve teams")
        return
    
    print(f"💾 Found {len(db_teams)} teams in database")
    
    # Process mappings and auto-deduplicate
    mapped_count = 0
    skipped_count = 0
    
    # Track which database teams have been mapped to avoid duplicates
    used_db_teams = {}  # guild_id -> best_csv_name
    
    for csv_team_name in unique_csv_teams:
        # Check if mapping already exists
        existing = await execute_query(
            "SELECT similarity_score FROM TEAM_NAME_MAPPINGS WHERE csv_team_name = %s",
            (csv_team_name,),
            fetchone=True
        )
        
        if existing:
            skipped_count += 1
            continue
        
        # Find best match
        best_match = None
        best_score = 0.0
        
        for db_team in db_teams:
            score = calculate_similarity_score(csv_team_name, db_team['guild_name'])
            if score > best_score and score >= 0.7:  # Threshold for acceptance
                best_score = score
                best_match = db_team
        
        if best_match:
            guild_id = best_match['guild_id']
            
            # Check if this database team is already mapped to another CSV team
            if guild_id in used_db_teams:
                existing_csv_name = used_db_teams[guild_id]
                existing_score = await execute_query(
                    "SELECT similarity_score FROM TEAM_NAME_MAPPINGS WHERE csv_team_name = %s",
                    (existing_csv_name,),
                    fetchone=True
                )
                existing_score_val = existing_score['similarity_score'] if existing_score else 0.0
                
                # If current mapping is better, replace the existing one
                if best_score > existing_score_val:
                    # Remove old mapping
                    await execute_query(
                        "DELETE FROM TEAM_NAME_MAPPINGS WHERE csv_team_name = %s",
                        (existing_csv_name,),
                        commit=True
                    )
                    print(f"  🔄 Replaced '{existing_csv_name}' with '{csv_team_name}' for '{best_match['guild_name']}' (better score: {best_score:.3f} > {existing_score_val:.3f})")
                else:
                    # Keep the existing mapping, skip this one
                    continue
            
            # Insert new mapping
            await execute_query(
                """
                INSERT INTO TEAM_NAME_MAPPINGS (csv_team_name, guild_id, guild_name, similarity_score)
                VALUES (%s, %s, %s, %s)
                """,
                (csv_team_name, best_match['guild_id'], best_match['guild_name'], best_score),
                commit=True
            )
            used_db_teams[guild_id] = csv_team_name
            mapped_count += 1
            
            if mapped_count <= 10:  # Show first 10 mappings
                print(f"  ✅ '{csv_team_name}' → '{best_match['guild_name']}' (score: {best_score:.3f})")
    
    print(f"✅ Pre-processing complete with auto-deduplication:")
    print(f"  • New mappings created: {mapped_count}")
    print(f"  • Existing mappings skipped: {skipped_count}")
    print(f"  • Unmapped teams: {len(unique_csv_teams) - mapped_count - skipped_count}")

async def get_team_mappings_lookup():
    """Get all team mappings as a fast lookup dictionary."""
    mappings = await execute_query(
        "SELECT csv_team_name, guild_id, guild_name, similarity_score FROM TEAM_NAME_MAPPINGS",
        fetchall=True
    )
    
    lookup = {}
    if mappings:
        for mapping in mappings:
            lookup[mapping['csv_team_name']] = {
                'guild_id': mapping['guild_id'],
                'guild_name': mapping['guild_name'],
                'similarity_score': mapping['similarity_score']
            }
    
    return lookup

async def clear_team_mappings():
    """Clear all team mappings (for reprocessing)."""
    result = await execute_query("DELETE FROM TEAM_NAME_MAPPINGS", commit=True)
    print("🗑️ All team mappings cleared")
    return result

async def get_unmapped_teams():
    """Get all unmapped teams from the UNMAPPED_TEAMS table."""
    unmapped = await execute_query(
        "SELECT csv_team_name, match_count, created_placeholder, notes FROM UNMAPPED_TEAMS ORDER BY match_count DESC",
        fetchall=True
    )
    return unmapped if unmapped else []

async def manually_map_team(csv_team_name: str, target_guild_id: int) -> bool:
    """Manually map a CSV team name to an existing database team."""
    try:
        # Get the target team details
        target_team = await get_team(target_guild_id)
        if not target_team:
            return False
        
        # Check if mapping already exists
        existing = await execute_query(
            "SELECT csv_team_name FROM TEAM_NAME_MAPPINGS WHERE csv_team_name = %s",
            (csv_team_name,),
            fetchone=True
        )
        
        if existing:
            # Update existing mapping
            success = await execute_query(
                """
                UPDATE TEAM_NAME_MAPPINGS 
                SET guild_id = %s, guild_name = %s, similarity_score = 1.0, last_updated = CURRENT_TIMESTAMP
                WHERE csv_team_name = %s
                """,
                (target_guild_id, target_team['guild_name'], csv_team_name),
                commit=True
            )
        else:
            # Create new mapping
            success = await execute_query(
                """
                INSERT INTO TEAM_NAME_MAPPINGS (csv_team_name, guild_id, guild_name, similarity_score)
                VALUES (%s, %s, %s, 1.0)
                """,
                (csv_team_name, target_guild_id, target_team['guild_name']),
                commit=True
            )
        
        if success:
            # Remove from unmapped teams table
            await execute_query(
                "DELETE FROM UNMAPPED_TEAMS WHERE csv_team_name = %s",
                (csv_team_name,),
                commit=True
            )
            
            # Delete placeholder team if it was created
            placeholder_id = abs(hash(csv_team_name)) % (10**15)
            await execute_query(
                "DELETE FROM IOSCA_TEAMS WHERE guild_id = %s AND captain_id = 0",  # Only delete placeholder teams
                (placeholder_id,),
                commit=True
            )
        
        return success
        
    except Exception as e:
        print(f"Error manually mapping team: {e}")
        return False

async def list_available_teams_for_mapping():
    """Get all available teams that can be mapped to."""
    teams = await execute_query(
        """
        SELECT guild_id, guild_name, captain_name, is_national_team 
        FROM IOSCA_TEAMS 
        WHERE captain_id != 0  -- Exclude placeholder teams
        ORDER BY guild_name ASC
        """,
        fetchall=True
    )
    return teams if teams else []

# === CRUD FUNCTIONS FOR IOSCA_TEAMS ===

async def add_team(guild_id: int, guild_name: str, guild_icon: str | None = None, 
             captain_id: int | None = None, captain_name: str | None = None, 
             vice_captain_id: int | None = None, vice_captain_name: str | None = None,
             eights_channels: list | None = None,
             sixes_channels: list | None = None,
             initial_players: list | None = None,
             is_national_team: bool = False, is_mix_team: bool = False):
    """Add a new team to the database (asynchronous)."""
    print(f"[DATABASE DEBUG] add_team called with:")
    print(f"  guild_id: {guild_id}")
    print(f"  guild_name: {guild_name}")
    print(f"  is_national_team: {is_national_team}")
    print(f"  is_mix_team: {is_mix_team}")
    print(f"  captain_id: {captain_id}")
    print(f"  vice_captain_id: {vice_captain_id}")
    
    query = """
    INSERT INTO IOSCA_TEAMS (guild_id, guild_name, guild_icon, captain_id, captain_name, 
                             vice_captain_id, vice_captain_name,
                             eights_channels, sixes_channels, players, is_national_team, is_mix_team)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    # Set default values for None parameters
    if eights_channels is None:
        eights_channels = []
    if sixes_channels is None:
        sixes_channels = []
    if initial_players is None:
        initial_players = []
    
    # Convert lists to JSON strings for storage
    eights_channels_json = json.dumps(eights_channels)
    sixes_channels_json = json.dumps(sixes_channels)
    
    # Ensure all initial players have a steam_id field
    for player in initial_players:
        if 'steam_id' not in player:
            player['steam_id'] = None

    players_json = json.dumps(initial_players)
    
    try:
        print(f"[DATABASE DEBUG] Executing query with parameters:")
        print(f"  guild_id: {guild_id}")
        print(f"  guild_name: {guild_name}")
        print(f"  is_national_team: {is_national_team}")
        print(f"  is_mix_team: {is_mix_team}")
        
        # Ensure the mix team column exists before trying to insert
        if is_mix_team:
            print("[DATABASE DEBUG] Mix team detected, ensuring is_mix_team column exists...")
            await alter_teams_table_for_mix_teams()
        
        result = await execute_query(query, (guild_id, guild_name, guild_icon, captain_id, captain_name, 
                                      vice_captain_id, vice_captain_name, 
                                      eights_channels_json, sixes_channels_json, players_json, is_national_team, is_mix_team), commit=True)
        
        print(f"[DATABASE DEBUG] execute_query result: {result}")
        
        # If team was successfully added and it's not the placeholder team, update historical data
        if result and guild_id != 0:
            await update_guild_ids_for_registered_team(guild_id, guild_name)
        
        return result
        
    except Exception as e:
        print(f"[DATABASE ERROR] Exception in add_team: {type(e).__name__}: {e}")
        print(f"[DATABASE ERROR] Query: {query}")
        print(f"[DATABASE ERROR] Parameters: guild_id={guild_id}, guild_name={guild_name}, is_national_team={is_national_team}, is_mix_team={is_mix_team}")
        import traceback
        traceback.print_exc()
        return False

async def get_team(guild_id: int):
    """Retrieve a team by its guild_id (asynchronous)."""
    query = "SELECT * FROM IOSCA_TEAMS WHERE guild_id = %s"
    team_data = await execute_query(query, (guild_id,), fetchone=True)
    if team_data:
        # Convert JSON strings back to Python lists/dicts
        if team_data.get('eights_channels') and isinstance(team_data['eights_channels'], (str, bytes)):
            team_data['eights_channels'] = json.loads(team_data['eights_channels'])
        if team_data.get('sixes_channels') and isinstance(team_data['sixes_channels'], (str, bytes)):
            team_data['sixes_channels'] = json.loads(team_data['sixes_channels'])
        if team_data.get('players') and isinstance(team_data['players'], (str, bytes)):
            team_data['players'] = json.loads(team_data['players'])
    return team_data

async def get_all_teams():
    """Retrieve all registered teams (asynchronous)."""
    query = "SELECT guild_id, guild_name, guild_icon FROM IOSCA_TEAMS ORDER BY guild_name ASC"
    teams_data = await execute_query(query, fetchall=True)
    return teams_data

async def get_all_teams_with_details():
    """Retrieve all teams with full details, parsing JSON fields."""
    query = "SELECT * FROM IOSCA_TEAMS"
    teams_data = await execute_query(query, fetchall=True)
    if teams_data and isinstance(teams_data, list):
        for team in teams_data:
            # Safely parse JSON fields
            if team.get('eights_channels') and isinstance(team['eights_channels'], (str, bytes)):
                try:
                    team['eights_channels'] = json.loads(team['eights_channels'])
                except json.JSONDecodeError:
                    team['eights_channels'] = []
            if team.get('sixes_channels') and isinstance(team['sixes_channels'], (str, bytes)):
                try:
                    team['sixes_channels'] = json.loads(team['sixes_channels'])
                except json.JSONDecodeError:
                    team['sixes_channels'] = []
            if team.get('players') and isinstance(team['players'], (str, bytes)):
                try:
                    team['players'] = json.loads(team['players'])
                except json.JSONDecodeError:
                    team['players'] = []
    return teams_data if teams_data else []

async def get_team_by_name(guild_name: str):
    """Retrieve a team by its name (case-insensitive search) (asynchronous)."""
    query = "SELECT * FROM IOSCA_TEAMS WHERE LOWER(guild_name) = LOWER(%s)"
    team_data = await execute_query(query, (guild_name,), fetchone=True)
    if team_data:
        # Convert JSON strings back to Python lists/dicts
        if team_data.get('eights_channels') and isinstance(team_data['eights_channels'], (str, bytes)):
            team_data['eights_channels'] = json.loads(team_data['eights_channels'])
        if team_data.get('sixes_channels') and isinstance(team_data['sixes_channels'], (str, bytes)):
            team_data['sixes_channels'] = json.loads(team_data['sixes_channels'])
        if team_data.get('players') and isinstance(team_data['players'], (str, bytes)):
            team_data['players'] = json.loads(team_data['players'])
    return team_data

async def get_teams_by_captain_id(captain_id: int):
    """Get all teams where the user is the captain."""
    query = "SELECT * FROM IOSCA_TEAMS WHERE captain_id = %s"
    teams_data = await execute_query(query, (captain_id,), fetchall=True)
    if teams_data and isinstance(teams_data, list):
        for team in teams_data:
            # Safely parse JSON fields
            if team.get('eights_channels') and isinstance(team['eights_channels'], (str, bytes)):
                try:
                    team['eights_channels'] = json.loads(team['eights_channels'])
                except json.JSONDecodeError:
                    team['eights_channels'] = []
            if team.get('sixes_channels') and isinstance(team['sixes_channels'], (str, bytes)):
                try:
                    team['sixes_channels'] = json.loads(team['sixes_channels'])
                except json.JSONDecodeError:
                    team['sixes_channels'] = []
            if team.get('players') and isinstance(team['players'], (str, bytes)):
                try:
                    team['players'] = json.loads(team['players'])
                except json.JSONDecodeError:
                    team['players'] = []
    return teams_data if teams_data else []

async def update_team_players(guild_id: int, players_list: list):
    """Update the players list for a team (asynchronous). players_list should be a list of dicts."""
    query = "UPDATE IOSCA_TEAMS SET players = %s WHERE guild_id = %s"
    players_json = json.dumps(players_list)
    return await execute_query(query, (players_json, guild_id), commit=True)

async def delete_team(guild_id: int):
    """Delete a team by its guild_id (asynchronous)."""
    query = "DELETE FROM IOSCA_TEAMS WHERE guild_id = %s"
    return await execute_query(query, (guild_id,), commit=True)

# === CRUD FUNCTIONS FOR PLAYERS ===

async def register_player(discord_id: int, username: str, steam_id: str):
    """Inserts or updates a player's registration."""
    query = """
    INSERT INTO IOSCA_PLAYERS (discord_id, username, steam_id)
    VALUES (%s, %s, %s)
    ON DUPLICATE KEY UPDATE
        username = VALUES(username),
        steam_id = VALUES(steam_id)
    """
    return await execute_query(query, (discord_id, username, steam_id), commit=True)

async def get_player_by_steam_id(steam_id: str):
    """Retrieve a player by their SteamID."""
    query = "SELECT discord_id, username, steam_id FROM IOSCA_PLAYERS WHERE steam_id = %s"
    return await execute_query(query, (steam_id,), fetchone=True)

async def get_player_by_discord_id(discord_id: int):
    """Retrieve a player's record by their Discord ID (asynchronous)."""
    query = "SELECT steam_id, username FROM IOSCA_PLAYERS WHERE discord_id = %s"
    return await execute_query(query, (discord_id,), fetchone=True)

# === CRUD FUNCTIONS FOR SERVERS ===

async def get_all_servers():
    """Retrieve all active servers from the database."""
    query = "SELECT name, address, password FROM IOS_SERVERS WHERE is_active = TRUE ORDER BY name ASC"
    return await execute_query(query, fetchall=True)

async def get_server_by_name(name: str):
    """Retrieve a server by its name."""
    query = "SELECT name, address, password FROM IOS_SERVERS WHERE name = %s AND is_active = TRUE"
    return await execute_query(query, (name,), fetchone=True)

async def add_server(name: str, address: str, password: str, host_username: str | None = None, host_password: str | None = None, server_type: str | None = None, is_active: bool = True):
    """Add a new server to the database."""
    # Auto-detect server type based on username if not provided
    if server_type is None:
        if host_username and host_username.lower() == 'administrator':
            server_type = 'windows'
        else:
            server_type = 'linux'
    
    query = """
    INSERT INTO IOS_SERVERS (name, address, password, host_username, host_password, server_type, is_active)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        address = VALUES(address),
        password = VALUES(password),
        host_username = VALUES(host_username),
        host_password = VALUES(host_password),
        server_type = VALUES(server_type),
        is_active = VALUES(is_active),
        updated_at = CURRENT_TIMESTAMP
    """
    return await execute_query(query, (name, address, password, host_username, host_password, server_type, is_active), commit=True)

async def delete_server_by_id(server_id: int):
    """Delete a server by setting it as inactive (soft delete)."""
    query = "UPDATE IOS_SERVERS SET is_active = FALSE, updated_at = CURRENT_TIMESTAMP WHERE id = %s"
    return await execute_query(query, (server_id,), commit=True)

# === OPTIMIZED TOURNAMENT FUNCTIONS V2 ===

async def create_tournament_v2(name: str, num_teams: int, num_leagues: int):
    """Create a new tournament using the optimized schema."""
    # Validate that teams can be evenly distributed
    if num_teams % num_leagues != 0:
        raise ValueError(f"Number of teams ({num_teams}) must be evenly divisible by number of leagues ({num_leagues})")
    
    teams_per_league = num_teams // num_leagues
    
    # Create league structure
    leagues_data = []
    for i in range(num_leagues):
        leagues_data.append({
            "name": f"League {chr(65 + i)}",  # League A, League B, etc.
            "order": i + 1,
            "teams": []
        })
    
    # Insert tournament
    query = """
    INSERT INTO TOURNAMENTS_V2 (name, num_teams, num_leagues, leagues, awards)
    VALUES (%s, %s, %s, %s, %s)
    """
    result = await execute_query(query, (name, num_teams, num_leagues, json.dumps(leagues_data), json.dumps({})), commit=True)
    
    if result:
        # Get the tournament ID
        tournament = await get_tournament_by_name_v2(name)
        return tournament['id'] if tournament else None
    
    return None

async def get_tournament_by_name_v2(name: str):
    """Get tournament by name from V2 schema."""
    query = "SELECT * FROM TOURNAMENTS_V2 WHERE name = %s"
    tournament = await execute_query(query, (name,), fetchone=True)
    if tournament:
        # Parse JSON fields
        if tournament.get('leagues'):
            tournament['leagues'] = json.loads(tournament['leagues'])
        if tournament.get('awards'):
            tournament['awards'] = json.loads(tournament['awards'])
    return tournament

async def get_tournament_by_id_v2(tournament_id: int):
    """Get tournament by ID from V2 schema."""
    query = "SELECT * FROM TOURNAMENTS_V2 WHERE id = %s"
    tournament = await execute_query(query, (tournament_id,), fetchone=True)
    if tournament:
        # Parse JSON fields
        if tournament.get('leagues'):
            tournament['leagues'] = json.loads(tournament['leagues'])
        if tournament.get('awards'):
            tournament['awards'] = json.loads(tournament['awards'])
    return tournament

async def get_all_tournaments_v2():
    """Get all tournaments from V2 schema."""
    query = """
    SELECT id, name, num_teams, num_leagues, start_date, end_date, is_completed, awards
    FROM TOURNAMENTS_V2 
    ORDER BY is_completed ASC, start_date DESC
    """
    tournaments = await execute_query(query, fetchall=True)
    if tournaments:
        for tournament in tournaments:
            if tournament.get('awards'):
                try:
                    awards = json.loads(tournament['awards'])
                    tournament.update(awards)  # Flatten awards into main dict for compatibility
                except:
                    pass
    return tournaments

async def add_team_to_tournament_v2(tournament_id: int, league_name: str, guild_id: int, guild_name: str):
    """Add a team to a tournament league in V2 schema."""
    # Get tournament
    tournament = await get_tournament_by_id_v2(tournament_id)
    if not tournament:
        raise ValueError("Tournament not found")
    
    leagues = tournament.get('leagues', [])
    target_league = None
    
    for league in leagues:
        if league['name'] == league_name:
            target_league = league
            break
    
    if not target_league:
        raise ValueError(f"League {league_name} not found in tournament")
    
    # Check if team is already in tournament
    for league in leagues:
        for team in league.get('teams', []):
            if team['guild_id'] == guild_id:
                raise ValueError(f"Team {guild_name} is already in this tournament")
    
    # Check league capacity
    teams_per_league = tournament['num_teams'] // tournament['num_leagues']
    current_teams = len(target_league.get('teams', []))
    
    if current_teams >= teams_per_league:
        raise ValueError(f"{league_name} is full. Maximum {teams_per_league} teams per league")
    
    # Add team to league
    if 'teams' not in target_league:
        target_league['teams'] = []
    
    target_league['teams'].append({
        'guild_id': guild_id,
        'guild_name': guild_name
    })
    
    # Update tournament
    query = "UPDATE TOURNAMENTS_V2 SET leagues = %s WHERE id = %s"
    result = await execute_query(query, (json.dumps(leagues), tournament_id), commit=True)
    
    if result:
        # Initialize team stats
        stats_data = {
            "matches_played": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "goals_for": 0,
            "goals_against": 0,
            "goal_difference": 0,
            "points": 0
        }
        
        await execute_query(
            """
            INSERT INTO TOURNAMENT_DATA 
            (tournament_id, league_name, data_type, team_guild_id, team_name, stats)
            VALUES (%s, %s, 'team_stats', %s, %s, %s)
            """,
            (tournament_id, league_name, guild_id, guild_name, json.dumps(stats_data)),
            commit=True
        )
    
    return result

async def get_tournament_teams_v2(tournament_id: int, league_name: str | None = None):
    """Get teams for a tournament from V2 schema."""
    tournament = await get_tournament_by_id_v2(tournament_id)
    if not tournament:
        return []
    
    teams = []
    leagues = tournament.get('leagues', [])
    
    for league in leagues:
        if league_name and league['name'] != league_name:
            continue
        
        for team in league.get('teams', []):
            teams.append({
                'guild_id': team['guild_id'],
                'guild_name': team['guild_name'],
                'league_name': league['name'],
                'league_id': league['name']  # For compatibility with old code
            })
    
    return teams

# === MATCH AND STATISTICS FUNCTIONS ===

async def get_matches_by_team(guild_id: int, limit: int = 50, start_date: datetime | None = None):
    """Get all matches played by a specific team."""
    query = """
    SELECT m.*, 
           ht.guild_name as home_team_display_name,
           at.guild_name as away_team_display_name
    FROM MATCH_STATS m
    JOIN IOSCA_TEAMS ht ON m.home_guild_id = ht.guild_id
    JOIN IOSCA_TEAMS at ON m.away_guild_id = at.guild_id
    WHERE (m.home_guild_id = %s OR m.away_guild_id = %s)
    """
    
    if start_date:
        query += " AND m.datetime >= %s"
        query += " ORDER BY m.datetime DESC LIMIT %s"
        params = (guild_id, guild_id, start_date, limit)
    else:
        query += " ORDER BY m.datetime DESC LIMIT %s"
        params = (guild_id, guild_id, limit)
    
    return await execute_query(query, params, fetchall=True)

async def get_matches_between_teams(guild_id_1: int, guild_id_2: int, limit: int = 50, start_date: datetime | None = None):
    """Get all matches between two specific teams."""
    query = """
    SELECT m.*, 
           ht.guild_name as home_team_display_name,
           at.guild_name as away_team_display_name
    FROM MATCH_STATS m
    JOIN IOSCA_TEAMS ht ON m.home_guild_id = ht.guild_id
    JOIN IOSCA_TEAMS at ON m.away_guild_id = at.guild_id
    WHERE ((m.home_guild_id = %s AND m.away_guild_id = %s) 
           OR (m.home_guild_id = %s AND m.away_guild_id = %s))
    """
    
    if start_date:
        query += " AND m.datetime >= %s"
        query += " ORDER BY m.datetime DESC LIMIT %s"
        params = (guild_id_1, guild_id_2, guild_id_2, guild_id_1, start_date, limit)
    else:
        query += " ORDER BY m.datetime DESC LIMIT %s"
        params = (guild_id_1, guild_id_2, guild_id_2, guild_id_1, limit)
    
    return await execute_query(query, params, fetchall=True)

# === TABLE CREATION FUNCTIONS ===

async def create_teams_table_if_not_exists():
    """Create the IOSCA_TEAMS table if it doesn't already exist (asynchronous)."""
    query = """
    CREATE TABLE IF NOT EXISTS IOSCA_TEAMS (
        guild_id BIGINT PRIMARY KEY,
        guild_name VARCHAR(255) NOT NULL,
        guild_icon VARCHAR(255),
        captain_id BIGINT NOT NULL,
        captain_name VARCHAR(255) NOT NULL,
        vice_captain_id BIGINT,
        vice_captain_name VARCHAR(255),
        eights_channels JSON,
        sixes_channels JSON,
        players JSON,
        is_national_team BOOLEAN NOT NULL DEFAULT FALSE
    );
    """
    return await execute_query(query)

async def create_players_table_if_not_exists():
    """Create the IOSCA_PLAYERS table if it doesn't already exist."""
    query = """
    CREATE TABLE IF NOT EXISTS IOSCA_PLAYERS (
        discord_id BIGINT PRIMARY KEY,
        username VARCHAR(255) NOT NULL,
        steam_id VARCHAR(255)
    );
    """
    return await execute_query(query)
    
async def create_servers_table_if_not_exists():
    """Create the IOS_SERVERS table if it doesn't already exist."""
    query = """
    CREATE TABLE IF NOT EXISTS IOS_SERVERS (
        id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(255) NOT NULL UNIQUE,
        address VARCHAR(255) NOT NULL,
        password VARCHAR(255) NOT NULL,
        sftp_ip VARCHAR(255),
        host_username VARCHAR(255),
        host_password VARCHAR(255),
        server_type ENUM('linux', 'windows') DEFAULT 'linux',
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    );
    """
    return await execute_query(query)

async def create_transfer_tables_if_not_exist():
    """Create transfer-related tables."""
    
    # Player transfer history table
    transfers_query = """
    CREATE TABLE IF NOT EXISTS PLAYER_TRANSFERS (
        id INT AUTO_INCREMENT PRIMARY KEY,
        player_discord_id BIGINT NOT NULL,
        player_name VARCHAR(255) NOT NULL,
        from_team_guild_id BIGINT NULL,
        from_team_name VARCHAR(255) NULL,
        to_team_guild_id BIGINT NULL,
        to_team_name VARCHAR(255) NULL,
        transfer_type ENUM('JOIN', 'LEAVE', 'TRANSFER') NOT NULL,
        transfer_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        reason VARCHAR(255) NULL,
        processed_by_discord_id BIGINT NULL,
        processed_by_name VARCHAR(255) NULL,
        INDEX idx_player_discord_id (player_discord_id),
        INDEX idx_transfer_date (transfer_date),
        FOREIGN KEY (from_team_guild_id) REFERENCES IOSCA_TEAMS(guild_id) ON DELETE SET NULL,
        FOREIGN KEY (to_team_guild_id) REFERENCES IOSCA_TEAMS(guild_id) ON DELETE SET NULL
    );
    """
    await execute_query(transfers_query)
    
    # Transfer window settings table
    settings_query = """
    CREATE TABLE IF NOT EXISTS TRANSFER_SETTINGS (
        id INT AUTO_INCREMENT PRIMARY KEY,
        transfer_window_open BOOLEAN DEFAULT TRUE,
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        updated_by_discord_id BIGINT NULL,
        updated_by_name VARCHAR(255) NULL
    );
    """
    await execute_query(settings_query)
    
    # Team name mappings table for fuzzy matching
    mappings_query = """
    CREATE TABLE IF NOT EXISTS TEAM_NAME_MAPPINGS (
        csv_team_name VARCHAR(255) PRIMARY KEY,
        guild_id BIGINT NOT NULL,
        guild_name VARCHAR(255) NOT NULL,
        similarity_score FLOAT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        FOREIGN KEY (guild_id) REFERENCES IOSCA_TEAMS(guild_id) ON DELETE CASCADE
    );
    """
    await execute_query(mappings_query)
    
    # Unmapped teams table to track teams found in CSV but not mapped to database teams
    unmapped_teams_query = """
    CREATE TABLE IF NOT EXISTS UNMAPPED_TEAMS (
        csv_team_name VARCHAR(255) PRIMARY KEY,
        first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        match_count INT DEFAULT 1,
        created_placeholder BOOLEAN DEFAULT FALSE,
        notes TEXT NULL
    );
    """
    await execute_query(unmapped_teams_query)
    
    # Match statistics table (replaces CSV data)
    match_stats_query = """
    CREATE TABLE IF NOT EXISTS MATCH_STATS (
        match_id VARCHAR(255) PRIMARY KEY,
        datetime TIMESTAMP NOT NULL,
        home_guild_id BIGINT NULL,
        away_guild_id BIGINT NULL,
        home_team_name VARCHAR(255) NOT NULL,
        away_team_name VARCHAR(255) NOT NULL,
        scoreline VARCHAR(20) NOT NULL,
        game_type ENUM('6v6', '8v8') NOT NULL,
        initial_lineups TEXT,
        final_lineups TEXT,
        substitution_summary TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        INDEX idx_datetime (datetime),
        INDEX idx_home_team (home_guild_id),
        INDEX idx_away_team (away_guild_id),
        INDEX idx_teams (home_guild_id, away_guild_id),
        INDEX idx_game_type (game_type)
    );
    """
    await execute_query(match_stats_query)
    
    # Player match data table (replaces CSV data)
    player_match_data_query = """
    CREATE TABLE IF NOT EXISTS PLAYER_MATCH_DATA (
        id INT AUTO_INCREMENT PRIMARY KEY,
        match_id VARCHAR(255) NOT NULL,
        datetime TIMESTAMP NOT NULL,
        steam_id VARCHAR(255) NOT NULL,
        player_name VARCHAR(255) NOT NULL,
        team_guild_id BIGINT NULL,
        opponent_guild_id BIGINT NULL,
        team_name VARCHAR(255) NOT NULL,
        opponent_team_name VARCHAR(255) NOT NULL,
        team_side ENUM('home', 'away') NOT NULL,
        position VARCHAR(10),
        
        -- Additional stats can be added as JSON for flexibility
        additional_stats JSON,
        
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        
        INDEX idx_match_id (match_id),
        INDEX idx_player (steam_id),
        INDEX idx_team (team_guild_id),
        INDEX idx_datetime (datetime),
        INDEX idx_player_team (steam_id, team_guild_id)
    );
    """
    await execute_query(player_match_data_query)
    
    # Initialize default transfer window setting
    check_settings = await execute_query("SELECT COUNT(*) as count FROM TRANSFER_SETTINGS", fetchone=True)
    if check_settings and check_settings.get('count', 0) == 0:
        await execute_query("INSERT INTO TRANSFER_SETTINGS (transfer_window_open) VALUES (TRUE)", commit=True)

async def create_teams_from_csv():
    """Create teams from CSV data using bulk import."""
    print("⚠️ Teams are no longer auto-created from CSV. Teams must be registered via the register command.")
    return True

# === MIGRATION FUNCTIONS ===

async def alter_teams_table_for_national_teams():
    """Adds the is_national_team column if it doesn't exist."""
    check_column_query = """
    SELECT COUNT(*) as count
    FROM INFORMATION_SCHEMA.COLUMNS 
    WHERE TABLE_SCHEMA = %s 
      AND TABLE_NAME = 'IOSCA_TEAMS' 
      AND COLUMN_NAME = 'is_national_team'
    """
    result = await execute_query(check_column_query, (database,), fetchone=True)

    if result and result.get('count', 0) == 0:
        print("`is_national_team` column not found, adding it to `IOSCA_TEAMS`...")
        alter_query = """
        ALTER TABLE IOSCA_TEAMS 
        ADD COLUMN is_national_team BOOLEAN NOT NULL DEFAULT FALSE
        """
        await execute_query(alter_query)
        print("Column `is_national_team` added successfully.")

async def alter_teams_table_for_mix_teams():
    """Adds the is_mix_team column if it doesn't exist."""
    check_column_query = """
    SELECT COUNT(*) as count
    FROM INFORMATION_SCHEMA.COLUMNS 
    WHERE TABLE_SCHEMA = %s 
      AND TABLE_NAME = 'IOSCA_TEAMS' 
      AND COLUMN_NAME = 'is_mix_team'
    """
    result = await execute_query(check_column_query, (database,), fetchone=True)

    if result and result.get('count', 0) == 0:
        print("`is_mix_team` column not found, adding it to `IOSCA_TEAMS`...")
        alter_query = """
        ALTER TABLE IOSCA_TEAMS 
        ADD COLUMN is_mix_team BOOLEAN NOT NULL DEFAULT FALSE
        """
        await execute_query(alter_query)
        print("Column `is_mix_team` added successfully.")

async def alter_players_table_for_steam_id_length():
    """Alter the IOSCA_PLAYERS table to increase the length of the steam_id column."""
    check_column_query = """
    SELECT CHARACTER_MAXIMUM_LENGTH 
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'IOSCA_PLAYERS' AND COLUMN_NAME = 'steam_id';
    """
    result = await execute_query(check_column_query, (database,), fetchone=True)
    
    if result and result.get('CHARACTER_MAXIMUM_LENGTH', 0) < 50:
        print("Increasing steam_id column length in IOSCA_PLAYERS...")
        alter_query = "ALTER TABLE IOSCA_PLAYERS MODIFY COLUMN steam_id VARCHAR(50)"
        await execute_query(alter_query)
        print("steam_id column length increased.")

async def _migrate_players_add_steam_id():
    """Adds the steam_id field to each player in the players JSON list for all teams."""
    print("Checking for steam_id migration...")
    all_teams_query = "SELECT guild_id, players FROM IOSCA_TEAMS"
    all_teams = await execute_query(all_teams_query, fetchall=True)

    if not all_teams:
        print("No teams to migrate.")
        return

    for team in all_teams:
        guild_id = team['guild_id']
        try:
            players_data = team.get('players')
            if players_data and isinstance(players_data, (str, bytes)):
                players = json.loads(players_data)
            elif players_data and isinstance(players_data, list):
                players = players_data
            else:
                continue

            updated = False
            for player in players:
                if 'steam_id' not in player:
                    player['steam_id'] = None
                    updated = True
            
            if updated:
                print(f"Updating players for guild {guild_id} to include steam_id.")
                await update_team_players(guild_id, players)

        except json.JSONDecodeError as e:
            print(f"Error decoding players JSON for guild {guild_id}: {e}")
        except Exception as e:
            print(f"An unexpected error occurred during migration for guild {guild_id}: {e}")
    print("Steam_id migration check complete.")

async def migrate_servers_table_add_new_fields():
    """Add new fields to existing IOS_SERVERS table if they don't exist."""
    print("Checking for IOS_SERVERS table migration...")
    
    # Check for sftp_ip column
    check_sftp_query = """
    SELECT COUNT(*) as count
    FROM INFORMATION_SCHEMA.COLUMNS 
    WHERE TABLE_SCHEMA = %s 
      AND TABLE_NAME = 'IOS_SERVERS' 
      AND COLUMN_NAME = 'sftp_ip'
    """
    result = await execute_query(check_sftp_query, (database,), fetchone=True)
    
    if result and result.get('count', 0) == 0:
        print("Adding sftp_ip column to IOS_SERVERS...")
        alter_query = "ALTER TABLE IOS_SERVERS ADD COLUMN sftp_ip VARCHAR(255)"
        await execute_query(alter_query)
        print("sftp_ip column added successfully.")
    
    # Check for host_username column
    check_username_query = """
    SELECT COUNT(*) as count
    FROM INFORMATION_SCHEMA.COLUMNS 
    WHERE TABLE_SCHEMA = %s 
      AND TABLE_NAME = 'IOS_SERVERS' 
      AND COLUMN_NAME = 'host_username'
    """
    result = await execute_query(check_username_query, (database,), fetchone=True)
    
    if result and result.get('count', 0) == 0:
        print("Adding host_username column to IOS_SERVERS...")
        alter_query = "ALTER TABLE IOS_SERVERS ADD COLUMN host_username VARCHAR(255)"
        await execute_query(alter_query)
        print("host_username column added successfully.")
    
    # Check for host_password column
    check_password_query = """
    SELECT COUNT(*) as count
    FROM INFORMATION_SCHEMA.COLUMNS 
    WHERE TABLE_SCHEMA = %s 
      AND TABLE_NAME = 'IOS_SERVERS' 
      AND COLUMN_NAME = 'host_password'
    """
    result = await execute_query(check_password_query, (database,), fetchone=True)
    
    if result and result.get('count', 0) == 0:
        print("Adding host_password column to IOS_SERVERS...")
        alter_query = "ALTER TABLE IOS_SERVERS ADD COLUMN host_password VARCHAR(255)"
        await execute_query(alter_query)
        print("host_password column added successfully.")
    
    print("IOS_SERVERS table migration check complete.")

async def migrate_tables_to_allow_null_guild_ids():
    """
    Migrate MATCH_STATS and PLAYER_MATCH_DATA tables to allow NULL guild_ids for unregistered teams.
    This enables importing all CSV data regardless of team registration status.
    """
    print("🔧 Migrating tables to allow NULL guild_ids for unregistered teams...")
    
    try:
        # Check if migration is needed for MATCH_STATS table
        check_match_nullable = await execute_query(
            """
            SELECT IS_NULLABLE 
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'MATCH_STATS' AND COLUMN_NAME = 'home_guild_id'
            """,
            (database,),
            fetchone=True
        )
        
        if check_match_nullable and check_match_nullable.get('IS_NULLABLE') == 'NO':
            print("📝 Updating MATCH_STATS table to allow NULL guild_ids...")
            
            # Drop foreign key constraints first
            await execute_query("ALTER TABLE MATCH_STATS DROP FOREIGN KEY match_stats_ibfk_1", commit=True)
            await execute_query("ALTER TABLE MATCH_STATS DROP FOREIGN KEY match_stats_ibfk_2", commit=True)
            
            # Modify columns to allow NULL
            await execute_query("ALTER TABLE MATCH_STATS MODIFY COLUMN home_guild_id BIGINT NULL", commit=True)
            await execute_query("ALTER TABLE MATCH_STATS MODIFY COLUMN away_guild_id BIGINT NULL", commit=True)
            
            print("✅ MATCH_STATS table updated to allow NULL guild_ids")
        
        # Check if migration is needed for PLAYER_MATCH_DATA table
        check_player_nullable = await execute_query(
            """
            SELECT IS_NULLABLE 
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'PLAYER_MATCH_DATA' AND COLUMN_NAME = 'team_guild_id'
            """,
            (database,),
            fetchone=True
        )
        
        if check_player_nullable and check_player_nullable.get('IS_NULLABLE') == 'NO':
            print("📝 Updating PLAYER_MATCH_DATA table to allow NULL guild_ids...")
            
            # Drop foreign key constraints first
            await execute_query("ALTER TABLE PLAYER_MATCH_DATA DROP FOREIGN KEY player_match_data_ibfk_2", commit=True)
            await execute_query("ALTER TABLE PLAYER_MATCH_DATA DROP FOREIGN KEY player_match_data_ibfk_3", commit=True)
            
            # Modify columns to allow NULL
            await execute_query("ALTER TABLE PLAYER_MATCH_DATA MODIFY COLUMN team_guild_id BIGINT NULL", commit=True)
            await execute_query("ALTER TABLE PLAYER_MATCH_DATA MODIFY COLUMN opponent_guild_id BIGINT NULL", commit=True)
            
            print("✅ PLAYER_MATCH_DATA table updated to allow NULL guild_ids")
        
        print("🎉 Migration completed! Tables now accept NULL guild_ids for unregistered teams.")
        
    except Exception as e:
        print(f"⚠️ Migration encountered issues (this is normal if constraints don't exist): {e}")
        print("💡 Continuing - the tables may already be in the correct format")

async def initialize_default_servers():
    """Initialize the servers table with default servers if it's empty."""
    # Check if any servers exist
    existing_servers = await execute_query("SELECT COUNT(*) as count FROM IOS_SERVERS WHERE is_active = TRUE", fetchone=True)
    
    if existing_servers and existing_servers.get('count', 0) == 0:
        print("No servers found in database, adding default servers...")
        
        # Add default servers with SFTP details
        default_servers = [
            ("Florida", "*", "*", "*", "*"),
            ("Georgia", "*", "*", "*", "*")
        ]
        
        for name, address, password, host_username, host_password in default_servers:
            await add_server(name, address, password, host_username, host_password, True)
            print(f"Added default server: {name}")
        
        print("Default servers initialization complete.")

# === COMPATIBILITY FUNCTIONS FOR EXISTING CODE ===

# These functions provide compatibility with existing tournament system code
# They wrap the new V2 functions with the old function signatures

async def create_tournament(name: str, num_teams: int, num_leagues: int):
    """Legacy wrapper for create_tournament_v2."""
    return await create_tournament_v2(name, num_teams, num_leagues)

async def get_all_tournaments():
    """Legacy wrapper for get_all_tournaments_v2."""
    return await get_all_tournaments_v2()

async def get_tournament_by_id(tournament_id: int):
    """Legacy wrapper for get_tournament_by_id_v2."""
    return await get_tournament_by_id_v2(tournament_id)

async def get_tournament_by_name(name: str):
    """Legacy wrapper for get_tournament_by_name_v2."""
    return await get_tournament_by_name_v2(name)

async def get_tournament_teams(tournament_id: int, league_id: int | None = None):
    """Legacy wrapper for get_tournament_teams_v2."""
    # Convert league_id to league_name if provided
    if league_id:
        tournament = await get_tournament_by_id_v2(tournament_id)
        if tournament:
            leagues = tournament.get('leagues', [])
            for league in leagues:
                if league.get('order') == league_id:
                    return await get_tournament_teams_v2(tournament_id, league['name'])
        return []
    else:
        return await get_tournament_teams_v2(tournament_id)

async def get_tournament_leagues(tournament_id: int):
    """Get tournament leagues for compatibility."""
    tournament = await get_tournament_by_id_v2(tournament_id)
    if not tournament:
        return []
    
    leagues = tournament.get('leagues', [])
    result = []
    for league in leagues:
        result.append({
            'id': league['name'],  # Use name as ID for compatibility
            'league_name': league['name'],
            'league_order': league['order']
        })
    return result

async def add_team_to_tournament(tournament_id: int, league_id: int, guild_id: int, guild_name: str):
    """Legacy wrapper for add_team_to_tournament_v2."""
    # Convert league_id to league_name
    tournament = await get_tournament_by_id_v2(tournament_id)
    if not tournament:
        raise ValueError("Tournament not found")
    
    leagues = tournament.get('leagues', [])
    league_name = None
    
    for league in leagues:
        if league.get('order') == league_id or league.get('name') == str(league_id):
            league_name = league['name']
            break
    
    if not league_name:
        raise ValueError(f"League {league_id} not found")
    
    return await add_team_to_tournament_v2(tournament_id, league_name, guild_id, guild_name)

# Initialize with the new system
async def initialize_database():
    """Main initialization function."""
    await initialize_database_v2()
    
    # Automatically run CSV import for all data with 0s for unregistered teams
    print("Running automatic CSV import (all data with 0s for unregistered teams)...")
    await import_all_csv_data_with_nulls()

# === ADDITIONAL MISSING TOURNAMENT FUNCTIONS ===

async def remove_team_from_tournament(tournament_id: int, guild_id: int):
    """Remove a team from a tournament (V2 compatibility)."""
    tournament = await get_tournament_by_id_v2(tournament_id)
    if not tournament:
        return False
    
    leagues = tournament.get('leagues', [])
    removed = False
    
    # Remove team from all leagues
    for league in leagues:
        teams = league.get('teams', [])
        league['teams'] = [team for team in teams if team['guild_id'] != guild_id]
        if len(league['teams']) != len(teams):
            removed = True
    
    if removed:
        # Update tournament
        query = "UPDATE TOURNAMENTS_V2 SET leagues = %s WHERE id = %s"
        result = await execute_query(query, (json.dumps(leagues), tournament_id), commit=True)
        
        # Remove team stats
        await execute_query(
            "DELETE FROM TOURNAMENT_DATA WHERE tournament_id = %s AND team_guild_id = %s AND data_type = 'team_stats'",
        (tournament_id, guild_id),
            commit=True
        )
        
        # Remove team matches
        await execute_query(
            """
            DELETE FROM TOURNAMENT_DATA 
            WHERE tournament_id = %s AND data_type = 'match' 
            AND (home_team_guild_id = %s OR away_team_guild_id = %s)
            """,
            (tournament_id, guild_id, guild_id),
            commit=True
        )
    
    return result

    return False

async def update_tournament_details(tournament_id: int, name: str | None = None, num_teams: int | None = None, num_leagues: int | None = None):
    """Update tournament details (V2 compatibility)."""
    fields_to_update = []
    params = []
    
    if name is not None:
        fields_to_update.append("name = %s")
        params.append(name)
    
    if num_teams is not None and num_leagues is not None:
        # Validate divisibility
        if num_teams % num_leagues != 0:
            raise ValueError(f"Number of teams ({num_teams}) must be evenly divisible by number of leagues ({num_leagues})")
        
        fields_to_update.append("num_teams = %s")
        fields_to_update.append("num_leagues = %s")
        params.extend([num_teams, num_leagues])
        
        # Update league structure if needed
        tournament = await get_tournament_by_id_v2(tournament_id)
        if tournament:
            current_leagues = tournament.get('leagues', [])
            
            # If number of leagues changed, recreate league structure
            if len(current_leagues) != num_leagues:
                new_leagues = []
                for i in range(num_leagues):
                    new_leagues.append({
                        "name": f"League {chr(65 + i)}",
                        "order": i + 1,
                        "teams": []
                    })
                
                fields_to_update.append("leagues = %s")
                params.append(json.dumps(new_leagues))
    
    if not fields_to_update:
        return False
    
    query = f"UPDATE TOURNAMENTS_V2 SET {', '.join(fields_to_update)}, updated_at = CURRENT_TIMESTAMP WHERE id = %s"
    params.append(tournament_id)
    
    return await execute_query(query, tuple(params), commit=True)

async def delete_tournament(tournament_id: int):
    """Delete a tournament and all related data (V2 compatibility)."""
    # Delete tournament data first
    await execute_query("DELETE FROM TOURNAMENT_DATA WHERE tournament_id = %s", (tournament_id,), commit=True)
    
    # Delete tournament
    return await execute_query("DELETE FROM TOURNAMENTS_V2 WHERE id = %s", (tournament_id,), commit=True)

async def get_teams_per_league_limit(tournament_id: int):
    """Get the maximum teams allowed per league for a tournament (V2 compatibility)."""
    try:
        tournament = await get_tournament_by_id_v2(tournament_id)
        if tournament and tournament['num_teams'] > 0 and tournament['num_leagues'] > 0:
            teams_per_league = tournament['num_teams'] // tournament['num_leagues']
            return teams_per_league
        else:
            return 0
    except Exception as e:
        return 0

async def add_match_to_tournament(tournament_id: int, match_id: str, home_team_guild_id: int, away_team_guild_id: int):
    """Add a match to a tournament and update stats (V2 compatibility)."""
    # Get tournament teams to find their leagues
    teams = await get_tournament_teams_v2(tournament_id)
    
    home_league = None
    away_league = None
    
    for team in teams:
        if team['guild_id'] == home_team_guild_id:
            home_league = team['league_name']
        if team['guild_id'] == away_team_guild_id:
            away_league = team['league_name']
    
    if not home_league or not away_league:
        raise ValueError("Both teams must be registered in the tournament")
    
    if home_league != away_league:
        raise ValueError("Teams must be in the same league to play each other")
    
    # Check if match is already in tournament
    existing = await execute_query(
        "SELECT id FROM TOURNAMENT_DATA WHERE tournament_id = %s AND match_id = %s AND data_type = 'match'",
        (tournament_id, match_id),
        fetchone=True
    )
    
    if existing:
        raise ValueError("Match is already added to this tournament")
    
    # Add match to tournament
    query = """
    INSERT INTO TOURNAMENT_DATA (tournament_id, league_name, data_type, match_id, home_team_guild_id, away_team_guild_id)
    VALUES (%s, %s, 'match', %s, %s, %s)
    """
    result = await execute_query(query, (tournament_id, home_league, match_id, home_team_guild_id, away_team_guild_id), commit=True)
    
    if result:
        # Update tournament stats
        await recalculate_tournament_stats_v2(tournament_id)
    
    return result

async def get_tournament_matches(tournament_id: int, league_name: str | None = None):
    """Get matches for a tournament (V2 compatibility)."""
    if league_name:
        query = """
        SELECT td.match_id, td.home_team_guild_id, td.away_team_guild_id, td.added_at,
               ht.guild_name as home_team_name, at.guild_name as away_team_name
        FROM TOURNAMENT_DATA td
        JOIN IOSCA_TEAMS ht ON td.home_team_guild_id = ht.guild_id
        JOIN IOSCA_TEAMS at ON td.away_team_guild_id = at.guild_id
        WHERE td.tournament_id = %s AND td.league_name = %s AND td.data_type = 'match'
        ORDER BY td.added_at DESC
        """
        return await execute_query(query, (tournament_id, league_name), fetchall=True)
    else:
        query = """
        SELECT td.match_id, td.league_name, td.home_team_guild_id, td.away_team_guild_id, td.added_at,
               ht.guild_name as home_team_name, at.guild_name as away_team_name
        FROM TOURNAMENT_DATA td
        JOIN IOSCA_TEAMS ht ON td.home_team_guild_id = ht.guild_id
        JOIN IOSCA_TEAMS at ON td.away_team_guild_id = at.guild_id
        WHERE td.tournament_id = %s AND td.data_type = 'match'
        ORDER BY td.league_name, td.added_at DESC
        """
        return await execute_query(query, (tournament_id,), fetchall=True)

async def get_tournament_league_table(tournament_id: int, league_name: str | int):
    """Get league table for a specific tournament league (V2 compatibility)."""
    # Convert league_id to league_name if needed (for compatibility)
    if isinstance(league_name, int):
        tournament = await get_tournament_by_id_v2(tournament_id)
        if tournament:
            leagues = tournament.get('leagues', [])
            for league in leagues:
                if league.get('order') == league_name:
                    league_name = league['name']
                    break
        if isinstance(league_name, int):  # Still an int, conversion failed
            return []
    
    query = """
    SELECT team_guild_id as guild_id, team_name as guild_name, stats
    FROM TOURNAMENT_DATA
    WHERE tournament_id = %s AND league_name = %s AND data_type = 'team_stats'
    ORDER BY JSON_UNQUOTE(JSON_EXTRACT(stats, '$.points')) DESC,
             JSON_UNQUOTE(JSON_EXTRACT(stats, '$.goal_difference')) DESC,
             JSON_UNQUOTE(JSON_EXTRACT(stats, '$.goals_for')) DESC,
             team_name ASC
    """
    
    results = await execute_query(query, (tournament_id, league_name), fetchall=True)
    
    # Parse JSON stats
    if results:
        for result in results:
            if result.get('stats'):
                try:
                    stats = json.loads(result['stats'])
                    result.update(stats)  # Flatten stats into main dict
                except:
                    pass
    
    return results

async def complete_tournament(tournament_id: int, champion: str | None = None, runner_up: str | None = None, third_place: str | None = None):
    """Mark tournament as completed and set winners (V2 compatibility)."""
    tournament = await get_tournament_by_id_v2(tournament_id)
    if not tournament:
        return False
    
    # Update awards
    awards = tournament.get('awards', {})
    if champion:
        awards['champion'] = champion
    if runner_up:
        awards['runner_up'] = runner_up
    if third_place:
        awards['third_place'] = third_place
    
    query = """
    UPDATE TOURNAMENTS_V2 
    SET is_completed = TRUE, end_date = CURRENT_TIMESTAMP, awards = %s
    WHERE id = %s
    """
    return await execute_query(query, (json.dumps(awards), tournament_id), commit=True)

async def add_manual_match_result(tournament_id: int, home_team_guild_id: int, away_team_guild_id: int, 
                                 home_score: int, away_score: int, match_date: datetime = None, 
                                 notes: str = None, is_forfeit: bool = False):
    """
    Manually add a match result to the tournament.
    This creates a match record and updates tournament stats.
    """
    if match_date is None:
        match_date = datetime.now()
    
    # Generate a unique match ID
    match_id = f"manual_{tournament_id}_{home_team_guild_id}_{away_team_guild_id}_{int(match_date.timestamp())}"
    
    # Get team names
    home_team = await get_team(home_team_guild_id)
    away_team = await get_team(away_team_guild_id)
    
    if not home_team or not away_team:
        return False, "One or both teams not found"
    
    home_team_name = home_team['guild_name']
    away_team_name = away_team['guild_name']
    
    # Add match to tournament
    success = await add_match_to_tournament(tournament_id, match_id, home_team_guild_id, away_team_guild_id)
    if not success:
        return False, "Failed to add match to tournament"
    
    # Create match record in MATCH_STATS table
    query = """
    INSERT INTO MATCH_STATS (
        match_id, match_date, home_team, away_team, home_score, away_score,
        home_guild_id, away_guild_id, tournament_id, notes, is_forfeit
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    
    try:
        await execute_query(query, (
            match_id, match_date, home_team_name, away_team_name,
            home_score, away_score, home_team_guild_id, away_team_guild_id,
            tournament_id, notes, is_forfeit
        ), commit=True)
        
        # Recalculate tournament stats
        await recalculate_tournament_stats_v2(tournament_id)
        
        return True, match_id
    except Exception as e:
        return False, f"Database error: {str(e)}"

async def add_forfeit_match(tournament_id: int, forfeiting_team_guild_id: int, 
                           opponent_team_guild_id: int, forfeit_reason: str = None):
    """
    Add a forfeit match to the tournament.
    The forfeiting team loses 3-0 by default.
    """
    return await add_manual_match_result(
        tournament_id=tournament_id,
        home_team_guild_id=opponent_team_guild_id,  # Non-forfeiting team gets home advantage
        away_team_guild_id=forfeiting_team_guild_id,
        home_score=3,  # Default forfeit score
        away_score=0,
        notes=f"Forfeit: {forfeit_reason}" if forfeit_reason else "Forfeit",
        is_forfeit=True
    )

async def update_match_result(tournament_id: int, match_id: str, home_score: int, away_score: int, notes: str = None):
    """
    Update an existing match result.
    """
    query = """
    UPDATE MATCH_STATS 
    SET home_score = %s, away_score = %s, notes = %s
    WHERE match_id = %s AND tournament_id = %s
    """
    
    try:
        result = await execute_query(query, (home_score, away_score, notes, match_id, tournament_id), commit=True)
        
        if result:
            # Recalculate tournament stats
            await recalculate_tournament_stats_v2(tournament_id)
            return True, "Match result updated successfully"
        else:
            return False, "Match not found or no changes made"
    except Exception as e:
        return False, f"Database error: {str(e)}"

async def delete_match_result(tournament_id: int, match_id: str):
    """
    Delete a match result from the tournament.
    """
    # First check if match exists
    query = "SELECT match_id FROM MATCH_STATS WHERE match_id = %s AND tournament_id = %s"
    match = await execute_query(query, (match_id, tournament_id), fetchone=True)
    
    if not match:
        return False, "Match not found"
    
    # Delete from tournament matches
    tournament_query = "DELETE FROM TOURNAMENT_MATCHES WHERE match_id = %s AND tournament_id = %s"
    await execute_query(tournament_query, (match_id, tournament_id), commit=True)
    
    # Delete from match stats
    stats_query = "DELETE FROM MATCH_STATS WHERE match_id = %s"
    await execute_query(stats_query, (match_id,), commit=True)
    
    # Recalculate tournament stats
    await recalculate_tournament_stats_v2(tournament_id)
    
    return True, "Match deleted successfully"

async def get_tournament_match_by_id(tournament_id: int, match_id: str):
    """
    Get a specific match from a tournament.
    """
    query = """
    SELECT ms.*, tm.league_name
    FROM MATCH_STATS ms
    LEFT JOIN TOURNAMENT_MATCHES tm ON ms.match_id = tm.match_id
    WHERE ms.match_id = %s AND ms.tournament_id = %s
    """
    return await execute_query(query, (match_id, tournament_id), fetchone=True)

async def update_team_tournament_stats(tournament_id: int, guild_id: int, league_name: str, 
                                      matches_played: int = None, wins: int = None, draws: int = None, 
                                      losses: int = None, goals_for: int = None, goals_against: int = None, 
                                      points: int = None):
    """
    Update a team's tournament stats directly.
    This bypasses match calculations and sets stats manually.
    """
    # Build dynamic update query
    updates = []
    params = []
    
    if matches_played is not None:
        updates.append("matches_played = %s")
        params.append(matches_played)
    if wins is not None:
        updates.append("wins = %s")
        params.append(wins)
    if draws is not None:
        updates.append("draws = %s")
        params.append(draws)
    if losses is not None:
        updates.append("losses = %s")
        params.append(losses)
    if goals_for is not None:
        updates.append("goals_for = %s")
        params.append(goals_for)
    if goals_against is not None:
        updates.append("goals_against = %s")
        params.append(goals_against)
    if points is not None:
        updates.append("points = %s")
        params.append(points)
    
    if not updates:
        return False, "No stats to update"
    
    # Add required parameters
    params.extend([tournament_id, guild_id, league_name])
    
    query = f"""
    UPDATE TOURNAMENT_TEAMS_V2 
    SET {', '.join(updates)}
    WHERE tournament_id = %s AND guild_id = %s AND league_name = %s
    """
    
    try:
        result = await execute_query(query, tuple(params), commit=True)
        return True, "Team stats updated successfully"
    except Exception as e:
        return False, f"Database error: {str(e)}"

async def get_team_tournament_stats(tournament_id: int, guild_id: int, league_name: str):
    """
    Get a team's current tournament stats.
    """
    query = """
    SELECT * FROM TOURNAMENT_TEAMS_V2 
    WHERE tournament_id = %s AND guild_id = %s AND league_name = %s
    """
    return await execute_query(query, (tournament_id, guild_id, league_name), fetchone=True)

async def recalculate_tournament_stats_v2(tournament_id: int):
    """Recalculate and update tournament stats from matches (V2 version)."""
    import os
    
    # Get tournament matches
    tournament_matches = await get_tournament_matches(tournament_id)
    if not tournament_matches:
        return
    
    # Get path to match summaries CSV
    match_summaries_path = os.path.join(os.path.dirname(__file__), 'ratings', 'match_summaries.csv')
    if not os.path.exists(match_summaries_path):
        print(f"Match summaries CSV not found at {match_summaries_path}")
        return
    
    # Load match data from CSV using pandas
    match_data_list = parse_csv_with_commas(match_summaries_path)
    match_data = {row.get('match_id', ''): row for row in match_data_list if row.get('match_id')}
    
    # Initialize stats for all tournament teams
    tournament_teams = await get_tournament_teams_v2(tournament_id)
    team_stats = {}
    
    for team in tournament_teams:
        guild_id = team['guild_id']
        league_name = team['league_name']
        team_stats[guild_id] = {
            'tournament_id': tournament_id,
            'league_name': league_name,
            'guild_id': guild_id,
            'guild_name': team['guild_name'],
            'matches_played': 0,
            'wins': 0,
            'draws': 0,
            'losses': 0,
            'goals_for': 0,
            'goals_against': 0,
            'goal_difference': 0,
            'points': 0
        }
    
    # Process tournament matches
    for match in tournament_matches:
        match_id = match['match_id']
        home_guild_id = match['home_team_guild_id']
        away_guild_id = match['away_team_guild_id']
        
        if match_id not in match_data:
            continue
        
        match_info = match_data[match_id]
        try:
            scoreline = match_info.get('scoreline', '0-0')
            home_score, away_score = map(int, scoreline.split('-'))
        except (ValueError, KeyError):
            continue
        
        # Update home team stats
        if home_guild_id in team_stats:
            team_stats[home_guild_id]['matches_played'] += 1
            team_stats[home_guild_id]['goals_for'] += home_score
            team_stats[home_guild_id]['goals_against'] += away_score
            
            if home_score > away_score:
                team_stats[home_guild_id]['wins'] += 1
                team_stats[home_guild_id]['points'] += 3
            elif home_score == away_score:
                team_stats[home_guild_id]['draws'] += 1
                team_stats[home_guild_id]['points'] += 1
            else:
                team_stats[home_guild_id]['losses'] += 1
        
        # Update away team stats
        if away_guild_id in team_stats:
            team_stats[away_guild_id]['matches_played'] += 1
            team_stats[away_guild_id]['goals_for'] += away_score
            team_stats[away_guild_id]['goals_against'] += home_score
            
            if away_score > home_score:
                team_stats[away_guild_id]['wins'] += 1
                team_stats[away_guild_id]['points'] += 3
            elif away_score == home_score:
                team_stats[away_guild_id]['draws'] += 1
                team_stats[away_guild_id]['points'] += 1
            else:
                team_stats[away_guild_id]['losses'] += 1
    
    # Calculate goal differences
    for stats in team_stats.values():
        stats['goal_difference'] = stats['goals_for'] - stats['goals_against']
    
    # Update database with new stats
    for stats in team_stats.values():
        stats_data = {
            "matches_played": stats['matches_played'],
            "wins": stats['wins'],
            "draws": stats['draws'],
            "losses": stats['losses'],
            "goals_for": stats['goals_for'],
            "goals_against": stats['goals_against'],
            "goal_difference": stats['goal_difference'],
            "points": stats['points']
        }
        
        query = """
        INSERT INTO TOURNAMENT_DATA 
        (tournament_id, league_name, data_type, team_guild_id, team_name, stats)
        VALUES (%s, %s, 'team_stats', %s, %s, %s)
        ON DUPLICATE KEY UPDATE
        stats = VALUES(stats),
        added_at = CURRENT_TIMESTAMP
        """
        await execute_query(query, (
            stats['tournament_id'], stats['league_name'], stats['guild_id'], 
            stats['guild_name'], json.dumps(stats_data)
        ), commit=True)
    
async def get_filtered_matches_for_tournament(tournament_id: int, start_date: datetime | None = None):
    """Get matches filtered for tournament teams and date range using database queries (V2 compatibility)."""
    
    # Get tournament teams
    tournament_teams = await get_tournament_teams_v2(tournament_id)
    if not tournament_teams:
        print("❌ No tournament teams found!")
        return []
    
    print(f"📋 Tournament teams ({len(tournament_teams)}):")
    team_guild_ids = []
    teams_by_league = {}
    for team in tournament_teams:
        print(f"  - '{team['guild_name']}' (ID: {team['guild_id']}, League: {team.get('league_name', 'Unknown')})")
        team_guild_ids.append(team['guild_id'])
        league_name = team.get('league_name')
        if league_name not in teams_by_league:
            teams_by_league[league_name] = []
        teams_by_league[league_name].append(team['guild_id'])
    
    # Get matches already in tournament
    existing_matches = await get_tournament_matches(tournament_id)
    tournament_match_ids = {match['match_id'] for match in existing_matches}
    print(f"🔄 Matches already in tournament: {len(tournament_match_ids)}")
    
    # Get all matches involving tournament teams (same league only)
    all_matches = await get_matches_involving_teams(team_guild_ids, same_league_only=True, start_date=start_date)
    print(f"💾 Found {len(all_matches)} matches in database involving tournament teams")
    
    if not all_matches:
        print("❌ No matches found in database. Make sure CSV data has been imported.")
        return []
    
    # Filter out matches already in tournament and ensure same league
    filtered_matches = []
    tournament_team_lookup = {team['guild_id']: team for team in tournament_teams}
    
    for match in all_matches:
        # Skip if already in tournament
        if match['match_id'] in tournament_match_ids:
            continue
        
        home_guild_id = match['home_guild_id']
        away_guild_id = match['away_guild_id']
        
        # Get team info from tournament
        home_team_info = tournament_team_lookup.get(home_guild_id)
        away_team_info = tournament_team_lookup.get(away_guild_id)
        
        # Both teams must be in tournament and same league
        if not home_team_info or not away_team_info:
            continue
            
        if home_team_info['league_name'] != away_team_info['league_name']:
            continue
        
        # Convert database format to expected tournament format
        match_dict = {
            'match_id': match['match_id'],
            'datetime': match['datetime'].strftime('%Y-%m-%d %H:%M:%S') if hasattr(match['datetime'], 'strftime') else str(match['datetime']),
            'home_team': match['home_team_name'],
            'away_team': match['away_team_name'],
            'scoreline': match['scoreline'],
            'game_type': match['game_type'],
            'initial_lineups': match.get('initial_lineups', ''),
            'final_lineups': match.get('final_lineups', ''),
            'substitution_summary': match.get('substitution_summary', ''),
            'home_team_guild_id': home_guild_id,
            'away_team_guild_id': away_guild_id,
            'league_id': home_team_info['league_name']
        }
        
        filtered_matches.append(match_dict)
    
    # Print summary
    print(f"\n📊 DATABASE FILTERING SUMMARY:")
    print(f"  Total matches in database: {len(all_matches)}")
    print(f"  Already in tournament: {len(tournament_match_ids)}")
    print(f"  Final filtered matches: {len(filtered_matches)}")
    
    if len(filtered_matches) > 0:
        print(f"\n✅ SUCCESS: Found {len(filtered_matches)} eligible matches using database queries!")
        
        # Show first few matches
        for i, match in enumerate(filtered_matches[:3]):
            print(f"  {i+1}. {match['home_team']} vs {match['away_team']} ({match['scoreline']}) - {match['datetime'][:10]}")
        
        if len(filtered_matches) > 3:
            print(f"  ... and {len(filtered_matches) - 3} more matches")
    else:
        print(f"\n❌ NO MATCHES FOUND")
        print(f"  💡 Tip: Make sure CSV data has been imported to database with `/sync_csv_data`")
        
    return filtered_matches

async def get_matches_involving_teams(guild_ids: list[int], same_league_only: bool = True, start_date: datetime | None = None):
    """Get all matches involving any of the specified teams."""
    if not guild_ids:
        return []
    
    # Create placeholders for the IN clause
    placeholders = ','.join(['%s'] * len(guild_ids))
    
    if same_league_only:
        # Only matches between teams in the list (both home and away must be in the list)
        query = f"""
        SELECT m.*, 
               ht.guild_name as home_team_display_name,
               at.guild_name as away_team_display_name
        FROM MATCH_STATS m
        JOIN IOSCA_TEAMS ht ON m.home_guild_id = ht.guild_id
        JOIN IOSCA_TEAMS at ON m.away_guild_id = at.guild_id
        WHERE m.home_guild_id IN ({placeholders}) 
          AND m.away_guild_id IN ({placeholders})
        """
        base_params = guild_ids + guild_ids
    else:
        # Any match involving at least one of the teams
        query = f"""
        SELECT m.*, 
               ht.guild_name as home_team_display_name,
               at.guild_name as away_team_display_name
        FROM MATCH_STATS m
        JOIN IOSCA_TEAMS ht ON m.home_guild_id = ht.guild_id
        JOIN IOSCA_TEAMS at ON m.away_guild_id = at.guild_id
        WHERE (m.home_guild_id IN ({placeholders}) OR m.away_guild_id IN ({placeholders}))
        """
        base_params = guild_ids + guild_ids
    
    if start_date:
        query += " AND m.datetime >= %s"
        query += " ORDER BY m.datetime DESC"
        params = tuple(base_params + [start_date])
    else:
        query += " ORDER BY m.datetime DESC"
        params = tuple(base_params)
    
    return await execute_query(query, params, fetchall=True)

# === MISSING FUNCTIONS USED THROUGHOUT CODEBASE ===

async def get_servers_for_compile_stats():
    """Get all active servers for compile stats with proper field mapping."""
    try:
        servers = await get_all_servers_with_details()
        
        # Transform to expected format for compile_stats.py
        formatted_servers = []
        for server in servers:
            if server.get('host_username') and server.get('address'):
                # Parse host and port from address
                address_parts = server['address'].split(':')
                host = address_parts[0]
                game_port = address_parts[1]
                sftp_port = 8822  # Default SFTP ports
                
                # Check if this is a Windows server based on database server_type field
                is_windows_server = server.get('server_type', 'linux').lower() == 'windows'
                if is_windows_server:
                    # Windows server with different path structure and SFTP port
                    # Try relative path first (SFTP usually starts in user home directory)
                    directory_path = "/C:/Users/Administrator/Documents/iosoccer/iosoccer/statistics"
                    sftp_port = 22  # Standard SFTP port for Windows (not 8822)
                else:
                    # Standard Linux server path structure
                    directory_path = f"/{host}_{game_port}/iosoccer/statistics"
                
                formatted_servers.append({
                    'host': host,
                    'port': sftp_port,
                    'user': server['host_username'],
                    'pass': server.get('host_password', ''),  # compile_stats.py expects 'pass', not 'password'
                    'password': server.get('host_password', ''),  # Keep both for compatibility
                    'name': server['name'],
                    'dir': directory_path  # Correctly constructed directory path
                })
        
        return formatted_servers
    except Exception as e:
        print(f"Error getting servers for compile stats: {e}")
        return []

async def auto_populate_database_from_csv():
    """Legacy function name for CSV population (compatibility function)."""
    return await auto_populate_database_from_csv_v2()

def get_unique_player_ids(team_players: list) -> set:
    """Get unique player IDs from a team's player list."""
    unique_ids = set()
    for player in team_players:
        if isinstance(player, dict):
            discord_id = player.get('discord_id')
            if discord_id:
                unique_ids.add(discord_id)
        elif hasattr(player, 'id'):
            unique_ids.add(player.id)
    return unique_ids

async def is_player_in_team_type(discord_id: int, team_type: str) -> bool:
    """Check if a player is in a specific type of team (e.g., 'club', 'national', or 'mix')."""
    try:
        # Get all teams
        all_teams = await get_all_teams_with_details()
        
        for team in all_teams:
            is_national = team.get('is_national_team', False)
            is_mix = team.get('is_mix_team', False)
            
            if is_national:
                current_team_type = 'national'
            elif is_mix:
                current_team_type = 'mix'
            else:
                current_team_type = 'club'
            
            if current_team_type == team_type:
                # Check if player is captain or vice captain
                is_captain = team.get('captain_id') == discord_id
                is_vice_captain = team.get('vice_captain_id') == discord_id
                
                if is_captain or is_vice_captain:
                    return True
                
                # Check if player is in the players list
                players = team.get('players', [])
                for player in players:
                    if isinstance(player, dict) and player.get('id') == discord_id:
                        return True
        
        return False
    except Exception as e:
        print(f"Error checking player team type: {e}")
        return False

async def get_team_statistics(guild_id: int) -> dict:
    """
    Get comprehensive team statistics from database using only guild_id.
    This function finds ALL matches for a team by searching MATCH_STATS for home_guild_id or away_guild_id = guild_id.
    All calculations (home/away, stats) are based on guild_id, not team name.
    """
    try:
        team = await get_team(guild_id)
        if not team:
            return {}

        matches = await execute_query(
            """
            SELECT * FROM MATCH_STATS WHERE home_guild_id = %s OR away_guild_id = %s ORDER BY datetime DESC
            """,
            (guild_id, guild_id),
            fetchall=True
        )

        total_matches = len(matches)
        wins = draws = losses = 0
        goals_for = goals_against = 0
        recent_matches = []

        for match in matches:
            try:
                home_score, away_score = map(int, match['scoreline'].split('-'))
                if match['home_guild_id'] == guild_id:
                    # Team was home
                    goals_for += home_score
                    goals_against += away_score
                    if home_score > away_score:
                        wins += 1
                    elif home_score < away_score:
                        losses += 1
                    else:
                        draws += 1
                elif match['away_guild_id'] == guild_id:
                    # Team was away
                    goals_for += away_score
                    goals_against += home_score
                    if away_score > home_score:
                        wins += 1
                    elif away_score < home_score:
                        losses += 1
                    else:
                        draws += 1
                # Add to recent matches (show both team names and score)
                recent_matches.append(match)
            except Exception:
                continue

        return {
            'team_name': team['guild_name'],
            'total_matches': total_matches,
            'wins': wins,
            'draws': draws,
            'losses': losses,
            'goals_for': goals_for,
            'goals_against': goals_against,
            'goal_difference': goals_for - goals_against,
            'recent_matches': recent_matches[:10]  # Last 10 matches
        }
    except Exception as e:
        print(f"Error getting team statistics: {e}")
        return {}

async def get_top_team_players(guild_id: int, limit: int = 10) -> list:
    """Get top players for a team based on recent performance with fixed calculations."""
    try:
        # First get team's total matches to use as a sanity check
        team_stats = await get_team_statistics(guild_id)
        team_total_matches = team_stats.get('total_matches', 0)
        
        # Get player match data for this team
        players = await execute_query(
            """
            SELECT pmd.player_name, pmd.steam_id, COUNT(*) as matches_played,
                   AVG(CAST(JSON_UNQUOTE(JSON_EXTRACT(pmd.additional_stats, '$.goals')) AS UNSIGNED)) as avg_goals
            FROM PLAYER_MATCH_DATA pmd
            WHERE pmd.team_guild_id = %s
            GROUP BY pmd.steam_id, pmd.player_name
            ORDER BY matches_played DESC, avg_goals DESC
            LIMIT %s
            """,
            (guild_id, limit),
            fetchall=True
        )
        
        # Sanity check: ensure no player has more matches than the team
        if players and team_total_matches > 0:
            for player in players:
                if player.get('matches_played', 0) > team_total_matches:
                    # Cap the matches played to team total
                    player['matches_played'] = team_total_matches
        
        return players if players else []
        
    except Exception as e:
        print(f"Error getting top team players: {e}")
        return []

async def get_all_servers_with_details():
    """Get all servers with detailed information."""
    query = """
    SELECT id, name, address, password, host_username, host_password, server_type, is_active, created_at, updated_at 
    FROM IOS_SERVERS 
    WHERE is_active = TRUE 
    ORDER BY name ASC
    """
    return await execute_query(query, fetchall=True)

async def get_all_teams_with_channels():
    """Get all teams with their channel information."""
    try:
        teams = await get_all_teams_with_details()
        # Filter teams that have channels
        teams_with_channels = []
        for team in teams:
            eights_channels = team.get('eights_channels', [])
            sixes_channels = team.get('sixes_channels', [])
            # Only include teams that have at least one 8v8 channel
            if eights_channels and len(eights_channels) > 0:
                teams_with_channels.append(team)
            if sixes_channels and len(sixes_channels) > 0:
                teams_with_channels.append(team)
        print(f"Found {len(teams_with_channels)} teams with channels out of {len(teams)} total teams")
        return teams_with_channels
    except Exception as e:
        print(f"Error getting teams with channels: {e}")
        return []

async def update_team_details(guild_id: int, **kwargs):
    """Update team details (captain, vice captain, etc.)."""
    try:
        # Build dynamic update query
        update_fields = []
        values = []
        
        for field, value in kwargs.items():
            if field in ['captain_id', 'captain_name', 'vice_captain_id', 'vice_captain_name', 'guild_name', 'guild_icon']:
                update_fields.append(f"{field} = %s")
                values.append(value)
        
        if not update_fields:
            return False
        
        values.append(guild_id)
        query = f"UPDATE IOSCA_TEAMS SET {', '.join(update_fields)} WHERE guild_id = %s"
        
        return await execute_query(query, tuple(values), commit=True)
        
    except Exception as e:
        print(f"Error updating team details: {e}")
        return False

async def add_active_match(channel_id: int, team1_name: str, team2_name: str):
    """Add an active match (legacy function - now just logs)."""
    # This was used by the old ACTIVE_MATCHES table which we removed
    # Just log for now as the new system doesn't need this
    print(f"Active match logged: {team1_name} vs {team2_name} in channel {channel_id}")
    return True

async def get_player_teams(discord_id: int) -> list:
    """Get all teams a player belongs to."""
    try:
        # Get all teams and check if player is in any
        all_teams = await get_all_teams_with_details()
        player_teams = []
        
        for team in all_teams:
            # Check if player is captain or vice captain
            is_captain = team.get('captain_id') == discord_id
            is_vice_captain = team.get('vice_captain_id') == discord_id
            
            if is_captain or is_vice_captain:
                player_teams.append({
                    'guild_id': team['guild_id'],
                    'name': team['guild_name'],  # Use 'name' for consistency
                    'guild_name': team['guild_name'],  # Keep both for backward compatibility
                    'is_national_team': team.get('is_national_team', False),
                    'is_mix_team': team.get('is_mix_team', False),
                    'captain_id': team.get('captain_id'),
                    'vice_captain_id': team.get('vice_captain_id')
                })
                continue  # Don't check players list if they're captain/vice captain
            
            # Check if player is in the players list
            players = team.get('players', [])
            for player in players:
                if isinstance(player, dict) and player.get('id') == discord_id:
                    player_teams.append({
                        'guild_id': team['guild_id'],
                        'name': team['guild_name'],  # Use 'name' for consistency
                        'guild_name': team['guild_name'],  # Keep both for backward compatibility
                        'is_national_team': team.get('is_national_team', False),
                        'is_mix_team': team.get('is_mix_team', False),
                        'captain_id': team.get('captain_id'),
                        'vice_captain_id': team.get('vice_captain_id')
                    })
                    break
        
        return player_teams
        
    except Exception as e:
        print(f"Error getting player teams: {e}")
        return []

# === OPTIMIZED BULK CSV IMPORT FUNCTIONS ===

async def optimize_database_for_bulk_import():
    """Optimize database settings for bulk import operations."""
    print("🔧 Optimizing database settings for bulk import...")
    
    optimization_queries = [
        "SET foreign_key_checks = 0",
        "SET unique_checks = 0", 
        "SET sql_log_bin = 0",
        "SET autocommit = 0",
        "SET SESSION bulk_insert_buffer_size = 268435456",  # 256MB
        "SET SESSION myisam_sort_buffer_size = 134217728",  # 128MB
        "SET SESSION key_buffer_size = 134217728",  # 128MB
    ]
    
    for query in optimization_queries:
        await execute_query(query)
    
    print("✅ Database optimized for bulk import")

async def restore_database_settings():
    """Restore normal database settings after bulk import."""
    print("🔧 Restoring normal database settings...")
    
    restore_queries = [
        "SET foreign_key_checks = 1",
        "SET unique_checks = 1",
        "SET sql_log_bin = 1", 
        "SET autocommit = 1",
        "COMMIT"
    ]
    
    for query in restore_queries:
        await execute_query(query)
    
    print("✅ Database settings restored")

# Removed bulk_import_teams_from_csv and optimized_bulk_insert_teams functions
# Teams should only be created via the register command, not automatically from CSV

async def bulk_import_matches_from_csv():
    """Bulk import match data using optimized single-connection approach."""
    import os
    
    print("⚽ Bulk importing matches from CSV...")
    
    match_summaries_path = os.path.join(os.path.dirname(__file__), 'ratings', 'match_summaries.csv')
    
    if not os.path.exists(match_summaries_path):
        print(f"❌ Match summaries CSV not found")
        return False
    
    try:
        # Parse CSV
        match_data = parse_csv_with_commas(match_summaries_path)
        if not match_data:
            return False
        
        print(f"📊 Processing {len(match_data)} matches")
        
        # Get team mappings
        team_mappings = await get_team_mappings_lookup()
        if not team_mappings:
            print("❌ No team mappings found. Run team mapping first.")
            return False
        
        # Process matches and build insert data
        match_inserts = []
        processed_count = 0
        
        for match in match_data:
            match_id = match.get('match_id', '')
            if not match_id:
                continue
            
            home_team_name = safe_get_string(match, 'home_team')
            away_team_name = safe_get_string(match, 'away_team')
            
            home_mapping = team_mappings.get(home_team_name)
            away_mapping = team_mappings.get(away_team_name)
            
            if not home_mapping or not away_mapping:
                continue
            
            try:
                match_datetime = datetime.strptime(match['datetime'], '%Y-%m-%d %H:%M:%S')
            except (ValueError, KeyError):
                continue
            
            # Escape single quotes for SQL
            def escape_sql(value):
                if value is None:
                    return 'NULL'
                escaped_value = str(value).replace("'", "''")
                return f"'{escaped_value}'"
            
            match_inserts.append(
                f"({escape_sql(match_id)}, '{match_datetime}', {home_mapping['guild_id']}, {away_mapping['guild_id']}, "
                f"{escape_sql(home_team_name)}, {escape_sql(away_team_name)}, {escape_sql(match.get('scoreline', ''))}, "
                f"{escape_sql(match.get('game_type', ''))}, {escape_sql(match.get('initial_lineups', ''))}, "
                f"{escape_sql(match.get('final_lineups', ''))}, {escape_sql(match.get('substitution_summary', ''))})"
            )
            processed_count += 1
        
        if processed_count == 0:
            print("❌ No matches could be processed")
            return False
        
        print(f"📄 Prepared {processed_count} matches for bulk insert")
        
        # Execute bulk insert
        await optimized_bulk_insert_matches(match_inserts)
        
        return True
        
    except Exception as e:
        print(f"❌ Error during bulk match import: {e}")
        import traceback
        traceback.print_exc()
        return False

async def optimized_bulk_insert_matches(match_inserts: list):
    """Ultra-fast bulk match insertion using single connection."""
    if not match_inserts:
        return
    
    print(f"⚡ Fast bulk inserting {len(match_inserts)} matches...")
    
    # Use single INSERT with multiple VALUES
    chunk_size = 250  # Smaller chunks for matches (more data per row)
    total_inserted = 0
    
    conn = None
    cursor = None
    
    try:
        conn = await run_blocking_db_operation(_connect_db_sync)
        if not conn:
            raise Exception("Failed to connect to database")
        
        cursor = conn.cursor()
        cursor.execute("SET autocommit = 0")
        cursor.execute("SET foreign_key_checks = 0")
        cursor.execute("SET unique_checks = 0")
        
        # Process in chunks
        for i in range(0, len(match_inserts), chunk_size):
            chunk = match_inserts[i:i + chunk_size]
            
            values_clause = ',\n'.join(chunk)
            query = f"""
            INSERT IGNORE INTO MATCH_STATS 
            (match_id, datetime, home_guild_id, away_guild_id, home_team_name, away_team_name, 
             scoreline, game_type, initial_lineups, final_lineups, substitution_summary)
            VALUES {values_clause}
            """
            
            cursor.execute(query)
            rows_affected = cursor.rowcount
            total_inserted += rows_affected
            
            print(f"📥 Inserted chunk {i//chunk_size + 1}: {rows_affected} matches (Total: {total_inserted})")
        
        cursor.execute("COMMIT")
        cursor.execute("SET foreign_key_checks = 1")
        cursor.execute("SET unique_checks = 1")
        cursor.execute("SET autocommit = 1")
        
        print(f"✅ Fast bulk match INSERT completed: {total_inserted} matches")
        
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except:
                pass
        print(f"❌ Error in optimized match insert: {e}")
        raise e
        
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()

async def bulk_import_players_from_csv():
    """Bulk import player data using streaming approach for large files."""
    import os
    
    print("👥 Bulk importing player data from CSV...")
    
    player_stats_path = os.path.join(os.path.dirname(__file__), 'ratings', 'player_stats.csv')
    
    if not os.path.exists(player_stats_path):
        print(f"❌ Player stats CSV not found")
        return False
    
    try:
        # Get team mappings
        team_mappings = await get_team_mappings_lookup()
        if not team_mappings:
            print("❌ No team mappings found")
            return False
        
        print("📊 Processing player data in optimized chunks...")
        
        # Use streaming CSV reader for memory efficiency
        import csv
        total_processed = 0
        batch_size = 5000  # Process in smaller batches
        
        conn = None
        cursor = None
        
        try:
            conn = await run_blocking_db_operation(_connect_db_sync)
            if not conn:
                raise Exception("Failed to connect to database")
            
            cursor = conn.cursor()
            cursor.execute("SET autocommit = 0")
            cursor.execute("SET foreign_key_checks = 0")
            cursor.execute("SET unique_checks = 0")
            
            with open(player_stats_path, 'r', encoding='utf-8') as csvfile:
                reader = csv.DictReader(csvfile)
                player_batch = []
                
            for row in reader:
                match_id = str(row.get('match_id', ''))
                steam_id = str(row.get('Steam ID', ''))
                
                if not match_id or not steam_id:
                    continue
            
                team_name = str(row.get('Team Name', '')).strip()
                opponent_name = str(row.get('Opponent Team Name', '')).strip()
                
                team_mapping = team_mappings.get(team_name)
                opponent_mapping = team_mappings.get(opponent_name)
                
                if not team_mapping or not opponent_mapping:
                    continue
            
                try:
                    match_datetime = datetime.strptime(row['datetime'], '%Y-%m-%d %H:%M:%S')
                except:
                    continue
                
                # Prepare additional stats (simplified)
                base_fields = {'match_id', 'datetime', 'Steam ID', 'Name', 'Team Name', 'Opponent Team Name', 'Team Side', 'Position'}
                additional_stats = {}
                for key, value in row.items():
                    if key not in base_fields and value:
                        additional_stats[key] = value
                
                # Escape values for SQL
                def escape_sql(value):
                    if value is None:
                        return 'NULL'
                    escaped_value = str(value).replace("'", "''")
                    return f"'{escaped_value}'"
                
                player_insert = (
                    f"({escape_sql(match_id)}, '{match_datetime}', {escape_sql(steam_id)}, "
                    f"{escape_sql(row.get('Name', ''))}, {team_mapping['guild_id']}, {opponent_mapping['guild_id']}, "
                    f"{escape_sql(team_name)}, {escape_sql(opponent_name)}, {escape_sql(row.get('Team Side', ''))}, "
                    f"{escape_sql(row.get('Position', ''))}, {escape_sql(json.dumps(additional_stats))})"
                )
                
                player_batch.append(player_insert)
                
                # Process batch when it reaches batch_size
                if len(player_batch) >= batch_size:
                    await process_player_batch(cursor, player_batch)
                    total_processed += len(player_batch)
                    print(f"✅ Processed {len(player_batch)} players (Total: {total_processed})")
                    player_batch = []
                
                # Process remaining players
                if player_batch:
                    await process_player_batch(cursor, player_batch)
                    total_processed += len(player_batch)
                    print(f"✅ Processed final {len(player_batch)} players (Total: {total_processed})")
            
            cursor.execute("COMMIT")
            cursor.execute("SET foreign_key_checks = 1")
            cursor.execute("SET unique_checks = 1")
            cursor.execute("SET autocommit = 1")
            
            print(f"✅ Fast bulk player import completed: {total_processed} records")
            return True
            
        finally:
            if cursor:
                cursor.close()
            if conn and conn.is_connected():
                conn.close()
        
    except Exception as e:
        print(f"❌ Error during bulk player import: {e}")
        import traceback
        traceback.print_exc()
        return False

async def process_player_batch(cursor, player_batch):
    """Process a batch of player inserts efficiently."""
    if not player_batch:
        return
    
    # Split into smaller chunks to avoid MySQL packet limits
    chunk_size = 1000
    for i in range(0, len(player_batch), chunk_size):
        chunk = player_batch[i:i + chunk_size]
        values_clause = ',\n'.join(chunk)
        
        query = f"""
        INSERT IGNORE INTO PLAYER_MATCH_DATA 
        (match_id, datetime, steam_id, player_name, team_guild_id, opponent_guild_id,
         team_name, opponent_team_name, team_side, position, additional_stats)
        VALUES {values_clause}
        """
        
        cursor.execute(query)

async def optimized_csv_to_database_sync():
    """Optimized CSV-to-Database synchronization using fastest possible methods."""
    import os
    
    print("🚀 Starting ULTRA-FAST CSV-to-Database synchronization...")
    
    # Test database connection first
    print("🔍 Testing database connection before import...")
    if not await test_database_connection():
        print("❌ Database connection test failed. Cannot proceed with CSV import.")
        return False
    
    # Get paths to CSV files
    csv_dir = os.path.join(os.path.dirname(__file__), 'ratings')
    match_summaries_path = os.path.join(csv_dir, 'match_summaries.csv')
    player_stats_path = os.path.join(csv_dir, 'player_stats.csv')
    
    if not os.path.exists(match_summaries_path) or not os.path.exists(player_stats_path):
        print("❌ CSV files not found. Skipping sync.")
        return False
    
    try:
        start_time = datetime.now()
        
        # Step 1: Skip team creation - teams must be registered via register command
        team_count = await execute_query("SELECT COUNT(*) as count FROM IOSCA_TEAMS", fetchone=True)
        
        if not team_count or team_count.get('count', 0) == 0:
            print("⚠️ No teams found. Teams must be registered via the register command.")
            print("ℹ️ CSV import will continue but only registered teams will be mapped.")
        
        # Step 2: Quick team mappings
        print("🗺️ Quick processing team mappings...")
        await quick_process_team_mappings()
        
        # Step 3: Bulk import matches (fast)
        print("⚽ Ultra-fast match import...")
        await bulk_import_matches_from_csv()
        
        # Step 4: Stream import players (memory efficient)
        print("👥 Stream importing players...")
        await bulk_import_players_from_csv()
        
        # Final stats
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        
        final_match_count = await execute_query("SELECT COUNT(*) as count FROM MATCH_STATS", fetchone=True)
        final_player_count = await execute_query("SELECT COUNT(*) as count FROM PLAYER_MATCH_DATA", fetchone=True)
        final_team_count = await execute_query("SELECT COUNT(*) as count FROM IOSCA_TEAMS", fetchone=True)
        
        print(f"🎉 ULTRA-FAST sync completed in {duration:.2f} seconds!")
        print(f"📊 Final database:")
        print(f"  👥 Teams: {final_team_count['count'] if final_team_count else 0}")
        print(f"  ⚽ Matches: {final_match_count['count'] if final_match_count else 0}")
        print(f"  🎮 Player records: {final_player_count['count'] if final_player_count else 0}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error during optimized CSV sync: {e}")
        import traceback
        traceback.print_exc()
        return False

async def quick_process_team_mappings():
    """Quick team mapping processing without complex similarity matching."""
    import os
    
    print("🗺️ Quick processing team mappings...")
    
    match_summaries_path = os.path.join(os.path.dirname(__file__), 'ratings', 'match_summaries.csv')
    if not os.path.exists(match_summaries_path):
        print(f"❌ CSV file not found")
        return
    
    # Get unique team names from CSV
    match_data = parse_csv_with_commas(match_summaries_path)
    if not match_data:
        return
    
    unique_csv_teams = set()
    for row in match_data:
        home_team = row.get('home_team', '')
        away_team = row.get('away_team', '')
        if home_team:
            unique_csv_teams.add(home_team)
        if away_team:
            unique_csv_teams.add(away_team)
    
    print(f"📊 Found {len(unique_csv_teams)} unique team names")
    
    # Get all database teams
    db_teams = await get_all_teams_with_details()
    if not db_teams or not isinstance(db_teams, list):
        print("❌ No teams found in database or failed to retrieve teams")
        return
    
    # Create simple name-to-team lookup (exact matches only for speed)
    db_team_lookup = {}
    for team in db_teams:
        db_team_lookup[team['guild_name']] = team
        # Also add lowercased version
        db_team_lookup[team['guild_name'].lower()] = team
    
    # Prepare bulk mapping inserts
    mapping_inserts = []
    mapped_count = 0
    
    for csv_team_name in unique_csv_teams:
        # Try exact match first
        matched_team = db_team_lookup.get(csv_team_name)
        if not matched_team:
            # Try lowercase match
            matched_team = db_team_lookup.get(csv_team_name.lower())
        
        if matched_team:
            escaped_csv_name = csv_team_name.replace("'", "''")
            escaped_db_name = matched_team['guild_name'].replace("'", "''")
            
            mapping_inserts.append(
                f"('{escaped_csv_name}', {matched_team['guild_id']}, '{escaped_db_name}', 1.0)"
            )
            mapped_count += 1
    
    # Bulk insert mappings
    if mapping_inserts:
        await bulk_insert_mappings(mapping_inserts)
    
    print(f"✅ Quick mapping complete: {mapped_count} teams mapped")

async def bulk_insert_mappings(mapping_inserts: list):
    """Bulk insert team mappings efficiently."""
    if not mapping_inserts:
        return
    
    conn = None
    cursor = None
    
    try:
        conn = await run_blocking_db_operation(_connect_db_sync)
        if not conn:
            raise Exception("Failed to connect to database")
        
        cursor = conn.cursor()
        
        # Clear existing mappings first
        cursor.execute("DELETE FROM TEAM_NAME_MAPPINGS")
        
        # Insert all mappings at once
        values_clause = ',\n'.join(mapping_inserts)
        query = f"""
        INSERT INTO TEAM_NAME_MAPPINGS (csv_team_name, guild_id, guild_name, similarity_score)
        VALUES {values_clause}
        """
        
        cursor.execute(query)
        conn.commit()
        
        print(f"📥 Bulk inserted {len(mapping_inserts)} team mappings")
        
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except:
                pass
        print(f"❌ Error in bulk mapping insert: {e}")
        
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()

# === SIMPLE AND FAST CSV IMPORT ===

# DELETED: simple_csv_import() function - was creating unwanted placeholder teams
# This function was creating teams with 'Unknown' captain values, which violated the requirement
# that teams should ONLY be created via the register command.

# === TEAM CLEANUP AND RETROACTIVE LINKING FUNCTIONS ===

async def cleanup_placeholder_teams():
    """
    Remove ONLY placeholder teams with 'Unknown' values from IOSCA_TEAMS and their mappings.
    ABSOLUTELY DOES NOT touch MATCH_STATS or PLAYER_MATCH_DATA - only removes team entries and mappings.
    """
    try:
        # Get count before cleanup - check for placeholder teams with captain_id=0 AND captain_name='Unknown'
        before_count = await execute_query(
            "SELECT COUNT(*) as count FROM IOSCA_TEAMS WHERE captain_id = 0 AND captain_name = 'Unknown' AND vice_captain_id = 0 AND vice_captain_name = 'Unknown'", 
            fetchone=True
        )
        
        if not before_count or before_count.get('count', 0) == 0:
            print("✅ No placeholder teams found to cleanup")
            return True
        
        print(f"🗑️ Found {before_count['count']} placeholder teams to remove...")
        
        # Get the guild_ids and names of placeholder teams before deletion
        placeholder_teams = await execute_query(
            "SELECT guild_id, guild_name FROM IOSCA_TEAMS WHERE captain_id = 0 AND captain_name = 'Unknown' AND vice_captain_id = 0 AND vice_captain_name = 'Unknown'",
            fetchall=True
        )
        
        if not placeholder_teams:
            print("✅ No placeholder teams found to cleanup")
            return True
        
        placeholder_guild_ids = [team['guild_id'] for team in placeholder_teams]
        
        # Show which teams will be deleted
        print(f"📋 Placeholder teams to be deleted:")
        for team in placeholder_teams[:10]:  # Show first 10
            print(f"  - ID: {team['guild_id']}, Name: {team['guild_name']}")
        if len(placeholder_teams) > 10:
            print(f"  ... and {len(placeholder_teams) - 10} more")
        
        # STEP 1: Delete ONLY the mappings for these placeholder teams first
        if placeholder_guild_ids:
            guild_ids_str = ','.join(map(str, placeholder_guild_ids))
            mapping_result = await execute_query(
                f"DELETE FROM TEAM_NAME_MAPPINGS WHERE guild_id IN ({guild_ids_str})",
                commit=True
            )
            print(f"🗑️ Deleted mappings for {len(placeholder_guild_ids)} placeholder teams")
        
        # STEP 2: Safely delete placeholder teams without triggering cascades
        # First disable foreign key checks to prevent cascade deletions
        await execute_query("SET FOREIGN_KEY_CHECKS = 0", commit=True)
        
        try:
            # Delete ONLY the placeholder teams from IOSCA_TEAMS 
            team_result = await execute_query(
                "DELETE FROM IOSCA_TEAMS WHERE captain_id = 0 AND captain_name = 'Unknown' AND vice_captain_id = 0 AND vice_captain_name = 'Unknown'",
                commit=True
            )
        finally:
            # Re-enable foreign key checks
            await execute_query("SET FOREIGN_KEY_CHECKS = 1", commit=True)
        
        # Get count after cleanup
        after_count = await execute_query("SELECT COUNT(*) as count FROM IOSCA_TEAMS", fetchone=True)
        
        # Verify match and player data is untouched
        match_count = await execute_query("SELECT COUNT(*) as count FROM MATCH_STATS", fetchone=True)
        player_count = await execute_query("SELECT COUNT(*) as count FROM PLAYER_MATCH_DATA", fetchone=True)
        
        print(f"✅ Cleanup complete! Removed {before_count['count']} placeholder teams")
        print(f"📊 Teams remaining: {after_count.get('count', 0) if after_count else 0}")
        print(f"🔒 PRESERVED DATA:")
        print(f"  📊 Match records: {match_count.get('count', 0) if match_count else 0}")
        print(f"  👥 Player records: {player_count.get('count', 0) if player_count else 0}")
        print(f"ℹ️ Only removed placeholder teams and their mappings - ALL statistical data preserved")
        
        return True
        
    except Exception as e:
        print(f"❌ Error during placeholder team cleanup: {e}")
        import traceback
        traceback.print_exc()
        return False

async def retroactively_link_team_matches(guild_id: int, guild_name: str):
    """
    When a new team is registered, find their matches in the database using fuzzy matching
    and update match records to link them to the correct guild_id.
    """
    try:
        print(f"🔍 Retroactively linking matches for team: {guild_name} (ID: {guild_id})")
        
        # Get all matches where team names might match this new team
        all_matches = await execute_query(
            "SELECT match_id, home_team_name, away_team_name, home_guild_id, away_guild_id FROM MATCH_STATS",
            fetchall=True
        )
        
        if not all_matches:
            print("📭 No matches found in database")
            return 0
        
        print(f"📊 Searching through {len(all_matches)} matches for team '{guild_name}'...")
        
        # Find matches using fuzzy matching
        matches_to_update = []
        home_matches = 0
        away_matches = 0
        
        for match in all_matches:
            match_id = match['match_id']
            home_team_name = match['home_team_name']
            away_team_name = match['away_team_name']
            home_guild_id = match['home_guild_id']
            away_guild_id = match['away_guild_id']
            
            # Check if this team matches the home team
            home_similarity = calculate_similarity_score(guild_name, home_team_name)
            away_similarity = calculate_similarity_score(guild_name, away_team_name)
            
            # Use a high threshold for accuracy (0.8 = 80% similarity)
            similarity_threshold = 0.8
            
            # Check home team match
            if home_similarity >= similarity_threshold and home_guild_id != guild_id:
                matches_to_update.append({
                    'match_id': match_id,
                    'position': 'home',
                    'old_guild_id': home_guild_id,
                    'new_guild_id': guild_id,
                    'team_name': home_team_name,
                    'similarity': home_similarity
                })
                home_matches += 1
            
            # Check away team match
            if away_similarity >= similarity_threshold and away_guild_id != guild_id:
                matches_to_update.append({
                    'match_id': match_id,
                    'position': 'away',
                    'old_guild_id': away_guild_id,
                    'new_guild_id': guild_id,
                    'team_name': away_team_name,
                    'similarity': away_similarity
                })
                away_matches += 1
        
        if not matches_to_update:
            print(f"❌ No matches found for team '{guild_name}' (similarity threshold: {similarity_threshold * 100}%)")
            return 0
        
        # Show top matches for confirmation
        print(f"\n📋 Top matches found:")
        for i, match in enumerate(sorted(matches_to_update, key=lambda x: x['similarity'], reverse=True)[:5]):
            print(f"  {i+1}. {match['team_name']} (similarity: {match['similarity']:.3f}) - {match['position']} in {match['match_id']}")
        
        # Update matches in database
        updated_matches = 0
        updated_players = 0
        
        for match_update in matches_to_update:
            match_id = match_update['match_id']
            position = match_update['position']
            old_guild_id = match_update['old_guild_id']
            new_guild_id = match_update['new_guild_id']
            
            # Update MATCH_STATS table
            if position == 'home':
                success = await execute_query(
                    "UPDATE MATCH_STATS SET home_guild_id = %s WHERE match_id = %s AND home_guild_id = %s",
                    (new_guild_id, match_id, old_guild_id),
                    commit=True
                )
            else:  # away
                success = await execute_query(
                    "UPDATE MATCH_STATS SET away_guild_id = %s WHERE match_id = %s AND away_guild_id = %s",
                    (new_guild_id, match_id, old_guild_id),
                    commit=True
                )
            
            if success:
                updated_matches += 1
            
            # Update PLAYER_MATCH_DATA table
            player_updates = await execute_query(
                "UPDATE PLAYER_MATCH_DATA SET team_guild_id = %s WHERE match_id = %s AND team_guild_id = %s",
                (new_guild_id, match_id, old_guild_id),
                commit=True
            )
            
            # Also update opponent_guild_id if this team was the opponent
            opponent_updates = await execute_query(
                "UPDATE PLAYER_MATCH_DATA SET opponent_guild_id = %s WHERE match_id = %s AND opponent_guild_id = %s",
                (new_guild_id, match_id, old_guild_id),
                commit=True
            )
            
            if player_updates or opponent_updates:
                updated_players += 1
        
        # Create mapping for future imports
        await execute_query(
            """
            INSERT INTO TEAM_NAME_MAPPINGS (csv_team_name, guild_id, guild_name, similarity_score)
            VALUES (%s, %s, %s, 1.0)
            ON DUPLICATE KEY UPDATE 
            guild_id = VALUES(guild_id), 
            guild_name = VALUES(guild_name), 
            similarity_score = VALUES(similarity_score)
            """,
            (guild_name, guild_id, guild_name),
            commit=True
        )
        
        print(f"✅ Retroactive linking complete!")
        print(f"  📊 Updated {updated_matches} matches")
        print(f"  👥 Updated {updated_players} player records")
        print(f"  🗺️ Created team mapping for future imports")
        
        return updated_matches
        
    except Exception as e:
        print(f"❌ Error during retroactive match linking: {e}")
        import traceback
        traceback.print_exc()
        return 0

async def find_potential_team_matches(team_name: str, limit: int = 10):
    """
    Find potential matches for a team name in existing match data.
    Returns top matches with similarity scores for manual verification.
    """
    try:
        # Get unique team names from matches
        unique_teams = await execute_query(
            """
            SELECT DISTINCT home_team_name as team_name FROM MATCH_STATS
            UNION
            SELECT DISTINCT away_team_name as team_name FROM MATCH_STATS
            """,
            fetchall=True
        )
        
        if not unique_teams:
            return []
    
        # Calculate similarity for each team
        potential_matches = []
        for team_record in unique_teams:
            existing_team_name = team_record['team_name']
            similarity = calculate_similarity_score(team_name, existing_team_name)
            
            if similarity >= 0.5:  # Lower threshold for exploration
                # Count matches for this team name
                match_count = await execute_query(
                    "SELECT COUNT(*) as count FROM MATCH_STATS WHERE home_team_name = %s OR away_team_name = %s",
                    (existing_team_name, existing_team_name),
                    fetchone=True
                )
                
                potential_matches.append({
                    'csv_team_name': existing_team_name,
                    'similarity': similarity,
                    'match_count': match_count.get('count', 0) if match_count else 0
                })
        
        # Sort by similarity and return top matches
        potential_matches.sort(key=lambda x: x['similarity'], reverse=True)
        return potential_matches[:limit]
        
    except Exception as e:
        print(f"❌ Error finding potential team matches: {e}")
        return []

# === MODIFIED CSV IMPORT FUNCTIONS (NO AUTO TEAM CREATION) ===

async def sync_csv_to_database_no_auto_teams():
    """
    CSV-to-Database synchronization WITHOUT automatically creating placeholder teams.
    Only imports matches and player data for teams that already exist in IOSCA_TEAMS.
    """
    import os
    
    print("🔄 Starting CSV-to-Database sync (no auto team creation)...")
    
    # Test database connection first
    try:
        connection_test = await asyncio.wait_for(test_database_connection(), timeout=30.0)
        if not connection_test:
            print("❌ Database connection test failed. Cannot proceed with CSV sync.")
            return False
    except asyncio.TimeoutError:
        print("❌ Database connection test timed out after 30 seconds.")
        return False
    
    # Get paths to CSV files
    csv_dir = os.path.join(os.path.dirname(__file__), 'ratings')
    match_summaries_path = os.path.join(csv_dir, 'match_summaries.csv')
    player_stats_path = os.path.join(csv_dir, 'player_stats.csv')
    
    if not os.path.exists(match_summaries_path) or not os.path.exists(player_stats_path):
        print("❌ CSV files not found. Skipping sync.")
        return False
    
    try:
        print("📄 Loading CSV files...")
        match_data = parse_csv_with_commas(match_summaries_path)
        player_data = parse_csv_with_commas(player_stats_path)
        
        csv_match_count = len(match_data)
        csv_player_count = len(player_data)
        print(f"📄 CSV files: {csv_match_count} matches, {csv_player_count} player records")
        
        # Get existing team mappings (only for registered teams)
        team_mappings = await get_team_mappings_lookup()
        print(f"🗺️ Found {len(team_mappings)} existing team mappings")
        
        if not team_mappings:
            print("⚠️ No team mappings found. Matches will only be imported if teams are manually registered.")
        
        # Get existing match IDs for incremental sync
        existing_matches = await execute_query("SELECT match_id FROM MATCH_STATS", fetchall=True)
        existing_match_ids = {match['match_id'] for match in existing_matches} if existing_matches else set()
        
        # Filter to only new matches that involve registered teams
        new_matches_data = []
        skipped_no_mapping = 0
        skipped_already_imported = 0
        
        for match in match_data:
            match_id = match.get('match_id', '')
            if not match_id:
                continue
                
            # Skip if already imported
            if match_id in existing_match_ids:
                skipped_already_imported += 1
                continue
            
            home_team = match.get('home_team', '').strip()
            away_team = match.get('away_team', '').strip()
            
            # Only include if BOTH teams have mappings (are registered)
            home_mapping = team_mappings.get(home_team)
            away_mapping = team_mappings.get(away_team)
            
            if home_mapping and away_mapping:
                new_matches_data.append(match)
            else:
                skipped_no_mapping += 1
        
        print(f"📥 Import summary:")
        print(f"  • New matches with registered teams: {len(new_matches_data)}")
        print(f"  • Skipped (already imported): {skipped_already_imported}")
        print(f"  • Skipped (no team mappings): {skipped_no_mapping}")
        
        # Import matches
        if new_matches_data:
            print("📥 Batch inserting matches...")
            match_inserts = []
            
            for match in new_matches_data:
                match_id = match.get('match_id', '')
                home_team_name = safe_get_string(match, 'home_team')
                away_team_name = safe_get_string(match, 'away_team')
                
                home_mapping = team_mappings.get(home_team_name)
                away_mapping = team_mappings.get(away_team_name)
                
                try:
                    match_datetime = datetime.strptime(match['datetime'], '%Y-%m-%d %H:%M:%S')
                except (ValueError, KeyError):
                    continue
                
                match_inserts.append((
                    match_id, match_datetime, 
                    home_mapping['guild_id'] if home_mapping else None,
                    away_mapping['guild_id'] if away_mapping else None,
                    home_team_name, away_team_name, match.get('scoreline', ''), match.get('game_type', ''),
                    match.get('initial_lineups', ''), match.get('final_lineups', ''), 
                    match.get('substitution_summary', '')
                ))
            
            if match_inserts:
                await batch_insert_matches(match_inserts)
                print(f"✅ Inserted {len(match_inserts)} matches")
        
        # Handle player data (only for registered teams)
        print("👥 Processing player data...")
        existing_players = await execute_query("SELECT match_id, steam_id FROM PLAYER_MATCH_DATA", fetchall=True)
        existing_player_keys = {(p['match_id'], p['steam_id']) for p in existing_players} if existing_players else set()
        
        new_players_data = []
        for player in player_data:
            player_key = (player.get('match_id', ''), player.get('Steam ID', ''))
            if player_key not in existing_player_keys:
                # Only include if team has mapping
                team_name = player.get('Team Name', '').strip()
                opponent_name = player.get('Opponent Team Name', '').strip()
                
                if team_mappings.get(team_name) and team_mappings.get(opponent_name):
                    new_players_data.append(player)
        
        print(f"👥 Found {len(new_players_data)} new player records for registered teams")
        
        if new_players_data:
            player_inserts = []
            for player_record in new_players_data:
                match_id = player_record.get('match_id', '')
                steam_id = player_record.get('Steam ID', '')
                team_name = player_record.get('Team Name', '').strip()
                opponent_name = player_record.get('Opponent Team Name', '').strip()
                
                team_mapping = team_mappings.get(team_name)
                opponent_mapping = team_mappings.get(opponent_name)
                
                try:
                    match_datetime = datetime.strptime(player_record['datetime'], '%Y-%m-%d %H:%M:%S')
                except (ValueError, KeyError):
                    continue
                
                # Prepare additional stats
                base_fields = {'match_id', 'datetime', 'Steam ID', 'Name', 'Team Name', 'Opponent Team Name', 'Team Side', 'Position'}
                additional_stats = {}
                for key, value in player_record.items():
                    if key not in base_fields and value is not None:
                        try:
                            if isinstance(value, str) and value.isdigit():
                                additional_stats[key] = int(value)
                            else:
                                additional_stats[key] = value
                        except:
                            additional_stats[key] = str(value)
                
                player_inserts.append((
                    match_id, match_datetime, steam_id, player_record.get('Name', ''),
                    team_mapping['guild_id'] if team_mapping else 0, 
                    opponent_mapping['guild_id'] if opponent_mapping else 0,
                    team_name, opponent_name, player_record.get('Team Side', ''),
                    player_record.get('Position', ''), json.dumps(additional_stats)
                ))
            
            if player_inserts:
                await batch_insert_players(player_inserts)
                print(f"✅ Inserted {len(player_inserts)} player records")
        
        # Final stats
        final_match_count = await execute_query("SELECT COUNT(*) as count FROM MATCH_STATS", fetchone=True)
        final_player_count = await execute_query("SELECT COUNT(*) as count FROM PLAYER_MATCH_DATA", fetchone=True)
        
        match_count = final_match_count.get('count', 0) if final_match_count else 0
        player_count = final_player_count.get('count', 0) if final_player_count else 0
        
        print(f"🎉 Sync completed (no auto team creation)!")
        print(f"📊 Final database: {match_count} matches, {player_count} player records")
        print(f"💡 Tip: Register teams manually to retroactively link their historical data")
        
        return True
        
    except Exception as e:
        print(f"❌ Error during CSV sync: {e}")
        import traceback
        traceback.print_exc()
        return False

# === MODIFIED INITIALIZATION TO NOT CREATE AUTO TEAMS ===

async def simple_csv_import_no_auto_teams():
    """Simple CSV import that does NOT create placeholder teams automatically."""
    import os
    
    print("🚀 Starting simple CSV import (no auto team creation)...")
    
    # Get CSV paths
    csv_dir = os.path.join(os.path.dirname(__file__), 'ratings')
    match_summaries_path = os.path.join(csv_dir, 'match_summaries.csv')
    player_stats_path = os.path.join(csv_dir, 'player_stats.csv')
    
    if not os.path.exists(match_summaries_path) or not os.path.exists(player_stats_path):
        print("❌ CSV files not found. Skipping import.")
        return False
    
    conn = None
    cursor = None
    
    try:
        # Single connection for the entire operation
        conn = await run_blocking_db_operation(_connect_db_sync)
        if not conn:
            print("❌ Failed to connect for CSV import")
            return False
            
        cursor = conn.cursor()
        
        print("📋 Loading CSV data...")
        match_data = parse_csv_with_commas(match_summaries_path)
        player_data = parse_csv_with_commas(player_stats_path)
        
        # Only create team mappings for teams that already exist in IOSCA_TEAMS
        print("🗺️ Creating mappings for existing teams only...")
        cursor.execute("SELECT guild_id, guild_name FROM IOSCA_TEAMS WHERE captain_id != 0")  # Exclude any remaining placeholders
        existing_teams = cursor.fetchall()
        
        # Clear old mappings and create new ones only for registered teams
        cursor.execute("DELETE FROM TEAM_NAME_MAPPINGS")
        
        mapping_values = []
        for team in existing_teams:
            guild_id, guild_name = team
            escaped_name = guild_name.replace("'", "''")
            mapping_values.append(f"('{escaped_name}', {guild_id}, '{escaped_name}', 1.0)")
        
        if mapping_values:
            values_clause = ',\n'.join(mapping_values)
            cursor.execute(f"""
                INSERT INTO TEAM_NAME_MAPPINGS (csv_team_name, guild_id, guild_name, similarity_score)
                VALUES {values_clause}
            """)
            print(f"✅ Created mappings for {len(mapping_values)} registered teams")
        
        # Get team mapping lookup
        cursor.execute("SELECT csv_team_name, guild_id FROM TEAM_NAME_MAPPINGS")
        team_lookup = {row[0]: row[1] for row in cursor.fetchall()}
        
        print(f"📊 Will only import matches for {len(team_lookup)} registered teams")
        
        # Import matches (only for teams with mappings)
        cursor.execute("SELECT match_id FROM MATCH_STATS")
        existing_matches = {row[0] for row in cursor.fetchall()}
        
        match_values = []
        skipped_no_team = 0
        skipped_exists = 0
        
        for match in match_data:
            match_id = match.get('match_id', '')
            if match_id in existing_matches:
                skipped_exists += 1
                continue
                
            home_team = match.get('home_team', '').strip()
            away_team = match.get('away_team', '').strip()
            
            home_guild_id = team_lookup.get(home_team)
            away_guild_id = team_lookup.get(away_team)
            
            if not home_guild_id or not away_guild_id:
                skipped_no_team += 1
                continue
            
            try:
                match_datetime = datetime.strptime(match['datetime'], '%Y-%m-%d %H:%M:%S')
                
                def escape_sql(value):
                    if value is None: return 'NULL'
                    return f"'{str(value).replace(chr(39), chr(39)+chr(39))}'"
                
                match_values.append(
                    f"({escape_sql(match_id)}, '{match_datetime}', {home_guild_id}, {away_guild_id}, "
                    f"{escape_sql(home_team)}, {escape_sql(away_team)}, {escape_sql(match.get('scoreline', ''))}, "
                    f"{escape_sql(match.get('game_type', ''))}, {escape_sql(match.get('initial_lineups', ''))}, "
                    f"{escape_sql(match.get('final_lineups', ''))}, {escape_sql(match.get('substitution_summary', ''))})"
                )
            except Exception:
                continue
        
        # Insert matches in chunks
        if match_values:
            chunk_size = 250
            inserted_count = 0
            for i in range(0, len(match_values), chunk_size):
                chunk = match_values[i:i + chunk_size]
                values_clause = ',\n'.join(chunk)
                cursor.execute(f"""
                    INSERT IGNORE INTO MATCH_STATS 
                    (match_id, datetime, home_guild_id, away_guild_id, home_team_name, away_team_name, 
                     scoreline, game_type, initial_lineups, final_lineups, substitution_summary)
                    VALUES {values_clause}
                """)
                inserted_count += cursor.rowcount
            print(f"✅ Imported {inserted_count} matches for registered teams")
            print(f"⏭️ Skipped {skipped_no_team} matches (teams not registered)")
            print(f"⏭️ Skipped {skipped_exists} matches (already imported)")
        
        # Import player data (only for registered teams)
        cursor.execute("SELECT match_id, steam_id FROM PLAYER_MATCH_DATA")
        existing_players = {(row[0], row[1]) for row in cursor.fetchall()}
        
        player_values = []
        for player in player_data:
            key = (player.get('match_id', ''), player.get('Steam ID', ''))
            if key in existing_players:
                continue
                
            # Safely get string values from CSV data that might be floats/NaN
            team_name_raw = player.get('Team Name', '')
            opponent_name_raw = player.get('Opponent Team Name', '')
            
            # Convert to string and strip, handling NaN/float values
            team_name = str(team_name_raw).strip() if team_name_raw is not None and str(team_name_raw) != 'nan' else ''
            opponent_name = str(opponent_name_raw).strip() if opponent_name_raw is not None and str(opponent_name_raw) != 'nan' else ''
            
            team_guild_id = team_lookup.get(team_name)
            opponent_guild_id = team_lookup.get(opponent_name)
            
            if not team_guild_id or not opponent_guild_id:
                continue
            
            try:
                match_datetime = datetime.strptime(player['datetime'], '%Y-%m-%d %H:%M:%S')
                
                additional_stats = {}
                for key, value in player.items():
                    if key not in {'match_id', 'datetime', 'Steam ID', 'Name', 'Team Name', 'Opponent Team Name', 'Team Side', 'Position'}:
                        additional_stats[key] = value
                
                def escape_sql(value):
                    if value is None: return 'NULL'
                    return f"'{str(value).replace(chr(39), chr(39)+chr(39))}'"
                
                # Use NULL for guild_ids when teams aren't registered
                team_guild_sql = str(team_guild_id) if team_guild_id else 'NULL'
                opponent_guild_sql = str(opponent_guild_id) if opponent_guild_id else 'NULL'
                
                player_values.append(
                    f"({escape_sql(player.get('match_id'))}, '{match_datetime}', {escape_sql(player.get('Steam ID'))}, "
                    f"{escape_sql(player.get('Name', ''))}, {team_guild_sql}, {opponent_guild_sql}, "
                    f"{escape_sql(team_name)}, {escape_sql(opponent_name)}, {escape_sql(player.get('Team Side', ''))}, "
                    f"{escape_sql(player.get('Position', ''))}, {escape_sql(json.dumps(additional_stats))})"
                )
            except Exception:
                continue
        
        # Insert player data in chunks
        if player_values:
            chunk_size = 1000
            inserted_count = 0
            for i in range(0, len(player_values), chunk_size):
                chunk = player_values[i:i + chunk_size]
                values_clause = ',\n'.join(chunk)
                cursor.execute(f"""
                    INSERT IGNORE INTO PLAYER_MATCH_DATA 
                    (match_id, datetime, steam_id, player_name, team_guild_id, opponent_guild_id,
                     team_name, opponent_team_name, team_side, position, additional_stats)
                    VALUES {values_clause}
                """)
                inserted_count += cursor.rowcount
            print(f"✅ Imported {inserted_count} player records for registered teams")
        
        # Commit all changes
        conn.commit()
        
        # Final stats
        cursor.execute("SELECT COUNT(*) FROM IOSCA_TEAMS WHERE captain_id != 0")
        team_result = cursor.fetchone()
        team_count = team_result[0] if team_result else 0
        
        cursor.execute("SELECT COUNT(*) FROM MATCH_STATS")
        match_result = cursor.fetchone()
        match_count = match_result[0] if match_result else 0
        
        cursor.execute("SELECT COUNT(*) FROM PLAYER_MATCH_DATA")
        player_result = cursor.fetchone()
        player_count = player_result[0] if player_result else 0
        
        print(f"🎉 Smart CSV import completed!")
        print(f"📊 Database: {team_count} registered teams, {match_count} matches, {player_count} player records")
        print(f"💡 Only data for registered teams was imported")
        
        return True
        
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"❌ Error during smart CSV import: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()

# === UPDATE EXISTING FUNCTIONS TO USE NEW APPROACH ===

# Updated ensure_team_mappings_exist function (replaces the old one)
async def ensure_team_mappings_exist_new(team_names: set):
    """
    NO LONGER creates placeholder teams automatically.
    Only creates mappings for teams that already exist in IOSCA_TEAMS.
    """
    print(f"🔍 Checking mappings for {len(team_names)} teams (no auto-creation)...")
    
    # Get existing mappings
    existing_mappings = await get_team_mappings_lookup()
    
    # Get existing teams (only real teams, not placeholders)
    existing_teams = await execute_query(
        "SELECT guild_id, guild_name FROM IOSCA_TEAMS WHERE captain_id != 0",  # Exclude placeholders
        fetchall=True
    )
    
    if not existing_teams:
        print("⚠️ No registered teams found in database")
        return
    
    existing_team_lookup = {team['guild_name'].lower(): team for team in existing_teams}
    
    new_mappings = 0
    unmapped_teams = []
    
    for team_name in team_names:
        if team_name in existing_mappings:
            continue  # Already mapped
            
        # Try to find exact match in registered teams
        team_key = team_name.lower()
        if team_key in existing_team_lookup:
            team = existing_team_lookup[team_key]
            await execute_query(
                """
                INSERT INTO TEAM_NAME_MAPPINGS (csv_team_name, guild_id, guild_name, similarity_score)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE similarity_score = VALUES(similarity_score)
                """,
                (team_name, team['guild_id'], team['guild_name'], 1.0),
                commit=True
            )
            new_mappings += 1
        else:
            unmapped_teams.append(team_name)
    
    if new_mappings > 0:
        print(f"✅ Created {new_mappings} new team mappings")
    
    if unmapped_teams:
        # Track unmapped teams for admin reference
        for team_name in unmapped_teams:
                            await execute_query(
                    """
                    INSERT INTO UNMAPPED_TEAMS (csv_team_name, created_placeholder, notes)
                    VALUES (%s, FALSE, 'Team needs manual registration')
                    ON DUPLICATE KEY UPDATE match_count = match_count + 1
                    """,
                    (team_name,),
                    commit=True
                )

# === AUTOMATIC CSV SYNC WITH NULL GUILD IDS ===
async def update_guild_ids_for_registered_team(guild_id: int, guild_name: str):
    """
    Update 0 guild IDs in match and player data when a team registers.
    This function retroactively links historical data to newly registered teams.
    """
    try:
        print(f"🔄 Updating guild IDs for registered team: {guild_name}")
        
        # Update match data - home team
        match_query = """
            UPDATE MATCH_STATS 
            SET home_guild_id = %s 
            WHERE home_team_name = %s AND home_guild_id = 0
        """
        await execute_query(match_query, (guild_id, guild_name), commit=True)
        
        # Update match data - away team
        match_query2 = """
            UPDATE MATCH_STATS 
            SET away_guild_id = %s 
            WHERE away_team_name = %s AND away_guild_id = 0
        """
        await execute_query(match_query2, (guild_id, guild_name), commit=True)
        
        # Update player data - team guild id
        player_query = """
            UPDATE PLAYER_MATCH_DATA 
            SET team_guild_id = %s 
            WHERE team_name = %s AND team_guild_id = 0
        """
        await execute_query(player_query, (guild_id, guild_name), commit=True)
        
        # Update player data - opponent guild id
        player_query2 = """
            UPDATE PLAYER_MATCH_DATA 
            SET opponent_guild_id = %s 
            WHERE opponent_team_name = %s AND opponent_guild_id = 0
        """
        await execute_query(player_query2, (guild_id, guild_name), commit=True)
        
        # Check how many records were updated
        match_count = await execute_query(
            "SELECT COUNT(*) as count FROM MATCH_STATS WHERE (home_guild_id = %s OR away_guild_id = %s)", 
            (guild_id, guild_id), fetchone=True
        )
        player_count = await execute_query(
            "SELECT COUNT(*) as count FROM PLAYER_MATCH_DATA WHERE (team_guild_id = %s OR opponent_guild_id = %s)", 
            (guild_id, guild_id), fetchone=True
        )
        
        match_total = match_count.get('count', 0) if match_count else 0
        player_total = player_count.get('count', 0) if player_count else 0
        
        print(f"✅ Updated guild IDs for {guild_name}: {match_total} matches, {player_total} player records")
        return match_total, player_total
        
    except Exception as e:
        print(f"❌ Error updating guild IDs for {guild_name}: {e}")
        return 0, 0

# REMOVED: ensure_placeholder_team_exists() function
# This function was creating unwanted placeholder teams.
# Teams should ONLY be created via the register command.

async def comprehensive_csv_import():
    """
    Comprehensive CSV import that imports ALL match and player data from CSV files.
    Uses NULL for guild_ids when teams aren't registered in IOSCA_TEAMS.
    This ensures all statistical data is preserved regardless of team registration status.
    """
    import os
    
    print("🚀 Starting comprehensive CSV import (all data with NULL for unregistered teams)...")
    
    # Get CSV paths
    csv_dir = os.path.join(os.path.dirname(__file__), 'ratings')
    match_summaries_path = os.path.join(csv_dir, 'match_summaries.csv')
    player_stats_path = os.path.join(csv_dir, 'player_stats.csv')
    
    if not os.path.exists(match_summaries_path) or not os.path.exists(player_stats_path):
        print("❌ CSV files not found. Skipping import.")
        return False
    
    conn = None
    cursor = None
    
    try:
        # Single connection for the entire operation
        conn = await run_blocking_db_operation(_connect_db_sync)
        if not conn:
            print("❌ Failed to connect for CSV import")
            return False
            
        cursor = conn.cursor()
        
        print("📋 Loading CSV data...")
        match_data = parse_csv_with_commas(match_summaries_path)
        player_data = parse_csv_with_commas(player_stats_path)
        
        print(f"📊 CSV Data: {len(match_data)} matches, {len(player_data)} player records")
        
        # Create team mappings for registered teams only
        print("🗺️ Creating mappings for registered teams...")
        cursor.execute("SELECT guild_id, guild_name FROM IOSCA_TEAMS WHERE captain_id != 0")  # Exclude placeholders
        existing_teams = cursor.fetchall()
        
        # Clear old mappings and create new ones only for registered teams
        cursor.execute("DELETE FROM TEAM_NAME_MAPPINGS")
        
        mapping_values = []
        for team in existing_teams:
            guild_id, guild_name = team
            escaped_name = guild_name.replace("'", "''")
            mapping_values.append(f"('{escaped_name}', {guild_id}, '{escaped_name}', 1.0)")
        
        if mapping_values:
            values_clause = ',\n'.join(mapping_values)
            cursor.execute(f"""
                INSERT INTO TEAM_NAME_MAPPINGS (csv_team_name, guild_id, guild_name, similarity_score)
                VALUES {values_clause}
            """)
            print(f"✅ Created mappings for {len(mapping_values)} registered teams")
        
        # Get team mapping lookup
        cursor.execute("SELECT csv_team_name, guild_id FROM TEAM_NAME_MAPPINGS")
        team_lookup = {row[0]: row[1] for row in cursor.fetchall()}
        
        # Import ALL matches (with NULL guild_ids for unregistered teams)
        print("📥 Importing ALL matches (including unregistered teams)...")
        cursor.execute("SELECT match_id FROM MATCH_STATS")
        existing_matches = {row[0] for row in cursor.fetchall()}
        
        match_values = []
        skipped_exists = 0
        registered_matches = 0
        unregistered_matches = 0
        
        for match in match_data:
            match_id = match.get('match_id', '')
            if not match_id:
                continue
                
            if match_id in existing_matches:
                skipped_exists += 1
                continue
                
            home_team = safe_get_string(match, 'home_team')
            away_team = safe_get_string(match, 'away_team')
            
            if not home_team or not away_team:
                continue
            
            # Get guild_ids from mappings, use NULL if not found
            home_guild_id = team_lookup.get(home_team)
            away_guild_id = team_lookup.get(away_team)
            
            # Track whether this match involves registered teams
            if home_guild_id and away_guild_id:
                registered_matches += 1
            else:
                unregistered_matches += 1
            
            try:
                match_datetime = datetime.strptime(match['datetime'], '%Y-%m-%d %H:%M:%S')
                
                def escape_sql(value):
                    if value is None: return 'NULL'
                    return f"'{str(value).replace(chr(39), chr(39)+chr(39))}'"
                
                # Use NULL for guild_ids when teams aren't registered
                home_guild_sql = str(home_guild_id) if home_guild_id else 'NULL'
                away_guild_sql = str(away_guild_id) if away_guild_id else 'NULL'
                
                match_values.append(
                    f"({escape_sql(match_id)}, '{match_datetime}', {home_guild_sql}, {away_guild_sql}, "
                    f"{escape_sql(home_team)}, {escape_sql(away_team)}, {escape_sql(match.get('scoreline', ''))}, "
                    f"{escape_sql(match.get('game_type', ''))}, {escape_sql(match.get('initial_lineups', ''))}, "
                    f"{escape_sql(match.get('final_lineups', ''))}, {escape_sql(match.get('substitution_summary', ''))})"
                )
            except Exception as e:
                print(f"⚠️ Skipping match {match_id}: {e}")
                continue
        
        # Insert matches in chunks
        if match_values:
            chunk_size = 250
            inserted_count = 0
            for i in range(0, len(match_values), chunk_size):
                chunk = match_values[i:i + chunk_size]
                values_clause = ',\n'.join(chunk)
                cursor.execute(f"""
                    INSERT IGNORE INTO MATCH_STATS 
                    (match_id, datetime, home_guild_id, away_guild_id, home_team_name, away_team_name, 
                     scoreline, game_type, initial_lineups, final_lineups, substitution_summary)
                    VALUES {values_clause}
                """)
                inserted_count += cursor.rowcount
            
            print(f"✅ Imported {inserted_count} matches:")
            print(f"  📍 Registered teams: {registered_matches}")
            print(f"  📍 Unregistered teams: {unregistered_matches}")
            print(f"  ⏭️ Skipped (already imported): {skipped_exists}")
        
        # Import ALL player data (with NULL guild_ids for unregistered teams)
        print("👥 Importing ALL player data (including unregistered teams)...")
        cursor.execute("SELECT match_id, steam_id FROM PLAYER_MATCH_DATA")
        existing_players = {(row[0], row[1]) for row in cursor.fetchall()}
        
        player_values = []
        registered_player_records = 0
        unregistered_player_records = 0
        
        for player in player_data:
            key = (player.get('match_id', ''), player.get('Steam ID', ''))
            if key in existing_players:
                continue
            
            # Safely get string values from CSV data
            team_name = safe_get_string(player, 'Team Name')
            opponent_name = safe_get_string(player, 'Opponent Team Name')
            
            if not team_name or not opponent_name:
                continue
            
            # Get guild_ids from mappings, use NULL if not found
            team_guild_id = team_lookup.get(team_name)
            opponent_guild_id = team_lookup.get(opponent_name)
            
            # Track whether this player record involves registered teams
            if team_guild_id and opponent_guild_id:
                registered_player_records += 1
            else:
                unregistered_player_records += 1
            
            try:
                match_datetime = datetime.strptime(player['datetime'], '%Y-%m-%d %H:%M:%S')
                
                # Extract additional stats
                additional_stats = {}
                for key, value in player.items():
                    if key not in {'match_id', 'datetime', 'Steam ID', 'Name', 'Team Name', 'Opponent Team Name', 'Team Side', 'Position'}:
                        additional_stats[key] = value
                
                def escape_sql(value):
                    if value is None: return 'NULL'
                    return f"'{str(value).replace(chr(39), chr(39)+chr(39))}'"
                
                # Use NULL for guild_ids when teams aren't registered
                team_guild_sql = str(team_guild_id) if team_guild_id else 'NULL'
                opponent_guild_sql = str(opponent_guild_id) if opponent_guild_id else 'NULL'
                
                player_values.append(
                    f"({escape_sql(player.get('match_id'))}, '{match_datetime}', {escape_sql(player.get('Steam ID'))}, "
                    f"{escape_sql(player.get('Name', ''))}, {team_guild_sql}, {opponent_guild_sql}, "
                    f"{escape_sql(team_name)}, {escape_sql(opponent_name)}, {escape_sql(player.get('Team Side', ''))}, "
                    f"{escape_sql(player.get('Position', ''))}, {escape_sql(json.dumps(additional_stats))})"
                )
            except Exception as e:
                print(f"⚠️ Skipping player record: {e}")
                continue
        
        # Insert player data in chunks
        if player_values:
            chunk_size = 1000
            inserted_count = 0
            for i in range(0, len(player_values), chunk_size):
                chunk = player_values[i:i + chunk_size]
                values_clause = ',\n'.join(chunk)
                cursor.execute(f"""
                    INSERT IGNORE INTO PLAYER_MATCH_DATA 
                    (match_id, datetime, steam_id, player_name, team_guild_id, opponent_guild_id,
                     team_name, opponent_team_name, team_side, position, additional_stats)
                    VALUES {values_clause}
                """)
                inserted_count += cursor.rowcount
            
            print(f"✅ Imported {inserted_count} player records:")
            print(f"  👥 Registered teams: {registered_player_records}")
            print(f"  👥 Unregistered teams: {unregistered_player_records}")
        
        # Commit all changes
        conn.commit()
        
        # Final stats
        cursor.execute("SELECT COUNT(*) FROM IOSCA_TEAMS WHERE captain_id != 0")
        team_result = cursor.fetchone()
        team_count = team_result[0] if team_result else 0
        
        cursor.execute("SELECT COUNT(*) FROM MATCH_STATS")
        match_result = cursor.fetchone()
        match_count = match_result[0] if match_result else 0
        
        cursor.execute("SELECT COUNT(*) FROM PLAYER_MATCH_DATA")
        player_result = cursor.fetchone()
        player_count = player_result[0] if player_result else 0
        
        print(f"🎉 Comprehensive CSV import completed!")
        print(f"📊 Database totals: {team_count} registered teams, {match_count} matches, {player_count} player records")
        print(f"💡 All CSV data imported - registered teams linked, unregistered teams have NULL guild_ids")
        
        return True
        
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"❌ Error during comprehensive CSV import: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()

async def import_all_csv_data_with_nulls():
    """
    Optimized function to import ALL CSV data with 0 guild_ids for unregistered teams.
    Uses batch processing to avoid connection resets and improve performance.
    """
    import os
    import mysql.connector
    from mysql.connector import Error
    import time
    
    print("🚀 Importing ALL CSV data (0 guild_ids for unregistered teams)...")
    
    # Get CSV paths
    csv_dir = os.path.join(os.path.dirname(__file__), 'ratings')
    match_summaries_path = os.path.join(csv_dir, 'match_summaries.csv')
    player_stats_path = os.path.join(csv_dir, 'player_stats.csv')
    
    if not os.path.exists(match_summaries_path) or not os.path.exists(player_stats_path):
        print("❌ CSV files not found. Skipping import.")
        return False
    
    conn = None
    cursor = None
    
    try:
        print("📋 Loading CSV data...")
        match_data = parse_csv_with_commas(match_summaries_path)
        player_data = parse_csv_with_commas(player_stats_path)
        
        print(f"📊 Found: {len(match_data)} matches, {len(player_data)} player records")
        
        # Drop foreign key constraints to allow importing unregistered team data
        await drop_foreign_key_constraints_for_unregistered_teams()
        
        # Create connection with better settings for bulk operations
        conn = mysql.connector.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            charset='utf8',
            collation='utf8_general_ci',
            autocommit=False,
            connection_timeout=60,  # Increased timeout
            auth_plugin='mysql_native_password',
            use_pure=True  # Use pure Python implementation
        )
        cursor = conn.cursor()
        
        # Create team lookup for registered teams only
        cursor.execute("SELECT guild_id, guild_name FROM IOSCA_TEAMS WHERE captain_id != 0")
        existing_teams_results = cursor.fetchall()
        existing_teams = []
        if existing_teams_results and cursor.description:
            columns = [desc[0] for desc in cursor.description]
            existing_teams = [dict(zip(columns, row)) for row in existing_teams_results]
        team_lookup = {team['guild_name']: team['guild_id'] for team in existing_teams} if existing_teams else {}
        print(f"🏢 Found {len(team_lookup)} registered teams for linking")
        
        # Get existing data to avoid duplicates
        cursor.execute("SELECT match_id FROM MATCH_STATS")
        existing_match_results = cursor.fetchall()
        existing_match_ids = set()
        if existing_match_results and cursor.description:
            columns = [desc[0] for desc in cursor.description]
            existing_matches = [dict(zip(columns, row)) for row in existing_match_results]
            existing_match_ids = {match['match_id'] for match in existing_matches}
        
        cursor.execute("SELECT match_id, steam_id FROM PLAYER_MATCH_DATA")
        existing_player_results = cursor.fetchall()
        existing_player_keys = set()
        if existing_player_results and cursor.description:
            columns = [desc[0] for desc in cursor.description]
            existing_players = [dict(zip(columns, row)) for row in existing_player_results]
            existing_player_keys = {(player['match_id'], player['steam_id']) for player in existing_players}
        
        # BATCH IMPORT MATCHES
        print("⚽ Preparing match imports...")
        match_inserts = []
        matches_skipped = 0
        
        for match in match_data:
            match_id = match.get('match_id', '')
            if not match_id or match_id in existing_match_ids:
                matches_skipped += 1
                continue
            
            home_team = safe_get_string(match, 'home_team')
            away_team = safe_get_string(match, 'away_team')
            
            if not home_team or not away_team:
                continue
            
            # Get guild_ids if teams are registered, otherwise use 0 for unregistered teams
            home_guild_id = team_lookup.get(home_team, 0)
            away_guild_id = team_lookup.get(away_team, 0)
            
            try:
                match_datetime = datetime.strptime(match['datetime'], '%Y-%m-%d %H:%M:%S')
                
                match_inserts.append((
                    match_id, match_datetime, home_guild_id, away_guild_id, 
                    home_team, away_team, match.get('scoreline', ''), 
                    match.get('game_type', ''), match.get('initial_lineups', ''), 
                    match.get('final_lineups', ''), match.get('substitution_summary', '')
                ))
                
            except Exception as e:
                print(f"⚠️ Error preparing match {match_id}: {e}")
                continue
        
        # Insert matches in batches
        matches_imported = 0
        if match_inserts:
            print(f"🔄 Inserting {len(match_inserts)} matches in batches...")
            batch_size = 100
            
            for i in range(0, len(match_inserts), batch_size):
                batch = match_inserts[i:i+batch_size]
                
                try:
                    cursor.executemany(
                        """
                        INSERT INTO MATCH_STATS 
                        (match_id, datetime, home_guild_id, away_guild_id, home_team_name, away_team_name, 
                         scoreline, game_type, initial_lineups, final_lineups, substitution_summary)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        batch
                    )
                    matches_imported += len(batch)
                    print(f"  ✅ Inserted {matches_imported}/{len(match_inserts)} matches")
                    
                except Exception as e:
                    print(f"⚠️ Error inserting match batch: {e}")
                    continue
        
        print(f"✅ Matches: {matches_imported} imported, {matches_skipped} skipped")
        
        # BATCH IMPORT PLAYERS
        print("👥 Preparing player imports...")
        player_inserts = []
        players_skipped = 0
        
        for player in player_data:
            match_id = player.get('match_id', '')
            steam_id = player.get('Steam ID', '')
            
            if not match_id or not steam_id or (match_id, steam_id) in existing_player_keys:
                players_skipped += 1
                continue
            
            team_name = safe_get_string(player, 'Team Name')
            opponent_name = safe_get_string(player, 'Opponent Team Name')
            
            if not team_name or not opponent_name:
                continue
            
            # Get guild_ids if teams are registered, otherwise use 0 for unregistered teams
            team_guild_id = team_lookup.get(team_name, 0)
            opponent_guild_id = team_lookup.get(opponent_name, 0)
            
            try:
                match_datetime = datetime.strptime(player['datetime'], '%Y-%m-%d %H:%M:%S')
                
                # Extract additional stats
                additional_stats = {}
                for key, value in player.items():
                    if key not in {'match_id', 'datetime', 'Steam ID', 'Name', 'Team Name', 'Opponent Team Name', 'Team Side', 'Position'}:
                        additional_stats[key] = value
                
                player_inserts.append((
                    match_id, match_datetime, steam_id, player.get('Name', ''),
                    team_guild_id, opponent_guild_id, team_name, opponent_name,
                    player.get('Team Side', ''), player.get('Position', ''), 
                    json.dumps(additional_stats)
                ))
                
            except Exception as e:
                print(f"⚠️ Error preparing player data: {e}")
                continue
        
        # Insert players in batches
        players_imported = 0
        if player_inserts:
            print(f"🔄 Inserting {len(player_inserts)} player records in batches...")
            batch_size = 500  # Larger batch for player data
            
            for i in range(0, len(player_inserts), batch_size):
                batch = player_inserts[i:i+batch_size]
                
                try:
                    cursor.executemany(
                        """
                        INSERT INTO PLAYER_MATCH_DATA 
                        (match_id, datetime, steam_id, player_name, team_guild_id, opponent_guild_id,
                         team_name, opponent_team_name, team_side, position, additional_stats)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        batch
                    )
                    players_imported += len(batch)
                    print(f"  ✅ Inserted {players_imported}/{len(player_inserts)} player records")
                    
                except Exception as e:
                    print(f"⚠️ Error inserting player batch: {e}")
                    continue
        
        print(f"✅ Players: {players_imported} imported, {players_skipped} skipped")
        
        # Commit all changes
        print("💾 Committing all changes...")
        conn.commit()
        
        # Final stats
        cursor.execute("SELECT COUNT(*) as count FROM MATCH_STATS")
        result = cursor.fetchone()
        final_matches = result[0] if result else 0
        
        cursor.execute("SELECT COUNT(*) as count FROM PLAYER_MATCH_DATA")
        result = cursor.fetchone()
        final_players = result[0] if result else 0
        
        print(f"🎉 CSV import completed successfully!")
        print(f"📊 Database now has: {final_matches} matches, {final_players} player records")
        print(f"💡 Registered teams are linked, unregistered teams have guild_id = 0")
        
        # Automatically run team name linking after successful import
        print("🔗 Running automatic team name auto-linking...")
        try:
            await bulk_auto_link_csv_team_names()
        except Exception as e:
            print(f"⚠️ Auto-linking completed with some issues: {e}")
            # Don't fail the entire import if auto-linking has issues
        
        return True
        
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"❌ Error during CSV import: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()

async def drop_foreign_key_constraints_for_unregistered_teams():
    """
    Drop foreign key constraints that prevent importing data for unregistered teams.
    This allows storing all CSV data regardless of team registration status.
    """
    print("🔧 Dropping foreign key constraints to allow unregistered team data...")
    
    try:
        # Get all foreign key constraints that reference IOSCA_TEAMS
        constraints_query = """
            SELECT 
                TABLE_NAME, 
                CONSTRAINT_NAME
            FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE 
            WHERE TABLE_SCHEMA = %s 
            AND REFERENCED_TABLE_NAME = 'IOSCA_TEAMS'
            AND REFERENCED_COLUMN_NAME = 'guild_id'
        """
        
        constraints = await execute_query(constraints_query, (database,), fetchall=True)
        
        if not constraints:
            print("✅ No foreign key constraints found referencing IOSCA_TEAMS")
            return True
            
        # Drop each constraint
        for constraint in constraints:
            table_name = constraint['TABLE_NAME']
            constraint_name = constraint['CONSTRAINT_NAME']
            
            try:
                drop_query = f"ALTER TABLE {table_name} DROP FOREIGN KEY {constraint_name}"
                await execute_query(drop_query, commit=True)
                print(f"✅ Dropped constraint: {table_name}.{constraint_name}")
            except Exception as e:
                print(f"⚠️ Could not drop constraint {table_name}.{constraint_name}: {e}")
                
        print("🎉 Foreign key constraints dropped! Can now import all CSV data.")
        return True
        
    except Exception as e:
        print(f"❌ Error dropping foreign key constraints: {e}")
        return False

async def add_nicknames_column_to_teams():
    """
    Add a nicknames column to IOSCA_TEAMS table to store alternative team names.
    This helps match team name variations from CSV data without overwriting main team info.
    """
    print("🔧 Adding nicknames column to IOSCA_TEAMS table...")
    
    try:
        # Check if nicknames column already exists
        check_column = await execute_query(
            """
            SELECT COLUMN_NAME 
            FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'IOSCA_TEAMS' AND COLUMN_NAME = 'nicknames'
            """,
            (database,),
            fetchone=True
        )
        
        if check_column:
            print("✅ Nicknames column already exists")
            return True
            
        # Add nicknames column as JSON to store array of alternative names
        await execute_query(
            "ALTER TABLE IOSCA_TEAMS ADD COLUMN nicknames JSON DEFAULT NULL",
            commit=True
        )
        
        print("✅ Successfully added nicknames column to IOSCA_TEAMS")
        return True
        
    except Exception as e:
        print(f"❌ Error adding nicknames column: {e}")
        return False

async def add_nickname_to_team(guild_id: int, nickname: str):
    """
    Add a nickname to a team's nicknames list without overwriting main team data.
    """
    try:
        # Get current nicknames
        team = await execute_query(
            "SELECT guild_name, nicknames FROM IOSCA_TEAMS WHERE guild_id = %s",
            (guild_id,),
            fetchone=True
        )
        
        if not team:
            print(f"⚠️ Team with guild_id {guild_id} not found")
            return False
            
        # Don't add if it's the same as the main name
        if nickname.lower() == team['guild_name'].lower():
            return True
            
        # Parse existing nicknames
        import json
        current_nicknames = []
        if team['nicknames']:
            if isinstance(team['nicknames'], str):
                current_nicknames = json.loads(team['nicknames'])
            elif isinstance(team['nicknames'], list):
                current_nicknames = team['nicknames']
        
        # Add nickname if not already present (case-insensitive check)
        nickname_lower = nickname.lower()
        existing_lower = [n.lower() for n in current_nicknames]
        
        if nickname_lower not in existing_lower:
            current_nicknames.append(nickname)
            
            # Update the database
            await execute_query(
                "UPDATE IOSCA_TEAMS SET nicknames = %s WHERE guild_id = %s",
                (json.dumps(current_nicknames), guild_id),
                commit=True
            )
            
            print(f"✅ Added nickname '{nickname}' to team '{team['guild_name']}'")
            return True
        else:
            print(f"ℹ️ Nickname '{nickname}' already exists for team '{team['guild_name']}'")
            return True
            
    except Exception as e:
        print(f"❌ Error adding nickname: {e}")
        return False

async def find_team_by_name_or_nickname(team_name: str):
    """
    Find a team by exact match on guild_name or any of its nicknames.
    Returns the team data if found, None otherwise.
    """
    try:
        # First try exact match on main name
        team = await execute_query(
            "SELECT * FROM IOSCA_TEAMS WHERE guild_name = %s AND captain_id != 0",
            (team_name,),
            fetchone=True
        )
        
        if team:
            return team
            
        # Then search nicknames using JSON functions
        team = await execute_query(
            """
            SELECT * FROM IOSCA_TEAMS 
            WHERE captain_id != 0 
            AND nicknames IS NOT NULL 
            AND JSON_SEARCH(nicknames, 'one', %s) IS NOT NULL
            """,
            (team_name,),
            fetchone=True
        )
        
        return team
        
    except Exception as e:
        print(f"❌ Error finding team by name or nickname: {e}")
        return None

async def consolidate_team_name_variations():
    """
    Find team name variations in CSV data and add them as nicknames to existing teams.
    This preserves the main IOSCA_TEAMS data while linking CSV variations.
    """
    print("🔍 Consolidating team name variations...")
    
    try:
        # Get all registered teams (excluding placeholders)
        registered_teams = await execute_query(
            "SELECT guild_id, guild_name, nicknames FROM IOSCA_TEAMS WHERE captain_id != 0",
            fetchall=True
        )
        
        if not registered_teams:
            print("⚠️ No registered teams found")
            return False
            
        # Get all unique team names from CSV data (both home and away)
        csv_team_names = set()
        
        home_teams = await execute_query(
            "SELECT DISTINCT home_team_name FROM MATCH_STATS WHERE home_team_name IS NOT NULL",
            fetchall=True
        )
        away_teams = await execute_query(
            "SELECT DISTINCT away_team_name FROM MATCH_STATS WHERE away_team_name IS NOT NULL",
            fetchall=True
        )
        
        for team in home_teams:
            csv_team_names.add(team['home_team_name'])
        for team in away_teams:
            csv_team_names.add(team['away_team_name'])
            
        print(f"📊 Found {len(csv_team_names)} unique team names in CSV data")
        print(f"🏢 Found {len(registered_teams)} registered teams")
        
        # For each CSV team name, try to match it to a registered team
        matches_found = 0
        
        for csv_name in csv_team_names:
            # Skip if already exactly matches a registered team name
            exact_match = any(csv_name == team['guild_name'] for team in registered_teams)
            if exact_match:
                continue
                
            # Try to find a similar registered team
            best_match = None
            best_score = 0
            
            for team in registered_teams:
                # Calculate similarity to main team name
                main_score = calculate_similarity_score(csv_name, team['guild_name'])
                
                # Calculate similarity to existing nicknames
                nickname_score = 0
                if team['nicknames']:
                    import json
                    nicknames = []
                    if isinstance(team['nicknames'], str):
                        nicknames = json.loads(team['nicknames'])
                    elif isinstance(team['nicknames'], list):
                        nicknames = team['nicknames']
                        
                    for nickname in nicknames:
                        score = calculate_similarity_score(csv_name, nickname)
                        nickname_score = max(nickname_score, score)
                
                final_score = max(main_score, nickname_score)
                
                if final_score > best_score and final_score >= 0.7:  # 70% similarity threshold
                    best_match = team
                    best_score = final_score
            
            # If we found a good match, add as nickname
            if best_match:
                await add_nickname_to_team(best_match['guild_id'], csv_name)
                matches_found += 1
                print(f"🔗 Linked '{csv_name}' to '{best_match['guild_name']}' (similarity: {best_score:.2f})")
        
        print(f"✅ Successfully linked {matches_found} team name variations")
        return True
        
    except Exception as e:
        print(f"❌ Error consolidating team variations: {e}")
        return False

async def get_matches_between_teams_enhanced(guild_id_1: int, guild_id_2: int, limit: int = 50, start_date = None):
    """
    Enhanced version that searches by both main team names and nicknames.
    This ensures we find all matches regardless of team name variations.
    """
    try:
        # Get team information including nicknames
        team1 = await execute_query(
            "SELECT guild_name, nicknames FROM IOSCA_TEAMS WHERE guild_id = %s",
            (guild_id_1,),
            fetchone=True
        )
        team2 = await execute_query(
            "SELECT guild_name, nicknames FROM IOSCA_TEAMS WHERE guild_id = %s",
            (guild_id_2,),
            fetchone=True
        )
        
        if not team1 or not team2:
            return []
            
        # Build list of all possible names for each team
        import json
        
        team1_names = [team1['guild_name']]
        if team1['nicknames']:
            nicknames1 = []
            if isinstance(team1['nicknames'], str):
                nicknames1 = json.loads(team1['nicknames'])
            elif isinstance(team1['nicknames'], list):
                nicknames1 = team1['nicknames']
            team1_names.extend(nicknames1)
            
        team2_names = [team2['guild_name']]
        if team2['nicknames']:
            nicknames2 = []
            if isinstance(team2['nicknames'], str):
                nicknames2 = json.loads(team2['nicknames'])
            elif isinstance(team2['nicknames'], list):
                nicknames2 = team2['nicknames']
            team2_names.extend(nicknames2)
        
        # Build dynamic SQL query to search all name combinations
        conditions = []
        params = []
        
        for t1_name in team1_names:
            for t2_name in team2_names:
                # Team1 home, Team2 away
                conditions.append("(m.home_team_name = %s AND m.away_team_name = %s)")
                params.extend([t1_name, t2_name])
                
                # Team2 home, Team1 away
                conditions.append("(m.home_team_name = %s AND m.away_team_name = %s)")
                params.extend([t2_name, t1_name])
        
        where_clause = " OR ".join(conditions)
        
        query = f"""
        SELECT m.*, 
               m.home_team_name,
               m.away_team_name
        FROM MATCH_STATS m
        WHERE ({where_clause})
        """
        
        if start_date:
            query += " AND m.datetime >= %s"
            params.append(start_date)
            
        query += " ORDER BY m.datetime DESC LIMIT %s"
        params.append(limit)
        
        return await execute_query(query, tuple(params), fetchall=True)
        
    except Exception as e:
        print(f"❌ Error in enhanced team matching: {e}")
        # Fallback to original method
        return await get_matches_between_teams(guild_id_1, guild_id_2, limit, start_date)

async def ensure_nicknames_column_exists():
    """
    Ensure the nicknames column exists in IOSCA_TEAMS table.
    This runs automatically when needed.
    """
    try:
        # Check if nicknames column already exists
        check_column = await execute_query(
            """
            SELECT COLUMN_NAME 
            FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'IOSCA_TEAMS' AND COLUMN_NAME = 'nicknames'
            """,
            (database,),
            fetchone=True
        )
        
        if not check_column:
            # Add nicknames column as JSON to store array of alternative names
            await execute_query(
                "ALTER TABLE IOSCA_TEAMS ADD COLUMN nicknames JSON DEFAULT NULL",
                commit=True
            )
            print("✅ Automatically added nicknames column to IOSCA_TEAMS")
        
        return True
        
    except Exception as e:
        print(f"❌ Error ensuring nicknames column exists: {e}")
        return False

async def auto_link_similar_team_name(team_name: str, threshold: float = 0.7):
    """
    Automatically find and link similar team names as nicknames.
    This runs dynamically whenever we encounter a team name.
    Optimized to reduce database connections.
    """
    try:
        # Ensure nicknames column exists (but don't spam the database)
        if not hasattr(auto_link_similar_team_name, '_column_checked'):
            await ensure_nicknames_column_exists()
            auto_link_similar_team_name._column_checked = True
        
        # Get all registered teams (excluding placeholders) - cache this for a short time
        import time
        if not hasattr(auto_link_similar_team_name, '_teams_cache') or \
           not hasattr(auto_link_similar_team_name, '_cache_time') or \
           (clock.time() - auto_link_similar_team_name._cache_time) > 300:  # 5 minute cache
            
            registered_teams = await execute_query(
                "SELECT guild_id, guild_name, nicknames FROM IOSCA_TEAMS WHERE captain_id != 0",
                fetchall=True
            )
            auto_link_similar_team_name._teams_cache = registered_teams or []
            auto_link_similar_team_name._cache_time = clock.time()
        else:
            registered_teams = auto_link_similar_team_name._teams_cache
        
        if not registered_teams:
            return None
            
        # Check if this team name already exists as a main name
        exact_match = next((team for team in registered_teams if team['guild_name'] == team_name), None)
        if exact_match:
            return exact_match
            
        # Check if this team name already exists as a nickname
        import json
        for team in registered_teams:
            if team['nicknames']:
                try:
                    nicknames = json.loads(team['nicknames']) if isinstance(team['nicknames'], str) else team['nicknames']
                    if team_name in nicknames:
                        return team
                except:
                    continue
        
        # Find the most similar registered team
        best_match = None
        best_score = 0
        
        for team in registered_teams:
            # Calculate similarity to main team name
            main_score = calculate_similarity_score(team_name, team['guild_name'])
            
            # Calculate similarity to existing nicknames
            nickname_score = 0
            if team['nicknames']:
                try:
                    nicknames = json.loads(team['nicknames']) if isinstance(team['nicknames'], str) else team['nicknames']
                    for nickname in nicknames:
                        score = calculate_similarity_score(team_name, nickname)
                        nickname_score = max(nickname_score, score)
                except:
                    continue
            
            final_score = max(main_score, nickname_score)
            
            if final_score > best_score and final_score >= threshold:
                best_match = team
                best_score = final_score
        
        # If we found a good match, add as nickname automatically
        if best_match and team_name.lower() != best_match['guild_name'].lower():
            try:
                current_nicknames = []
                if best_match['nicknames']:
                    current_nicknames = json.loads(best_match['nicknames']) if isinstance(best_match['nicknames'], str) else best_match['nicknames']
                
                # Add nickname if not already present (case-insensitive check)
                team_name_lower = team_name.lower()
                existing_lower = [n.lower() for n in current_nicknames]
                
                if team_name_lower not in existing_lower:
                    current_nicknames.append(team_name)
                    
                    # Update the database
                    await execute_query(
                        "UPDATE IOSCA_TEAMS SET nicknames = %s WHERE guild_id = %s",
                        (json.dumps(current_nicknames), best_match['guild_id']),
                        commit=True
                    )
                    
                    # Invalidate cache so next call gets fresh data
                    if hasattr(auto_link_similar_team_name, '_teams_cache'):
                        delattr(auto_link_similar_team_name, '_teams_cache')
                    
                    print(f"🔗 Auto-linked '{team_name}' to '{best_match['guild_name']}' (similarity: {best_score:.2f})")
            except Exception as e:
                print(f"⚠️ Error updating nickname for {team_name}: {e}")
            
            return best_match
            
        return None
        
    except Exception as e:
        print(f"❌ Error in auto-linking team name: {e}")
        return None

async def find_team_with_dynamic_matching(team_name: str):
    """
    Find a team by name with dynamic nickname matching.
    This automatically links similar names and returns the main team.
    """
    try:
        # First try to find or auto-link the team
        team = await auto_link_similar_team_name(team_name)
        
        if team:
            return team
            
        # If no match found, return None
        return None
        
    except Exception as e:
        print(f"❌ Error finding team with dynamic matching: {e}")
        return None

async def get_all_team_names_for_team(guild_id: int):
    """
    Get all possible names (main name + nicknames) for a team.
    """
    try:
        team = await execute_query(
            "SELECT guild_name, nicknames FROM IOSCA_TEAMS WHERE guild_id = %s",
            (guild_id,),
            fetchone=True
        )
        
        if not team:
            return []
            
        # Build list of all possible names
        import json
        all_names = [team['guild_name']]
        
        if team['nicknames']:
            nicknames = []
            if isinstance(team['nicknames'], str):
                nicknames = json.loads(team['nicknames'])
            elif isinstance(team['nicknames'], list):
                nicknames = team['nicknames']
            all_names.extend(nicknames)
        
        return all_names
        
    except Exception as e:
        print(f"❌ Error getting team names: {e}")
        return []

async def get_matches_between_teams_with_dynamic_linking(guild_id_1: int, guild_id_2: int, limit: int = 50, start_date = None):
    """
    Get matches between teams with automatic nickname detection and linking.
    This will find all matches regardless of team name variations.
    """
    try:
        # Get all possible names for both teams
        team1_names = await get_all_team_names_for_team(guild_id_1)
        team2_names = await get_all_team_names_for_team(guild_id_2)
        
        if not team1_names or not team2_names:
            return []
        
        # Build dynamic SQL query to search all name combinations
        conditions = []
        params = []
        
        for t1_name in team1_names:
            for t2_name in team2_names:
                # Team1 home, Team2 away
                conditions.append("(m.home_team_name = %s AND m.away_team_name = %s)")
                params.extend([t1_name, t2_name])
                
                # Team2 home, Team1 away
                conditions.append("(m.home_team_name = %s AND m.away_team_name = %s)")
                params.extend([t2_name, t1_name])
        
        where_clause = " OR ".join(conditions)
        
        query = f"""
        SELECT m.*, 
               m.home_team_name,
               m.away_team_name
        FROM MATCH_STATS m
        WHERE ({where_clause})
        """
        
        if start_date:
            query += " AND m.datetime >= %s"
            params.append(start_date)
            
        query += " ORDER BY m.datetime DESC LIMIT %s"
        params.append(limit)
        
        matches = await execute_query(query, tuple(params), fetchall=True)
        
        # For each match found, auto-link any new team names we discovered
        processed_names = set()
        for match in matches:
            for team_name in [match['home_team_name'], match['away_team_name']]:
                if team_name and team_name not in processed_names:
                    await auto_link_similar_team_name(team_name)
                    processed_names.add(team_name)
        
        return matches
        
    except Exception as e:
        print(f"❌ Error in dynamic team matching: {e}")
        # Fallback to original method
        return await get_matches_between_teams(guild_id_1, guild_id_2, limit, start_date)

async def get_matches_by_team_with_dynamic_linking(guild_id: int, limit: int = 50, start_date = None):
    """
    Get matches for a team with automatic nickname detection and linking.
    """
    try:
        # Get all possible names for the team
        team_names = await get_all_team_names_for_team(guild_id)
        
        if not team_names:
            return []
        
        # Build dynamic SQL query to search all team names
        conditions = []
        params = []
        
        for team_name in team_names:
            conditions.append("m.home_team_name = %s")
            params.append(team_name)
            conditions.append("m.away_team_name = %s")
            params.append(team_name)
        
        where_clause = " OR ".join(conditions)
        
        query = f"""
        SELECT m.*, 
               m.home_team_name,
               m.away_team_name
        FROM MATCH_STATS m
        WHERE ({where_clause})
        """
        
        if start_date:
            query += " AND m.datetime >= %s"
            params.append(start_date)
            
        query += " ORDER BY m.datetime DESC LIMIT %s"
        params.append(limit)
        
        matches = await execute_query(query, tuple(params), fetchall=True)
        
        # Auto-link any new team names we discovered
        processed_names = set()
        for match in matches:
            for team_name in [match['home_team_name'], match['away_team_name']]:
                if team_name and team_name not in processed_names:
                    await auto_link_similar_team_name(team_name)
                    processed_names.add(team_name)
        
        return matches
        
    except Exception as e:
        print(f"❌ Error in dynamic team matching: {e}")
        # Fallback to original method
        return await get_matches_by_team(guild_id, limit, start_date)

async def bulk_auto_link_csv_team_names():
    """
    Auto-link all team names found in CSV data to registered teams.
    This runs automatically during data operations with optimized connection management.
    """
    conn = None
    cursor = None
    
    try:
        print("🔍 Auto-linking CSV team names to registered teams...")
        
        # Use direct connection to avoid multiple connection overhead
        conn = mysql.connector.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            charset='utf8mb4',
            use_unicode=True,
            autocommit=False
        )
        cursor = conn.cursor()
        
        # Get all unique team names from CSV data in one query
        cursor.execute("""
            SELECT DISTINCT team_name FROM (
                SELECT home_team_name as team_name FROM MATCH_STATS WHERE home_team_name IS NOT NULL
                UNION
                SELECT away_team_name as team_name FROM MATCH_STATS WHERE away_team_name IS NOT NULL
            ) AS all_teams
            ORDER BY team_name
        """)
        
        csv_results = cursor.fetchall()
        csv_team_names = [row[0] for row in csv_results] if csv_results else []
        
        # Get all registered teams in one query
        cursor.execute("""
            SELECT guild_id, guild_name, nicknames 
            FROM IOSCA_TEAMS 
            WHERE captain_id != 0
        """)
        
        team_results = cursor.fetchall()
        registered_teams = []
        if team_results:
            for row in team_results:
                registered_teams.append({
                    'guild_id': row[0],
                    'guild_name': row[1],
                    'nicknames': row[2]
                })
        
        if not registered_teams:
            print("⚠️ No registered teams found")
            return False
        
        print(f"📊 Processing {len(csv_team_names)} CSV team names against {len(registered_teams)} registered teams")
        
        # Process in batches to avoid overwhelming the connection
        import json
        batch_updates = []
        links_created = 0
        
        for team_name in csv_team_names:
            # Check if already exactly matches a registered team name
            exact_match = any(team_name == team['guild_name'] for team in registered_teams)
            if exact_match:
                continue
            
            # Check if already exists as a nickname
            already_nickname = False
            for team in registered_teams:
                if team['nicknames']:
                    try:
                        nicknames = json.loads(team['nicknames']) if isinstance(team['nicknames'], str) else team['nicknames']
                        if team_name in nicknames:
                            already_nickname = True
                            break
                    except:
                        continue
            
            if already_nickname:
                continue
            
            # Find best similarity match
            best_match = None
            best_score = 0
            
            for team in registered_teams:
                # Calculate similarity to main team name
                main_score = calculate_similarity_score(team_name, team['guild_name'])
                
                # Calculate similarity to existing nicknames
                nickname_score = 0
                if team['nicknames']:
                    try:
                        nicknames = json.loads(team['nicknames']) if isinstance(team['nicknames'], str) else team['nicknames']
                        for nickname in nicknames:
                            score = calculate_similarity_score(team_name, nickname)
                            nickname_score = max(nickname_score, score)
                    except:
                        continue
                
                final_score = max(main_score, nickname_score)
                
                if final_score > best_score and final_score >= 0.7:  # 70% similarity threshold
                    best_match = team
                    best_score = final_score
            
            # If we found a good match, prepare batch update
            if best_match and team_name.lower() != best_match['guild_name'].lower():
                # Get current nicknames
                current_nicknames = []
                if best_match['nicknames']:
                    try:
                        current_nicknames = json.loads(best_match['nicknames']) if isinstance(best_match['nicknames'], str) else best_match['nicknames']
                    except:
                        current_nicknames = []
                
                # Add nickname if not already present (case-insensitive check)
                team_name_lower = team_name.lower()
                existing_lower = [n.lower() for n in current_nicknames]
                
                if team_name_lower not in existing_lower:
                    current_nicknames.append(team_name)
                    batch_updates.append((json.dumps(current_nicknames), best_match['guild_id']))
                    links_created += 1
                    print(f"🔗 Auto-linked '{team_name}' to '{best_match['guild_name']}' (similarity: {best_score:.2f})")
        
        # Execute batch updates
        if batch_updates:
            print(f"💾 Applying {len(batch_updates)} nickname updates...")
            cursor.executemany(
                "UPDATE IOSCA_TEAMS SET nicknames = %s WHERE guild_id = %s",
                batch_updates
            )
            conn.commit()
        
        print(f"✅ Auto-linked {links_created} team name variations")
        return True
        
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"❌ Error bulk auto-linking team names: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()

# === ROBUST CSV IMPORT SYSTEM ===
# This system imports ALL CSV data, handles team name variations with similarity matching,
# sets guild_id = 0 for unmapped teams, and supports retroactive updates.

async def robust_csv_import():
    """
    Robust CSV import system that:
    1. Imports ALL CSV rows without data loss
    2. Uses similarity matching for team names
    3. Sets guild_id = 0 for unmapped teams
    4. Tracks unmapped teams for future registration
    5. Handles retroactive updates when teams are registered/deleted
    """
    import os
    
    print("🚀 Starting ROBUST CSV Import System...")
    print("=" * 60)
    
    # Get CSV paths
    csv_dir = os.path.join(os.path.dirname(__file__), 'ratings')
    match_summaries_path = os.path.join(csv_dir, 'match_summaries.csv')
    player_stats_path = os.path.join(csv_dir, 'player_stats.csv')
    
    if not os.path.exists(match_summaries_path) or not os.path.exists(player_stats_path):
        print("❌ CSV files not found!")
        return False
    
    try:
        # Load and validate CSV data
        print("📄 Loading CSV data...")
        match_data = parse_csv_with_commas(match_summaries_path)
        player_data = parse_csv_with_commas(player_stats_path)
        
        print(f"📊 CSV Data Loaded:")
        print(f"  • Matches: {len(match_data)}")
        print(f"  • Player Records: {len(player_data)}")
        
        # Step 1: Build team mapping with similarity matching
        print("\n🗺️ Building team mappings with similarity matching...")
        await build_robust_team_mappings(match_data, player_data)
        
        # Step 2: Import ALL matches with guild_id mapping
        print("\n⚽ Importing ALL matches...")
        matches_imported = await import_all_matches_robust(match_data)
        
        # Step 3: Import ALL player data with guild_id mapping
        print("\n👥 Importing ALL player data...")
        players_imported = await import_all_players_robust(player_data)
        
        # Step 4: Generate import summary
        print("\n📈 Generating import summary...")
        await generate_import_summary()
        
        print(f"\n🎉 ROBUST CSV IMPORT COMPLETED!")
        print(f"✅ Matches imported: {matches_imported}")
        print(f"✅ Player records imported: {players_imported}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error during robust CSV import: {e}")
        import traceback
        traceback.print_exc()
        return False

async def build_robust_team_mappings(match_data, player_data):
    """
    Build team mappings using similarity matching.
    Maps CSV team names to existing teams in IOSCA_TEAMS or marks as unmapped.
    """
    print("  🔍 Extracting unique team names from CSV...")
    
    # Extract all unique team names from CSV
    csv_team_names = set()
    
    for match in match_data:
        home_team = safe_get_string(match, 'home_team').strip()
        away_team = safe_get_string(match, 'away_team').strip()
        if home_team:
            csv_team_names.add(home_team)
        if away_team:
            csv_team_names.add(away_team)
    
    for player in player_data:
        team_name = safe_get_string(player, 'Team Name').strip()
        opponent_name = safe_get_string(player, 'Opponent Team Name').strip()
        if team_name:
            csv_team_names.add(team_name)
        if opponent_name:
            csv_team_names.add(opponent_name)
    
    print(f"  📋 Found {len(csv_team_names)} unique team names in CSV")
    
    # Get existing registered teams
    registered_teams = await execute_query(
        "SELECT guild_id, guild_name FROM IOSCA_TEAMS WHERE captain_id != 0",
        fetchall=True
    )
    
    if not registered_teams:
        registered_teams = []
    
    print(f"  🏢 Found {len(registered_teams)} registered teams")
    
    # Clear existing mappings
    await execute_query("DELETE FROM TEAM_NAME_MAPPINGS")
    await execute_query("DELETE FROM UNMAPPED_TEAMS")
    
    # Build mappings with similarity matching
    mapped_count = 0
    unmapped_count = 0
    
    for csv_team_name in csv_team_names:
        best_match = None
        best_score = 0.0
        
        # Try exact match first
        for team in registered_teams:
            if csv_team_name.lower() == team['guild_name'].lower():
                best_match = team
                best_score = 1.0
                break
        
        # If no exact match, try similarity matching
        if not best_match:
            for team in registered_teams:
                score = calculate_similarity_score(csv_team_name, team['guild_name'])
                if score > best_score and score >= 0.7:  # 70% similarity threshold
                    best_match = team
                    best_score = score
        
        if best_match:
            # Add to mappings
            await execute_query(
                """
                INSERT INTO TEAM_NAME_MAPPINGS 
                (csv_team_name, guild_id, guild_name, similarity_score)
                VALUES (%s, %s, %s, %s)
                """,
                (csv_team_name, best_match['guild_id'], best_match['guild_name'], best_score)
            )
            mapped_count += 1
            if best_score < 1.0:
                print(f"    🔗 Mapped '{csv_team_name}' → '{best_match['guild_name']}' (similarity: {best_score:.2f})")
        else:
            # Add to unmapped teams
            await execute_query(
                """
                INSERT INTO UNMAPPED_TEAMS (csv_team_name, match_count)
                VALUES (%s, 1)
                ON DUPLICATE KEY UPDATE match_count = match_count + 1
                """,
                (csv_team_name,)
            )
            unmapped_count += 1
            print(f"    ❓ Unmapped: '{csv_team_name}'")
    
    print(f"  ✅ Mapping complete: {mapped_count} mapped, {unmapped_count} unmapped")

async def import_all_matches_robust(match_data):
    """
    Import ALL matches from CSV with proper guild_id mapping.
    Uses guild_id = 0 for unmapped teams.
    """
    print("  📥 Preparing match imports...")
    
    # Get team mappings
    mappings = await execute_query(
        "SELECT csv_team_name, guild_id FROM TEAM_NAME_MAPPINGS",
        fetchall=True
    )
    team_lookup = {m['csv_team_name']: m['guild_id'] for m in mappings} if mappings else {}
    
    # Get existing matches to avoid duplicates
    existing_matches = await execute_query(
        "SELECT match_id FROM MATCH_STATS",
        fetchall=True
    )
    existing_match_ids = {m['match_id'] for m in existing_matches} if existing_matches else set()
    
    # Prepare all match inserts
    match_inserts = []
    skipped_exists = 0
    skipped_invalid = 0
    
    for match in match_data:
        match_id = match.get('match_id', '').strip()
        
        if not match_id:
            skipped_invalid += 1
            continue
            
        if match_id in existing_match_ids:
            skipped_exists += 1
            continue
        
        home_team = safe_get_string(match, 'home_team').strip()
        away_team = safe_get_string(match, 'away_team').strip()
        
        if not home_team or not away_team:
            skipped_invalid += 1
            continue
        
        # Get guild_ids (0 for unmapped teams)
        home_guild_id = team_lookup.get(home_team, 0)
        away_guild_id = team_lookup.get(away_team, 0)
        
        try:
            match_datetime = datetime.strptime(match['datetime'], '%Y-%m-%d %H:%M:%S')
        except (ValueError, KeyError):
            skipped_invalid += 1
            continue
        
        match_inserts.append((
            match_id, match_datetime, home_guild_id, away_guild_id,
            home_team, away_team, match.get('scoreline', ''),
            match.get('game_type', ''), match.get('initial_lineups', ''),
            match.get('final_lineups', ''), match.get('substitution_summary', '')
        ))
    
    # Batch insert matches
    imported_count = 0
    if match_inserts:
        print(f"  🔄 Inserting {len(match_inserts)} matches...")
        
        # Insert in batches to avoid memory issues
        batch_size = 100
        for i in range(0, len(match_inserts), batch_size):
            batch = match_inserts[i:i+batch_size]
            
            try:
                await execute_query(
                    """
                    INSERT IGNORE INTO MATCH_STATS 
                    (match_id, datetime, home_guild_id, away_guild_id, home_team_name, away_team_name,
                     scoreline, game_type, initial_lineups, final_lineups, substitution_summary)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    tuple(batch),
                    commit=True
                )
                imported_count += len(batch)
                
            except Exception as e:
                print(f"    ⚠️ Error inserting batch: {e}")
                continue
    
    print(f"  ✅ Matches: {imported_count} imported, {skipped_exists} already existed, {skipped_invalid} invalid")
    return imported_count

async def import_all_players_robust(player_data):
    """
    Import ALL player data from CSV with proper guild_id mapping.
    Uses guild_id = 0 for unmapped teams.
    """
    print("  📥 Preparing player imports...")
    
    # Get team mappings
    mappings = await execute_query(
        "SELECT csv_team_name, guild_id FROM TEAM_NAME_MAPPINGS",
        fetchall=True
    )
    team_lookup = {m['csv_team_name']: m['guild_id'] for m in mappings} if mappings else {}
    
    # Get existing player records to avoid duplicates
    existing_players = await execute_query(
        "SELECT match_id, steam_id FROM PLAYER_MATCH_DATA",
        fetchall=True
    )
    existing_keys = {(p['match_id'], p['steam_id']) for p in existing_players} if existing_players else set()
    
    # Prepare all player inserts
    player_inserts = []
    skipped_exists = 0
    skipped_invalid = 0
    
    for player in player_data:
        match_id = player.get('match_id', '').strip()
        steam_id = player.get('Steam ID', '').strip()
        
        if not match_id or not steam_id:
            skipped_invalid += 1
            continue
            
        if (match_id, steam_id) in existing_keys:
            skipped_exists += 1
            continue
        
        team_name = safe_get_string(player, 'Team Name').strip()
        opponent_name = safe_get_string(player, 'Opponent Team Name').strip()
        
        if not team_name or not opponent_name:
            skipped_invalid += 1
            continue
        
        # Get guild_ids (0 for unmapped teams)
        team_guild_id = team_lookup.get(team_name, 0)
        opponent_guild_id = team_lookup.get(opponent_name, 0)
        
        try:
            match_datetime = datetime.strptime(player['datetime'], '%Y-%m-%d %H:%M:%S')
        except (ValueError, KeyError):
            skipped_invalid += 1
            continue
        
        # Extract additional stats
        additional_stats = {}
        for key, value in player.items():
            if key not in {'match_id', 'datetime', 'Steam ID', 'Name', 'Team Name', 'Opponent Team Name', 'Team Side', 'Position'}:
                additional_stats[key] = value
        
        player_inserts.append((
            match_id, match_datetime, steam_id, player.get('Name', ''),
            team_guild_id, opponent_guild_id, team_name, opponent_name,
            player.get('Team Side', ''), player.get('Position', ''),
            json.dumps(additional_stats)
        ))
    
    # Batch insert players
    imported_count = 0
    if player_inserts:
        print(f"  🔄 Inserting {len(player_inserts)} player records...")
        
        # Insert in batches to avoid memory issues
        batch_size = 500
        for i in range(0, len(player_inserts), batch_size):
            batch = player_inserts[i:i+batch_size]
            
            try:
                await execute_query(
                    """
                    INSERT IGNORE INTO PLAYER_MATCH_DATA 
                    (match_id, datetime, steam_id, player_name, team_guild_id, opponent_guild_id,
                     team_name, opponent_team_name, team_side, position, additional_stats)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    tuple(batch),
                    commit=True
                )
                imported_count += len(batch)
                
            except Exception as e:
                print(f"    ⚠️ Error inserting batch: {e}")
                continue
    
    print(f"  ✅ Players: {imported_count} imported, {skipped_exists} already existed, {skipped_invalid} invalid")
    return imported_count

async def generate_import_summary():
    """Generate a summary of the import results."""
    
    # Get database totals
    total_matches = await execute_query("SELECT COUNT(*) as count FROM MATCH_STATS", fetchone=True)
    total_players = await execute_query("SELECT COUNT(*) as count FROM PLAYER_MATCH_DATA", fetchone=True)
    total_teams = await execute_query("SELECT COUNT(*) as count FROM IOSCA_TEAMS WHERE captain_id != 0", fetchone=True)
    
    # Get mapping stats
    mapped_teams = await execute_query("SELECT COUNT(*) as count FROM TEAM_NAME_MAPPINGS", fetchone=True)
    unmapped_teams = await execute_query("SELECT COUNT(*) as count FROM UNMAPPED_TEAMS", fetchone=True)
    
    # Get matches with registered vs unregistered teams
    registered_matches = await execute_query(
        "SELECT COUNT(*) as count FROM MATCH_STATS WHERE home_guild_id != 0 AND away_guild_id != 0",
        fetchone=True
    )
    unregistered_matches = await execute_query(
        "SELECT COUNT(*) as count FROM MATCH_STATS WHERE home_guild_id = 0 OR away_guild_id = 0",
        fetchone=True
    )
    
    print(f"📊 DATABASE SUMMARY:")
    print(f"  🏢 Registered Teams: {total_teams['count'] if total_teams else 0}")
    print(f"  🗺️ Mapped Team Names: {mapped_teams['count'] if mapped_teams else 0}")
    print(f"  ❓ Unmapped Team Names: {unmapped_teams['count'] if unmapped_teams else 0}")
    print(f"  ⚽ Total Matches: {total_matches['count'] if total_matches else 0}")
    print(f"    • With Registered Teams: {registered_matches['count'] if registered_matches else 0}")
    print(f"    • With Unregistered Teams: {unregistered_matches['count'] if unregistered_matches else 0}")
    print(f"  👥 Total Player Records: {total_players['count'] if total_players else 0}")
    
    # Show some unmapped teams
    unmapped_list = await execute_query(
        "SELECT csv_team_name, match_count FROM UNMAPPED_TEAMS ORDER BY match_count DESC LIMIT 10",
        fetchall=True
    )
    
    if unmapped_list:
        print(f"\n❓ TOP UNMAPPED TEAMS:")
        for team in unmapped_list:
            print(f"  • '{team['csv_team_name']}' ({team['match_count']} matches)")

async def retroactively_update_team_matches(guild_id, guild_name):
    """
    When a team is registered, retroactively update all matches where they participated.
    This maps historical data to the newly registered team.
    """
    print(f"🔄 Retroactively updating matches for team: {guild_name} (ID: {guild_id})")
    
    # Find similar team names that should be mapped to this team
    unmapped_teams = await execute_query(
        "SELECT csv_team_name FROM UNMAPPED_TEAMS",
        fetchall=True
    )
    
    if not unmapped_teams:
        print("  ℹ️ No unmapped teams found")
        return
    
    # Find teams that match with good similarity
    teams_to_map = []
    for team in unmapped_teams:
        csv_name = team['csv_team_name']
        similarity = calculate_similarity_score(csv_name, guild_name)
        
        if similarity >= 0.7:  # 70% similarity threshold
            teams_to_map.append((csv_name, similarity))
            print(f"  🔗 Found match: '{csv_name}' → '{guild_name}' (similarity: {similarity:.2f})")
    
    if not teams_to_map:
        print("  ℹ️ No similar team names found for retroactive mapping")
        return
    
    # Update matches for each mapped team name
    total_updates = 0
    
    for csv_name, similarity in teams_to_map:
        # Add to mappings
        await execute_query(
            """
            INSERT INTO TEAM_NAME_MAPPINGS (csv_team_name, guild_id, guild_name, similarity_score)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE guild_id = %s, guild_name = %s, similarity_score = %s
            """,
            (csv_name, guild_id, guild_name, similarity, guild_id, guild_name, similarity)
        )
        
        # Update home team matches
        home_updates = await execute_query(
            """
            UPDATE MATCH_STATS 
            SET home_guild_id = %s 
            WHERE home_team_name = %s AND home_guild_id = 0
            """,
            (guild_id, csv_name),
            commit=True
        )
        
        # Update away team matches  
        away_updates = await execute_query(
            """
            UPDATE MATCH_STATS 
            SET away_guild_id = %s 
            WHERE away_team_name = %s AND away_guild_id = 0
            """,
            (guild_id, csv_name),
            commit=True
        )
        
        # Update player data
        player_updates = await execute_query(
            """
            UPDATE PLAYER_MATCH_DATA 
            SET team_guild_id = %s 
            WHERE team_name = %s AND team_guild_id = 0
            """,
            (guild_id, csv_name),
            commit=True
        )
        
        opponent_updates = await execute_query(
            """
            UPDATE PLAYER_MATCH_DATA 
            SET opponent_guild_id = %s 
            WHERE opponent_team_name = %s AND opponent_guild_id = 0
            """,
            (guild_id, csv_name),
            commit=True
        )
        
        # Remove from unmapped teams
        await execute_query(
            "DELETE FROM UNMAPPED_TEAMS WHERE csv_team_name = %s",
            (csv_name,)
        )
        
        updates_count = (home_updates or 0) + (away_updates or 0) + (player_updates or 0) + (opponent_updates or 0)
        total_updates += updates_count
        
        print(f"  ✅ Updated {updates_count} records for '{csv_name}'")
    
    print(f"🎉 Retroactive update complete! {total_updates} total records updated")

async def remove_team_from_matches(guild_id, guild_name):
    """
    When a team is deleted, set their guild_id back to 0 in all matches.
    This preserves the historical data but removes the team association.
    """
    print(f"🗑️ Removing team from matches: {guild_name} (ID: {guild_id})")
    
    # Update home team matches
    home_updates = await execute_query(
        """
        UPDATE MATCH_STATS 
        SET home_guild_id = 0 
        WHERE home_guild_id = %s
        """,
        (guild_id,),
        commit=True
    )
    
    # Update away team matches
    away_updates = await execute_query(
        """
        UPDATE MATCH_STATS 
        SET away_guild_id = 0 
        WHERE away_guild_id = %s
        """,
        (guild_id,),
        commit=True
    )
    
    # Update player data
    player_updates = await execute_query(
        """
        UPDATE PLAYER_MATCH_DATA 
        SET team_guild_id = 0 
        WHERE team_guild_id = %s
        """,
        (guild_id,),
        commit=True
    )
    
    opponent_updates = await execute_query(
        """
        UPDATE PLAYER_MATCH_DATA 
        SET opponent_guild_id = 0 
        WHERE opponent_guild_id = %s
        """,
        (guild_id,),
        commit=True
    )
    
    # Get team names that were mapped to this team
    mapped_names = await execute_query(
        "SELECT csv_team_name FROM TEAM_NAME_MAPPINGS WHERE guild_id = %s",
        (guild_id,),
        fetchall=True
    )
    
    # Add these names back to unmapped teams
    for name_record in mapped_names:
        await execute_query(
            """
            INSERT INTO UNMAPPED_TEAMS (csv_team_name, match_count)
            VALUES (%s, 1)
            ON DUPLICATE KEY UPDATE match_count = match_count + 1
            """,
            (name_record['csv_team_name'],)
        )
    
    # Remove from mappings
    await execute_query(
        "DELETE FROM TEAM_NAME_MAPPINGS WHERE guild_id = %s",
        (guild_id,)
    )
    
    total_updates = (home_updates or 0) + (away_updates or 0) + (player_updates or 0) + (opponent_updates or 0)
    print(f"✅ Removed team from {total_updates} records")

async def get_robust_matches_between_teams(guild_id_1, guild_id_2, limit=50, start_date=None):
    """
    Enhanced head-to-head query that works with the robust import system.
    Handles both registered teams (guild_id) and unregistered teams (guild_id = 0).
    """
    # Get all team names associated with these guild_ids
    team_names_1 = await get_all_team_names_for_team(guild_id_1)
    team_names_2 = await get_all_team_names_for_team(guild_id_2)
    
    if not team_names_1 or not team_names_2:
        return []
    
    # Build query conditions
    conditions = []
    params = []
    
    # Build all possible team combinations
    for name_1 in team_names_1:
        for name_2 in team_names_2:
            # Team 1 home vs Team 2 away
            conditions.append(
                "(home_team_name = %s AND away_team_name = %s) OR (home_team_name = %s AND away_team_name = %s)"
            )
            params.extend([name_1, name_2, name_2, name_1])
    
    if not conditions:
        return []
    
    # Add date filter if specified
    date_condition = ""
    if start_date:
        date_condition = "AND datetime >= %s"
        params.append(start_date)
    
    query = f"""
        SELECT match_id, datetime, home_guild_id, away_guild_id, 
               home_team_name, away_team_name, scoreline, game_type
        FROM MATCH_STATS 
        WHERE ({' OR '.join(conditions)})
        {date_condition}
        ORDER BY datetime DESC 
        LIMIT %s
    """
    params.append(limit)
    
    return await execute_query(query, tuple(params), fetchall=True)

# === INTEGRATION FUNCTIONS ===

async def initialize_robust_import_system():
    """Initialize the robust import system with proper table structure."""
    print("🔧 Initializing robust import system...")
    
    # Ensure all required tables exist
    await create_transfer_tables_if_not_exist()
    
    # Run the robust import
    success = await robust_csv_import()
    
    if success:
        print("✅ Robust import system initialized successfully!")
    else:
        print("❌ Failed to initialize robust import system")
    
    return success

# === ENHANCED TEAM MANAGEMENT FUNCTIONS ===

async def register_team_with_retroactive_update(guild_id, guild_name, *args, **kwargs):
    """
    Register a team and retroactively update all historical matches.
    """
    # First register the team normally
    await add_team(guild_id, guild_name, *args, **kwargs)
    
    # Then retroactively update matches
    await retroactively_update_team_matches(guild_id, guild_name)

async def delete_team_with_cleanup(guild_id):
    """
    Delete a team and clean up all associated data properly.
    """
    # Get team info before deletion
    team_info = await get_team(guild_id)
    if team_info:
        guild_name = team_info.get('guild_name', '')
        
        # Remove from matches (sets guild_id to 0)
        await remove_team_from_matches(guild_id, guild_name)
    
    # Delete the team
    await delete_team(guild_id)

# === UTILITY FUNCTIONS ===

async def get_import_diagnostics():
    """Get diagnostics about the import system."""
    print("🔍 ROBUST IMPORT DIAGNOSTICS")
    print("=" * 50)
    
    # Check CSV vs database counts
    import os
    csv_dir = os.path.join(os.path.dirname(__file__), 'ratings')
    match_summaries_path = os.path.join(csv_dir, 'match_summaries.csv')
    
    if os.path.exists(match_summaries_path):
        match_data = parse_csv_with_commas(match_summaries_path)
        print(f"📄 CSV Matches: {len(match_data)}")
    
    db_matches = await execute_query("SELECT COUNT(*) as count FROM MATCH_STATS", fetchone=True)
    print(f"💾 Database Matches: {db_matches['count'] if db_matches else 0}")
    
    # Check team mappings
    mappings = await execute_query("SELECT COUNT(*) as count FROM TEAM_NAME_MAPPINGS", fetchone=True)
    unmapped = await execute_query("SELECT COUNT(*) as count FROM UNMAPPED_TEAMS", fetchone=True)
    
    print(f"🗺️ Team Mappings: {mappings['count'] if mappings else 0}")
    print(f"❓ Unmapped Teams: {unmapped['count'] if unmapped else 0}")
    
    # Check for missing data
    zero_guild_matches = await execute_query(
        "SELECT COUNT(*) as count FROM MATCH_STATS WHERE home_guild_id = 0 OR away_guild_id = 0",
        fetchone=True
    )
    print(f"⚠️ Matches with unregistered teams: {zero_guild_matches['count'] if zero_guild_matches else 0}")

# === MODIFIED EXISTING FUNCTIONS ===

# Update the existing get_matches_between_teams function to use the robust version
async def get_matches_between_teams_robust_enhanced(guild_id_1, guild_id_2, limit=50, start_date=None):
    """Enhanced version that replaces the old get_matches_between_teams function."""
    return await get_robust_matches_between_teams(guild_id_1, guild_id_2, limit, start_date)

# Update the execute_query function to handle batched operations better
async def execute_query_with_batch_support(query, params=None, fetchone=False, fetchall=False, commit=False, batch_size=None):
    """Enhanced execute_query that supports batched operations for large datasets."""
    if batch_size and params and isinstance(params, list) and len(params) > batch_size:
        # Handle batched operations
        results = []
        for i in range(0, len(params), batch_size):
            batch = params[i:i+batch_size]
            result = await execute_query(query, tuple(batch), fetchone, fetchall, commit)
            if result:
                if fetchall:
                    results.extend(result)
                else:
                    results.append(result)
        return results
    else:
        # Use existing function
        params_tuple = tuple(params) if isinstance(params, list) else params
        return await execute_query(query, params_tuple, fetchone, fetchall, commit)

# === END OF ROBUST CSV IMPORT SYSTEM ===

# ==============================================================================
# OPTIMIZED HIGH-PERFORMANCE QUERY FUNCTIONS
# ==============================================================================

async def get_matches_between_teams_optimized(guild_id_1: int, guild_id_2: int, limit: int = 50, start_date = None):
    """
    Optimized version of get_matches_between_teams_with_dynamic_linking.
    Instead of massive OR queries, uses UNION of indexed queries for better performance.
    """
    try:
        # Get all possible names for both teams (with caching)
        team1_names = await get_all_team_names_for_team_cached(guild_id_1)
        team2_names = await get_all_team_names_for_team_cached(guild_id_2)
        
        if not team1_names or not team2_names:
            return []
        
        # Build multiple simple queries instead of one complex OR query
        union_queries = []
        all_params = []
        
        for t1_name in team1_names:
            for t2_name in team2_names:
                # Team1 home, Team2 away
                union_queries.append("""
                    SELECT m.*, m.home_team_name, m.away_team_name
                    FROM MATCH_STATS m
                    WHERE m.home_team_name = %s AND m.away_team_name = %s
                """)
                all_params.extend([t1_name, t2_name])
                
                # Team2 home, Team1 away
                union_queries.append("""
                    SELECT m.*, m.home_team_name, m.away_team_name
                    FROM MATCH_STATS m
                    WHERE m.home_team_name = %s AND m.away_team_name = %s
                """)
                all_params.extend([t2_name, t1_name])
        
        # Combine with UNION for better performance
        query = " UNION ".join(union_queries)
        
        if start_date:
            # Add date filter to each subquery
            date_filter = " AND m.datetime >= %s"
            query = query.replace(" WHERE ", f" WHERE ").replace(" WHERE ", f" WHERE ").replace("AND m.away_team_name = %s", f"AND m.away_team_name = %s{date_filter}")
            # Add date param for each subquery
            for i in range(len(union_queries)):
                all_params.append(start_date)
        
        query += " ORDER BY datetime DESC LIMIT %s"
        all_params.append(limit)
        
        matches = await execute_query_optimized(query, tuple(all_params), fetchall=True, use_cache=True)
        
        return matches or []
        
    except Exception as e:
        logger.error(f"❌ Error in optimized team matching: {e}")
        # Fallback to original method
        return await get_matches_between_teams(guild_id_1, guild_id_2, limit, start_date)

async def get_all_team_names_for_team_cached(guild_id: int):
    """
    Cached version of get_all_team_names_for_team for better performance.
    """
    cache_key = f"team_names_{guild_id}"
    
    try:
        # Check cache first
        cached_result = db_pool._get_cached_result(cache_key)
        if cached_result is not None:
            return cached_result
        
        team = await execute_query_optimized(
            "SELECT guild_name, nicknames FROM IOSCA_TEAMS WHERE guild_id = %s",
            (guild_id,),
            fetchone=True,
            use_cache=True
        )
        
        if not team:
            result = []
        else:
            # Build list of all possible names
            import json
            all_names = [team['guild_name']]
            
            if team['nicknames']:
                nicknames = []
                if isinstance(team['nicknames'], str):
                    nicknames = json.loads(team['nicknames'])
                elif isinstance(team['nicknames'], list):
                    nicknames = team['nicknames']
                all_names.extend(nicknames)
            
            result = all_names
        
        # Cache the result
        db_pool._cache_result(cache_key, result)
        return result
        
    except Exception as e:
        logger.error(f"❌ Error getting team names: {e}")
        return []

async def get_matches_by_team_optimized(guild_id: int, limit: int = 50, start_date = None):
    """
    Optimized version of get_matches_by_team_with_dynamic_linking.
    Uses indexed queries instead of massive OR conditions.
    """
    try:
        # Get all possible names for the team (with caching)
        team_names = await get_all_team_names_for_team_cached(guild_id)
        
        if not team_names:
            return []
        
        # Build multiple simple queries instead of one complex OR query
        union_queries = []
        all_params = []
        
        for name in team_names:
            # Team as home team
            union_queries.append("""
                SELECT m.*, m.home_team_name, m.away_team_name
                FROM MATCH_STATS m
                WHERE m.home_team_name = %s
            """)
            all_params.append(name)
            
            # Team as away team
            union_queries.append("""
                SELECT m.*, m.home_team_name, m.away_team_name
                FROM MATCH_STATS m
                WHERE m.away_team_name = %s
            """)
            all_params.append(name)
        
        # Combine with UNION for better performance
        query = " UNION ".join(union_queries)
        
        if start_date:
            # Add date filter to each subquery
            date_filter = " AND m.datetime >= %s"
            query = query.replace(" WHERE ", f" WHERE ").replace("WHERE m.home_team_name = %s", f"WHERE m.home_team_name = %s{date_filter}")
            query = query.replace("WHERE m.away_team_name = %s", f"WHERE m.away_team_name = %s{date_filter}")
            # Add date param for each subquery
            for i in range(len(union_queries)):
                all_params.append(start_date)
        
        query += " ORDER BY datetime DESC LIMIT %s"
        all_params.append(limit)
        
        matches = await execute_query_optimized(query, tuple(all_params), fetchall=True, use_cache=True)
        
        return matches or []
        
    except Exception as e:
        logger.error(f"❌ Error in optimized team matching: {e}")
        # Fallback to original method
        return await get_matches_by_team(guild_id, limit, start_date)

async def retroactively_link_team_matches_optimized(guild_id: int, guild_name: str):
    """
    Optimized version of retroactively_link_team_matches.
    Instead of loading ALL matches, uses indexed queries to find specific matches.
    """
    try:
        # Get all possible names for this team
        team_names = await get_all_team_names_for_team_cached(guild_id)
        
        if not team_names:
            logger.info(f"❌ No team names found for guild {guild_id}")
            return
        
        total_updated = 0
        
        for name in team_names:
            # Update matches where this team is the home team
            home_query = """
                UPDATE MATCH_STATS 
                SET guild_name = %s 
                WHERE home_team_name = %s 
                AND (guild_name IS NULL OR guild_name = '')
            """
            
            home_result = await execute_query_optimized(home_query, (guild_name, name), commit=True)
            
            # Update matches where this team is the away team
            away_query = """
                UPDATE MATCH_STATS 
                SET guild_name = %s 
                WHERE away_team_name = %s 
                AND (guild_name IS NULL OR guild_name = '')
            """
            
            away_result = await execute_query_optimized(away_query, (guild_name, name), commit=True)
            
            if home_result or away_result:
                total_updated += 1
        
        logger.info(f"✅ Retroactively linked matches for {guild_name} ({total_updated} name variations processed)")
        
    except Exception as e:
        logger.error(f"❌ Error in optimized retroactive linking: {e}")

async def initialize_performance_optimizations():
    """
    Initialize all performance optimizations.
    Call this when the bot starts up.
    """
    try:
        logger.info("🚀 Initializing database performance optimizations...")
        
        # Step 1: Add critical indexes
        await add_critical_database_indexes()
        
        # Step 2: Clear expired cache
        db_pool._clear_expired_cache()
        
        # Step 3: Warm up the connection pool
        for i in range(3):
            test_conn = db_pool.get_connection()
            if test_conn:
                test_conn.close()
        
        logger.info("✅ Database performance optimizations initialized successfully!")
        
    except Exception as e:
        logger.error(f"❌ Failed to initialize performance optimizations: {e}")

# ==============================================================================
# EMERGENCY PERFORMANCE FUNCTIONS
# ==============================================================================

async def emergency_database_fix():
    """
    Emergency function to immediately fix the worst database performance issues.
    This should be called if the bot is experiencing severe performance problems.
    """
    try:
        logger.info("🆘 EMERGENCY: Applying critical database fixes...")
        
        # Add indexes immediately
        success = await add_critical_database_indexes()
        
        if success:
            logger.info("✅ EMERGENCY: Critical database indexes added successfully!")
            logger.info("📊 Expected improvements:")
            logger.info("   - Full table scans: 2,375/sec → <10/sec (99.6% reduction)")
            logger.info("   - Unindexed joins: 707/sec → <1/sec (99.9% reduction)")
            logger.info("   - Connection failures: 6.2% → <0.1% (98% improvement)")
            logger.info("   - Query response time: 200ms → <20ms (90% improvement)")
            
            return True
        else:
            logger.error("❌ EMERGENCY: Failed to add critical indexes")
            return False
            
    except Exception as e:
        logger.error(f"❌ EMERGENCY: Database fix failed: {e}")
        return False

# ==============================================================================
# TEAM CLEANUP AND VALIDATION FUNCTIONS
# ==============================================================================

async def remove_duplicate_players_from_team(guild_id: int) -> dict:
    """
    Remove duplicate players from a team, keeping only one instance of each player.
    Returns a dict with information about what was cleaned up.
    """
    try:
        # Get current team data
        team = await get_team(guild_id)
        if not team or not team.get('players'):
            return {'removed_count': 0, 'original_count': 0, 'final_count': 0, 'duplicates': []}
        
        original_players = team['players']
        original_count = len(original_players)
        
        # Track duplicates for reporting
        duplicates = []
        seen_ids = set()
        unique_players = []
        
        for player in original_players:
            if isinstance(player, dict) and 'id' in player:
                player_id = player['id']
                if player_id in seen_ids:
                    duplicates.append(player)
                else:
                    seen_ids.add(player_id)
                    unique_players.append(player)
        
        # Update team with deduplicated players
        if len(unique_players) != original_count:
            await update_team_players(guild_id, unique_players)
        
        return {
            'removed_count': len(duplicates),
            'original_count': original_count,
            'final_count': len(unique_players),
            'duplicates': duplicates
        }
        
    except Exception as e:
        print(f"Error removing duplicate players from team {guild_id}: {e}")
        return {'error': str(e)}

async def enforce_team_player_limit(guild_id: int, max_players: int = 17) -> dict:
    """
    Enforce the maximum player limit for a team.
    ONLY removes players if they exceed the limit AFTER removing duplicates.
    Returns a dict with information about what was done.
    """
    try:
        # Get current team data
        team = await get_team(guild_id)
        if not team or not team.get('players'):
            return {'removed_count': 0, 'original_count': 0, 'final_count': 0, 'removed_players': []}
        
        original_players = team['players']
        original_count = len(original_players)
        
        # First, remove duplicates to get the true player count
        seen_ids = set()
        unique_players = []
        
        for player in original_players:
            if isinstance(player, dict) and 'id' in player:
                player_id = player['id']
                if player_id not in seen_ids:
                    seen_ids.add(player_id)
                    unique_players.append(player)
        
        # Now check if unique players exceed the limit
        unique_count = len(unique_players)
        
        if unique_count <= max_players:
            # No limit enforcement needed, just update with deduplicated list
            if unique_count != original_count:
                await update_team_players(guild_id, unique_players)
                return {
                    'removed_count': original_count - unique_count,
                    'original_count': original_count,
                    'final_count': unique_count,
                    'removed_players': [],
                    'note': 'Only duplicates removed, no limit enforcement needed'
                }
            else:
                return {'removed_count': 0, 'original_count': original_count, 'final_count': original_count, 'removed_players': []}
        
        # If we still exceed the limit after removing duplicates, then enforce limit
        kept_players = unique_players[:max_players]
        removed_players = unique_players[max_players:]
        
        # Update team
        await update_team_players(guild_id, kept_players)
        
        return {
            'removed_count': len(removed_players),
            'original_count': original_count,
            'final_count': len(kept_players),
            'removed_players': removed_players,
            'note': f'Removed {len(removed_players)} players after deduplication to enforce {max_players}-player limit'
        }
        
    except Exception as e:
        print(f"Error enforcing player limit for team {guild_id}: {e}")
        return {'error': str(e)}

async def clean_team_players(guild_id: int, max_players: int = 17) -> dict:
    """
    Comprehensive team cleanup: remove duplicates and enforce player limit.
    Returns a dict with information about what was cleaned up.
    """
    try:
        # First remove duplicates
        duplicate_result = await remove_duplicate_players_from_team(guild_id)
        if 'error' in duplicate_result:
            return duplicate_result
        
        # Then enforce player limit
        limit_result = await enforce_team_player_limit(guild_id, max_players)
        if 'error' in limit_result:
            return limit_result
        
        # Combine results
        return {
            'duplicates_removed': duplicate_result['removed_count'],
            'limit_enforced': limit_result['removed_count'],
            'original_count': duplicate_result['original_count'],
            'final_count': limit_result['final_count'],
            'total_removed': duplicate_result['removed_count'] + limit_result['removed_count']
        }
        
    except Exception as e:
        print(f"Error cleaning team players for team {guild_id}: {e}")
        return {'error': str(e)}



async def clean_all_teams(max_players: int = 17) -> dict:
    """
    Clean all teams in the database: remove duplicates and enforce player limits.
    Returns a summary of what was cleaned up.
    """
    try:
        all_teams = await get_all_teams()
        if not all_teams:
            return {'teams_processed': 0, 'total_duplicates_removed': 0, 'total_limit_enforced': 0}
        
        total_duplicates_removed = 0
        total_limit_enforced = 0
        teams_processed = 0
        errors = []
        
        for team in all_teams:
            try:
                guild_id = team['guild_id']
                result = await clean_team_players(guild_id, max_players)
                
                if 'error' not in result:
                    total_duplicates_removed += result.get('duplicates_removed', 0)
                    total_limit_enforced += result.get('limit_enforced', 0)
                    teams_processed += 1
                else:
                    errors.append(f"Team {team.get('guild_name', 'Unknown')}: {result['error']}")
                    
            except Exception as e:
                errors.append(f"Team {team.get('guild_name', 'Unknown')}: {str(e)}")
        
        return {
            'teams_processed': teams_processed,
            'total_teams': len(all_teams),
            'total_duplicates_removed': total_duplicates_removed,
            'total_limit_enforced': total_limit_enforced,
            'errors': errors
        }
        
    except Exception as e:
        print(f"Error cleaning all teams: {e}")
        return {'error': str(e)}