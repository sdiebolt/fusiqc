"""Configuration helpers for fusiqc."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class QcConfig:
    """Runtime configuration for one QC session."""

    bids_root: Path
    output_dir: Path
    workers: int | None = None

    @property
    def tsv_path(self) -> Path:
        """Return the QC TSV output path."""
        return self.output_dir / "quality-control.tsv"

    @property
    def figures_dir(self) -> Path:
        """Return the QC figures output directory."""
        return self.output_dir / "figures"


def resolve_output_dir(bids_root: Path, output_dir: Path | None = None) -> Path:
    """Return the resolved QC output directory."""
    if output_dir is None:
        return bids_root / "derivatives" / "fusiqc"
    return output_dir.expanduser().resolve()


def make_config(
    bids_root: Path,
    output_dir: Path | None = None,
    workers: int | None = None,
) -> QcConfig:
    """Create a QC configuration object."""
    bids_root = bids_root.expanduser().resolve()
    return QcConfig(
        bids_root=bids_root,
        output_dir=resolve_output_dir(bids_root, output_dir),
        workers=workers,
    )
