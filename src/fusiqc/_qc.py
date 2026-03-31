"""QC refresh and plotting helpers for fusiqc."""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import confusius as cf
import matplotlib.figure
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import xarray as xr
from confusius.qc import compute_cv, compute_dvars
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    track,
)

from fusiqc._config import QcConfig
from fusiqc._dataset import PwdRecording, discover_pwd_recordings

QC_PANELS = ("mean_power_doppler", "cv", "carpet", "dvars")
QC_COLUMNS = (
    "pwd_path",
    "session_label",
    "subject",
    "session",
    "task",
    "run",
    "n_timepoints",
    "qc_status",
    "qc_notes",
)
CONSOLE = Console()


def load_qc_table(config: QcConfig) -> pd.DataFrame:
    """Load the QC TSV, returning an empty table when absent."""
    if not config.tsv_path.exists():
        return pd.DataFrame(columns=QC_COLUMNS)
    table = pd.read_csv(config.tsv_path, sep="\t", dtype=str).fillna("")
    for column in QC_COLUMNS:
        if column not in table.columns:
            table[column] = ""
    return table.loc[:, QC_COLUMNS]


def save_qc_table(config: QcConfig, table: pd.DataFrame) -> Path:
    """Save the QC TSV."""
    config.output_dir.mkdir(parents=True, exist_ok=True)
    table.loc[:, QC_COLUMNS].to_csv(config.tsv_path, sep="\t", index=False)
    return config.tsv_path


def get_qc_plot_paths(config: QcConfig, recording: PwdRecording) -> dict[str, Path]:
    """Return output paths for all QC panels."""
    base_dir = (
        config.figures_dir / f"sub-{recording.subject}" / f"ses-{recording.session}"
    )
    return {
        panel: base_dir / f"{recording.session_label}_{panel}.png"
        for panel in QC_PANELS
    }


def _default_workers() -> int:
    """Return a conservative default worker count for QC refresh."""
    cpu_count = os.cpu_count() or 1
    return max(1, min(8, cpu_count - 1))


def _prepare_preview_map(data: xr.DataArray) -> tuple[xr.DataArray, str, float]:
    """Return a 3D preview volume plus slice settings for one spatial map."""
    preview = data.squeeze()
    if preview.ndim == 2:
        return preview.expand_dims(z=[0.0]), "z", 0.0
    if preview.ndim == 3:
        return (
            preview,
            "z",
            float(preview.coords["z"].values[len(preview.coords["z"]) // 2]),
        )
    raise ValueError(
        f"Expected 2D or 3D preview map, got shape {preview.shape} with dims {list(preview.dims)}."
    )


def _get_map_figsize(preview: xr.DataArray, slice_mode: str) -> tuple[float, float]:
    """Return a figure size that keeps the colorbar close to image height."""
    display_dims = [dim for dim in preview.dims if dim != slice_mode]
    if len(display_dims) != 2:
        return (5.4, 4.8)
    row_dim, col_dim = display_dims
    row_extent = float(preview.sizes[row_dim])
    col_extent = float(preview.sizes[col_dim])
    if row_dim in preview.coords and preview.coords[row_dim].size > 1:
        row_coords = np.asarray(preview.coords[row_dim].values, dtype=float)
        row_extent = float(np.abs(row_coords[-1] - row_coords[0]))
    if col_dim in preview.coords and preview.coords[col_dim].size > 1:
        col_coords = np.asarray(preview.coords[col_dim].values, dtype=float)
        col_extent = float(np.abs(col_coords[-1] - col_coords[0]))
    if row_extent <= 0 or col_extent <= 0:
        return (5.4, 4.8)
    image_aspect = col_extent / row_extent
    figure_height = 4.8
    figure_width = float(np.clip(1.8 + figure_height * image_aspect, 5.0, 8.8))
    return figure_width, figure_height


def _save_map_plot(
    data: xr.DataArray,
    output_path: Path,
    cmap: str,
    cbar_label: str,
    vmin: float | None = None,
    vmax: float | None = None,
) -> Path:
    """Save one dark-themed spatial QC plot."""
    preview, slice_mode, slice_coord = _prepare_preview_map(data)
    figure = plt.figure(
        figsize=_get_map_figsize(preview, slice_mode),
        constrained_layout=True,
    )
    try:
        figure.patch.set_alpha(0.0)
        subfig: matplotlib.figure.Figure = figure.subfigures(1, 1)[0]
        subfig.patch.set_alpha(0.0)
        ax = subfig.subplots(1, 1)
        if vmin is None or vmax is None:
            vmin, vmax = np.nanpercentile(preview.values, [1.0, 99.0])
        cf.plotting.plot_volume(
            preview,
            slice_mode=slice_mode,
            slice_coords=[slice_coord],
            figure=subfig,
            axes=np.asarray([[ax]]),
            cmap=cmap,
            black_bg=True,
            show_titles=False,
            show_colorbar=True,
            cbar_label=cbar_label,
            vmin=vmin,
            vmax=vmax,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(output_path, dpi=150, bbox_inches="tight", transparent=True)
        return output_path
    finally:
        plt.close(figure)


def _save_dvars_plot(power_doppler: xr.DataArray, output_path: Path) -> Path:
    """Save one dark-themed DVARS plot."""
    figure, ax = plt.subplots(figsize=(8.6, 3.6), constrained_layout=True)
    try:
        figure.patch.set_alpha(0.0)
        ax.set_facecolor("none")
        dvars = compute_dvars(power_doppler)
        ax.plot(dvars.coords["time"], dvars.values, linewidth=1.2, color="white")
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("DVARS")
        ax.grid(alpha=0.25, color="white")
        ax.tick_params(colors="white")
        ax.xaxis.label.set_color("white")
        ax.yaxis.label.set_color("white")
        for spine in ax.spines.values():
            spine.set_color("white")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(output_path, dpi=150, bbox_inches="tight", transparent=True)
        return output_path
    finally:
        plt.close(figure)


def _save_carpet_plot(power_doppler: xr.DataArray, output_path: Path) -> Path:
    """Save one dark-themed carpet plot."""
    figure, ax = plt.subplots(figsize=(9.2, 3.8), constrained_layout=True)
    try:
        figure.patch.set_alpha(0.0)
        power_doppler.fusi.plot.carpet(ax=ax, title=None, black_bg=True)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(output_path, dpi=150, bbox_inches="tight", transparent=True)
        return output_path
    finally:
        plt.close(figure)


def _save_recording_plots(
    config: QcConfig,
    recording: PwdRecording,
    pwd: xr.DataArray,
) -> dict[str, Path]:
    """Save all QC plots for one recording."""
    output_paths = get_qc_plot_paths(config, recording)
    mean_image = pwd.mean(dim="time").compute().fusi.scale.db()
    cv_map = compute_cv(pwd)
    _save_map_plot(
        mean_image,
        output_paths["mean_power_doppler"],
        "gray",
        "Mean power Doppler (dB)",
    )
    _save_map_plot(cv_map, output_paths["cv"], "magma", "CV", vmin=0.0, vmax=1.0)
    _save_carpet_plot(pwd, output_paths["carpet"])
    _save_dvars_plot(pwd, output_paths["dvars"])
    return output_paths


def _plots_exist(config: QcConfig, recording: PwdRecording) -> bool:
    """Return True if all QC plot files exist for a recording."""
    return all(p.exists() for p in get_qc_plot_paths(config, recording).values())


def _refresh_one_recording(
    bids_root: str,
    output_dir: str,
    recording_dict: dict[str, str],
    existing_row: dict[str, str] | None,
    force: bool,
) -> dict[str, str]:
    """Refresh one recording row in a worker process."""
    config = QcConfig(bids_root=Path(bids_root), output_dir=Path(output_dir))
    recording = PwdRecording(
        pwd_path=Path(recording_dict["pwd_path"]),
        session_label=recording_dict["session_label"],
        subject=recording_dict["subject"],
        session=recording_dict["session"],
        task=recording_dict["task"],
        run=recording_dict["run"],
    )
    if not force and _plots_exist(config, recording) and existing_row is not None:
        return existing_row
    pwd = cf.load(recording.pwd_path).compute()
    if not _plots_exist(config, recording) or force:
        _save_recording_plots(config, recording, pwd)
    qc_status = "pending"
    qc_notes = ""
    if existing_row is not None:
        qc_status = existing_row.get("qc_status", "") or "pending"
        qc_notes = existing_row.get("qc_notes", "") or ""
    return {
        "pwd_path": str(recording.pwd_path),
        "session_label": recording.session_label,
        "subject": recording.subject,
        "session": recording.session,
        "task": recording.task,
        "run": recording.run,
        "n_timepoints": str(pwd.sizes["time"]),
        "qc_status": qc_status,
        "qc_notes": qc_notes,
    }


def refresh_qc_table(
    config: QcConfig, force: bool = False
) -> tuple[pd.DataFrame, Path]:
    """Refresh plots and the QC TSV."""
    recordings = discover_pwd_recordings(config)
    if not recordings:
        raise FileNotFoundError(
            f"No supported *_pwd recordings found under {config.bids_root}."
        )
    existing = load_qc_table(config)
    existing_rows = {
        row["pwd_path"]: row.to_dict()
        for _, row in existing.iterrows()
        if row.get("pwd_path", "")
    }
    rows: list[dict[str, str]] = []
    worker_count = (
        _default_workers() if config.workers is None else max(1, config.workers)
    )
    action = "Refreshing QC table" if not force else "Generating QC assets"
    CONSOLE.log(f"{action} for {len(recordings)} recording(s)")
    if worker_count == 1:
        for recording in track(recordings, description=action, console=CONSOLE):
            rows.append(
                _refresh_one_recording(
                    str(config.bids_root),
                    str(config.output_dir),
                    {
                        "pwd_path": str(recording.pwd_path),
                        "session_label": recording.session_label,
                        "subject": recording.subject,
                        "session": recording.session,
                        "task": recording.task,
                        "run": recording.run,
                    },
                    existing_rows.get(str(recording.pwd_path)),
                    force,
                )
            )
    else:
        CONSOLE.log(f"Using {worker_count} worker(s)")
        with ProcessPoolExecutor(max_workers=worker_count) as executor:
            future_to_recording = {
                executor.submit(
                    _refresh_one_recording,
                    str(config.bids_root),
                    str(config.output_dir),
                    {
                        "pwd_path": str(recording.pwd_path),
                        "session_label": recording.session_label,
                        "subject": recording.subject,
                        "session": recording.session,
                        "task": recording.task,
                        "run": recording.run,
                    },
                    existing_rows.get(str(recording.pwd_path)),
                    force,
                ): recording
                for recording in recordings
            }
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
                console=CONSOLE,
            ) as progress:
                task_id = progress.add_task(action, total=len(future_to_recording))
                for future in as_completed(future_to_recording):
                    rows.append(future.result())
                    progress.advance(task_id)
    table = pd.DataFrame(rows, columns=QC_COLUMNS).sort_values(
        ["subject", "session", "task", "run", "session_label"]
    )
    tsv_path = save_qc_table(config, table)
    CONSOLE.log(f"Saved QC table: {tsv_path}")
    return table, tsv_path


def update_qc_entry(
    config: QcConfig, pwd_path: str, qc_status: str, qc_notes: str
) -> Path:
    """Update one QC row and save the TSV."""
    table = load_qc_table(config).copy()
    row_mask = table["pwd_path"] == pwd_path
    if not row_mask.any():
        raise ValueError(f"No QC row found for recording {pwd_path!r}.")
    table.loc[row_mask, "qc_status"] = qc_status
    table.loc[row_mask, "qc_notes"] = qc_notes
    return save_qc_table(config, table)
