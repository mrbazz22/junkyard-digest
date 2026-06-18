#!/usr/bin/env python3
"""
JUNKYARD DIGEST PIPELINE — orchestrator
======================================
Runs the full daily flow:
  1. Scrape Cagle's inventory
  2. eBay Browse API research (NEW + backfill)
  3. Build research_data.json + part_locations.json
  4. Copy fresh data + app.html into docs/ for GitHub Pages

Usage:
    export EBAY_CLIENT_SECRET='PRD-xxxx'
    python3 scripts/run_pipeline.py [--quick] [--skip-scrape] [--skip-research] [--publish]

Env:
    EBAY_CLIENT_SECRET       required for research
    EBAY_CERT_PATH           optional override path to eBay cert id
"""

import os
import sys
import json
import shutil
import subprocess
import argparse
from pathlib import Path
from datetime import datetime

# --- paths ---
REPO = Path(__file__).resolve().parent.parent
DATA_DIR = REPO / "data"
DOCS_DIR = REPO / "docs"
RESEARCH_SCRIPT = DOCS_DIR / "research_fast_v3.py"

# --- eBay credentials ---
EBAY_CRED = json.load(open(Path.home() / ".openclaw" / "ebay_credentials.json"))
CLIENT_ID = EBAY_CRED["production"]["app_id"]
CLIENT_SECRET = os.environ.get("EBAY_CLIENT_SECRET") or EBAY_CRED["production"]["cert_id"]

# --- colors for terminal ---
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
RESET = "\033[0m"


def step(msg):
    print(f"\n{BLUE}{'='*60}\n{msg}\n{'='*60}{RESET}")


def ok(msg):
    print(f"{GREEN}✅ {msg}{RESET}")


def warn(msg):
    print(f"{YELLOW}⚠️  {msg}{RESET}")


def fail(msg):
    print(f"{RED}❌ {msg}{RESET}")


def scrape_cagles():
    """Quick scrape test — returns count + saves to data/cagles_inventory_latest.json"""
    import requests, re
    step("[1/4] Scraping Cagle's inventory…")
    try:
        r = requests.get("https://caglesupullit.com/inventory.aspx", timeout=20)
        r.raise_for_status()
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", r.text, re.DOTALL)
        vehicles = []
        for row in rows:
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
            if len(cells) >= 5:
                clean = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
                clean = [re.sub(r"\s+", " ", c).strip() for c in clean]
                # skip header
                if clean[0].lower() == "year":
                    continue
                vehicles.append({
                    "year": clean[0],
                    "make": clean[1],
                    "model": clean[2],
                    "yard_row": clean[3],
                    "arrival_date": clean[4],
                    "yard": "Cagle's",
                })
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(DATA_DIR / "cagles_inventory_latest.json", "w") as f:
            json.dump({"vehicles": vehicles, "scraped_at": datetime.now().isoformat()}, f, indent=2)
        ok(f"Found {len(vehicles)} vehicles at Cagle's")
        return vehicles
    except Exception as e:
        fail(f"Scrape failed: {e}")
        return []


def run_research(quick=False):
    """Run research_fast_v3.py with env vars set"""
    step("[2/4] Running eBay research (may take 5-30 minutes)…")
    if not CLIENT_SECRET:
        fail("EBAY_CLIENT_SECRET not set")
        sys.exit(1)
    env = os.environ.copy()
    env["EBAY_CLIENT_SECRET"] = CLIENT_SECRET
    args = [sys.executable, str(RESEARCH_SCRIPT)]
    if quick:
        # patch max_vehicles would need arg parsing in v3
        warn("Quick mode not implemented in research_fast_v3.py — running full")
    try:
        result = subprocess.run(args, env=env, cwd=str(REPO), check=False)
        if result.returncode != 0:
            fail(f"Research script exited {result.returncode}")
            return False
        ok("Research complete")
        return True
    except Exception as e:
        fail(f"Research failed: {e}")
        return False


def transform_research_output():
    """Convert research_output_latest.json (array) into research_data.json (dict with stats)"""
    step("[3/4] Transforming research output…")
    src = DATA_DIR / "research_output_latest.json"
    if not src.exists():
        fail(f"Missing {src}")
        return False
    with open(src) as f:
        results = json.load(f)
    if not results:
        fail("Research output is empty")
        return False

    # enrich with is_new, is_hv flags based on ledger
    ledger_path = DATA_DIR / "research_ledger.json"
    ledger = {}
    if ledger_path.exists():
        try:
            with open(ledger_path) as f:
                ledger = json.load(f)
        except:
            pass

    def vid(v):
        return f"{v['year']}|{v['make']}|{v['model']}|{v.get('yard_row','')}|{v.get('arrival_date','')}"

    for r in results:
        v = r["vehicle"]
        r["is_new"] = vid(v) not in ledger
        r["is_hv"] = r.get("best_margin", 0) >= 500

    margins = [r.get("best_margin", 0) for r in results]
    new_count = sum(1 for r in results if r.get("is_new"))
    hv_count = sum(1 for r in results if r.get("is_hv"))
    total_parts = sum(len(r.get("parts", {})) for r in results)

    payload = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "yard": "Cagle's",
        "stats": {
            "total": len(results),
            "new": new_count,
            "hv": hv_count,
            "avg_margin": round(sum(margins) / len(margins), 2) if margins else 0,
            "best_margin": max(margins) if margins else 0,
            "total_parts": total_parts,
        },
        "vehicles": results,
    }
    # write to repo root AND docs/ for Pages
    for out_path in [REPO / "research_data.json", DOCS_DIR / "research_data.json"]:
        with open(out_path, "w") as f:
            json.dump(payload, f, indent=2)
    ok(f"Transformed {len(results)} vehicles (new={new_count}, hv={hv_count}, best=${max(margins):.0f})")
    return True


def publish():
    """Copy fresh assets into docs/ for GitHub Pages and commit"""
    step("[4/4] Publishing to GitHub Pages (docs/)…")

    # Always copy app.html → index.html
    src_app = DOCS_DIR / "app.html"
    dst_idx = DOCS_DIR / "index.html"
    if src_app.exists():
        shutil.copy(src_app, dst_idx)
        ok(f"docs/index.html ← docs/app.html")
    else:
        fail(f"Missing {src_app}")
        return False

    # Confirm data files exist in docs/
    for fname in ["research_data.json", "part_locations.json", "yard_map.json"]:
        src = REPO / fname
        dst = DOCS_DIR / fname
        if src.exists():
            shutil.copy(src, dst)
            size_kb = src.stat().st_size / 1024
            ok(f"docs/{fname} ← {fname} ({size_kb:.0f} KB)")
        else:
            warn(f"{fname} not found at repo root, skipping")

    # Git commit + push
    print()
    if subprocess.run(["git", "-C", str(REPO), "diff", "--cached", "--quiet"]).returncode == 0:
        # see if there's anything new to stage
        r = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain"],
                          capture_output=True, text=True)
        if r.stdout.strip():
            subprocess.run(["git", "-C", str(REPO), "add", "-A"], check=False)
        else:
            ok("Nothing new to commit")
            return True

    msg = f"Digest: {datetime.now().strftime('%Y-%m-%d %H:%M')} — auto-publish"
    subprocess.run(["git", "-C", str(REPO), "commit", "-m", msg], check=False)
    r = subprocess.run(["git", "-C", str(REPO), "push", "origin", "main"],
                       capture_output=True, text=True)
    if r.returncode == 0:
        ok(f"Pushed: {msg}")
        return True
    else:
        fail(f"Push failed: {r.stderr}")
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-scrape", action="store_true")
    parser.add_argument("--skip-research", action="store_true")
    parser.add_argument("--publish-only", action="store_true",
                        help="Just rebuild docs/ from existing data + push")
    parser.add_argument("--no-push", action="store_true")
    args = parser.parse_args()

    print(f"{BLUE}🚗 JUNK{'='*50}YARD DIGEST PIPELINE")
    print(f"{'='*60}")
    print(f"Repo: {REPO}")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}{RESET}\n")

    if args.publish_only:
        publish()
        return 0

    if not args.skip_scrape:
        scrape_cagles()
    if not args.skip_research:
        if not run_research():
            return 1
    if not transform_research_output():
        return 1
    if not args.no_push:
        publish()
    ok("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())