"""
iOS Bot Database Module - PostgreSQL with asyncpg
Main database interface and connection pool management
"""

from .connection import DatabasePool
from .teams import TeamOperations
from .players import PlayerOperations
from .matches import MatchOperations
from .servers import ServerOperations
from .server_assets import ServerAssetOperations
from .tournaments import TournamentOperations
from .stats_moderation import ensure_stats_moderation_schema
from .utils import calculate_similarity, normalize_team_name

__all__ = [
    'DatabasePool',
    'TeamOperations',
    'PlayerOperations',
    'MatchOperations',
    'ServerOperations',
    'ServerAssetOperations',
    'TournamentOperations',
    'calculate_similarity',
    'normalize_team_name'
]


class Database:
    """Main database interface combining all operations"""
    
    def __init__(self, connection_string: str):
        self.pool = DatabasePool(connection_string)
        self.teams = TeamOperations(self.pool)
        self.players = PlayerOperations(self.pool)
        self.matches = MatchOperations(self.pool)
        self.servers = ServerOperations(self.pool)
        self.server_assets = ServerAssetOperations(self.pool)
        self.tournaments = TournamentOperations(self.pool)
    
    async def initialize(self):
        """Initialize connection pool"""
        await self.pool.initialize()
        async with self.pool.acquire() as conn:
            await ensure_stats_moderation_schema(conn)
    
    async def close(self):
        """Close connection pool"""
        await self.pool.close()
    
    async def __aenter__(self):
        await self.initialize()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
