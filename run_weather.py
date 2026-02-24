#!/usr/bin/env python3
"""Direct run Weather Sniper for Account 2"""
import sys
import os

# Fix encoding for Windows
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(__file__))

from core.config import load_config, Account
from core.logger import init_logger
from strategies.sniper import LimitSniper

logger = init_logger()

PRESET = "weather_sniper"
ACCOUNT_IDX = 1  # Account 2

def main():
    print("=" * 60)
    print("  WEATHER SNIPER - Account 2")
    print("=" * 60)
    
    config = load_config()
    acc_data = config.to_dict()['accounts'][ACCOUNT_IDX]
    account = Account.from_dict(acc_data)
    
    print(f"\nAccount: {account.name}")
    print(f"Preset: {PRESET}")
    print(f"Proxy: {account.proxy or 'direct'}")
    print("\nPress Ctrl+C to stop\n")
    print("-" * 60)
    
    sniper = LimitSniper(account, PRESET)
    sniper.run()

if __name__ == "__main__":
    main()
