#!/usr/bin/env python3
"""Sell all positions at MARKET price (FOK) for active account"""
import sys
import os
import time
os.environ['PYTHONIOENCODING'] = 'utf-8'
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from core.config import load_config, Account
from core.logger import init_logger
from core.client import get_clob_client, patch_httpx_for_proxy
from core.data_api import DataAPI
from py_clob_client.clob_types import MarketOrderArgs, OrderType, BalanceAllowanceParams, AssetType

logger = init_logger()

def main():
    config = load_config()
    accounts = [Account.from_dict(a) for a in config.to_dict()['accounts'] if a.get('enabled')]
    
    if not accounts:
        print("No active accounts!")
        return
    
    account = accounts[0]
    print(f"=== MARKET SELL ALL POSITIONS - {account.name} ===\n")
    
    if account.proxy:
        patch_httpx_for_proxy(account.proxy, force=True)
    
    client = get_clob_client(account, force_new=True)
    wallet = account.proxy_wallet or client.get_address()
    data_api = DataAPI(proxy=account.proxy)
    
    # Approve conditional tokens
    print("Approving conditional tokens...")
    
    # Get positions
    positions = data_api.get_all_positions(wallet)
    
    if not positions:
        print("No positions found!")
        return
    
    # Filter sellable
    sellable = []
    for pos in positions:
        token_id = pos.get('asset', '') or pos.get('tokenId', '')
        size = float(pos.get('size', 0) or 0)
        if token_id and size >= 1:
            sellable.append(pos)
    
    if not sellable:
        print("No sellable positions!")
        return
    
    total_shares = sum(float(p.get('size', 0) or 0) for p in sellable)
    print(f"Positions: {len(sellable)} ({total_shares:.0f} shares)")
    print("=" * 70)
    
    sold = 0
    failed = 0
    skipped = 0
    total_usdc = 0.0
    
    for i, pos in enumerate(sellable, 1):
        token_id = pos.get('asset', '') or pos.get('tokenId', '')
        size = float(pos.get('size', 0) or 0)
        title = (pos.get('title', '?') or '?')[:45]
        outcome = pos.get('outcome', '?')[:5]
        sell_size = int(size)
        
        if sell_size < 1:
            skipped += 1
            continue
        
        # Get orderbook to find best bid
        orderbook = data_api.get_orderbook(token_id)
        if not orderbook:
            skipped += 1
            print(f"  [{i}/{len(sellable)}] SKIP (closed): [{outcome}] {title}")
            continue
        
        bids = orderbook.get('bids', [])
        if not bids:
            skipped += 1
            print(f"  [{i}/{len(sellable)}] SKIP (no bids): [{outcome}] {title}")
            continue
        
        best_bid = float(bids[0].get('price', 0))
        if best_bid <= 0:
            skipped += 1
            print(f"  [{i}/{len(sellable)}] SKIP (bid=0): [{outcome}] {title}")
            continue
        
        # Approve token
        try:
            client.update_balance_allowance(
                params=BalanceAllowanceParams(
                    asset_type=AssetType.CONDITIONAL,
                    token_id=token_id
                )
            )
        except:
            pass
        
        # Create market sell order (FOK)
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
                    sold += 1
                    print(f"  [{i}/{len(sellable)}] SOLD: [{outcome}] {title} | {sell_size} @ ${best_bid:.4f} -> ${usdc:.2f}")
                else:
                    sold += 1
                    est_usdc = sell_size * best_bid
                    total_usdc += est_usdc
                    print(f"  [{i}/{len(sellable)}] SOLD: [{outcome}] {title} | {sell_size} @ ${best_bid:.4f} (~${est_usdc:.2f})")
            else:
                status = result.get('status', '')
                error = result.get('errorMsg', str(result))[:80]
                failed += 1
                print(f"  [{i}/{len(sellable)}] FAIL: [{outcome}] {title} | {status} {error}")
        except Exception as e:
            failed += 1
            print(f"  [{i}/{len(sellable)}] ERROR: [{outcome}] {title} | {str(e)[:80]}")
        
        time.sleep(0.2)
    
    print(f"\n{'='*70}")
    print(f"  RESULTS: sold={sold} | failed={failed} | skipped={skipped}")
    print(f"  USDC received: ~${total_usdc:.2f}")
    print(f"{'='*70}")

if __name__ == "__main__":
    main()
