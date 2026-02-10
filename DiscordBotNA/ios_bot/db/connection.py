"""
Database connection pool management for PostgreSQL with asyncpg
"""

import asyncpg
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


class DatabasePool:
    """Manages PostgreSQL connection pool with asyncpg"""
    
    def __init__(self, connection_string: str):
        self.connection_string = connection_string
        self.pool: Optional[asyncpg.Pool] = None
        self._initialized = False
    
    async def initialize(self):
        """Initialize the connection pool"""
        if self._initialized:
            return
        
        try:
            self.pool = await asyncpg.create_pool(
                self.connection_string,
                min_size=5,
                max_size=20,
                command_timeout=60,
                statement_cache_size=0  # Disable for pgbouncer compatibility
            )
            self._initialized = True
            logger.info("✅ Database connection pool initialized")
        except Exception as e:
            logger.error(f"❌ Failed to initialize database pool: {e}")
            raise
    
    async def close(self):
        """Close the connection pool"""
        if self.pool:
            await self.pool.close()
            self._initialized = False
            logger.info("Database connection pool closed")
    
    async def execute(self, query: str, *args):
        """Execute a query that doesn't return results (INSERT, UPDATE, DELETE)"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        
        async with self.pool.acquire() as conn:
            return await conn.execute(query, *args)
    
    async def fetch(self, query: str, *args):
        """Fetch multiple rows"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        
        async with self.pool.acquire() as conn:
            return await conn.fetch(query, *args)
    
    async def fetchrow(self, query: str, *args):
        """Fetch a single row"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(query, *args)
    
    async def fetchval(self, query: str, *args):
        """Fetch a single value"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        
        async with self.pool.acquire() as conn:
            return await conn.fetchval(query, *args)
    
    async def transaction(self):
        """Get a transaction context"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        
        return self.pool.acquire()


def get_connection_string() -> str:
    """Get PostgreSQL connection string from environment"""
    conn_string = os.getenv('SUPABASE_DB_URL')
    if not conn_string:
        raise ValueError("SUPABASE_DB_URL environment variable not set")
    return conn_string
