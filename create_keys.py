"""Create API keys for new account and save to config.json"""
import sys
sys.path.insert(0, '.')
from pathlib import Path

from core.client import patch_httpx_for_proxy
from core.config import CONFIG_FILE
from py_clob_client.client import ClobClient

PRIVATE_KEY = "82b9fd47d4a05a926ddfb31fa438d78c269dbb8edbf005a009aa7d12c559620a"
PROXY = "http://sp1keworkzxc:hZ5bwasMKy@176.106.62.204:50100"

patch_httpx_for_proxy(PROXY, force=True)
client = ClobClient(host="https://clob.polymarket.com", key=PRIVATE_KEY, chain_id=137)
print(f"Wallet: {client.get_address()}")

try:
    api_creds = client.create_api_key()
except Exception:
    api_creds = client.derive_api_key()

print(f"api_key:        {api_creds.api_key}")
print(f"api_secret:     {api_creds.api_secret}")
print(f"api_passphrase: {api_creds.api_passphrase}")

# Save to config.json
import json
with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
    cfg = json.load(f)
cfg['accounts'][0]['api_key'] = api_creds.api_key
cfg['accounts'][0]['api_secret'] = api_creds.api_secret
cfg['accounts'][0]['api_passphrase'] = api_creds.api_passphrase
with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
    json.dump(cfg, f, indent=2, ensure_ascii=False)
print("\nSaved to config.json")
