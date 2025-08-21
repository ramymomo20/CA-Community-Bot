import logging
import os
import traceback
from datetime import datetime
from typing import Optional, Any, Dict
import json

class ErrorLogger:
    """Centralized error logging system for the Discord bot."""
    
    def __init__(self, log_file: str = "bot_errors.log"):
        # Ensure the log file is created in the bot's root directory
        import os
        bot_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.log_file = os.path.join(bot_root, log_file)
        print(f"[ERROR LOGGER] Log file path: {self.log_file}")
        self.logger = self._setup_logger()
    
    def _setup_logger(self) -> logging.Logger:
        """Set up the logger with file and console handlers."""
        logger = logging.getLogger('ios_bot_errors')
        logger.setLevel(logging.ERROR)
        
        # Prevent duplicate handlers
        if logger.handlers:
            return logger
        
        # Create logs directory if it doesn't exist
        log_dir = os.path.dirname(self.log_file)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir)
        
        # File handler
        file_handler = logging.FileHandler(self.log_file, mode='a', encoding='utf-8')
        file_handler.setLevel(logging.ERROR)
        
        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.ERROR)
        
        # Formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)
        
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)
        
        return logger
    
    def log_error(self, error: Exception, context: Optional[Dict[str, Any]] = None, 
                  user_id: Optional[int] = None, guild_id: Optional[int] = None,
                  channel_id: Optional[int] = None, command: Optional[str] = None):
        """Log an error with context information."""
        print(f"[ERROR LOGGER] Attempting to log error: {type(error).__name__}: {error}")
        try:
            error_info = {
                'timestamp': datetime.now().isoformat(),
                'error_type': type(error).__name__,
                'error_message': str(error),
                'traceback': traceback.format_exc(),
                'context': context or {},
                'user_id': user_id,
                'guild_id': guild_id,
                'channel_id': channel_id,
                'command': command
            }
            
            # Log to file
            self.logger.error(f"ERROR: {json.dumps(error_info, indent=2)}")
            
            # Also print to console for immediate visibility
            print(f"❌ ERROR LOGGED: {error_info['error_type']}: {error_info['error_message']}")
            print(f"   Log file: {self.log_file}")
            if context:
                print(f"   Context: {context}")
            
        except Exception as e:
            # Fallback logging if the main logging fails
            print(f"CRITICAL: Error logging failed: {e}")
            print(f"Original error: {error}")
            print(f"Log file path: {self.log_file}")
    
    def log_warning(self, message: str, context: Optional[Dict[str, Any]] = None,
                   user_id: Optional[int] = None, guild_id: Optional[int] = None):
        """Log a warning with context information."""
        try:
            warning_info = {
                'timestamp': datetime.now().isoformat(),
                'type': 'WARNING',
                'message': message,
                'context': context or {},
                'user_id': user_id,
                'guild_id': guild_id
            }
            
            self.logger.warning(f"WARNING: {json.dumps(warning_info, indent=2)}")
            print(f"⚠️ WARNING: {message}")
            
        except Exception as e:
            print(f"CRITICAL: Warning logging failed: {e}")
            print(f"Original warning: {message}")
    
    def log_info(self, message: str, context: Optional[Dict[str, Any]] = None):
        """Log informational messages."""
        try:
            info_data = {
                'timestamp': datetime.now().isoformat(),
                'type': 'INFO',
                'message': message,
                'context': context or {}
            }
            
            self.logger.info(f"INFO: {json.dumps(info_data, indent=2)}")
            
        except Exception as e:
            print(f"CRITICAL: Info logging failed: {e}")

# Global error logger instance
error_logger = ErrorLogger()

def log_error(error: Exception, **kwargs):
    """Convenience function to log errors."""
    error_logger.log_error(error, **kwargs)

def log_warning(message: str, **kwargs):
    """Convenience function to log warnings."""
    error_logger.log_warning(message, **kwargs)

def log_info(message: str, **kwargs):
    """Convenience function to log info messages."""
    error_logger.log_info(message, **kwargs) 