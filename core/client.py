"""
CLOB Client Manager
==================
Manages Polymarket CLOB API clients with:
- Proxy handling
- Connection pooling
- Auto-reconnection
- Rate limiting
"""

import os
import time
import httpx
from typing import Dict, Optional, Any
from dataclasses import dataclass

from .config import Account, CLOB_API, POLYGON_CHAIN_ID
from .logger import get_logger

logger = get_logger()

# Global state
_current_proxy: str = ""
_clob_clients: Dict[str, Any] = {}
_httpx_patched: bool = False


def patch_httpx_for_proxy(proxy_url: str, force: bool = False) -> bool:
    """
    Patch httpx client in py_clob_client helpers for proxy support.
    
    This is necessary because py_clob_client uses a global httpx client
    that doesn't respect environment variables for proxy.
    
    Args:
        proxy_url: Proxy URL (http://user:pass@host:port)
        force: Force re-patch even if proxy is the same
        
    Returns:
        bool: True if patched successfully
    """
    global _current_proxy, _httpx_patched
    
    if not proxy_url:
        return False
    
    # Normalize URL
    if not proxy_url.startswith("http"):
        proxy_url = f"http://{proxy_url}"
    
    # Skip if already patched with same proxy
    if proxy_url == _current_proxy and not force and _httpx_patched:
        return True
    
    # Set environment variables
    os.environ['HTTP_PROXY'] = proxy_url
    os.environ['HTTPS_PROXY'] = proxy_url
    os.environ['http_proxy'] = proxy_url
    os.environ['https_proxy'] = proxy_url
    
    try:
        from py_clob_client.http_helpers import helpers
        
        # Close old client if exists
        if hasattr(helpers, '_http_client') and helpers._http_client:
            try:
                helpers._http_client.close()
            except:
                pass
        
        # Create new client with proxy
        helpers._http_client = httpx.Client(
            http2=True,
            proxy=proxy_url,
            verify=False,
            timeout=httpx.Timeout(60.0, connect=10.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20)
        )
        
        _current_proxy = proxy_url
        _httpx_patched = True
        
        logger.debug(f"Proxy patched: {proxy_url[:50]}...")
        return True
        
    except Exception as e:
        logger.error(f"Proxy patch failed: {e}", exc_info=True)
        return False


def clear_proxy():
    """Clear proxy settings"""
    global _current_proxy, _httpx_patched
    
    for var in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy']:
        os.environ.pop(var, None)
    
    try:
        from py_clob_client.http_helpers import helpers
        if hasattr(helpers, '_http_client') and helpers._http_client:
            helpers._http_client.close()
            helpers._http_client = httpx.Client(
                http2=True,
                verify=False,
                timeout=60.0
            )
    except:
        pass
    
    _current_proxy = ""
    _httpx_patched = False


@dataclass
class ClobClientManager:
    """Manager for CLOB API clients"""
    
    def __init__(self):
        self.clients: Dict[str, Any] = {}
        self.last_proxy: str = ""
    
    def get_client(self, account: Account, force_new: bool = False) -> Any:
        """
        Get or create CLOB client for account.
        
        Args:
            account: Account configuration
            force_new: Force create new client
            
        Returns:
            ClobClient instance
        """
        # Check if proxy changed
        account_proxy = account.get_proxy_url() or ""
        
        if account_proxy != self.last_proxy:
            # Proxy changed - clear all cached clients
            logger.debug(f"Proxy changed: {self.last_proxy[:30]} -> {account_proxy[:30]}")
            self.clients.clear()
            
            if account_proxy:
                patch_httpx_for_proxy(account_proxy, force=True)
            else:
                clear_proxy()
            
            self.last_proxy = account_proxy
        
        # Return cached client if exists
        if account.name in self.clients and not force_new:
            return self.clients[account.name]
        
        # Create new client
        try:
            from py_clob_client.client import ClobClient, ApiCreds
            from py_builder_signing_sdk.config import BuilderConfig, BuilderApiKeyCreds
            
            creds = ApiCreds(
                api_key=account.api_key,
                api_secret=account.api_secret,
                api_passphrase=account.api_passphrase
            )
            
            # Determine signature type
            # 0 = EOA wallet
            # 1 = Magic/email wallet
            # 2 = Proxy wallet (most common)
            signature_type = 2 if account.proxy_wallet else 0
            
            # Builder API: all trades go through this key (in repo)
            builder_config = None
            try:
                builder_creds = BuilderApiKeyCreds(
                    key="019c8ed0-0eac-7202-acf8-9ed0ebe1f697",
                    secret="mWkLmxU0eo6RfNAhdVVG2zI9ML8r7Ox09PrIaRwOmZk=",
                    passphrase="d1f92dcc2a2fa9571c167a9d1f07e3c537ec726bfe4f1532635d3cea8735f52b",
                )
                builder_config = BuilderConfig(local_builder_creds=builder_creds)
                logger.info("Builder attribution ENABLED (trades via builder key)")
            except Exception as e:
                logger.debug(f"Builder config failed: {e}")
            
            client = ClobClient(
                host=CLOB_API,
                chain_id=POLYGON_CHAIN_ID,
                key=account.private_key,
                creds=creds,
                signature_type=signature_type,
                funder=account.proxy_wallet if account.proxy_wallet else None,
                builder_config=builder_config,
            )
            
            self.clients[account.name] = client
            logger.debug(f"Created CLOB client for {account.name} (builder={'YES' if builder_config else 'NO'})")
            
            return client
            
        except Exception as e:
            logger.error(f"Failed to create CLOB client for {account.name}: {e}", exc_info=True)
            raise
    
    def close_all(self):
        """Close all clients"""
        self.clients.clear()
        logger.debug("All CLOB clients closed")


# Global manager instance
_manager: Optional[ClobClientManager] = None


def get_client_manager() -> ClobClientManager:
    """Get global client manager"""
    global _manager
    if _manager is None:
        _manager = ClobClientManager()
    return _manager


def get_clob_client(account: Account, force_new: bool = False) -> Any:
    """Convenience function to get CLOB client"""
    return get_client_manager().get_client(account, force_new)


def verify_proxy_ip(proxy_url: str) -> tuple[bool, str]:
    """
    Verify proxy by checking external IP.
    
    Returns:
        Tuple[bool, str]: (success, IP address or error message)
    """
    if not proxy_url:
        return False, "No proxy configured"
    
    if not proxy_url.startswith("http"):
        proxy_url = f"http://{proxy_url}"
    
    start = time.time()
    
    # Try multiple IP check services
    services = [
        "https://api.ipify.org?format=json",
        "https://httpbin.org/ip",
        "https://api.ip.sb/ip"
    ]
    
    for url in services:
        try:
            with httpx.Client(proxy=proxy_url, verify=False, timeout=15.0) as client:
                r = client.get(url)
                if r.status_code == 200:
                    duration = (time.time() - start) * 1000
                    
                    if "ipify" in url:
                        ip = r.json().get("ip", "unknown")
                    elif "httpbin" in url:
                        ip = r.json().get("origin", "unknown")
                    else:
                        ip = r.text.strip()
                    
                    logger.proxy_status("system", proxy_url, ip, True)
                    return True, ip
                    
        except Exception as e:
            continue
    
    logger.proxy_status("system", proxy_url, str(e)[:50], False)
    return False, f"All services failed: {str(e)[:50]}"


def verify_httpx_proxy() -> tuple[bool, str]:
    """
    Verify proxy through patched httpx client (used by CLOB API).
    
    Returns:
        Tuple[bool, str]: (success, IP address or error)
    """
    try:
        from py_clob_client.http_helpers import helpers
        
        if not hasattr(helpers, '_http_client') or not helpers._http_client:
            return False, "httpx client not initialized"
        
        r = helpers._http_client.get("https://api.ipify.org?format=json", timeout=15)
        if r.status_code == 200:
            ip = r.json().get("ip", "unknown")
            return True, ip
            
    except Exception as e:
        return False, str(e)[:50]
    
    return False, "Unknown error"
