"""
Command-line entry point.

    python -m market_intel refresh --segment sleep_apnea_oral_appliances
    python -m market_intel pivot --segment sleep_apnea_oral_appliances --out pivot.xlsx
    python -m market_intel new-entrants --segment sleep_apnea_oral_appliances
    python -m market_intel top-movers --segment sleep_apnea_oral_appliances
    python -m market_intel category-trends --segment sleep_apnea_oral_appliances
    python -m market_intel companies --segment sleep_apnea_oral_appliances
    python -m market_intel list-segments
    python -m market_intel rebuild-events
"""

from __future__ import annotations

import argparse
import logging
import sys

import pandas as pd

from . import analysis, db, pipeline
from .segments import Segment, list_segments
from .sources.openfda import OpenFDAClient


def _print_or_save(df: pd.DataFrame, out: str | None, label: str) -> None:
    if out:
        if out.endswith(".xlsx"):
            analysis.export_excel({label[:31]: df}, out)
        else:
            analysis.export_csv(df, out)
        print(f"Wrote {label} ({len(df)} rows) to {out}")
    else:
        with pd.option_context("display.max_rows", 200, "display.width", 200):
            print(df)


def cmd_list_segments(args: argparse.Namespace) -> None:
    names = list_segments()
    if not names:
        print("No segments defined yet. Add a YAML file under config/segments/.")
        return
    for name in names:
        seg = Segment.load(name)
        print(f"{name}: {seg.description or '(no description)'}")


def cmd_refresh(args: argparse.Namespace) -> None:
    segment = Segment.load(args.segment)
    conn = db.connect()
    client = OpenFDAClient()
    stats = pipeline.run_segment(
        segment, conn=conn, client=client,
        endpoints=args.endpoint or None,
        max_records_per_endpoint=args.max_records,
    )
    for endpoint, s in stats.items():
        print(f"{endpoint:20s} {s['status']:8s} records={s['records']:<6d} {s['error'] or ''}")
    conn.close()


def cmd_rebuild_events(args: argparse.Namespace) -> None:
    conn = db.connect()
    n = pipeline.rebuild_events(conn=conn)
    print(f"Rebuilt {n} event rows from cached raw records.")
    conn.close()


def cmd_pivot(args: argparse.Namespace) -> None:
    conn = db.connect()
    df = analysis.load_events(conn, segment=args.segment, endpoints=args.endpoint or None,
                               event_types=args.event_type or None)
    pivot = analysis.pivot_product_company_year(df, include_category=args.include_category)

    if args.out and args.out.endswith(".xlsx"):
        listing = analysis.listing_510k(conn, segment=args.segment)
        analysis.export_excel({"Pivot": pivot, "510k Listing": listing}, args.out)
        print(f"Wrote pivot ({len(pivot)} rows) and 510k listing ({len(listing)} rows) to {args.out}")
    else:
        _print_or_save(pivot, args.out, f"{args.segment}_pivot")
    conn.close()


def cmd_new_entrants(args: argparse.Namespace) -> None:
    conn = db.connect()
    df = analysis.load_events(conn, segment=args.segment, endpoints=args.endpoint or None)
    result = analysis.new_entrants_by_year(df)
    if args.year:
        result = result[result["first_year"] == args.year]
    _print_or_save(result, args.out, f"{args.segment}_new_entrants")
    conn.close()


def cmd_top_movers(args: argparse.Namespace) -> None:
    conn = db.connect()
    df = analysis.load_events(conn, segment=args.segment, endpoints=args.endpoint or None)
    result = analysis.top_movers(df, recent_years=args.recent_years, prior_years=args.prior_years)
    _print_or_save(result, args.out, f"{args.segment}_top_movers")
    conn.close()


def cmd_category_trends(args: argparse.Namespace) -> None:
    conn = db.connect()
    df = analysis.load_events(conn, segment=args.segment, endpoints=args.endpoint or None)
    result = analysis.category_growth_trends(df, group_col=args.group_col)
    _print_or_save(result, args.out, f"{args.segment}_trends")
    conn.close()


def cmd_companies(args: argparse.Namespace) -> None:
    conn = db.connect()
    df = analysis.load_events(conn, segment=args.segment, endpoints=args.endpoint or None)
    counts = (
        df.groupby(["company_canonical", "company_category"])
        .size().reset_index(name="event_count")
        .sort_values("event_count", ascending=False)
    )
    _print_or_save(counts, args.out, f"{args.segment}_companies")
    conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="market_intel", description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list-segments", help="List saved segment definitions")
    p.set_defaults(func=cmd_list_segments)

    p = sub.add_parser("refresh", help="Fetch a segment from openFDA and store it")
    p.add_argument("--segment", required=True)
    p.add_argument("--endpoint", action="append",
                    help="Limit to specific endpoint(s); repeatable. Default: segment's own list.")
    p.add_argument("--max-records", type=int, default=None,
                    help="Cap records fetched per endpoint (useful for a quick test pull).")
    p.set_defaults(func=cmd_refresh)

    p = sub.add_parser("rebuild-events", help="Recompute events from cached raw records "
                                                "(e.g. after editing company_aliases.yaml)")
    p.set_defaults(func=cmd_rebuild_events)

    for name, func, extra in [
        ("pivot", cmd_pivot, None),
        ("new-entrants", cmd_new_entrants, "entrants"),
        ("top-movers", cmd_top_movers, "movers"),
        ("category-trends", cmd_category_trends, "trends"),
        ("companies", cmd_companies, "companies"),
    ]:
        p = sub.add_parser(name)
        p.add_argument("--segment", required=True)
        p.add_argument("--endpoint", action="append")
        p.add_argument("--out", help="Write to this .xlsx or .csv path instead of printing")
        p.set_defaults(func=func)

    # endpoint-specific extra flags, added after the shared block above
    pivot_parser = next(p for p in sub.choices.values() if p.prog.endswith("pivot"))
    pivot_parser.add_argument("--event-type", action="append")
    pivot_parser.add_argument("--include-category", action="store_true")

    entrants_parser = next(p for p in sub.choices.values() if p.prog.endswith("new-entrants"))
    entrants_parser.add_argument("--year", type=int)

    movers_parser = next(p for p in sub.choices.values() if p.prog.endswith("top-movers"))
    movers_parser.add_argument("--recent-years", type=int, default=2)
    movers_parser.add_argument("--prior-years", type=int, default=2)

    trends_parser = next(p for p in sub.choices.values() if p.prog.endswith("category-trends"))
    trends_parser.add_argument("--group-col", default="product_code",
                                choices=["product_code", "company_category"])

    return parser


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
