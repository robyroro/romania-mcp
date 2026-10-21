"""Local imports and scheduled checks. The MCP stdio entry point stays unchanged."""

import argparse
import asyncio
import json
import sys

from . import datasets, reports
from .common import make_client
from .storage import Store


def parser():
    root = argparse.ArgumentParser(prog="romania-mcp-data", description="Company reports, imports and local monitoring")
    root.add_argument("--data-path", help="SQLite database path; otherwise ROMANIA_MCP_DATA_PATH is used")
    commands = root.add_subparsers(dest="command", required=True)
    report = commands.add_parser("report", help="Read a company report")
    report.add_argument("cui")
    report.add_argument("--years", type=int, choices=range(1, 11), default=5)
    report.add_argument("--refresh", action="store_true")
    report.add_argument("--save", action="store_true", help="Save a fresh snapshot")
    search = commands.add_parser("search", help="Search the imported ONRC index")
    search.add_argument("name")
    search.add_argument("--county")
    search.add_argument("--caen")
    search.add_argument("--caen-version", choices=("2", "3"))
    search.add_argument("--limit", type=int, default=20)
    catalog = commands.add_parser("catalog", help="Find data.gov.ro resource IDs")
    catalog.add_argument("query")
    for name in ("import", "sync"):
        command = commands.add_parser(name, help="Import a local export" if name == "import" else "Download a data.gov.ro resource")
        command.add_argument("file" if name == "import" else "resource_id")
        command.add_argument("--kind", choices=("onrc", "caen", "procurement"), required=True)
        command.add_argument("--published-on", required=True)
        command.add_argument("--period-start")
        command.add_argument("--period-end")
        if name == "import":
            command.add_argument("--source-url", required=True)
    watch = commands.add_parser("watch", help="Add, update or pause a company watch")
    watch.add_argument("cui")
    watch.add_argument("--interval-hours", type=int, default=24)
    watch.add_argument("--disable", action="store_true")
    monitor = commands.add_parser("monitor", help="Check due watches; stdout contains events only")
    monitor.add_argument("--loop", action="store_true", help="Keep checking until stopped")
    monitor.add_argument("--poll-seconds", type=int, default=60)
    monitor.add_argument("--limit", type=int, default=5)
    suppliers = commands.add_parser("suppliers", help="Start or resume supplier checks")
    suppliers.add_argument("cuis", nargs="*")
    suppliers.add_argument("--job-id")
    suppliers.add_argument("--limit", type=int, default=3)
    suppliers.add_argument("--years", type=int, default=3)
    export = commands.add_parser("export", help="Read or export a supplier check")
    export.add_argument("job_id")
    export.add_argument("--format", choices=("json", "csv"), default="json")
    commands.add_parser("status", help="List imported datasets")
    return root


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2), flush=True)


async def run(args):
    store = Store(args.data_path)
    async with make_client() as client:
        if args.command == "report":
            result = await reports.company_report(client, args.cui, args.years, args.refresh or args.save, store)
            if args.save:
                result = {"snapshot_id": store.save(result), "report": result}
        elif args.command == "search":
            result = datasets.search(store, args.name, args.county, args.caen, args.limit, caen_version=args.caen_version)
        elif args.command == "catalog":
            result = await datasets.catalog(client, args.query)
        elif args.command == "import":
            result = await asyncio.to_thread(datasets.import_file, store, args.file, args.kind, args.source_url,
                                             args.published_on, args.period_start, args.period_end)
        elif args.command == "sync":
            result = await datasets.sync_resource(client, store, args.resource_id, args.kind,
                                                  args.published_on, args.period_start, args.period_end)
        elif args.command == "watch":
            from .anaf import clean_cui
            result = store.watch(clean_cui(args.cui), args.interval_hours, not args.disable)
        elif args.command == "monitor":
            if not 1 <= args.limit <= 50 or args.poll_seconds < 60:
                raise ValueError("Use limit 1..50 and poll-seconds of at least 60")
            while True:
                result = await reports.monitor(client, store, args.limit)
                for event in result["events"]:
                    print(json.dumps(event, ensure_ascii=False), flush=True)
                if not args.loop:
                    return
                await asyncio.sleep(args.poll_seconds)
        elif args.command == "suppliers":
            result = await reports.supplier_check(client, store, args.cuis or None, args.job_id, args.limit, args.years)
        elif args.command == "export":
            result = reports.supplier_results(store, args.job_id, args.format, limit=500)
            if args.format == "csv":
                print(result["csv"], end="")
                return
        else:
            result = {"datasets": datasets.manifests(store)}
    emit(result)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parser().parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        # Operational errors belong on stderr; scheduled output stays machine-readable.
        print(f"romania-mcp: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
