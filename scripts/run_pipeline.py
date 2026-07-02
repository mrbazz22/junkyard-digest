#!/usr/bin/env python3
"""
JUNKYARD DIGEST PIPELINE — orchestrator (FORMAT-SPEC v1.0)
========================================================
Runs the full daily flow:
  1. Scrape Cagle's inventory
  2. eBay Browse API research (NEW + backfill)
  3. Build research_data.json + part_locations.json
  4. Copy fresh data + app.html into docs/ for GitHub Pages
  5. Build Markdown digest (docs/digest_latest.md)
  6. Commit + push

Usage:
    export EBAY_CLIENT_SECRET='***'
    python3 scripts/run_pipeline.py [--quick] [--skip-scrape] [--skip-research] [--publish-only] [--no-push] [--dry-run]

Env:
    EBAY_CLIENT_SECRET       required for research
"""

import os
import sys
import json
import shutil
import subprocess
import argparse
import re
from pathlib import Path
from datetime import datetime

# --- paths ---
REPO = Path(__file__).resolve().parent.parent
DATA_DIR = REPO / "data"
DOCS_DIR = REPO / "docs"
RESEARCH_SCRIPT = DOCS_DIR / "research_fast_v3.py"

# --- format spec version (FORMAT-SPEC.md) ---
SPEC_VERSION = "v1.0"

# --- eBay credentials ---
EBAY_CRED_PATH = Path.home() / ".openclaw" / "ebay_credentials.json"
EBAY_CRED = json.load(open(EBAY_CRED_PATH))
CLIENT_ID = EBAY_CRED["production"]["app_id"]
CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET") or EBAY_CRED["production"]["cert_id"]

# --- thresholds (from FORMAT-SPEC §3) ---
HV_THRESHOLD = 500         # best_margin ≥ $500 → HV badge
LAST_THRESHOLD_DAYS = 13   # days_in_yard ≥ 13 → LAST badge
SHOW_THRESHOLD = 50        # best_margin ≥ $50 → "$50+" filter
MAX_VEHICLES = 50


def preflight_syntax_check(paths):
    """Validate Python syntax of every script we'll execute (or import).

    Catches the recurring failure mode where os.getenv / print calls get
    scrubbed to invalid `***...` placeholders in disk-written files.
    Runs once at the top of main() and before any subprocess invocation.
    Returns True iff every file parses cleanly.
    """
    import ast
    bad = []
    for p in paths:
        try:
            ast.parse(Path(p).read_text())
        except SyntaxError as e:
            bad.append((p, e.lineno, str(e)))
    if bad:
        print(f"{RED}❌ Preflight: {len(bad)} file(s) have syntax errors:{RESET}")
        for p, ln, msg in bad:
            print(f"   {p}:{ln}: {msg}")
        return False
    return True

# --- colors ---
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
    """[1/5] Quick scrape test — saves to data/cagles_inventory_latest.json"""
    import requests
    step("[1/5] Scraping Cagle's inventory…")
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


def run_research():
    """[2/5] Run research_fast_v3.py with env vars set (long-running)"""
    step("[2/5] Running eBay research (~36 min for 50 vehicles × 22 parts)…")
    if not CLIENT_SECRET:
        print("ERROR: EBAY_CLIENT_SECRET not set", file=sys.stderr)
        sys.exit(1)
    # Re-validate syntax of the long-running script (defense in depth — catches
    # anything that may have changed between main() preflight and now).
    if not preflight_syntax_check([str(RESEARCH_SCRIPT)]):
        fail("Research script has a syntax error. Refusing to launch.")
        return False
    env = os.environ.copy()
    env["EBAY_CLIENT_SECRET"] = CLIENT_SECRET
    try:
        result = subprocess.run([sys.executable, "-u", str(RESEARCH_SCRIPT)],
                                env=env, cwd=str(REPO), check=False)
        if result.returncode != 0:
            fail(f"Research script exited {result.returncode}")
            return False
        ok("Research complete")
        return True
    except Exception as e:
        fail(f"Research failed: {e}")
        return False


def transform_research_output():
    """[3/5] Convert research_output_latest.json (array) → research_data.json (dict w/ stats)"""
    step("[3/5] Transforming research output (FORMAT-SPEC §4 schema)…")
    src = DATA_DIR / "research_output_latest.json"
    if not src.exists():
        fail(f"Missing {src}")
        return False
    with open(src) as f:
        raw = json.load(f)
    if not raw:
        fail("Research output is empty")
        return False

    # Research script outputs nested: [{vehicle:{...}, parts:{...}, best_margin, total_margin, parts_with_data}, ...]
    # Flatten to the SPA schema: {year, make, model, yard_row, arrival_date, yard, best_margin, total_margin, parts, is_new, is_hv}
    results = []
    for r in raw:
        v = r.get("vehicle", r)  # fall back to flat if already flat
        # Skip header rows that slipped through
        if v.get("year", "").lower() == "year" or v.get("make", "").lower() == "make":
            continue
        results.append({
            "year": v.get("year", ""),
            "make": v.get("make", ""),
            "model": v.get("model", ""),
            "yard_row": v.get("yard_row", ""),
            "arrival_date": v.get("arrival_date", ""),
            "yard": v.get("yard", "Cagle's"),
            "best_margin": r.get("best_margin", 0),
            "total_margin": r.get("total_margin", 0),
            "parts_with_data": r.get("parts_with_data", len(r.get("parts", {}))),
            "parts": r.get("parts", {}),
        })

    # enrich with is_new, is_hv flags (FORMAT-SPEC §3)
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
        r["is_new"] = vid(r) not in ledger
        r["is_hv"] = r.get("best_margin", 0) >= HV_THRESHOLD

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
    for out_path in [REPO / "research_data.json", DOCS_DIR / "research_data.json"]:
        with open(out_path, "w") as f:
            json.dump(payload, f, indent=2)
    ok(f"{len(results)} vehicles (new={new_count}, hv={hv_count}, best=${max(margins):.0f})")
    return True


def build_markdown_digest():
    """[4/5] Build docs/digest_latest.md per FORMAT-SPEC §6"""
    step(f"[4/5] Building markdown digest (FORMAT-SPEC §6, {SPEC_VERSION})…")
    src = DOCS_DIR / "research_data.json"
    if not src.exists():
        fail(f"Missing {src}")
        return False
    with open(src) as f:
        data = json.load(f)

    today = datetime.now()
    md = []
    md.append(f"# 🚗 Junkyard Digest — Cagle's U Pull It")
    md.append(f"**Date:** {today.strftime('%A, %B %d, %Y')}")
    md.append(f"**Yard data from:** {data['date']}")
    md.append(f"**Total vehicles:** {data['stats']['total']} · **New arrivals:** {data['stats']['new']} · **High-value (HV):** {data['stats']['hv']}")
    md.append(f"**Avg best margin:** ${data['stats']['avg_margin']:.0f} · **Top margin:** ${data['stats']['best_margin']:.0f}")
    md.append(f"**Total parts researched:** {data['stats']['total_parts']:,}")
    md.append("")
    md.append("---")
    md.append("")

    # §6.2 — Top 10 — Best Margin
    top = sorted(data['vehicles'], key=lambda v: v.get('best_margin', 0), reverse=True)
    md.append("## 🏆 Top 10 — Best Margin")
    md.append("")
    md.append("| # | Vehicle | Row | Best Margin | Parts w/ Data |")
    md.append("|---|---------|-----|-------------|---------------|")
    for i, v in enumerate(top[:10], 1):
        parts_with = sum(1 for p, d in v['parts'].items() if d.get('est_margin', 0) > 0)
        total_parts = len(v['parts'])
        new_badge = "🆕 " if v.get('is_new') else ""
        hv_badge = " 💰" if v.get('is_hv') else ""
        md.append(f"| {i} | {new_badge}{v['year']} {v['make']} {v['model']}{hv_badge} | {v['yard_row']} | **${v['best_margin']:.0f}** | {parts_with}/{total_parts} |")
    md.append("")
    md.append("---")
    md.append("")

    # §6.3 — New Arrivals
    new = [v for v in data['vehicles'] if v.get('is_new')]
    md.append(f"## 🆕 New Arrivals ({len(new)})")
    if new:
        md.append("")
        md.append("| Vehicle | Row | Arrived | Best |")
        md.append("|---------|-----|---------|------|")
        for v in sorted(new, key=lambda v: v.get('best_margin', 0), reverse=True):
            md.append(f"| {v['year']} {v['make']} {v['model']} | {v['yard_row']} | {v['arrival_date']} | ${v['best_margin']:.0f} |")
    else:
        md.append("")
        md.append("_No new arrivals in this run._")
    md.append("")

    # §6.4 — Leaving Soon
    last_vehicles = []
    for v in data['vehicles']:
        try:
            m = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})', v.get('arrival_date', ''))
            if m:
                d = datetime(int(m.group(3)), int(m.group(1)), int(m.group(2)))
                days = (today - d).days
                if days >= LAST_THRESHOLD_DAYS:
                    last_vehicles.append((v, days))
        except:
            pass
    last_vehicles.sort(key=lambda x: -x[1])

    md.append(f"## ⏰ Leaving Soon ({LAST_THRESHOLD_DAYS}+ days, {len(last_vehicles)} vehicles)")
    md.append("")
    if last_vehicles:
        md.append("| Vehicle | Row | Days | Arrived | Best |")
        md.append("|---------|-----|------|---------|------|")
        for v, days in last_vehicles[:15]:
            md.append(f"| {v['year']} {v['make']} {v['model']} | {v['yard_row']} | {days}d | {v['arrival_date']} | ${v['best_margin']:.0f} |")
    md.append("")
    md.append("---")
    md.append("")
    md.append(f"**Live app:** https://mrbazz22.github.io/junkyard-digest/")
    md.append("")
    md.append(f"_Generated by junkyard-digest pipeline {SPEC_VERSION} ({today.strftime('%Y-%m-%d %H:%M')})_")

    text = "\n".join(md)
    out = DOCS_DIR / "digest_latest.md"
    out.write_text(text)
    ok(f"Wrote {out} ({len(text):,} chars, {len(md)} lines)")
    return True


def publish(dry_run=False):
    """[5/5] Copy fresh assets into docs/, commit, push (FORMAT-SPEC §8 invariants)"""
    step(f"[5/5] Publishing to GitHub Pages (FORMAT-SPEC {SPEC_VERSION})…")

    # Invariant: docs/index.html == docs/app.html
    src_app = DOCS_DIR / "app.html"
    dst_idx = DOCS_DIR / "index.html"
    if src_app.exists():
        shutil.copy(src_app, dst_idx)
        ok(f"docs/index.html ← docs/app.html")
    else:
        fail(f"Missing {src_app}")
        return False

    # Copy data files
    for fname in ["research_data.json", "part_locations.json", "yard_map.json"]:
        src = REPO / fname
        dst = DOCS_DIR / fname
        if src.exists():
            shutil.copy(src, dst)
            size_kb = src.stat().st_size / 1024
            ok(f"docs/{fname} ← {fname} ({size_kb:.0f} KB)")
        else:
            warn(f"{fname} not found at repo root, skipping")

    if dry_run:
        warn("--dry-run: skipping git commit + push")
        return True

    # Invariant: commit message format
    if subprocess.run(["git", "-C", str(REPO), "diff", "--cached", "--quiet"]).returncode == 0:
        r = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain"],
                          capture_output=True, text=True)
        if r.stdout.strip():
            subprocess.run(["git", "-C", str(REPO), "add", "-A"], check=False)
        else:
            ok("Nothing new to commit")
            return True

    msg = f"Digest: {datetime.now().strftime('%Y-%m-%d %H:%M')} — auto-publish"
    if not msg.startswith("Digest: "):
        fail(f"Invariant violation: commit message must start with 'Digest: '")
        return False

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
    parser = argparse.ArgumentParser(description=f"Junkyard Digest Pipeline {SPEC_VERSION}")
    parser.add_argument("--skip-scrape", action="store_true")
    parser.add_argument("--skip-research", action="store_true")
    parser.add_argument("--publish-only", action="store_true",
                        help="Just rebuild docs/ from existing data + push")
    parser.add_argument("--no-push", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--version", action="version", version=f"junkyard-digest {SPEC_VERSION}")
    args = parser.parse_args()

    print(f"{BLUE}🚗 JUNK{'='*50}YARD DIGEST PIPELINE")
    print(f"{'='*60}")
    print(f"Version: {SPEC_VERSION}")
    print(f"Repo: {REPO}")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}{RESET}\n")

    # Preflight: catch scrubbed-syntax failures before any subprocess runs
    preflight = [
        str(Path(__file__).resolve()),       # this orchestrator
        str(RESEARCH_SCRIPT),                  # the long-running research script
    ]
    if not preflight_syntax_check(preflight):
        fail("Preflight failed. Aborting before any subprocess is launched.")
        return 2

    if args.publish_only:
        publish(dry_run=args.dry_run)
        return 0

    if not args.skip_scrape:
        scrape_cagles()
    if not args.skip_research:
        if not run_research():
            return 1
    if not transform_research_output():
        return 1
    if not build_markdown_digest():
        return 1
    if not args.no_push:
        publish(dry_run=args.dry_run)

    ok("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())