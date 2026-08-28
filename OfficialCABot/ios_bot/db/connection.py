"""
Database connection pool management for PostgreSQL with asyncpg
"""

import asyncpg
import logging
import os
import asyncio
from typing import Optional

logger = logging.getLogger(__name__)


class DatabasePool:
    """Manages PostgreSQL connection pool with asyncpg"""
    
    def __init__(self, connection_string: str):
        self.connection_string = connection_string
        self.pool: Optional[asyncpg.Pool] = None
        self._initialized = False
        self._reconnect_lock = asyncio.Lock()
    
    async def initialize(self):
        """Initialize the connection pool"""
        if self._initialized:
            return
        
        try:
            # These were previously hardcoded (5/20/60), silently ignoring
            # the DB_POOL_* settings documented in .env for the Nano tier.
            min_size = int(os.getenv("DB_POOL_MIN_SIZE", "5"))
            max_size = int(os.getenv("DB_POOL_MAX_SIZE", "20"))
            command_timeout = int(os.getenv("DB_POOL_COMMAND_TIMEOUT", "60"))
            self.pool = await asyncpg.create_pool(
                self.connection_string,
                min_size=min_size,
                max_size=max_size,
                command_timeout=command_timeout,
                statement_cache_size=0,  # Disable for pgbouncer compatibility
                timeout=15,
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

    async def _reconnect_pool(self, failed_pool):
        """Force-recreate the pool after transient connectivity errors.

        `failed_pool` is the pool object the caller observed fail. Concurrent
        callers can hit a connection error around the same time; without this
        check each would tear down and rebuild the pool in turn, cancelling
        every other in-flight query on the pool they didn't personally fail
        on. Only the first caller actually rebuilds -- later callers see
        `self.pool` no longer matches `failed_pool` and just reuse it.
        """
        async with self._reconnect_lock:
            if self.pool is not failed_pool and self._initialized:
                return
            try:
                if self.pool:
                    await self.pool.close()
            except Exception:
                pass
            self.pool = None
            self._initialized = False
            await self.initialize()

    async def _run_with_retry(self, op):
        """Run a DB operation and retry once if connection failed."""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        current_pool = self.pool
        try:
            return await op()
        except (asyncpg.PostgresConnectionError, asyncio.TimeoutError, OSError) as first_error:
            logger.warning(f"DB operation failed, retrying once: {first_error}")
            try:
                await self._reconnect_pool(current_pool)
            except Exception as reconnect_error:
                logger.error(f"DB reconnect failed: {reconnect_error}")
                raise first_error
            return await op()
    
    async def execute(self, query: str, *args):
        """Execute a query that doesn't return results (INSERT, UPDATE, DELETE)"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        return await self._run_with_retry(self._make_execute_op(query, *args))
    
    async def fetch(self, query: str, *args):
        """Fetch multiple rows"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        return await self._run_with_retry(self._make_fetch_op(query, *args))
    
    async def fetchrow(self, query: str, *args):
        """Fetch a single row"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        return await self._run_with_retry(self._make_fetchrow_op(query, *args))
    
    async def fetchval(self, query: str, *args):
        """Fetch a single value"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        return await self._run_with_retry(self._make_fetchval_op(query, *args))

    def acquire(self):
        """Expose asyncpg pool acquire() for callers that manage their own transactions."""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        return self.pool.acquire()
    
    def _make_execute_op(self, query: str, *args):
        async def _op():
            async with self.pool.acquire() as conn:
                return await conn.execute(query, *args)
        return _op

    def _make_fetch_op(self, query: str, *args):
        async def _op():
            async with self.pool.acquire() as conn:
                return await conn.fetch(query, *args)
        return _op

    def _make_fetchrow_op(self, query: str, *args):
        async def _op():
            async with self.pool.acquire() as conn:
                return await conn.fetchrow(query, *args)
        return _op

    def _make_fetchval_op(self, query: str, *args):
        async def _op():
            async with self.pool.acquire() as conn:
                return await conn.fetchval(query, *args)
        return _op


def get_connection_string() -> str:
    """Get PostgreSQL connection string from environment"""
    conn_string = os.getenv('SUPABASE_DB_URL') or os.getenv('SUPABASE_POOLER_URL')
    if not conn_string:
        raise ValueError("SUPABASE_DB_URL or SUPABASE_POOLER_URL environment variable not set")
    return conn_string
