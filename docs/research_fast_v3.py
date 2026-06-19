#!/usr/bin/env python3
"""
FAST JUNKYARD RESEARCH v3 — Smart Selection + Expanded Parts
- New arrivals get priority
- Backfill with vehicles not researched in 7+ days
- Max 50 vehicles, 22 hidden-gold parts each (expanded from 15)
- Added: catalytic converters, A/C compressor, power steering, wheels, fog lights, TPMS, backup camera
"""

import os, sys, json, time, base64, requests
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
LEDGER_FILE = DATA_DIR / "research_ledger.json"

CLIENT_ID = "JeffBasw-junkyard-PRD-07b75c3f4-14dc40cd"
CLIENT_SECRET = os.environ.get("EBAY_CLIENT_SECRET", "")
OAUTH_URL = "https://api.ebay.com/identity/v1/oauth2/token"
BROWSE_API = "https://api.ebay.com/buy/browse/v1"

_last_call = 0

def rate_limit():
    global _last_call
    elapsed = time.time() - _last_call
    if elapsed < 2.0:
        time.sleep(2.0 - elapsed)
    _last_call = time.time()

def get_token():
    if not CLIENT_SECRET:
        print("ERROR: EBAY_CLIENT_SECRET not set", file=sys.stderr)
        return None
    creds = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    headers = {"Authorization": f"Basic {creds}", "Content-Type": "application/x-www-form-urlencoded"}
    data = {"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"}
    rate_limit()
    resp = requests.post(OAUTH_URL, headers=headers, data=data, timeout=15)
    if resp.status_code == 200:
        return resp.json()["access_token"]
    print(f"Auth failed: {resp.status_code}", file=sys.stderr)
    return None

def search_active(token, query):
    url = f"{BROWSE_API}/item_summary/search"
    headers = {"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": "EBAY_US"}
    params = {"q": query, "limit": 20, "filter": "conditionIds:{3000|4000|5000}"}
    try:
        rate_limit()
        resp = requests.get(url, headers=headers, params=params, timeout=20)
        if resp.status_code != 200:
            return None
        data = resp.json()
        items = data.get("itemSummaries", [])
        if not items:
            return None
        prices = [float(item["price"]["value"]) for item in items if item.get("price", {}).get("value")]
        if not prices:
            return None
        prices.sort()
        return {"avg": round(sum(prices)/len(prices), 2), "median": prices[len(prices)//2], "min": prices[0], "max": prices[-1], "count": len(prices)}
    except Exception as e:
        print(f"  Search error: {e}", file=sys.stderr)
        return None

def scrape_cagles():
    url = "https://caglesupullit.com/inventory.aspx"
    try:
        resp = requests.get(url, timeout=20)
        html = resp.text
        import re
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)
        vehicles = []
        for row in rows:
            cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
            if len(cells) >= 5:
                clean = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
                clean = [re.sub(r'\s+', ' ', c).strip() for c in clean]
                vehicles.append({"year": clean[0], "make": clean[1], "model": clean[2], "yard_row": clean[3], "arrival_date": clean[4], "yard": "Cagle's"})
        return vehicles
    except Exception as e:
        print(f"Scrape error: {e}", file=sys.stderr)
        return []

# EXPANDED PARTS LIST v3
PARTS = {
    # Core hidden-gold parts (original 15)
    "instrument cluster": {"cost": 25, "template": "{year} {make} {model} instrument cluster"},
    "ECU": {"cost": 30, "template": "{year} {make} {model} ECU ECM computer"},
    "ABS module": {"cost": 30, "template": "{year} {make} {model} ABS module"},
    "throttle body": {"cost": 20, "template": "{year} {make} {model} throttle body"},
    "amplifier": {"cost": 30, "template": "{year} {make} {model} amplifier amp"},
    "BCM": {"cost": 25, "template": "{year} {make} {model} BCM body control module"},
    "TCM": {"cost": 25, "template": "{year} {make} {model} TCM transmission control"},
    "HID ballast": {"cost": 20, "template": "{year} {make} {model} HID ballast"},
    "climate control": {"cost": 25, "template": "{year} {make} {model} climate control HVAC"},
    "fuel pump": {"cost": 20, "template": "{year} {make} {model} fuel pump"},
    "turbocharger": {"cost": 50, "template": "{year} {make} {model} turbocharger"},
    "seat control": {"cost": 20, "template": "{year} {make} {model} seat control module"},
    "alternator": {"cost": 25, "template": "{year} {make} {model} alternator"},
    "MAF sensor": {"cost": 15, "template": "{year} {make} {model} MAF sensor"},
    "radio nav": {"cost": 25, "template": "{year} {make} {model} radio navigation"},
    # NEW: High-value additions
    "catalytic converter": {"cost": 50, "template": "{year} {make} {model} catalytic converter OEM"},
    "A/C compressor": {"cost": 30, "template": "{year} {make} {model} A/C compressor"},
    "power steering pump": {"cost": 25, "template": "{year} {make} {model} power steering pump"},
    "wheels alloy set": {"cost": 60, "template": "{year} {make} {model} OEM alloy wheels set of 4"},
    "fog lights": {"cost": 20, "template": "{year} {make} {model} fog light kit"},
    "TPMS sensors": {"cost": 20, "template": "{year} {make} {model} TPMS tire pressure sensors set"},
    "backup camera": {"cost": 15, "template": "{year} {make} {model} backup camera OEM"},
}

MAKE_SCORES = {"BMW": 12, "MERCEDES-BENZ": 12, "CADILLAC": 10, "HUMMER": 10, "VOLVO": 10, "LEXUS": 9, "AUDI": 9, "INFINITI": 8, "ACURA": 8, "TOYOTA": 5, "HONDA": 5, "CHEVROLET": 5, "FORD": 5, "JEEP": 5, "GMC": 5, "MAZDA": 4, "NISSAN": 4, "SUBARU": 4, "HYUNDAI": 3, "KIA": 3}

def score(v):
    s = MAKE_SCORES.get(v.get("make","").upper(), 2)
    model = v.get("model","").upper()
    if any(w in model for w in ["NAV","DVD","SPORT","LUXURY","PREMIUM","TOURING","TURBO","AMG","HYBRID","DENALI","ESCALADE","M3","M5","S4","S5","C63","335","435"]):
        s += 3
    return s

def calc_margin(price, cost):
    if not price: return 0
    return round(price - cost - 12 - (price * 0.13 + 0.30), 2)

# ── Ledger functions ──────────────────────────────────────────────────────────

def load_ledger():
    if LEDGER_FILE.exists():
        try:
            with open(LEDGER_FILE) as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_ledger(ledger):
    LEDGER_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER_FILE, "w") as f:
        json.dump(ledger, f, indent=2)

def vehicle_id(v):
    return f"{v['year']}|{v['make']}|{v['model']}|{v['yard_row']}|{v['arrival_date']}"

def is_new_arrival(v, ledger):
    return vehicle_id(v) not in ledger

def days_since_researched(v, ledger):
    vid = vehicle_id(v)
    if vid not in ledger:
        return float('inf')
    try:
        last = datetime.fromisoformat(ledger[vid])
        return (datetime.now() - last).days
    except:
        return float('inf')

def select_vehicles(vehicles, ledger, max_vehicles=50):
    """Select vehicles: new first, then backfill with 7+ day old"""
    new_arrivals = [v for v in vehicles if is_new_arrival(v, ledger)]
    existing = [v for v in vehicles if not is_new_arrival(v, ledger)]
    
    selected = []
    
    # Priority 1: New arrivals (sorted by score)
    selected.extend(sorted(new_arrivals, key=score, reverse=True))
    
    # Priority 2: Existing not researched in 7+ days
    if len(selected) < max_vehicles:
        candidates = [v for v in existing if days_since_researched(v, ledger) >= 7]
        selected.extend(sorted(candidates, key=score, reverse=True)[:max_vehicles - len(selected)])
    
    # Priority 3: Fill with highest-scored remaining
    if len(selected) < max_vehicles:
        selected_ids = {vehicle_id(v) for v in selected}
        remaining = [v for v in existing if vehicle_id(v) not in selected_ids]
        selected.extend(sorted(remaining, key=score, reverse=True)[:max_vehicles - len(selected)])
    
    return selected[:max_vehicles]

def main():
    print("="*60)
    print("FAST JUNKYARD RESEARCH v3 — Smart Selection + 22 Parts")
    print("="*60)
    
    if not CLIENT_SECRET:
        print("ERROR: Set EBAY_CLIENT_SECRET", file=sys.stderr)
        sys.exit(1)
    
    # Auth
    print("\n[1/5] Authenticating...")
    token = get_token()
    if not token:
        print("Auth failed!")
        sys.exit(1)
    print("   ✅ Authenticated")
    
    # Scrape
    print("\n[2/5] Scraping Cagle's inventory...")
    vehicles = scrape_cagles()
    if not vehicles:
        print("No vehicles found!")
        sys.exit(1)
    print(f"   ✅ Found {len(vehicles)} vehicles")
    
    # Save inventory
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(DATA_DIR / "cagles_inventory_latest.json", "w") as f:
        json.dump({"vehicles": vehicles}, f, indent=2)
    
    # Load ledger and select vehicles
    print("\n[3/5] Selecting vehicles...")
    ledger = load_ledger()
    print(f"   📓 Ledger: {len(ledger)} previously researched")
    
    top = select_vehicles(vehicles, ledger, max_vehicles=50)
    new_count = sum(1 for v in top if is_new_arrival(v, ledger))
    print(f"   🎯 {len(top)} selected ({new_count} new, {len(top) - new_count} existing)")
    
    # Research
    print(f"\n[4/5] Researching {len(top)} vehicles × {len(PARTS)} parts...")
    print(f"   ⏱️  Estimated: ~{len(top) * len(PARTS) * 2 // 60} minutes")
    print(f"   Press Ctrl+C to cancel\n")
    
    results = []
    total_api_calls = 0
    
    for i, v in enumerate(top, 1):
        v_str = f"{v['year']} {v['make']} {v['model']}"
        is_new = "🆕 " if is_new_arrival(v, ledger) else "🔄 "
        print(f"[{i:2d}/{len(top)}] {is_new}{v_str}")
        
        part_data = {}
        for part_name, config in PARTS.items():
            query = config["template"].format(**v)
            stats = search_active(token, query)
            total_api_calls += 1
            
            if stats:
                m = calc_margin(stats["avg"], config["cost"])
                part_data[part_name] = {
                    "active": stats,
                    "yard_cost": config["cost"],
                    "est_margin": m
                }
                print(f"      {part_name:25s} avg ${stats['avg']:6.0f}  margin ${m:6.0f}")
            else:
                print(f"      {part_name:25s} no data")
        
        margins = {p: d["est_margin"] for p, d in part_data.items()}
        best = max(margins.values()) if margins else 0
        total_m = sum(margins.values()) if margins else 0
        
        results.append({
            "vehicle": v,
            "parts": part_data,
            "best_margin": best,
            "total_margin": total_m,
            "parts_with_data": len(part_data)
        })
        
        # Update ledger
        ledger[vehicle_id(v)] = datetime.now().isoformat()
        
        print(f"   → Best: ${best:.0f} | Total: ${total_m:.0f} | Data: {len(part_data)}/{len(PARTS)}")
        print()
        
        if i % 5 == 0:
            with open(DATA_DIR / "research_output_latest.json", "w") as f:
                json.dump(results, f, indent=2)
            save_ledger(ledger)
            print(f"💾 Checkpoint saved ({i} vehicles, {total_api_calls} API calls)")
            print()
    
    # Final saves
    with open(DATA_DIR / "research_output_latest.json", "w") as f:
        json.dump(results, f, indent=2)
    save_ledger(ledger)
    
    # Summary
    print(f"\n{'='*60}")
    print("✅ RESEARCH COMPLETE")
    print(f"{'='*60}")
    print(f"Vehicles:     {len(results)}")
    print(f"API calls:    {total_api_calls}")
    print(f"Avg margin:   ${sum(r['best_margin'] for r in results)/len(results):.0f}")
    best = max(results, key=lambda x: x['best_margin'])
    print(f"Best:         {best['vehicle']['year']} {best['vehicle']['make']} {best['vehicle']['model']} (+${best['best_margin']:.0f})")
    print(f"Ledger:       {LEDGER_FILE}")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted. Partial results saved.")
        sys.exit(0)
