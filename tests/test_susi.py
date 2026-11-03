"""Small end-to-end checks for static VoxelData QC and functional regression."""

import json
from dataclasses import asdict
from pathlib import Path

import confusius as cf
import matplotlib
import numpy as np
import pandas as pd
import pytest
from fusiqc._config import make_config
from fusiqc._dataset import discover_pwd_recordings
from fusiqc._qc import _refresh_one_recording, get_qc_plot_paths, save_qc_table
from fusiqc._web import _render_page

matplotlib.use("Agg")


@pytest.mark.parametrize("datatype,n_slices", [("susi", 1), ("susi", 3), ("fusi", 1)])
def test_voxeldata_qc(tmp_path: Path, datatype: str, n_slices: int) -> None:
    root = tmp_path / "dataset"
    folder = root / "sub-Mouse" / "ses-Test" / datatype
    folder.mkdir(parents=True)
    (root / "dataset_description.json").write_text(
        json.dumps({"Name": "QC check", "BIDSVersion": "1.10.0"})
    )
    path = folder / "sub-Mouse_ses-Test_task-awake_run-01_desc-mean_pwd.nii"
    rng = np.random.default_rng(0)
    shape = (n_slices, 6, 8) if datatype == "susi" else (20, n_slices, 6, 8)
    dims = ("k", "j", "i") if datatype == "susi" else ("time", "k", "j", "i")
    image = cf.xarray.create_voxeldata(
        rng.uniform(1, 10, shape).astype(np.float32),
        dims=dims,
        spacing=(0.4, 0.1, 0.11),
        dt=1 if datatype == "fusi" else None,
    )
    cf.save(image, path)
    config = make_config(root, tmp_path / "qc", workers=1)
    recordings = discover_pwd_recordings(config)
    assert len(recordings) == 1 and recordings[0].datatype == datatype
    recording = recordings[0]
    row = _refresh_one_recording(
        str(root),
        str(config.output_dir),
        {key: str(value) for key, value in asdict(recording).items()},
        {"qc_status": "good", "qc_notes": "Previous review"},
        False,
    )
    plots = get_qc_plot_paths(config, recording)
    assert all(path.is_file() for path in plots.values())
    assert row["qc_status"] == "good" and row["qc_notes"] == "Previous review"
    assert row["n_timepoints"] == ("1" if datatype == "susi" else "20")
    if datatype == "susi":
        assert set(plots) == {"mean_power_doppler"}
    else:
        assert set(plots) == {"mean_power_doppler", "cv", "carpet", "dvars"}
    save_qc_table(config, pd.DataFrame([row]))
    page = _render_page(config, {})
    assert 'data-filter-value="susi"' in page
    assert '"susi": ["mean_power_doppler"]' in page
