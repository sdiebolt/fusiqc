"""Dataset discovery for fusiqc."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from bids import BIDSLayout
from bids.layout import BIDSLayoutIndexer

from fusiqc._config import QcConfig

ALLOWED_PWD_SUFFIXES = ("_pwd.nii", "_pwd.nii.gz", "_pwd.zarr", "_pwd.scan")


@dataclass(frozen=True)
class PwdRecording:
    """One power-Doppler recording discovered in a BIDS dataset."""

    pwd_path: Path
    session_label: str
    subject: str
    session: str
    task: str
    run: str


@lru_cache(maxsize=8)
def get_bids_layout(bids_root: Path) -> BIDSLayout:
    """Return a cached PyBIDS layout for one dataset root."""
    return BIDSLayout(
        bids_root,
        validate=False,
        indexer=BIDSLayoutIndexer(force_index=[r".*\.zarr(?:/.*)?$", r".*\.scan$"]),
    )


def get_session_label_from_pwd_path(pwd_path: Path) -> str:
    """Return the basename without the power-Doppler suffix."""
    name = pwd_path.name
    for suffix in ALLOWED_PWD_SUFFIXES:
        if name.endswith(suffix):
            return name.removesuffix(suffix)
    return pwd_path.stem


def discover_pwd_recordings(config: QcConfig) -> list[PwdRecording]:
    """Return all supported power-Doppler recordings in the dataset."""
    layout = get_bids_layout(config.bids_root)
    files = layout.get(suffix="pwd", return_type="filename")
    recordings: list[PwdRecording] = []
    for filename in sorted(files):
        pwd_path = Path(filename)
        if not pwd_path.name.endswith(ALLOWED_PWD_SUFFIXES):
            continue
        entities = layout.parse_file_entities(str(pwd_path))
        recordings.append(
            PwdRecording(
                pwd_path=pwd_path,
                session_label=get_session_label_from_pwd_path(pwd_path),
                subject=str(entities.get("subject", entities.get("sub", ""))),
                session=str(entities.get("session", entities.get("ses", ""))),
                task=str(entities.get("task", "")),
                run=str(entities.get("run", "")),
            )
        )
    return recordings
