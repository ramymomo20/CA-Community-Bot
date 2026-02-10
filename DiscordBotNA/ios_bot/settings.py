# Bot Settings
# Environment variables and configuration settings

from __future__ import annotations
from typing import Optional
from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # ----- Discord -----
    DISCORD_BOT_TOKEN: SecretStr
    CLIENT_ID: SecretStr
    
    # Database-loaded configuration
    _main_guild_id: Optional[str] = None
    _admin_role_id: Optional[str] = None
    _team_leader_id: Optional[str] = None
    _results_channel: Optional[str] = None
    _fixtures_channel: Optional[str] = None
    _confirmed_channel: Optional[str] = None
    _captains_channel: Optional[str] = None

    # ----- PostgreSQL Database (Supabase) -----
    SUPABASE_DB_URL: SecretStr  # Full PostgreSQL connection string
    SUPABASE_POOLER_URL: Optional[SecretStr] = None  # Optional: Connection pooler URL
    SUPABASE_PROJECT_URL: Optional[str] = None  # Optional: Supabase project URL
    SUPABASE_ANON_KEY: Optional[SecretStr] = None  # Optional: Supabase anonymous key
    DB_POOL_MIN_SIZE: int = 3  # Minimum connection pool size
    DB_POOL_MAX_SIZE: int = 10  # Maximum connection pool size
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False
    )
    
    @field_validator("DISCORD_BOT_TOKEN")
    @classmethod
    def validate_token(cls, v: SecretStr) -> SecretStr:
        """Validate Discord token is not empty."""
        if not v.get_secret_value().strip():
            raise ValueError("DISCORD_BOT_TOKEN is required")
        return v
    
    @field_validator("SUPABASE_DB_URL")
    @classmethod
    def validate_db_url(cls, v: SecretStr) -> SecretStr:
        """Validate Supabase database URL is not empty."""
        if not v.get_secret_value().strip():
            raise ValueError("SUPABASE_DB_URL is required")
        return v
    
    @property
    def MAIN_GUILD_ID(self) -> Optional[str]:
        """Get main guild ID (loaded from database)."""
        return self._main_guild_id
    
    @property
    def ADMIN_ROLE_ID(self) -> Optional[str]:
        """Get admin role ID (loaded from database)."""
        return self._admin_role_id
    
    @property
    def TEAM_LEADER_ID(self) -> Optional[str]:
        """Get team leader role ID (loaded from database)."""
        return self._team_leader_id
    
    @property
    def RESULTS_CHANNEL(self) -> Optional[str]:
        """Get results channel ID (loaded from database)."""
        return self._results_channel

    @property
    def CONFIRMED_CHANNEL(self) -> Optional[str]:
        """Get confirmed schedule channel ID (loaded from database)."""
        return self._confirmed_channel

    @property
    def CAPTAINS_CHANNEL(self) -> Optional[str]:
        """Get captains scheduling channel ID (loaded from database)."""
        return self._captains_channel
    
    async def load_guild_config(self, db):
        """
        Load guild configuration from database.
        
        This should be called once during bot initialization.
        """
        try:
            try:
                result = await db.fetchrow(
                    "SELECT guild_id, admin_role_id, team_leader_role_id, results_channel, confirmed_channel, captains_channel FROM main_discord LIMIT 1"
                )
            except Exception:
                result = await db.fetchrow(
                    "SELECT guild_id, admin_role_id, team_leader_role_id, results_channel, confirmed_channel FROM main_discord LIMIT 1"
                )
            if result:
                self._main_guild_id = result['guild_id']
                self._admin_role_id = result['admin_role_id']
                self._team_leader_id = result['team_leader_role_id']
                self._results_channel = result['results_channel']
                self._confirmed_channel = result['confirmed_channel']
                self._captains_channel = result.get('captains_channel')
        except Exception as e:
            # Log but don't fail - these are optional
            print(f"Could not load guild config from database: {e}")

# Singleton instance
settings = Settings()
