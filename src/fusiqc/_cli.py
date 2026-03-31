"""fusiqc command-line interface."""

from __future__ import annotations

import argparse
from pathlib import Path

from rich.console import Console

from fusiqc._config import make_config
from fusiqc._qc import refresh_qc_table
from fusiqc._web import launch_web_app


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(
        prog="fusiqc",
        description="Generate and review QC plots for a fUSI-BIDS dataset.",
    )
    parser.add_argument("bids_root", type=Path, help="Path to the BIDS dataset root.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="QC output directory. Defaults to <bids_root>/derivatives/fusiqc.",
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="Host to bind the local web app."
    )
    parser.add_argument(
        "--port", type=int, default=8765, help="Port to bind the local web app."
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Force regeneration of all QC plots instead of only computing missing ones.",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open a browser automatically.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Number of worker processes to use while refreshing QC assets.",
    )
    return parser


def main() -> None:
    """Run the fusiqc CLI."""
    args = build_parser().parse_args()
    console = Console()
    config = make_config(
        args.bids_root, output_dir=args.output_dir, workers=args.workers
    )
    table, tsv_path = refresh_qc_table(config, force=args.refresh)
    console.print(f"QC table with {len(table)} recording(s): {tsv_path}")
    launch_web_app(
        config, host=args.host, port=args.port, open_browser=not args.no_browser
    )
