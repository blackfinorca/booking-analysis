#!/usr/bin/env python3
"""
Booking.com machiya/accommodation scraper & review analyzer.

Usage examples:
  # Search Kyoto for machiya, pick a property interactively
  python main.py search --location "Kyoto" --type "machiya"

  # Scrape a specific property URL and analyze its reviews
  python main.py scrape --url "https://www.booking.com/hotel/jp/benten-residences.html"

  # Load previously saved data and re-run the analysis
  python main.py analyze --input results/benten-residences.json

  # Full pipeline: search → pick → scrape → analyze
  python main.py full --location "Kyoto" --type "machiya"
"""

import argparse
import asyncio
import json
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
    from rich.table import Table
    from rich import print as rprint
    from rich.panel import Panel
    from rich.text import Text
    from rich.markdown import Markdown

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
        print(f"\n{'=' * 60}")
        print(f"  {text}")
        print('=' * 60)


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


def print_search_results(results: list[dict]):
    hdr("Search Results")
    if not results:
        warn("No properties found.")
        return

    if HAS_RICH:
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("#", style="dim", width=4)
        table.add_column("Property Name", min_width=30)
        table.add_column("Rating", justify="center", width=8)
        table.add_column("Price", width=16)
        table.add_column("Address", min_width=24)
        for i, p in enumerate(results, 1):
            table.add_row(
                str(i),
                p.get("name", ""),
                p.get("rating", "—"),
                p.get("price", "—"),
                p.get("address", "—"),
            )
        console.print(table)
    else:
        for i, p in enumerate(results, 1):
            print(f"{i}. {p.get('name')}  |  Rating: {p.get('rating','—')}  |  {p.get('price','—')}")
            if p.get("address"):
                print(f"   {p['address']}")
        print()


def print_analysis(analysis: dict):
    hdr("Review Analysis")

    if analysis.get("parse_error"):
        warn("Claude returned non-JSON output:")
        print(analysis.get("raw_analysis", ""))
        return

    prop = analysis.get("property_name", "Unknown")
    total = analysis.get("total_reviews_analyzed", 0)
    sentiment = analysis.get("overall_sentiment", "")
    summary = analysis.get("summary", "")

    if HAS_RICH:
        console.print(
            Panel(
                f"[bold]{prop}[/bold]\n"
                f"[dim]{total} reviews analyzed  ·  sentiment: {sentiment}[/dim]\n\n"
                f"{summary}",
                title="Overview",
                border_style="cyan",
            )
        )
    else:
        print(f"\n{prop}  |  {total} reviews  |  {sentiment}")
        print(summary)

    _print_theme_section("What Guests Love", analysis.get("what_guests_love", []),
                         color="green", freq_key="frequency", detail_key="detail",
                         quotes_key="example_quotes", label_key="theme")

    _print_theme_section("What Guests Dislike", analysis.get("what_guests_dislike", []),
                         color="red", freq_key="frequency", detail_key="detail",
                         quotes_key="example_quotes", label_key="theme")

    _print_area_section("What Works Well", analysis.get("what_works_well", []),
                        color="green", label_key="area", detail_key="finding")

    _print_area_section("What Does NOT Work", analysis.get("what_does_not_work", []),
                        color="red", label_key="area", detail_key="finding",
                        extra_key="severity")

    _print_want_section("Guests Want MORE Of", analysis.get("guests_want_more_of", []))
    _print_want_section("Guests Want LESS Of", analysis.get("guests_want_less_of", []))

    # Additional services
    services = analysis.get("additional_services", {})
    if services.get("praised") or services.get("criticised"):
        hdr("Additional Services")
        for svc in services.get("praised", []):
            _bullet(f"[green]✓[/green] {svc.get('service')}: {svc.get('why_appreciated')}", "green")
        for svc in services.get("criticised", []):
            _bullet(f"[red]✗[/red] {svc.get('service')}: {svc.get('issue')}", "red")

    # Action items
    actions = analysis.get("top_action_items", [])
    if actions:
        hdr("Top Action Items for Your Property")
        for i, action in enumerate(actions, 1):
            if HAS_RICH:
                console.print(f"  [bold yellow]{i}.[/bold yellow] {action}")
            else:
                print(f"  {i}. {action}")


def _bullet(text: str, color: str = "white"):
    if HAS_RICH:
        console.print(f"  • {text}")
    else:
        print(f"  • {text}")


def _print_theme_section(title, items, color, freq_key, detail_key, quotes_key, label_key):
    if not items:
        return
    hdr(title)
    for item in items:
        label = item.get(label_key, "")
        detail = item.get(detail_key, "")
        freq = item.get(freq_key, "")
        quotes = item.get(quotes_key, [])[:2]  # show max 2 quotes
        if HAS_RICH:
            console.print(f"  [{color}]{label}[/{color}] [dim]({freq})[/dim]")
            if detail:
                console.print(f"    {detail}")
            for q in quotes:
                console.print(f"    [italic dim]\"{q}\"[/italic dim]")
        else:
            print(f"  [{freq}] {label}: {detail}")
            for q in quotes:
                print(f"    \"{q}\"")


def _print_area_section(title, items, color, label_key, detail_key, extra_key=None):
    if not items:
        return
    hdr(title)
    for item in items:
        label = item.get(label_key, "")
        detail = item.get(detail_key, "")
        extra = f" [{item.get(extra_key)}]" if extra_key and item.get(extra_key) else ""
        quotes = item.get("quotes", [])[:1]
        if HAS_RICH:
            console.print(f"  [{color}]{label}[/{color}]{extra}")
            if detail:
                console.print(f"    {detail}")
            for q in quotes:
                console.print(f"    [italic dim]\"{q}\"[/italic dim]")
        else:
            print(f"  {label}{extra}: {detail}")
            for q in quotes:
                print(f"    \"{q}\"")


def _print_want_section(title, items):
    if not items:
        return
    hdr(title)
    for item in items:
        label = item.get("item", "")
        detail = item.get("detail", "")
        freq = item.get("frequency", "")
        if HAS_RICH:
            console.print(f"  [bold]{label}[/bold] [dim]({freq})[/dim]")
            if detail:
                console.print(f"    {detail}")
        else:
            print(f"  [{freq}] {label}: {detail}")


# ──────────────────────────────────────────────────────────────────────────────
# Output path helpers
# ──────────────────────────────────────────────────────────────────────────────

def make_output_path(name: str, suffix: str) -> str:
    safe = name.lower().replace(" ", "-").replace("/", "-")[:40]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path("results")
    out_dir.mkdir(exist_ok=True)
    return str(out_dir / f"{safe}_{ts}_{suffix}.json")


# ──────────────────────────────────────────────────────────────────────────────
# Commands
# ──────────────────────────────────────────────────────────────────────────────

async def cmd_search(args):
    hdr(f"Searching Booking.com: {args.location} · {args.type}")
    scraper = BookingScraper(headless=not args.no_headless, verbose=args.verbose)
    results = await scraper.search_properties(
        args.location, args.type, max_results=args.max_results
    )
    print_search_results(results)

    if args.save and results:
        path = make_output_path(f"{args.location}-{args.type}", "search")
        save_results(results, path)

    return results


async def cmd_scrape(args):
    url = args.url
    hdr(f"Scraping property: {url[:80]}...")
    scraper = BookingScraper(headless=not args.no_headless, verbose=args.verbose)
    data = await scraper.get_property_details(url)
    data_dict = data.to_dict()

    prop = data_dict["property"]
    n_reviews = len(data_dict["reviews"])

    success(f"Scraped: {prop.get('name') or 'Unknown'}")
    info(f"  Address : {prop.get('address', '—')}")
    info(f"  Rating  : {prop.get('rating', '—')}  ({prop.get('rating_count', '—')})")
    info(f"  Price   : {prop.get('price', '—')}")
    info(f"  Reviews : {n_reviews} collected")

    # Save raw data
    name = prop.get("name") or "property"
    path = make_output_path(name, "raw")
    save_results(data_dict, path)
    success(f"Raw data saved → {path}")

    return data_dict, path


async def cmd_analyze(args):
    if hasattr(args, "input") and args.input:
        info(f"Loading data from {args.input}")
        data_dict = load_results(args.input)
    else:
        raise ValueError("--input is required for analyze command")

    analyzer = ReviewAnalyzer()
    analysis = analyzer.analyze(data_dict)

    # Save analysis
    name = data_dict.get("property", {}).get("name") or "property"
    path = make_output_path(name, "analysis")
    save_results(analysis, path)
    success(f"Analysis saved → {path}")

    print_analysis(analysis)
    return analysis


async def cmd_full(args):
    """Search → interactive pick → scrape → analyze."""

    # Step 1: Search
    results = await cmd_search(args)
    if not results:
        err("No results — exiting.")
        return

    # Step 2: Pick a property
    if len(results) == 1:
        chosen = results[0]
        info(f"Only one result — auto-selecting: {chosen['name']}")
    else:
        while True:
            try:
                choice = input(f"\nSelect property [1-{len(results)}]: ").strip()
                idx = int(choice) - 1
                if 0 <= idx < len(results):
                    chosen = results[idx]
                    break
                else:
                    warn(f"Please enter a number between 1 and {len(results)}")
            except (ValueError, KeyboardInterrupt):
                err("Invalid input — exiting.")
                return

    hdr(f"Selected: {chosen['name']}")

    # Step 3: Scrape
    scraper = BookingScraper(headless=not args.no_headless, verbose=args.verbose)
    data = await scraper.get_property_details(chosen["url"])
    data_dict = data.to_dict()

    n_reviews = len(data_dict["reviews"])
    success(f"Scraped {n_reviews} reviews")

    name = data_dict["property"].get("name") or "property"
    raw_path = make_output_path(name, "raw")
    save_results(data_dict, raw_path)
    success(f"Raw data saved → {raw_path}")

    if n_reviews == 0:
        warn("No reviews scraped — skipping analysis.")
        return

    # Step 4: Analyze
    analyzer = ReviewAnalyzer()
    analysis = analyzer.analyze(data_dict)

    analysis_path = make_output_path(name, "analysis")
    save_results(analysis, analysis_path)
    success(f"Analysis saved → {analysis_path}")

    print_analysis(analysis)


# ──────────────────────────────────────────────────────────────────────────────
# CLI wiring
# ──────────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Booking.com scraper & review analyzer for Kyoto machiya properties",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--verbose", "-v", action="store_true", help="Verbose scraper output")
    shared.add_argument("--no-headless", action="store_true", help="Show browser window")

    sub = parser.add_subparsers(dest="command", required=True)

    # search
    p_search = sub.add_parser("search", parents=[shared], help="Search for properties")
    p_search.add_argument("--location", "-l", default="Kyoto", help="Location (default: Kyoto)")
    p_search.add_argument("--type", "-t", default="machiya", help="Accommodation type (default: machiya)")
    p_search.add_argument("--max-results", "-n", type=int, default=5)
    p_search.add_argument("--save", action="store_true", help="Save search results JSON")

    # scrape
    p_scrape = sub.add_parser("scrape", parents=[shared], help="Scrape a specific property URL")
    p_scrape.add_argument("--url", "-u", required=True, help="Booking.com property URL")

    # analyze
    p_analyze = sub.add_parser("analyze", parents=[shared], help="Analyze saved raw data")
    p_analyze.add_argument("--input", "-i", required=True, help="Path to raw JSON from scrape command")

    # full pipeline
    p_full = sub.add_parser("full", parents=[shared],
                             help="Full pipeline: search → pick → scrape → analyze")
    p_full.add_argument("--location", "-l", default="Kyoto")
    p_full.add_argument("--type", "-t", default="machiya")
    p_full.add_argument("--max-results", "-n", type=int, default=8)
    p_full.add_argument("--save", action="store_true", default=True)

    return parser


def main():
    load_dotenv()

    parser = build_parser()
    args = parser.parse_args()

    # Check API key early for commands that need it
    if args.command in ("analyze", "full"):
        if not os.environ.get("ANTHROPIC_API_KEY"):
            err("ANTHROPIC_API_KEY not set. Add it to .env or export it.")
            sys.exit(1)

    try:
        if args.command == "search":
            asyncio.run(cmd_search(args))
        elif args.command == "scrape":
            asyncio.run(cmd_scrape(args))
        elif args.command == "analyze":
            asyncio.run(cmd_analyze(args))
        elif args.command == "full":
            asyncio.run(cmd_full(args))
    except KeyboardInterrupt:
        warn("\nInterrupted.")
        sys.exit(0)


if __name__ == "__main__":
    main()
