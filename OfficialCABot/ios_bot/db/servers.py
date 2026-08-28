"""
Server operations for PostgreSQL database
"""

import logging
from typing import Optional, List, Dict, Any
from .connection import DatabasePool
from .cache import QueryCache
from ..utils.credential_crypto import encrypt_secret, decrypt_secret

logger = logging.getLogger(__name__)

# Servers change only via /edit_servers (add/edit/delete) or
# /set_server_ingame_name -- every one of those calls _invalidate_cache()
# right after writing, so this long TTL is just a safety net.
_SERVERS_CACHE_TTL_SECONDS = 900


def _decrypt_server_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """password/host_password are stored encrypted (see credential_crypto) --
    decrypt them here, once, right at the DB-read boundary, so every caller
    downstream (RCON connections, /edit_servers display, etc.) just sees the
    real value the way it did before encryption was added."""
    if "password" in row:
        row["password"] = decrypt_secret(row["password"])
    if "host_password" in row:
        row["host_password"] = decrypt_secret(row["host_password"])
    return row


class ServerOperations:
    """Handles all server-related database operations"""

    def __init__(self, pool: DatabasePool):
        self.pool = pool
        self._cache = QueryCache(safety_ttl_seconds=_SERVERS_CACHE_TTL_SECONDS)

    def _invalidate_cache(self) -> None:
        self._cache.invalidate_prefix("servers:")
    
    async def add_server(
        self,
        name: str,
        address: str,
        password: str,
        sftp_ip: Optional[str] = None,
        host_username: Optional[str] = None,
        host_password: Optional[str] = None,
        server_type: str = 'linux',
        is_active: bool = True
    ) -> Optional[int]:
        """Add a new server"""
        query = """
        INSERT INTO IOS_SERVERS (
            name, address, password, sftp_ip, host_username, host_password,
            server_type, is_active
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        RETURNING id
        """
        
        try:
            server_id = await self.pool.fetchval(
                query,
                name,
                address,
                encrypt_secret(password),
                sftp_ip,
                host_username,
                encrypt_secret(host_password),
                server_type,
                is_active
            )
            logger.info(f"✅ Server added: {name} (ID: {server_id})")
            self._invalidate_cache()
            return server_id
        except Exception as e:
            logger.error(f"❌ Failed to add server: {e}")
            return None
    
    async def get_all_servers(self) -> List[Dict[str, Any]]:
        """Retrieve all active servers (cached until a server is added/edited/removed).

        This is what /ready needs to even show a server picker, so on a DB
        error, fall back to the last successfully-fetched list instead of
        raising -- the server roster essentially never changes, and a stale
        list is far better than /ready being unusable during a DB blip.
        """
        cached = self._cache.get("servers:all")
        if cached is not None:
            return list(cached)
        try:
            query = """
            SELECT name, address, password
            FROM IOS_SERVERS
            WHERE is_active = TRUE
            ORDER BY name ASC
            """
            rows = await self.pool.fetch(query)
        except Exception as e:
            stale = self._cache.get_last_good("servers:all")
            if stale is not None:
                logger.warning(f"get_all_servers() DB fetch failed ({e}); serving last-known cached list")
                return list(stale)
            logger.error(f"get_all_servers() DB fetch failed and no cached fallback is available: {e}")
            raise
        data = [_decrypt_server_row(dict(row)) for row in rows]
        self._cache.set("servers:all", data)
        return data

    async def get_server_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Retrieve a server by its name (cached, with the same DB-outage
        fallback as get_all_servers -- /ready calls this once a server is
        picked, so it needs the same resilience)."""
        cache_key = f"servers:by_name:{name}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return dict(cached) if cached else None
        try:
            query = "SELECT * FROM IOS_SERVERS WHERE name = $1"
            row = await self.pool.fetchrow(query, name)
        except Exception as e:
            stale = self._cache.get_last_good(cache_key)
            if stale is not None:
                logger.warning(f"get_server_by_name({name!r}) DB fetch failed ({e}); serving last-known cached value")
                return dict(stale) if stale else None
            logger.error(f"get_server_by_name({name!r}) DB fetch failed and no cached fallback is available: {e}")
            raise
        result = _decrypt_server_row(dict(row)) if row else None
        self._cache.set(cache_key, result if result is not None else {})
        return result

    async def get_server_by_id(self, server_id: int) -> Optional[Dict[str, Any]]:
        """Retrieve a server by its ID"""
        query = "SELECT * FROM IOS_SERVERS WHERE id = $1"
        row = await self.pool.fetchrow(query, server_id)
        return _decrypt_server_row(dict(row)) if row else None

    async def get_server_by_ingame_name(self, in_game_server_name: str) -> Optional[Dict[str, Any]]:
        """Retrieve a server by the name it reports in match JSON (matchInfo.serverName),
        which is often different from the admin-facing `name` used for RCON selection."""
        if not in_game_server_name:
            return None
        query = "SELECT * FROM IOS_SERVERS WHERE in_game_server_name = $1"
        row = await self.pool.fetchrow(query, in_game_server_name)
        return _decrypt_server_row(dict(row)) if row else None

    async def set_ingame_server_name(self, server_id: int, in_game_server_name: str) -> bool:
        """Record the exact serverName string this server reports in match JSON."""
        try:
            await self.pool.execute(
                "UPDATE IOS_SERVERS SET in_game_server_name = $1, updated_at = CURRENT_TIMESTAMP WHERE id = $2",
                str(in_game_server_name or "").strip() or None,
                server_id,
            )
            self._invalidate_cache()
            return True
        except Exception as e:
            logger.error(f"Failed to set in-game server name for server {server_id}: {e}")
            return False
    
    async def update_server(
        self,
        server_id: int,
        name: Optional[str] = None,
        address: Optional[str] = None,
        password: Optional[str] = None,
        sftp_ip: Optional[str] = None,
        host_username: Optional[str] = None,
        host_password: Optional[str] = None,
        server_type: Optional[str] = None,
        is_active: Optional[bool] = None
    ) -> bool:
        """Update server information"""
        updates = []
        params = []
        param_count = 1
        
        if name is not None:
            updates.append(f"name = ${param_count}")
            params.append(name)
            param_count += 1
        
        if address is not None:
            updates.append(f"address = ${param_count}")
            params.append(address)
            param_count += 1
        
        if password is not None:
            updates.append(f"password = ${param_count}")
            params.append(encrypt_secret(password))
            param_count += 1
        
        if sftp_ip is not None:
            updates.append(f"sftp_ip = ${param_count}")
            params.append(sftp_ip)
            param_count += 1
        
        if host_username is not None:
            updates.append(f"host_username = ${param_count}")
            params.append(host_username)
            param_count += 1
        
        if host_password is not None:
            updates.append(f"host_password = ${param_count}")
            params.append(encrypt_secret(host_password))
            param_count += 1
        
        if server_type is not None:
            updates.append(f"server_type = ${param_count}")
            params.append(server_type)
            param_count += 1
        
        if is_active is not None:
            updates.append(f"is_active = ${param_count}")
            params.append(is_active)
            param_count += 1
        
        if not updates:
            return False
        
        updates.append(f"updated_at = CURRENT_TIMESTAMP")
        params.append(server_id)
        
        query = f"UPDATE IOS_SERVERS SET {', '.join(updates)} WHERE id = ${param_count}"
        
        try:
            await self.pool.execute(query, *params)
            logger.info(f"Server {server_id} updated")
            self._invalidate_cache()
            return True
        except Exception as e:
            logger.error(f"Failed to update server: {e}")
            return False

    async def delete_server(self, server_id: int) -> bool:
        """Delete a server"""
        query = "DELETE FROM IOS_SERVERS WHERE id = $1"

        try:
            await self.pool.execute(query, server_id)
            logger.info(f"Server {server_id} deleted")
            self._invalidate_cache()
            return True
        except Exception as e:
            logger.error(f"Failed to delete server: {e}")
            return False

    async def deactivate_server(self, server_id: int) -> bool:
        """Deactivate a server (soft delete)"""
        query = "UPDATE IOS_SERVERS SET is_active = FALSE, updated_at = CURRENT_TIMESTAMP WHERE id = $1"

        try:
            await self.pool.execute(query, server_id)
            logger.info(f"Server {server_id} deactivated")
            self._invalidate_cache()
            return True
        except Exception as e:
            logger.error(f"Failed to deactivate server: {e}")
            return False

    async def activate_server(self, server_id: int) -> bool:
        """Activate a server"""
        query = "UPDATE IOS_SERVERS SET is_active = TRUE, updated_at = CURRENT_TIMESTAMP WHERE id = $1"
        
        try:
            await self.pool.execute(query, server_id)
            logger.info(f"Server {server_id} activated")
            self._invalidate_cache()
            return True
        except Exception as e:
            logger.error(f"Failed to activate server: {e}")
            return False

    async def server_exists(self, name: str) -> bool:
        """Check if a server exists by name"""
        query = "SELECT EXISTS(SELECT 1 FROM IOS_SERVERS WHERE name = $1)"
        return await self.pool.fetchval(query, name)

    async def get_all_servers_with_details(self) -> List[Dict[str, Any]]:
        """Get all servers with detailed information including credentials
        (cached until a server is added/edited/removed)."""
        cached = self._cache.get("servers:with_details")
        if cached is not None:
            return list(cached)
        query = """
        SELECT id, name, address, password, sftp_ip, host_username, host_password,
               server_type, is_active, created_at, updated_at, in_game_server_name
        FROM IOS_SERVERS
        WHERE is_active = TRUE
        ORDER BY name ASC
        """
        rows = await self.pool.fetch(query)
        data = [_decrypt_server_row(dict(row)) for row in rows]
        self._cache.set("servers:with_details", data)
        return data
    
    async def initialize_default_servers(self) -> bool:
        """Initialize default servers if database is empty"""
        query = "SELECT COUNT(*) FROM IOS_SERVERS WHERE is_active = TRUE"
        count = await self.pool.fetchval(query)
        
        if count == 0:
            logger.info("No servers found, adding default servers...")
            
            default_servers = [
                ("Florida", "*", "*", "*", "*", "*", "linux"),
                ("Georgia", "*", "*", "*", "*", "*", "linux")
            ]
            
            for name, address, password, sftp_ip, host_username, host_password, server_type in default_servers:
                await self.add_server(
                    name=name,
                    address=address,
                    password=password,
                    sftp_ip=sftp_ip,
                    host_username=host_username,
                    host_password=host_password,
                    server_type=server_type,
                    is_active=True
                )
                logger.info(f"Added default server: {name}")
            
            logger.info("Default servers initialization complete")
            return True
        
        return False
    
    async def get_servers_for_compile_stats(self) -> List[Dict[str, Any]]:
        """Get servers formatted for compile stats with directory paths.
        
        Uses address field (e.g., 199.127.62.217:27015) to extract the game host/port
        for the remote statistics directory naming convention.
        SFTP connection prefers the explicit sftp_ip field when present, so custom SSH
        ports or a separate SFTP host are honored.
        Directory path is /{host}_{game_port}/iosoccer/statistics for Linux.
        """
        servers = await self.get_all_servers_with_details()
        formatted_servers = []
        
        for server in servers:
            if not server.get('host_username') or not server.get('address'):
                continue
            
            address_raw = str(server['address']).strip()
            address_parts = address_raw.split(':', 1)
            game_host = address_parts[0].strip()
            game_port = address_parts[1].strip() if len(address_parts) > 1 and address_parts[1].strip() else '27015'
            
            server_type = str(server.get('server_type', 'linux') or 'linux').strip().lower()
            is_windows = server_type.lower() == 'windows'

            sftp_host = game_host
            sftp_port = 22 if is_windows else 8822
            sftp_ip_raw = str(server.get('sftp_ip') or '').strip()
            if sftp_ip_raw:
                sftp_parts = sftp_ip_raw.split(':', 1)
                if sftp_parts[0].strip():
                    sftp_host = sftp_parts[0].strip()
                if len(sftp_parts) > 1 and sftp_parts[1].strip():
                    try:
                        sftp_port = int(sftp_parts[1].strip())
                    except ValueError:
                        pass
            
            if is_windows:
                # Windows server with different path structure and SFTP port
                directory_path = "/C:/Users/Administrator/Documents/iosoccer/iosoccer/statistics"
            else:
                # Standard Linux server path structure
                directory_path = f"/{game_host}_{game_port}/iosoccer/statistics"
            
            formatted_servers.append({
                'name': server['name'],
                'host': sftp_host,
                'port': sftp_port,
                'user': server['host_username'],
                'pass': server.get('host_password', ''),
                'password': server.get('host_password', ''),  # Keep both for compatibility
                'dir': directory_path,
                'game_host': game_host,
                'game_port': game_port,
                'server_type': server_type,
                'sftp_ip': sftp_ip_raw or None,
            })
        
        return formatted_servers
