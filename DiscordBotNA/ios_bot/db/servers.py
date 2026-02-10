"""
Server operations for PostgreSQL database
"""

import logging
from typing import Optional, List, Dict, Any
from .connection import DatabasePool

logger = logging.getLogger(__name__)


class ServerOperations:
    """Handles all server-related database operations"""
    
    def __init__(self, pool: DatabasePool):
        self.pool = pool
    
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
                password,
                sftp_ip,
                host_username,
                host_password,
                server_type,
                is_active
            )
            logger.info(f"✅ Server added: {name} (ID: {server_id})")
            return server_id
        except Exception as e:
            logger.error(f"❌ Failed to add server: {e}")
            return None
    
    async def get_all_servers(self) -> List[Dict[str, Any]]:
        """Retrieve all active servers"""
        query = """
        SELECT name, address, password
        FROM IOS_SERVERS
        WHERE is_active = TRUE
        ORDER BY name ASC
        """
        rows = await self.pool.fetch(query)
        return [dict(row) for row in rows]
    
    async def get_server_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Retrieve a server by its name"""
        query = "SELECT * FROM IOS_SERVERS WHERE name = $1"
        row = await self.pool.fetchrow(query, name)
        return dict(row) if row else None
    
    async def get_server_by_id(self, server_id: int) -> Optional[Dict[str, Any]]:
        """Retrieve a server by its ID"""
        query = "SELECT * FROM IOS_SERVERS WHERE id = $1"
        row = await self.pool.fetchrow(query, server_id)
        return dict(row) if row else None
    
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
            params.append(password)
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
            params.append(host_password)
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
            return True
        except Exception as e:
            logger.error(f"Failed to activate server: {e}")
            return False
    
    async def server_exists(self, name: str) -> bool:
        """Check if a server exists by name"""
        query = "SELECT EXISTS(SELECT 1 FROM IOS_SERVERS WHERE name = $1)"
        return await self.pool.fetchval(query, name)
    
    async def get_all_servers_with_details(self) -> List[Dict[str, Any]]:
        """Get all servers with detailed information including credentials"""
        query = """
        SELECT id, name, address, password, sftp_ip, host_username, host_password,
               server_type, is_active, created_at, updated_at
        FROM IOS_SERVERS
        WHERE is_active = TRUE
        ORDER BY name ASC
        """
        rows = await self.pool.fetch(query)
        return [dict(row) for row in rows]
    
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
        
        Uses address field (e.g., 199.127.62.217:27015) to extract host and game port.
        SFTP connection uses port 8822 for Linux, 22 for Windows.
        Directory path is /{host}_{game_port}/iosoccer/statistics for Linux.
        """
        servers = await self.get_all_servers_with_details()
        formatted_servers = []
        
        for server in servers:
            # Only need host_username and address (sftp_ip is not used)
            if not server.get('host_username') or not server.get('address'):
                continue
            
            # Parse host and port from address (e.g., 199.127.62.217:27015)
            address_parts = server['address'].split(':')
            host = address_parts[0]
            game_port = address_parts[1] if len(address_parts) > 1 else '27015'
            
            # Check server type
            server_type = server.get('server_type', 'linux')
            is_windows = server_type.lower() == 'windows'
            
            if is_windows:
                # Windows server with different path structure and SFTP port
                directory_path = "/C:/Users/Administrator/Documents/iosoccer/iosoccer/statistics"
                sftp_port = 22  # Standard SFTP port for Windows
            else:
                # Standard Linux server path structure
                directory_path = f"/{host}_{game_port}/iosoccer/statistics"
                sftp_port = 8822  # Linux SFTP port
            
            formatted_servers.append({
                'name': server['name'],
                'host': host,
                'port': sftp_port,
                'user': server['host_username'],
                'pass': server.get('host_password', ''),
                'password': server.get('host_password', ''),  # Keep both for compatibility
                'dir': directory_path
            })
        
        return formatted_servers
