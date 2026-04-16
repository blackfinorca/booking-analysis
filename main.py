#!/usr/bin/env python3
"""
Booking.com property scraper & review analyzer.

Usage:
  python main.py <booking_url>
  python main.py <booking_url> --verbose
  python main.py <booking_url> --no-headless
  python main.py --reanalyze results/my-property_raw.json
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from scraper import BookingScraper, save_results, load_results
from analyzer import ReviewAnalyzer

# ── Optional rich output ──────────────────────────────────────────────────────
try:
    from rich.console import Console
    from rich.panel import Panel

    console = Console()
    HAS_RICH = True
except ImportError:
    HAS_RICH = False
    console = None  # type: ignore


# ──────────────────────────────────────────────────────────────────────────────
# Display helpers
# ──────────────────────────────────────────────────────────────────────────────

def hdr(text: str):
    if HAS_RICH:
        console.rule(f"[bold cyan]{text}[/bold cyan]")
    else:
        print(f"\n{'=' * 60}\n  {text}\n{'=' * 60}")


def info(text: str):
    if HAS_RICH:
        console.print(f"[dim]{text}[/dim]")
    else:
        print(text)


def success(text: str):
    if HAS_RICH:
        console.print(f"[bold green]{text}[/bold green]")
    else:
        print(f"✓ {text}")


def warn(text: str):
    if HAS_RICH:
        console.print(f"[bold yellow]{text}[/bold yellow]")
    else:
        print(f"! {text}")


def err(text: str):
    if HAS_RICH:
        console.print(f"[bold red]{text}[/bold red]")
    else:
        print(f"ERROR: {text}", file=sys.stderr)


# ──────────────────────────────────────────────────────────────────────────────
# Analysis printer
# ──────────────────────────────────────────────────────────────────────────────

def print_analysis(analysis: dict):
    if analysis.get("parse_error"):
        warn("Claude returned non-JSON — raw output below:")
        print(analysis.get("raw_analysis", ""))
        return

    prop      = analysis.get("property_name", "Unknown")
    total     = analysis.get("total_reviews_analyzed", 0)
    sentiment = analysis.get("overall_sentiment", "")
    summary   = analysis.get("summary", "")

    hdr("Overview")
    if HAS_RICH:
        console.print(
            Panel(
                f"[bold]{prop}[/bold]\n"
                f"[dim]{total} reviews  ·  {sentiment}[/dim]\n\n{summary}",
                border_style="cyan",
            )
        )
    else:
        print(f"{prop}  |  {total} reviews  |  {sentiment}\n{summary}")

    _section_themes("What Guests Love",    analysis.get("what_guests_love", []),    "green", "theme", "detail", "example_quotes", "frequency")
    _section_themes("What Guests Dislike", analysis.get("what_guests_dislike", []), "red",   "theme", "detail", "example_quotes", "frequency")
    _section_areas ("What Works Well",     analysis.get("what_works_well", []),     "green", "area",  "finding")
    _section_areas ("What Does NOT Work",  analysis.get("what_does_not_work", []),  "red",   "area",  "finding", "severity")
    _section_wants ("Guests Want MORE Of", analysis.get("guests_want_more_of", []))
    _section_wants ("Guests Want LESS Of", analysis.get("guests_want_less_of", []))

    services = analysis.get("additional_services", {})
    if services.get("praised") or services.get("criticised"):
        hdr("Additional Services")
        for s in services.get("praised", []):
            _bullet(f"[green]✓[/green] {s.get('service')}: {s.get('why_appreciated')}")
        for s in services.get("criticised", []):
            _bullet(f"[red]✗[/red] {s.get('service')}: {s.get('issue')}")

    actions = analysis.get("top_action_items", [])
    if actions:
        hdr("Top Action Items for Your Property")
        for i, a in enumerate(actions, 1):
            if HAS_RICH:
                console.print(f"  [bold yellow]{i}.[/bold yellow] {a}")
            else:
                print(f"  {i}. {a}")


def _bullet(text: str):
    if HAS_RICH:
        console.print(f"  • {text}")
    else:
        import re
        clean = re.sub(r'\[.*?\]', '', text)
        print(f"  • {clean}")


def _section_themes(title, items, color, label_key, detail_key, quotes_key, freq_key):
    if not items:
        return
    hdr(title)
    for item in items:
        label  = item.get(label_key, "")
        detail = item.get(detail_key, "")
        freq   = item.get(freq_key, "")
        quotes = item.get(quotes_key, [])[:2]
        if HAS_RICH:
            console.print(f"  [{color}]{label}[/{color}] [dim]({freq})[/dim]")
            if detail:
                console.print(f"    {detail}")
            for q in quotes:
                console.print(f'    [italic dim]"{q}"[/italic dim]')
        else:
            print(f"  [{freq}] {label}: {detail}")
            for q in quotes:
                print(f'    "{q}"')


def _section_areas(title, items, color, label_key, detail_key, extra_key=None):
    if not items:
        return
    hdr(title)
    for item in items:
        label  = item.get(label_key, "")
        detail = item.get(detail_key, "")
        extra  = f" [{item.get(extra_key)}]" if extra_key and item.get(extra_key) else ""
        quotes = item.get("quotes", [])[:1]
        if HAS_RICH:
            console.print(f"  [{color}]{label}[/{color}]{extra}")
            if detail:
                console.print(f"    {detail}")
            for q in quotes:
                console.print(f'    [italic dim]"{q}"[/italic dim]')
        else:
            print(f"  {label}{extra}: {detail}")
            for q in quotes:
                print(f'    "{q}"')


def _section_wants(title, items):
    if not items:
        return
    hdr(title)
    for item in items:
        label  = item.get("item", "")
        detail = item.get("detail", "")
        freq   = item.get("frequency", "")
        if HAS_RICH:
            console.print(f"  [bold]{label}[/bold] [dim]({freq})[/dim]")
            if detail:
                console.print(f"    {detail}")
        else:
            print(f"  [{freq}] {label}: {detail}")


# ──────────────────────────────────────────────────────────────────────────────
# Output path helper
# ──────────────────────────────────────────────────────────────────────────────

def output_path(name: str, suffix: str) -> str:
    safe = name.lower().replace(" ", "-").replace("/", "-")[:40]
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    out  = Path("results")
    out.mkdir(exist_ok=True)
    return str(out / f"{safe}_{ts}_{suffix}.json")


# ──────────────────────────────────────────────────────────────────────────────
# Core pipeline
# ──────────────────────────────────────────────────────────────────────────────

async def run(url: str, headless: bool, verbose: bool):
    # 1 — Scrape
    hdr(f"Scraping  {url[:90]}{'…' if len(url) > 90 else ''}")
    scraper  = BookingScraper(headless=headless, verbose=verbose)
    data     = await scraper.get_property_details(url)
    raw_dict = data.to_dict()

    prop      = raw_dict["property"]
    n_reviews = len(raw_dict["reviews"])

    success(f"Property : {prop.get('name') or 'Unknown'}")
    info(f"  Address  : {prop.get('address') or '—'}")
    info(f"  Rating   : {prop.get('rating') or '—'}  {prop.get('rating_count') or ''}")
    info(f"  Price    : {prop.get('price') or '—'}")
    info(f"  Reviews  : {n_reviews} collected")

    name     = prop.get("name") or "property"
    raw_path = output_path(name, "raw")
    save_results(raw_dict, raw_path)
    success(f"Raw data  → {raw_path}")

    if n_reviews == 0:
        warn("No reviews found — skipping analysis.")
        return

    # 2 — Analyze
    hdr("Analyzing reviews with Claude")
    analyzer      = ReviewAnalyzer()
    analysis      = analyzer.analyze(raw_dict)
    analysis_path = output_path(name, "analysis")
    save_results(analysis, analysis_path)
    success(f"Analysis  → {analysis_path}")

    print_analysis(analysis)


def reanalyze(raw_path: str):
    hdr(f"Re-analyzing {raw_path}")
    raw_dict  = load_results(raw_path)
    analyzer  = ReviewAnalyzer()
    analysis  = analyzer.analyze(raw_dict)
    name      = raw_dict.get("property", {}).get("name") or "property"
    out       = output_path(name, "analysis")
    save_results(analysis, out)
    success(f"Analysis  → {out}")
    print_analysis(analysis)


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

def main():
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Scrape a Booking.com property URL and analyze its reviews with Claude",
    )
    parser.add_argument(
        "url",
        nargs="?",
        help="Booking.com property URL",
    )
    parser.add_argument(
        "--reanalyze", "-r",
        metavar="RAW_JSON",
        help="Skip scraping; re-run Claude analysis on a previously saved raw JSON file",
    )
    parser.add_argument("--verbose",     "-v", action="store_true", help="Verbose scraper logging")
    parser.add_argument("--no-headless",       action="store_true", help="Show the browser window")

    args = parser.parse_args()

    if args.reanalyze:
        if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            err("ANTHROPIC_API_KEY not set. Add it to .env or export it.")
            sys.exit(1)
        reanalyze(args.reanalyze)
        return

    if not args.url:
        parser.print_help()
        sys.exit(1)

    if not args.url.startswith("https://www.booking.com/"):
        err("URL must start with https://www.booking.com/")
        sys.exit(1)

    if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        err("ANTHROPIC_API_KEY not set. Add it to .env or export it.")
        sys.exit(1)

    try:
        asyncio.run(run(
            url=args.url,
            headless=not args.no_headless,
            verbose=args.verbose,
        ))
    except KeyboardInterrupt:
        warn("\nInterrupted.")
        sys.exit(0)


if __name__ == "__main__":
    main()
