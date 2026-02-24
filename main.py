#!/usr/bin/env python3
"""
PolyBetter - Polymarket Trading Tool v3.0
=========================================
Modular architecture, multiple strategies, CSV tracking.

Usage:
    python main.py              # Interactive menu
    python main.py sniper       # Run sniper directly
    python main.py smart        # Run smart sniper
    python main.py bot          # Run Telegram bot
    python main.py analyze      # Run market analyzer
    python main.py simulate     # Run strategy simulator
"""

import os
import sys
import time
import argparse
from pathlib import Path
from datetime import datetime

# Ensure imports work
sys.path.insert(0, str(Path(__file__).parent))

from core.config import load_config, save_config, load_presets, Account, ensure_presets
from core.logger import get_logger, init_logger

# Initialize logger
logger = init_logger()

TOOL_START_TIME = datetime.now()


def get_tool_runtime() -> str:
    """Get tool runtime"""
    delta = datetime.now() - TOOL_START_TIME
    hours, remainder = divmod(int(delta.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')


def print_header(title: str):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def print_info_bar():
    local_time = datetime.now().strftime("%H:%M:%S")
    tool_time = get_tool_runtime()
    print(f"[Time: {local_time} | Uptime: {tool_time}]")


def select_account() -> Account:
    """Select account from config"""
    config = load_config()
    accounts = [Account.from_dict(a) for a in config.to_dict()['accounts'] if a.get('enabled')]
    
    if not accounts:
        print("\nNo active accounts!")
        return None

    if len(accounts) == 1:
        return accounts[0]

    print("\nSelect account:")
    for i, acc in enumerate(accounts, 1):
        print(f"  {i}) {acc.name}")
    
    try:
        idx = int(input("Number: ")) - 1
        if 0 <= idx < len(accounts):
            return accounts[idx]
    except:
        pass
    
    return accounts[0]


def select_preset() -> str:
    """Select preset with market count and cost estimate"""
    from strategies.base import MarketFilter
    from core.data_api import DataAPI, extract_markets_from_events
    import json
    
    presets_data = load_presets()
    presets = presets_data.get("presets", {})
    preset_names = list(presets.keys())
    
    print("\n  Loading markets for analysis...")

    try:
        data_api = DataAPI()
        events = data_api.get_all_events(closed=False)
        markets = extract_markets_from_events(events)
        print(f"  Loaded: {len(events)} events, {len(markets)} markets\n")
    except Exception as e:
        print(f"  Load error: {e}")
        markets = []
    
    # Calculate matches for each preset
    preset_stats = {}
    for name in preset_names:
        p = presets[name]
        order_amt = p.get('order_amount', 0.2)
        
        if markets:
            try:
                mf = MarketFilter(name)
                matching = 0
                
                for market in markets:
                    passes, _ = mf.filter_market(market)
                    if not passes:
                        continue
                    
                    # Parse prices and count matching outcomes
                    prices = market.get('outcomePrices', '[]')
                    if isinstance(prices, str):
                        try:
                            prices = json.loads(prices)
                        except:
                            prices = []
                    prices = [float(x) for x in prices] if prices else []
                    
                    for price in prices:
                        passes_price, _ = mf.filter_price(price)
                        if passes_price:
                            matching += 1
                
                preset_stats[name] = {
                    'count': matching,
                    'cost': matching * order_amt
                }
            except:
                preset_stats[name] = {'count': 0, 'cost': 0}
        else:
            preset_stats[name] = {'count': 0, 'cost': 0}
    
    # Print compact list
    print("  #   Preset                        Markets   Need $")
    print("  " + "-" * 56)
    
    for i, name in enumerate(preset_names, 1):
        p = presets[name]
        display_name = p.get('name', name)[:28]
        description = p.get('description', '')[:60]
        stats = preset_stats.get(name, {'count': 0, 'cost': 0})
        count = stats['count']
        cost = stats['cost']
        
        print(f"  {i:<3} {display_name:<28} {count:>6}    ${cost:>8.2f}")
        if description:
            print(f"      └─ {description}")
    
    print()
    
    try:
        choice = input("Select preset (Enter for all_categories): ").strip()
        if not choice:
            return "all_categories"
        idx = int(choice) - 1
        if 0 <= idx < len(preset_names):
            return preset_names[idx]
    except:
        pass
    
    return "all_categories"


def run_sniper():
    """Run limit sniper (single account)"""
    from strategies.sniper import LimitSniper
    
    print_header("LIMIT SNIPER")
    
    account = select_account()
    if not account:
        return
    
    preset = select_preset()
    
    # Analyze existing positions and offer take-profit orders BEFORE starting
    analyze_and_place_take_profits([account], preset)
    
    print(f"\nStarting sniper for {account.name} with preset {preset}")
    print("Press Ctrl+C to stop\n")
    
    sniper = LimitSniper(account, preset)
    sniper.run()


def analyze_and_place_take_profits(accounts, preset_name: str) -> int:
    """
    Analyze positions across all accounts and offer to place take-profit orders.
    Simple yes/no prompt before starting strategy.
    
    Returns:
        int: Total take-profit orders placed
    """
    from strategies.sniper import LimitSniper
    from core.data_api import DataAPI
    
    presets = load_presets().get("presets", {})
    preset = presets.get(preset_name, {})
    
    if not preset.get('auto_take_profit', True):
        return 0
    
    take_profit_price = preset.get('take_profit_price', 0.50)
    
    # Analyze all accounts
    total_positions = 0
    total_shares = 0
    total_potential = 0.0
    account_summaries = []
    
    print("\n🔍 Analyzing positions (checking orderbooks & existing SELL orders)...")
    
    total_skipped_closed = 0
    total_skipped_has_sell = 0
    
    for acc in accounts:
        try:
            # Quick init to get positions
            sniper = LimitSniper(acc, preset_name)
            if not sniper.init():
                continue
            
            positions, skipped_closed, skipped_has_sell = sniper.analyze_existing_positions_for_take_profit()
            total_skipped_closed += skipped_closed
            total_skipped_has_sell += skipped_has_sell
            
            if positions:
                acc_shares = sum(p['sell_size'] for p in positions)
                acc_potential = sum(p['potential_profit'] for p in positions)
                
                total_positions += len(positions)
                total_shares += acc_shares
                total_potential += acc_potential
                
                account_summaries.append({
                    'account': acc,
                    'sniper': sniper,
                    'positions': positions,
                    'shares': acc_shares,
                    'potential': acc_potential
                })
        except Exception as e:
            print(f"  ⚠️ {acc.name}: {e}")
    
    if total_positions == 0:
        skip_parts = []
        if total_skipped_closed > 0:
            skip_parts.append(f"{total_skipped_closed} closed")
        if total_skipped_has_sell > 0:
            skip_parts.append(f"{total_skipped_has_sell} have SELL")
        skipped_msg = f" ({', '.join(skip_parts)})" if skip_parts else ""
        print(f"✅ No positions need take-profit orders.{skipped_msg}\n")
        return 0
    
    # Compact summary with skip info
    skip_parts = []
    if total_skipped_closed > 0:
        skip_parts.append(f"{total_skipped_closed} closed")
    if total_skipped_has_sell > 0:
        skip_parts.append(f"{total_skipped_has_sell} have SELL")
    skipped_msg = f" | skip: {', '.join(skip_parts)}" if skip_parts else ""
    print(f"\n📊 TAKE-PROFIT: {total_positions} pos, {total_shares:.0f} shares → ${total_shares * take_profit_price:.0f} @ 50¢{skipped_msg}")
    
    # Simple yes/no
    try:
        response = input("   Place SELL orders? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        response = 'n'
    
    if response != 'y':
        print("   Skipped.\n")
        return 0
    
    # Place orders
    total_placed = 0
    for summary in account_summaries:
        placed = summary['sniper'].place_all_take_profits_silent()
        total_placed += placed
        if placed > 0:
            print(f"   ✅ {summary['account'].name}: {placed} SELL orders")
    
    print(f"\n✅ Placed {total_placed} take-profit orders.\n")
    return total_placed


def run_sniper_multithread():
    """Run limit sniper for ALL accounts simultaneously"""
    from strategies.sniper import LimitSniper, RATE_LIMITS
    from concurrent.futures import ThreadPoolExecutor
    import threading
    
    print_header("LIMIT SNIPER - MULTI-ACCOUNT")
    
    config = load_config()
    accounts = [Account.from_dict(a) for a in config.to_dict()['accounts'] 
                if a.get('enabled') and a.get('api_key')]
    
    if not accounts:
        print("\nNo active accounts!")
        return
    
    preset = select_preset()
    presets = load_presets().get("presets", {})
    preset_config = presets.get(preset, {})
    
    # Analyze existing positions and offer take-profit orders BEFORE starting
    analyze_and_place_take_profits(accounts, preset)
    
    # Show rate limit info
    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║                    ⚡ RATE LIMITS (Polymarket API)                 ║
╠══════════════════════════════════════════════════════════════════╣
║  📤 POST /order:    60/sec sustained (3500 burst/10s)            ║
║  📊 Tick Size:      20/sec (200/10s)                             ║
║  📡 GAMMA /events:  50/sec (500/10s)                             ║
║                                                                  ║
║  Each account on its own proxy = independent rate limits       ║
║  Order delay: {RATE_LIMITS['order_delay']*1000:.0f}ms ({1/RATE_LIMITS['order_delay']:.0f} orders/sec)        ║
╚══════════════════════════════════════════════════════════════════╝
""")
    
    print(f"Starting sniper for {len(accounts)} accounts")
    print(f"   Preset: {preset}")
    print(f"   Order amount: ${preset_config.get('order_amount', 0.2)}")
    print(f"   Max tick: ${preset_config.get('max_tick', 0.01)}")
    print(f"\n   Accounts:")
    for i, acc in enumerate(accounts, 1):
        proxy_ip = acc.proxy.split('-ip-')[1].split(':')[0] if acc.proxy and '-ip-' in acc.proxy else 'direct'
        print(f"     {i}) {acc.name} → IP: {proxy_ip}")
    
    print("\n" + "="*70)
    print("  Press Ctrl+C to stop")
    print("="*70 + "\n")
    
    # Create snipers for each account
    snipers = []
    for acc in accounts:
        sniper = LimitSniper(acc, preset)
        snipers.append(sniper)
    
    def run_single_sniper(sniper):
        """Run sniper in thread"""
        try:
            sniper.run()
        except Exception as e:
            logger.exception(f"Sniper error for {sniper.account.name}: {e}")
    
    # Run all snipers in parallel threads
    with ThreadPoolExecutor(max_workers=len(accounts)) as executor:
        futures = [executor.submit(run_single_sniper, s) for s in snipers]
        
        try:
            # Wait for all to complete (they run until Ctrl+C)
            for future in futures:
                future.result()
        except KeyboardInterrupt:
            print("\n\nStopping all snipers...")
            for sniper in snipers:
                sniper.running = False
            
            # Print combined stats
            total_orders = sum(s.total_orders_session for s in snipers)
            total_errors = sum(s.api_errors for s in snipers)
            print(f"\nTOTAL:")
            print(f"   Orders: {total_orders}")
            print(f"   Errors: {total_errors}")
            for s in snipers:
                print(f"   • {s.account.name}: {s.total_orders_session} orders")
            print("Stopped")


def run_smart_sniper():
    """Run smart sniper (single account)"""
    from strategies.smart_sniper import SmartSniper
    
    print_header("SMART SNIPER")
    
    account = select_account()
    if not account:
        return
    
    preset = select_preset()
    
    print(f"\nStarting SMART sniper for {account.name}")
    print("Press Ctrl+C to stop\n")
    
    sniper = SmartSniper(account, preset)
    sniper.run()


def run_smart_sniper_multithread():
    """Run smart sniper for ALL accounts simultaneously"""
    from strategies.smart_sniper import SmartSniper
    from concurrent.futures import ThreadPoolExecutor
    
    print_header("SMART SNIPER - MULTI-ACCOUNT")
    
    config = load_config()
    accounts = [Account.from_dict(a) for a in config.to_dict()['accounts'] 
                if a.get('enabled') and a.get('api_key')]
    
    if not accounts:
        print("\nNo active accounts!")
        return
    
    preset = select_preset()
    presets = load_presets().get("presets", {})
    preset_config = presets.get(preset, {})
    
    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║                    🧠 SMART SNIPER                               ║
╠══════════════════════════════════════════════════════════════════╣
║  Quality > Quantity                                              ║
║                                                                  ║
║  Volume analysis                                                 ║
║  Liquidity check (order book depth)                              ║
║  Bid/ask spread analysis                                         ║
║  Market activity                                                 ║
║  Time to resolution                                              ║
║                                                                  ║
║  Result: Fewer orders, but on quality markets                    ║
╚══════════════════════════════════════════════════════════════════╝
""")
    
    print(f"Starting SMART sniper for {len(accounts)} accounts")
    print(f"   Preset: {preset}")
    print(f"   Min score: {preset_config.get('min_score', 50)}")
    print(f"   Min volume: ${preset_config.get('min_volume', 25000)}")
    print(f"\n   Accounts:")
    for i, acc in enumerate(accounts, 1):
        proxy_ip = acc.proxy.split('-ip-')[1].split(':')[0] if acc.proxy and '-ip-' in acc.proxy else 'direct'
        print(f"     {i}) {acc.name} → IP: {proxy_ip}")
    
    print("\n" + "="*70)
    print("  Press Ctrl+C to stop")
    print("="*70 + "\n")
    
    # Create snipers for each account
    snipers = []
    for acc in accounts:
        sniper = SmartSniper(acc, preset)
        snipers.append(sniper)
    
    def run_single_sniper(sniper):
        """Run sniper in thread"""
        try:
            sniper.run()
        except Exception as e:
            logger.exception(f"Smart Sniper error for {sniper.account.name}: {e}")
    
    # Run all snipers in parallel threads
    with ThreadPoolExecutor(max_workers=len(accounts)) as executor:
        futures = [executor.submit(run_single_sniper, s) for s in snipers]
        
        try:
            for future in futures:
                future.result()
        except KeyboardInterrupt:
            print("\n\nStopping all snipers...")
            for sniper in snipers:
                sniper.running = False
            
            # Print combined stats
            total_orders = sum(s.orders_placed for s in snipers)
            print(f"\nTOTAL:")
            print(f"   Orders: {total_orders}")
            for s in snipers:
                print(f"   • {s.account.name}: {s.orders_placed} orders")
            print("Stopped")


def run_bot():
    """Run Telegram bot"""
    from bot.telegram_bot_v2 import run_bot
    run_bot()


def run_analyzer():
    """Run market analyzer"""
    from tools.analyzer import main as analyzer_main
    analyzer_main()


def run_simulator():
    """Run strategy simulator"""
    from tools.simulator import main as simulator_main
    simulator_main()


def menu_check_proxy(account: Account):
    """Check proxy"""
    from core.client import verify_proxy_ip, verify_httpx_proxy, patch_httpx_for_proxy
    
    print_header(f"PROXY CHECK - {account.name}")

    proxy = account.get_proxy_url()
    print(f"\nProxy: {proxy or 'NOT SET'}")
    print("-" * 70)
    
    if proxy:
        print("\n[1] Check via requests:")
        ok, ip = verify_proxy_ip(proxy)
        if ok:
            print(f"    ✅ IP: {ip}")
        else:
            print(f"    Error: {ip}")
        
        print("\n[2] Check via httpx (CLOB API):")
        patch_httpx_for_proxy(proxy, force=True)
        ok, ip = verify_httpx_proxy()
        if ok:
            print(f"    ✅ IP: {ip}")
        else:
            print(f"    Error: {ip}")
    else:
        print("    Proxy not set!")


def menu_check_wallet(account: Account):
    """Check wallet balance"""
    from core.client import get_clob_client, patch_httpx_for_proxy
    
    print_header(f"WALLET CHECK - {account.name}")
    
    if account.proxy:
        patch_httpx_for_proxy(account.proxy, force=True)
    
    try:
        from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
        
        client = get_clob_client(account)
        address = client.get_address()
        
        print(f"\nMain wallet: {address}")
        if account.proxy_wallet:
            print(f"Proxy Wallet:     {account.proxy_wallet}")
        
        print("-" * 70)
        
        collateral = client.get_balance_allowance(
            params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        balance = float(collateral.get('balance', 0)) / 1e6
        print(f"\nUSDC balance: ${balance:,.2f}")
        
        orders = client.get_orders()
        print(f"Open orders: {len(orders)}")
        
    except Exception as e:
        print(f"\nError: {e}")
        logger.exception("Wallet check error")


def menu_show_positions(account: Account):
    """Show positions"""
    from core.data_api import DataAPI
    from core.client import get_clob_client
    
    print_header(f"POSITIONS - {account.name}")
    
    try:
        client = get_clob_client(account)
        wallet = account.proxy_wallet or client.get_address()
        
        data_api = DataAPI()
        positions = data_api.get_all_positions(wallet)
        
        if not positions:
            print("\nNo positions!")
            return
        
        total_value = sum(p.get('currentValue', 0) for p in positions)
        total_pnl = sum(p.get('cashPnl', 0) for p in positions)
        
        print(f"\nPositions: {len(positions)} | Value: ${total_value:,.2f} | PnL: ${total_pnl:+,.2f}")
        print("-" * 70)
        
        for i, pos in enumerate(positions[:15], 1):
            title = (pos.get('title', '?') or '?')[:35]
            outcome = pos.get('outcome', '?')[:3]
            size = pos.get('size', 0)
            pnl = pos.get('cashPnl', 0)
            print(f"  {i:3}. [{outcome}] {title} | {size:.1f} | ${pnl:+.2f}")
        
        if len(positions) > 15:
            print(f"  ... and {len(positions) - 15} more")
            
    except Exception as e:
        print(f"\nError: {e}")


def menu_create_api_keys(account: Account, account_idx: int):
    """Create API keys for account"""
    from core.client import patch_httpx_for_proxy
    
    print_header(f"CREATE API KEYS - {account.name}")
    
    if not account.private_key:
        print("\nError: Private key not set for this account!")
        print("   Add private_key to config.json first")
        return
    
    print(f"\nPrivate key: {account.private_key[:8]}...{account.private_key[-8:]}")
    if account.proxy:
        print(f"Proxy: {account.proxy.split('@')[1] if '@' in account.proxy else account.proxy}")
    print()
    
    confirm = input("Create API keys for this account? (yes/no): ").strip().lower()
    if confirm not in ['yes', 'y']:
        print("Cancelled")
        return
    
    print("\nCreating API keys...")
    print("-" * 70)
    
    try:
        # Setup proxy if needed
        if account.proxy:
            patch_httpx_for_proxy(account.proxy, force=True)
            print("Proxy configured")
        
        # Import here to avoid issues
        from py_clob_client.client import ClobClient
        
        # Clean private key
        private_key = account.private_key
        if private_key.startswith("0x"):
            private_key = private_key[2:]
        
        if len(private_key) != 64:
            print(f"Error: Invalid private key length: {len(private_key)} (need 64)")
            return
        
        # Create client
        client = ClobClient(
            host="https://clob.polymarket.com",
            key=private_key,
            chain_id=137,
        )
        
        wallet_address = client.get_address()
        print(f"Wallet address: {wallet_address}")
        
        # Try to create API key
        print("\nMethod 1: Trying create_api_key()...")
        api_creds = None
        
        try:
            api_creds = client.create_api_key()
            print("   Success via create_api_key()")
        except Exception as e1:
            print(f"   Error: {str(e1)[:50]}")
            print("\nMethod 2: Trying derive_api_key()...")
            try:
                api_creds = client.derive_api_key()
                print("   Success via derive_api_key()")
            except Exception as e2:
                print(f"   Error: {str(e2)[:50]}")
                print("\n" + "=" * 70)
                print("Both methods failed")
                print("=" * 70)
                print("\nPossible causes:")
                print("  1. Wallet not connected to polymarket.com")
                print("  2. Message for trading activation not signed")
                print("  3. Wallet has no activity on Polymarket")
                print("  4. Proxy or network issues")
                print("\nFix:")
                print("  1. Go to https://polymarket.com")
                print("  2. Connect this wallet")
                print("  3. Sign the message")
                print("  4. Try again")
                return
        
        if not api_creds:
            print("Failed to get API keys")
            return
        
        # Show keys
        print("\n" + "=" * 70)
        print("API keys created successfully!")
        print("=" * 70)
        print(f"\napi_key:        {api_creds.api_key}")
        print(f"api_secret:     {api_creds.api_secret}")
        print(f"api_passphrase: {api_creds.api_passphrase}")
        print()
        
        # Save to config
        save_confirm = input("Save keys to config.json? (yes/no): ").strip().lower()
        if save_confirm in ['yes', 'y']:
            config = load_config()
            config_dict = config.to_dict()
            
            config_dict['accounts'][account_idx]['api_key'] = api_creds.api_key
            config_dict['accounts'][account_idx]['api_secret'] = api_creds.api_secret
            config_dict['accounts'][account_idx]['api_passphrase'] = api_creds.api_passphrase
            
            config = config.from_dict(config_dict)
            save_config(config)
            
            print("API keys saved to config.json")
        else:
            print("Keys NOT saved. Copy them manually!")
        
    except Exception as e:
        print(f"\nError: {e}")
        logger.exception("API key creation error")


def menu_manage_accounts():
    """Account management menu"""
    config = load_config()
    
    while True:
        clear_screen()
        print_header("ACCOUNT MANAGEMENT")
        
        accounts = [Account.from_dict(a) for a in config.to_dict()['accounts']]
        
        print("\nAccounts:")
        for i, acc in enumerate(accounts, 1):
            status = "✅" if acc.enabled else "❌"
            keys = "🔑" if acc.api_key else "⚠️"
            print(f"  {i}) {status} {acc.name} {keys}")
        
        print("\n  A) Add account")
        print("  K) Get API keys")
        print("  T) Toggle status")
        print("  0) Back")

        choice = input("\nChoice: ").strip().lower()
        
        if choice == '0':
            break
        elif choice == 'a':
            name = input("Account name: ").strip()
            if name:
                new_acc = {
                    "name": name,
                    "enabled": False,
                    "private_key": "",
                    "api_key": "",
                    "api_secret": "",
                    "api_passphrase": "",
                    "proxy_wallet": "",
                    "proxy": ""
                }
                config_dict = config.to_dict()
                config_dict['accounts'].append(new_acc)
                config = config.from_dict(config_dict)
                save_config(config)
                print(f"Account '{name}' added")
                input("\nPress Enter...")
        elif choice == 'k':
            try:
                idx = int(input("Account number: ")) - 1
                if 0 <= idx < len(accounts):
                    menu_create_api_keys(accounts[idx], idx)
                    input("\nPress Enter...")
            except ValueError:
                print("Invalid number")
                input("\nPress Enter...")
            except Exception as e:
                print(f"Error: {e}")
                input("\nPress Enter...")
        elif choice == 't':
            try:
                idx = int(input("Account number: ")) - 1
                if 0 <= idx < len(accounts):
                    config_dict = config.to_dict()
                    config_dict['accounts'][idx]['enabled'] = not config_dict['accounts'][idx]['enabled']
                    config = config.from_dict(config_dict)
                    save_config(config)
                    print("Status updated")
                    input("\nPress Enter...")
            except:
                pass


def menu_view_stats():
    """View trading statistics"""
    from trackers.csv_tracker import get_trade_tracker, get_pnl_tracker
    
    print_header("STATISTICS")
    
    tracker = get_trade_tracker()
    stats = tracker.get_stats(days=7)
    
    print("\nSTATISTICS (7 days)")
    print("-" * 50)
    print(f"Total orders:     {stats['total_orders']}")
    print(f"Placed:           {stats['placed']}")
    print(f"Filled:           {stats['filled']}")
    print(f"Failed:           {stats['failed']}")
    print(f"Cancelled:        {stats['cancelled']}")
    print(f"\nVolume:           ${stats['total_volume']:.2f}")
    print(f"BUY orders:       {stats['by_side']['BUY']}")
    print(f"SELL orders:      {stats['by_side']['SELL']}")
    
    if stats['errors']:
        print("\nTop errors:")
        for error, count in list(stats['errors'].items())[:5]:
            print(f"  • {error}: {count}")


def menu_sell_all_positions(account: Account):
    """Sell ALL positions at MARKET price (FOK) for instant exit"""
    from core.client import get_clob_client, patch_httpx_for_proxy
    from core.data_api import DataAPI
    from py_clob_client.clob_types import MarketOrderArgs, OrderType, BalanceAllowanceParams, AssetType
    
    print_header(f"SELL ALL POSITIONS (MARKET) - {account.name}")
    
    if account.proxy:
        patch_httpx_for_proxy(account.proxy, force=True)
    
    try:
        client = get_clob_client(account, force_new=True)
        wallet = account.proxy_wallet or client.get_address()
        
        data_api = DataAPI(proxy=account.proxy)
        positions = data_api.get_all_positions(wallet)
        
        if not positions:
            print("\nNo positions to sell!")
            return
        
        # Filter positions with actual shares
        sellable = []
        for pos in positions:
            token_id = pos.get('asset', '') or pos.get('tokenId', '')
            size = float(pos.get('size', 0) or 0)
            if token_id and size >= 1:
                sellable.append(pos)
        
        if not sellable:
            print("\nNo positions with enough shares!")
            return
        
        # Show positions
        total_shares = sum(float(p.get('size', 0) or 0) for p in sellable)
        print(f"\nPositions to sell: {len(sellable)} ({total_shares:.0f} shares)")
        print("-" * 70)
        
        for i, pos in enumerate(sellable[:20], 1):
            title = (pos.get('title', '?') or '?')[:40]
            outcome = pos.get('outcome', '?')[:5]
            size = float(pos.get('size', 0) or 0)
            avg_price = float(pos.get('avgPrice', 0) or pos.get('avg_price', 0) or 0)
            print(f"  {i:3}. [{outcome}] {title} | {size:.0f} sh @ ${avg_price:.4f}")
        
        if len(sellable) > 20:
            print(f"  ... and {len(sellable) - 20} more")
        
        print("-" * 70)
        print(f"\nAll positions will be sold at MARKET (FOK).")
        print(f"    Instant sale at best available bid.")

        confirm = input("\nSell ALL positions? (yes/no): ").strip().lower()

        if confirm not in ['yes', 'y']:
            print("Cancelled.")
            return
        
        print(f"\nSelling {len(sellable)} positions at market...")
        
        sold = 0
        failed = 0
        skipped = 0
        total_usdc = 0.0
        
        for pos in sellable:
            token_id = pos.get('asset', '') or pos.get('tokenId', '')
            size = float(pos.get('size', 0) or 0)
            title = (pos.get('title', '?') or '?')[:40]
            outcome = pos.get('outcome', '?')[:5]
            sell_size = int(size)
            
            if sell_size < 1:
                skipped += 1
                continue
            
            # Get orderbook for best bid
            orderbook = data_api.get_orderbook(token_id)
            if not orderbook:
                skipped += 1
                print(f"  ⏭ SKIP (closed): [{outcome}] {title}")
                continue
            
            bids = orderbook.get('bids', [])
            if not bids:
                skipped += 1
                print(f"  ⏭ SKIP (no bids): [{outcome}] {title}")
                continue
            
            best_bid = float(bids[0].get('price', 0))
            if best_bid <= 0:
                skipped += 1
                continue
            
            # Approve conditional token
            try:
                client.update_balance_allowance(
                    params=BalanceAllowanceParams(
                        asset_type=AssetType.CONDITIONAL,
                        token_id=token_id
                    )
                )
            except:
                pass
            
            # Market sell (FOK)
            try:
                order_args = MarketOrderArgs(
                    token_id=token_id,
                    amount=sell_size,
                    price=best_bid,
                    side="SELL"
                )
                signed = client.create_market_order(order_args)
                result = client.post_order(signed, OrderType.FOK)
                
                if result.get('success'):
                    taking = result.get('takingAmount', 0)
                    if taking:
                        usdc = float(taking) / 1e6
                        total_usdc += usdc
                    else:
                        usdc = sell_size * best_bid
                        total_usdc += usdc
                    sold += 1
                    print(f"  SOLD: [{outcome}] {title} | {sell_size} sh @ ${best_bid:.4f} -> ${usdc:.2f}")
                else:
                    status = result.get('status', '')
                    error = result.get('errorMsg', str(result))[:50]
                    failed += 1
                    print(f"  ❌ FAIL: [{outcome}] {title} | {status} {error}")
            except Exception as e:
                failed += 1
                print(f"  ❌ ERROR: [{outcome}] {title} | {str(e)[:50]}")
            
            time.sleep(0.2)
        
        print(f"\n{'='*50}")
        print(f"Total: sold {sold}, errors {failed}, skipped {skipped}")
        print(f"USDC received: ~${total_usdc:.2f}")
        print(f"{'='*50}")
        
    except Exception as e:
        print(f"\nError: {e}")
        logger.exception("Sell all positions error")


def menu_full_exit(account: Account):
    """Full exit: cancel all orders + market sell all positions"""
    from core.client import get_clob_client, patch_httpx_for_proxy
    from core.data_api import DataAPI
    from py_clob_client.clob_types import MarketOrderArgs, OrderType, BalanceAllowanceParams, AssetType
    
    print_header(f"FULL EXIT - {account.name}")
    
    if account.proxy:
        patch_httpx_for_proxy(account.proxy, force=True)
    
    try:
        client = get_clob_client(account, force_new=True)
        wallet = account.proxy_wallet or client.get_address()
        data_api = DataAPI(proxy=account.proxy)
        
        # === 1. Check balance ===
        try:
            collateral = client.get_balance_allowance(
                params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
            balance = float(collateral.get('balance', 0)) / 1e6
        except:
            balance = 0
        
        # === 2. Get orders ===
        orders = client.get_orders()
        buy_orders = [o for o in orders if o.get('side') == 'BUY']
        sell_orders = [o for o in orders if o.get('side') == 'SELL']
        
        # === 3. Get positions ===
        positions = data_api.get_all_positions(wallet) or []
        sellable = [p for p in positions 
                    if float(p.get('size', 0) or 0) >= 1 
                    and (p.get('asset', '') or p.get('tokenId', ''))]
        total_shares = sum(float(p.get('size', 0) or 0) for p in sellable)
        
        # === Show summary ===
        print(f"""
  ╔══════════════════════════════════════════════════════════╗
  ║                  FULL ACCOUNT EXIT                        ║
  ╠══════════════════════════════════════════════════════════╣
  ║  USDC balance:      ${balance:>10.2f}                      ║
  ║  Open orders:        {len(orders):>5}  (🟢{len(buy_orders)} BUY / 🔴{len(sell_orders)} SELL) ║
  ║  Positions:          {len(sellable):>5}  ({total_shares:.0f} shares)             ║
  ╠══════════════════════════════════════════════════════════╣
  ║                                                          ║
  ║  Step 1: Cancel ALL open orders                          ║
  ║  Step 2: Sell ALL positions at MARKET (FOK)             ║
  ║                                                          ║
  ╚══════════════════════════════════════════════════════════╝
""")
        
        if len(orders) == 0 and len(sellable) == 0:
            print("Account clean — nothing to close!")
            return
        
        confirm = input("Execute FULL EXIT? (yes/no): ").strip().lower()

        if confirm not in ['yes', 'y']:
            print("Cancelled.")
            return
        
        # === Step 1: Cancel all orders ===
        if orders:
            print(f"\nStep 1: Cancelling {len(orders)} orders...")
            try:
                result = client.cancel_all()
                remaining = client.get_orders()
                cancelled = len(orders) - len(remaining)
                print(f"  Cancelled: {cancelled}/{len(orders)} orders")
                if remaining:
                    print(f"  Failed to cancel: {len(remaining)}")
            except Exception as e:
                print(f"  Cancel error: {str(e)[:50]}")
        else:
            print("\nStep 1: No orders to cancel")
        
        # === Step 2: Market sell all positions ===
        if sellable:
            print(f"\nStep 2: Selling {len(sellable)} positions at market...")
            sold = 0
            failed = 0
            skipped = 0
            total_usdc = 0.0
            
            for pos in sellable:
                token_id = pos.get('asset', '') or pos.get('tokenId', '')
                size = float(pos.get('size', 0) or 0)
                title = (pos.get('title', '?') or '?')[:40]
                outcome = pos.get('outcome', '?')[:5]
                sell_size = int(size)
                
                if sell_size < 1:
                    skipped += 1
                    continue
                
                # Get orderbook for best bid
                orderbook = data_api.get_orderbook(token_id)
                if not orderbook:
                    skipped += 1
                    continue
                
                bids = orderbook.get('bids', [])
                if not bids:
                    skipped += 1
                    print(f"  ⏭ SKIP (no bids): [{outcome}] {title}")
                    continue
                
                best_bid = float(bids[0].get('price', 0))
                if best_bid <= 0:
                    skipped += 1
                    continue
                
                # Approve conditional token
                try:
                    client.update_balance_allowance(
                        params=BalanceAllowanceParams(
                            asset_type=AssetType.CONDITIONAL,
                            token_id=token_id
                        )
                    )
                except:
                    pass
                
                # Market sell (FOK)
                try:
                    order_args = MarketOrderArgs(
                        token_id=token_id,
                        amount=sell_size,
                        price=best_bid,
                        side="SELL"
                    )
                    signed = client.create_market_order(order_args)
                    result = client.post_order(signed, OrderType.FOK)
                    
                    if result.get('success'):
                        taking = result.get('takingAmount', 0)
                        if taking:
                            usdc = float(taking) / 1e6
                            total_usdc += usdc
                        else:
                            usdc = sell_size * best_bid
                            total_usdc += usdc
                        sold += 1
                        print(f"  ✅ SOLD: [{outcome}] {title} | {sell_size} @ ${best_bid:.4f} → ${usdc:.2f}")
                    else:
                        failed += 1
                        error = result.get('errorMsg', result.get('error', ''))[:40]
                        print(f"  ❌ [{outcome}] {title} | {error}")
                except Exception as e:
                    failed += 1
                    print(f"  ❌ [{outcome}] {title} | {str(e)[:40]}")
                
                time.sleep(0.2)
            
            print(f"\n  Sold: {sold}, errors: {failed}, skipped: {skipped}")
            print(f"  USDC received: ~${total_usdc:.2f}")
        else:
            print("\nStep 2: No positions to sell")
        
        # === Final balance ===
        try:
            time.sleep(1)
            collateral = client.get_balance_allowance(
                params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
            new_balance = float(collateral.get('balance', 0)) / 1e6
            print(f"\n{'='*50}")
            print(f"  Balance before:  ${balance:.2f}")
            print(f"  Balance after: ${new_balance:.2f}")
            print(f"{'='*50}")
        except:
            pass
        
        print("\nFull exit completed!")
        
    except Exception as e:
        print(f"\nError: {e}")
        logger.exception("Full exit error")


def menu_cancel_orders(account: Account):
    """Cancel all orders for account"""
    from core.client import get_clob_client, patch_httpx_for_proxy
    
    print_header(f"CANCEL ORDERS - {account.name}")
    
    if account.proxy:
        patch_httpx_for_proxy(account.proxy, force=True)
    
    try:
        client = get_clob_client(account, force_new=True)
        
        # Get current orders
        orders = client.get_orders()
        
        if not orders:
            print("\nNo open orders!")
            return
        
        print(f"\nOrders found: {len(orders)}")
        print("-" * 50)
        
        # Show orders summary
        buy_orders = [o for o in orders if o.get('side') == 'BUY']
        sell_orders = [o for o in orders if o.get('side') == 'SELL']
        
        print(f"  🟢 BUY: {len(buy_orders)}")
        print(f"  🔴 SELL: {len(sell_orders)}")
        
        # Show first 10 orders
        for i, order in enumerate(orders[:10], 1):
            side = order.get('side', '?')
            price = float(order.get('price', 0))
            size = float(order.get('original_size', order.get('size', 0)))
            emoji = "🟢" if side == "BUY" else "🔴"
            print(f"  {i}. {emoji} {side} ${price:.4f} × {size:.0f}")
        
        if len(orders) > 10:
            print(f"  ... and {len(orders) - 10} more orders")
        
        print("-" * 50)
        
        # Confirmation
        confirm = input("\nCancel ALL orders? (yes/no): ").strip().lower()

        if confirm not in ['yes', 'y']:
            print("Cancelled.")
            return
        
        print("\nCancelling orders...")
        result = client.cancel_all()
        
        # Check result
        if "error" in str(result).lower():
            print(f"Error: {result}")
        else:
            print(f"Result: {result}")
        
        # Verify
        remaining = client.get_orders()
        if not remaining:
            print("All orders cancelled successfully!")
        else:
            print(f"Orders remaining: {len(remaining)}")
            
    except Exception as e:
        print(f"\nError: {e}")
        logger.exception("Cancel orders error")


def menu_cancel_all_accounts():
    """Cancel all orders for ALL accounts"""
    from core.client import get_clob_client, patch_httpx_for_proxy
    
    print_header("CANCEL ORDERS - ALL ACCOUNTS")
    
    config = load_config()
    accounts = [Account.from_dict(a) for a in config.to_dict()['accounts'] 
                if a.get('enabled') and a.get('api_key')]
    
    if not accounts:
        print("\nNo active accounts!")
        return
    
    print(f"\nChecking {len(accounts)} accounts...")
    print("-" * 50)
    
    total_orders = 0
    account_orders = {}
    
    for acc in accounts:
        try:
            if acc.proxy:
                patch_httpx_for_proxy(acc.proxy, force=True)
            
            client = get_clob_client(acc, force_new=True)
            orders = client.get_orders()
            
            count = len(orders)
            account_orders[acc.name] = {'client': client, 'count': count}
            total_orders += count
            
            buy = sum(1 for o in orders if o.get('side') == 'BUY')
            sell = count - buy
            print(f"  {acc.name}: {count} orders (🟢{buy} 🔴{sell})")
            
        except Exception as e:
            print(f"  ❌ {acc.name}: {str(e)[:30]}")
    
    if total_orders == 0:
        print("\n✅ Нет открытых ордеров!")
        return
    
    print("-" * 50)
    print(f"\nTOTAL: {total_orders} orders on {len(account_orders)} accounts")
    
    confirm = input("\nCancel ALL orders on ALL accounts? (yes/no): ").strip().lower()

    if confirm not in ['yes', 'y']:
        print("Cancelled.")
        return
    
    print("\n⏳ Отменяю ордера...")
    
    cancelled_total = 0
    for acc_name, data in account_orders.items():
        try:
            if data['count'] > 0:
                result = data['client'].cancel_all()
                cancelled = data['count']
                cancelled_total += cancelled
                print(f"  {acc_name}: cancelled {cancelled}")
        except Exception as e:
            print(f"  ❌ {acc_name}: {str(e)[:30]}")
    
    print(f"\nTotal cancelled: {cancelled_total} orders")


def verify_all_accounts():
    """Verify all accounts at once - proxy, balance, orders, positions"""
    from core.client import get_clob_client, patch_httpx_for_proxy, verify_proxy_ip
    from core.data_api import DataAPI
    from concurrent.futures import ThreadPoolExecutor
    
    print_header("CHECK ALL ACCOUNTS")
    
    config = load_config()
    accounts = [Account.from_dict(a) for a in config.to_dict()['accounts'] 
                if a.get('enabled') and a.get('api_key')]
    
    if not accounts:
        print("\nNo active accounts!")
        return
    
    print(f"\nChecking {len(accounts)} accounts...\n")
    print("-" * 75)
    print(f"{'Account':<15} {'Proxy IP':<16} {'USDC':<10} {'Positions':<10} {'Orders':<10} {'Status':<10}")
    print("-" * 75)
    
    total_usdc = 0
    total_positions = 0
    total_orders = 0
    success_count = 0
    
    for acc in accounts:
        try:
            # Check proxy
            proxy_ip = "direct"
            if acc.proxy:
                patch_httpx_for_proxy(acc.proxy, force=True)
                ok, ip = verify_proxy_ip(acc.proxy)
                if ok:
                    proxy_ip = ip[:15] if len(ip) > 15 else ip
                else:
                    proxy_ip = "❌ error"
            
            # Get client
            client = get_clob_client(acc, force_new=True)
            
            # Get balance
            from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
            collateral = client.get_balance_allowance(
                params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
            balance = float(collateral.get('balance', 0)) / 1e6
            total_usdc += balance
            
            # Get orders
            orders = client.get_orders()
            orders_count = len(orders)
            total_orders += orders_count
            
            # Get positions
            wallet = acc.proxy_wallet or client.get_address()
            data_api = DataAPI(proxy=acc.proxy)
            positions = data_api.get_all_positions(wallet) or []
            pos_count = len(positions)
            total_positions += pos_count
            
            # Calculate PnL
            pnl = sum(float(p.get('cashPnl', 0) or 0) for p in positions)
            pnl_str = f"${pnl:+.0f}"
            
            status = "✅ OK"
            success_count += 1
            
            print(f"{acc.name:<15} {proxy_ip:<16} ${balance:<9.2f} {pos_count:<10} {orders_count:<10} {status}")
            
        except Exception as e:
            error = str(e)[:25]
            print(f"{acc.name:<15} {'?':<16} {'?':<10} {'?':<10} {'?':<10} ❌ {error}")
    
    print("-" * 75)
    print(f"\nTOTAL: {success_count}/{len(accounts)} accounts OK")
    print(f"   💵 USDC: ${total_usdc:.2f}")
    print(f"   Positions: {total_positions}")
    print(f"   Orders: {total_orders}")


def main_menu():
    """Main interactive menu"""
    ensure_presets()
    
    while True:
        clear_screen()
        
        print("\n" + "=" * 70)
        print("  POLYBETTER v3.0")
        print("=" * 70)
        print_info_bar()
        
        config = load_config()
        accounts = [a for a in config.to_dict()['accounts'] if a.get('enabled')]
        
        print(f"\n  Accounts: {len(config.accounts)} (active: {len(accounts)})")
        
        print("""
  ─────────────── ACCOUNT ───────────────
  V) Check ALL accounts
  1) Check proxy
  2) Check wallet
  3) Show positions

  ─────────────── LIMIT SNIPER ──────────
  4) Limit Sniper (1 account)
  5) Limit Sniper (ALL accounts)

  ─────────────── SMART SNIPER ──────────
  6) Smart Sniper (1 account)
  7) Smart Sniper (ALL accounts)

  ─────────────── TOOLS ─────────────────
  8) Telegram bot
  9) Market analysis
  S) Strategy simulation
  T) Trading statistics

  ─────────────── CANCEL ORDERS ──────────
  C) Cancel orders (1 account)
  X) Cancel orders (ALL accounts)

  ─────────────── EXIT / SELL ───────────
  P) Sell ALL positions (1 account)
  E) FULL EXIT (cancel + sell)

  ─────────────── SETTINGS ──────────────
  A) Account management

  0) Exit
""")
        print("=" * 70)
        
        try:
            choice = input("Choice: ").strip().lower()
            
            if choice == '0':
                print("\nGoodbye!")
                break
            
            elif choice == 'v':
                verify_all_accounts()
                input("\nPress Enter...")
            
            elif choice in ['1', '2', '3']:
                account = select_account()
                if account:
                    if choice == '1':
                        menu_check_proxy(account)
                    elif choice == '2':
                        menu_check_wallet(account)
                    elif choice == '3':
                        menu_show_positions(account)
                    input("\nPress Enter...")
            
            elif choice == '4':
                run_sniper()
            
            elif choice == '5':
                run_sniper_multithread()
            
            elif choice == '6':
                run_smart_sniper()
            
            elif choice == '7':
                run_smart_sniper_multithread()
            
            elif choice == '8':
                run_bot()
            
            elif choice == '9':
                run_analyzer()
                input("\nPress Enter...")
            
            elif choice == 's':
                run_simulator()
                input("\nPress Enter...")
            
            elif choice == 't':
                menu_view_stats()
                input("\nPress Enter...")
            
            elif choice == 'c':
                account = select_account()
                if account:
                    menu_cancel_orders(account)
                    input("\nPress Enter...")
            
            elif choice == 'x':
                menu_cancel_all_accounts()
                input("\nPress Enter...")
            
            elif choice == 'p':
                account = select_account()
                if account:
                    menu_sell_all_positions(account)
                    input("\nPress Enter...")
            
            elif choice == 'e':
                account = select_account()
                if account:
                    menu_full_exit(account)
                    input("\nPress Enter...")
            
            elif choice == 'a':
                menu_manage_accounts()
            
        except KeyboardInterrupt:
            print("\n\nGoodbye!")
            break
        except Exception as e:
            logger.exception(f"Menu error: {e}")
            print(f"\nError: {e}")
            input("\nPress Enter...")


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description="PolyBetter - Polymarket Trading Tool v3.0")
    parser.add_argument("command", nargs="?", default="menu",
                       choices=["menu", "sniper", "sniper-all", "smart", "smart-all", "bot", "analyze", "simulate"],
                       help="Command to run")
    
    args = parser.parse_args()
    
    logger.info(f"Starting tool: command={args.command}")
    
    if args.command == "menu":
        main_menu()
    elif args.command == "sniper":
        run_sniper()
    elif args.command == "sniper-all":
        run_sniper_multithread()
    elif args.command == "smart":
        run_smart_sniper()
    elif args.command == "smart-all":
        run_smart_sniper_multithread()
    elif args.command == "bot":
        run_bot()
    elif args.command == "analyze":
        run_analyzer()
    elif args.command == "simulate":
        run_simulator()


if __name__ == "__main__":
    main()
