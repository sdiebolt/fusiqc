"""Small local QC review web app."""

from __future__ import annotations

import html
import json
import mimetypes
import urllib.parse
import webbrowser
from pathlib import Path
from wsgiref.simple_server import make_server

from rich.console import Console

from fusiqc._config import QcConfig
from fusiqc._dataset import get_session_label_from_pwd_path
from fusiqc._qc import (
    QC_PANELS,
    get_qc_plot_paths,
    load_qc_table,
    update_qc_entry,
)

QC_STATUSES = ("pending", "good", "bad")
METADATA_FIELDS = (
    "session_label",
    "subject",
    "session",
    "task",
    "run",
    "n_timepoints",
)
CONSOLE = Console()


def _ok(start_response, payload: str = "ok") -> list[bytes]:
    start_response("200 OK", [("Content-Type", "text/plain; charset=utf-8")])
    return [payload.encode("utf-8")]


def _get_filtered_table(config: QcConfig, status_filter: str):
    table = load_qc_table(config)
    if status_filter == "all":
        return table, table
    filtered = table.loc[table["qc_status"] == status_filter].reset_index(drop=True)
    return table, filtered


def _render_filter_links(status_filter: str, counts: dict[str, int]) -> str:
    labels = (("pending", "Pending"), ("good", "Good"), ("bad", "Bad"), ("all", "All"))
    links = []
    for value, label in labels:
        active_class = "active" if value == status_filter else ""
        links.append(
            f'<a class="filter-chip {active_class}" href="/?status={value}&index=0&panel=mean_power_doppler">{label} <span>{counts[value]}</span></a>'
        )
    return "".join(links)


def _render_empty_page(status_filter: str, table) -> str:
    counts = {
        "pending": int((table["qc_status"] == "pending").sum()),
        "good": int((table["qc_status"] == "good").sum()),
        "bad": int((table["qc_status"] == "bad").sum()),
        "all": int(len(table)),
    }
    filter_links = _render_filter_links(status_filter, counts)
    return f"""
<!doctype html>
<html><head><meta charset="utf-8" /><meta name="viewport" content="width=device-width, initial-scale=1" />
<title>fUSIQC</title><link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css">
<style>
:root {{ --pico-font-size: 95%; --panel-bg: #0e1420; --panel-border: #243247; }}
main {{ max-width: 1400px; margin: 0 auto; padding: 1.5rem 1rem 3rem; }}
.toolbar {{ display: flex; justify-content: space-between; gap: 1rem; align-items: center; flex-wrap: wrap; margin-bottom: 1rem; }}
.filter-bar {{ display: flex; gap: 0.75rem; flex-wrap: wrap; }}
.filter-chip {{ padding: 0.45rem 0.8rem; border-radius: 0.35rem; text-decoration: none; background: var(--pico-muted-border-color); color: inherit; }}
.filter-chip.active {{ background: var(--pico-primary); color: white; }}
.filter-chip span {{ opacity: 0.8; margin-left: 0.35rem; }}
.muted {{ color: var(--pico-muted-color); }}
</style></head>
<body><main><header><h1>fUSIQC</h1><p class="muted">Review quicklooks and update the TSV directly from the browser.</p></header>
<div class="toolbar"><div class="filter-bar">{filter_links}</div></div>
<article><h2>No {html.escape(status_filter)} recordings left.</h2><p><a href="/?status=all&index=0&panel=mean_power_doppler">Open all recordings</a></p></article></main></body></html>
"""


def _serve_qc_plot(
    config: QcConfig, start_response, query: dict[str, list[str]]
) -> list[bytes]:
    pwd_path = query.get("pwd_path", [""])[0]
    panel = query.get("panel", ["mean_power_doppler"])[0]
    allowed_panels = set(QC_PANELS)
    if not pwd_path or panel not in allowed_panels:
        start_response("404 Not Found", [("Content-Type", "text/plain; charset=utf-8")])
        return [b"QC plot not found."]
    recording = Path(pwd_path)
    plot_path = get_qc_plot_paths(
        config,
        type(
            "Recording",
            (),
            {
                "pwd_path": recording,
                "session_label": get_session_label_from_pwd_path(recording),
                "subject": recording.parts[-4].removeprefix("sub-"),
                "session": recording.parts[-3].removeprefix("ses-"),
                "task": "",
                "run": "",
            },
        )(),
    )[panel]
    figures_root = config.figures_dir.resolve()
    if not plot_path.exists() or not plot_path.resolve().is_relative_to(figures_root):
        start_response("404 Not Found", [("Content-Type", "text/plain; charset=utf-8")])
        return [b"QC plot not found."]
    mime_type = mimetypes.guess_type(plot_path.name)[0] or "image/png"
    start_response("200 OK", [("Content-Type", mime_type)])
    return [plot_path.read_bytes()]


def _render_page(config: QcConfig, query: dict[str, list[str]]) -> str:
    status_filter = query.get("status", [""])[0] or "pending"
    if status_filter not in (*QC_STATUSES, "all"):
        status_filter = "pending"
    table, filtered = _get_filtered_table(config, status_filter)
    if filtered.empty:
        return _render_empty_page(status_filter, table)
    counts = {
        "pending": int((table["qc_status"] == "pending").sum()),
        "good": int((table["qc_status"] == "good").sum()),
        "bad": int((table["qc_status"] == "bad").sum()),
        "all": int(len(table)),
    }
    initial_index = int(query.get("index", ["0"])[0])
    initial_index = max(0, min(initial_index, len(filtered) - 1))
    initial_panel = query.get("panel", [QC_PANELS[0]])[0]
    if initial_panel not in QC_PANELS:
        initial_panel = QC_PANELS[0]
    status_links = _render_filter_links(status_filter, counts)
    rows_json = json.dumps(table.to_dict(orient="records"))
    filtered_indices_json = json.dumps(
        table.index[table["qc_status"] == status_filter].to_list()
        if status_filter != "all"
        else table.index.to_list()
    )
    panel_labels_json = json.dumps(
        {
            "mean_power_doppler": "Power Doppler",
            "cv": "CV",
            "carpet": "Carpet plot",
            "dvars": "DVARS",
        }
    )
    metadata_fields_json = json.dumps(METADATA_FIELDS)
    initial_filter_json = json.dumps(status_filter)
    initial_index_json = json.dumps(initial_index)
    initial_panel_json = json.dumps(initial_panel)
    return f"""
<!doctype html>
<html><head><meta charset="utf-8" /><meta name="viewport" content="width=device-width, initial-scale=1" />
<title>fUSIQC</title><link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css">
<style>
:root {{ --pico-font-size: 95%; --panel-bg: #0e1420; --panel-border: #243247; }}
main {{ max-width: 1400px; margin: 0 auto; padding: 1.5rem 1rem 3rem; }}
.layout {{ display: grid; grid-template-columns: 1fr; gap: 1.25rem; align-items: start; }}
.toolbar {{ display: flex; justify-content: space-between; gap: 1rem; align-items: center; flex-wrap: wrap; margin-bottom: 1rem; }}
.filter-bar {{ display: flex; gap: 0.75rem; flex-wrap: wrap; }}
.filter-chip {{ padding: 0.45rem 0.8rem; border-radius: 0.35rem; text-decoration: none; background: var(--pico-muted-border-color); color: inherit; }}
.filter-chip.active {{ background: var(--pico-primary); color: white; }}
.filter-chip span {{ opacity: 0.8; margin-left: 0.35rem; }}
.nav-links {{ display: flex; gap: 1.15rem; align-items: center; flex-wrap: wrap; }}
.quicklook-panel {{ background: var(--panel-bg); border: 1px solid var(--panel-border); border-radius: 0.5rem; padding: 1rem 1rem 0.45rem; }}
.viewer-footer {{ display: flex; justify-content: flex-start; margin-top: 0.75rem; padding-left: 0.15rem; }}
.panel-tabs {{ display: flex; gap: 0.5rem; flex-wrap: wrap; justify-content: flex-start; }}
.panel-tab {{ border-radius: 0.35rem; padding: 0.35rem 0.8rem; border: 1px solid var(--panel-border); background: rgba(255,255,255,0.04); color: white; cursor: pointer; }}
.panel-tab.active {{ background: rgba(255,255,255,0.16); }}
.session-nav-button {{ border-radius: 0.35rem; padding: 0.45rem 0.8rem; }}
.quicklook-container {{ display: flex; justify-content: center; align-items: center; min-height: 360px; }}
.quicklook-panel img {{ width: auto; max-width: 100%; max-height: 380px; height: auto; border-radius: 0.35rem; display: block; }}
.quicklook-empty {{ display: flex; align-items: center; justify-content: center; min-height: 360px; color: white; opacity: 0.8; }}
.controls-stack {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(320px, 1fr); gap: 1rem; align-items: start; }}
.status-buttons {{ display: flex; gap: 0.75rem; flex-wrap: nowrap; margin-bottom: 1rem; }}
.status-button {{ flex: 1 1 0; min-width: 0; border-radius: 0.35rem; font-weight: 600; }}
.status-button.good {{ border-color: #2f9e44; color: #2f9e44; background: rgba(47, 158, 68, 0.08); }}
.status-button.good.selected {{ background: #2f9e44; color: white; }}
.status-button.bad {{ border-color: #d9485f; color: #d9485f; background: rgba(217, 72, 95, 0.08); }}
.status-button.bad.selected {{ background: #d9485f; color: white; }}
.status-button.pending {{ border-color: #f08c00; color: #c76b00; background: rgba(240, 140, 0, 0.1); }}
.status-button.pending.selected {{ background: #f08c00; color: white; }}
.metadata-table th {{ width: 36%; }}
textarea {{ min-height: 150px; }}
.muted {{ color: var(--pico-muted-color); }}
.autosave-status {{ min-height: 1.2rem; font-size: 0.9rem; }}
@media (max-width: 980px) {{ .controls-stack {{ grid-template-columns: 1fr; }} }}
</style></head>
<body><main><header><h1>fUSIQC</h1><p class="muted">Review quicklooks and update the TSV directly from the browser.</p></header>
<div class="toolbar"><div class="filter-bar">{status_links}</div><div class="nav-links"><span id="position-label" class="muted"></span><button id="previous-button" type="button" class="secondary outline session-nav-button">Previous</button><button id="next-button" type="button" class="secondary outline session-nav-button">Next</button></div></div>
<div class="layout"><article class="quicklook-panel"><div><h2 id="session-title" style="margin-bottom: 0.25rem; color: white;"></h2></div><div id="quicklook-container" class="quicklook-container"></div><div class="viewer-footer"><div id="panel-tabs" class="panel-tabs"></div></div></article>
<section class="controls-stack"><article><label><strong>QC status</strong></label><div id="status-buttons" class="status-buttons"></div><label for="qc_notes"><strong>Notes</strong></label><textarea id="qc_notes" name="qc_notes"></textarea><div id="autosave-status" class="autosave-status muted"></div></article><article><table id="metadata-table" class="metadata-table"></table></article></section></div></main>
<script>
const qcRows = {rows_json}; let currentFilter = {initial_filter_json}; let currentPanel = {initial_panel_json}; let filteredRowIndices = {filtered_indices_json}; let currentPosition = {initial_index_json};
const panelLabels = {panel_labels_json}; const metadataFields = {metadata_fields_json};
const notesArea = document.getElementById('qc_notes'); const autosaveStatus = document.getElementById('autosave-status'); const quicklookContainer = document.getElementById('quicklook-container'); const metadataTable = document.getElementById('metadata-table'); const statusButtonsContainer = document.getElementById('status-buttons'); const previousButton = document.getElementById('previous-button'); const nextButton = document.getElementById('next-button'); const positionLabel = document.getElementById('position-label'); const sessionTitle = document.getElementById('session-title'); const panelTabs = document.getElementById('panel-tabs'); let autosaveTimer = null; let autosaveCounter = 0; let suppressNotesAutosave = false;
function filterIndices() {{ if (currentFilter === 'all') return qcRows.map((_, idx) => idx); return qcRows.flatMap((row, idx) => row.qc_status === currentFilter ? [idx] : []); }}
function getCurrentRow() {{ return qcRows[filteredRowIndices[currentPosition]]; }}
function qcPlotUrl(pwdPath, panel) {{ return `/qc_plot?pwd_path=${{encodeURIComponent(pwdPath)}}&panel=${{encodeURIComponent(panel)}}`; }}
function updateUrl() {{ const params = new URLSearchParams({{ status: currentFilter, index: String(currentPosition), panel: currentPanel }}); window.history.replaceState(null, '', `/?${{params.toString()}}`); }}
function renderFilterCounts() {{ const counts = {{ pending: 0, good: 0, bad: 0, all: qcRows.length }}; qcRows.forEach((row) => {{ if (row.qc_status in counts) counts[row.qc_status] += 1; }}); document.querySelectorAll('.filter-chip').forEach((chip) => {{ const href = chip.getAttribute('href') || ''; const status = new URL(href, window.location.origin).searchParams.get('status'); chip.classList.toggle('active', status === currentFilter); const span = chip.querySelector('span'); if (span && status in counts) span.textContent = counts[status]; }}); }}
function renderPanelTabs() {{ panelTabs.innerHTML = Object.entries(panelLabels).map(([panel, label]) => `<button type="button" class="panel-tab ${{panel === currentPanel ? 'active' : ''}}" data-panel="${{panel}}">${{label}}</button>`).join(''); panelTabs.querySelectorAll('[data-panel]').forEach((button) => {{ button.addEventListener('click', () => {{ currentPanel = button.dataset.panel; renderCurrentRow(false); }}); }}); }}
function renderMetadata(row) {{ metadataTable.innerHTML = metadataFields.map((field) => `<tr><th>${{field}}</th><td>${{row[field] ?? ''}}</td></tr>`).join(''); }}
async function updateStatus(nextStatus) {{ const row = getCurrentRow(); await fetch('/update_status', {{ method: 'POST', headers: {{ 'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8' }}, body: new URLSearchParams({{ pwd_path: row.pwd_path, qc_status: nextStatus, qc_notes: notesArea.value }}) }}); row.qc_status = nextStatus; row.qc_notes = notesArea.value; filteredRowIndices = filterIndices(); if (filteredRowIndices.length === 0) {{ window.location.href = `/?status=${{encodeURIComponent(currentFilter)}}&index=0&panel=${{encodeURIComponent(currentPanel)}}`; return; }} const currentRowIndex = qcRows.indexOf(row); const nextFilteredPosition = filteredRowIndices.indexOf(currentRowIndex); currentPosition = nextFilteredPosition === -1 ? Math.min(currentPosition, filteredRowIndices.length - 1) : nextFilteredPosition; renderCurrentRow(); }}
function renderStatusButtons(row) {{ statusButtonsContainer.innerHTML = ['good', 'bad', 'pending'].map((status) => `<button type="button" class="status-button ${{status}} ${{row.qc_status === status ? 'selected' : ''}}" data-status="${{status}}">${{status.charAt(0).toUpperCase() + status.slice(1)}}</button>`).join(''); statusButtonsContainer.querySelectorAll('[data-status]').forEach((button) => {{ button.addEventListener('click', async () => {{ await updateStatus(button.dataset.status); }}); }}); }}
function preloadAdjacentImages() {{ [currentPosition - 1, currentPosition + 1].forEach((position) => {{ if (position < 0 || position >= filteredRowIndices.length) return; const adjacentRow = qcRows[filteredRowIndices[position]]; const image = new Image(); image.src = qcPlotUrl(adjacentRow.pwd_path, currentPanel); }}); }}
function renderQuicklook(row) {{ const img = document.createElement('img'); img.src = qcPlotUrl(row.pwd_path, currentPanel); img.alt = panelLabels[currentPanel]; img.onerror = () => {{ quicklookContainer.innerHTML = '<div class="quicklook-empty">No QC plot available for this recording.</div>'; }}; quicklookContainer.innerHTML = ''; quicklookContainer.appendChild(img); }}
function renderCurrentRow(updateNotes = true) {{ const row = getCurrentRow(); updateUrl(); renderFilterCounts(); renderPanelTabs(); positionLabel.textContent = `Viewing ${{currentPosition + 1}} / ${{filteredRowIndices.length}} in filter "${{currentFilter}}".`; previousButton.disabled = currentPosition === 0; nextButton.disabled = currentPosition >= filteredRowIndices.length - 1; sessionTitle.textContent = row.session_label; renderMetadata(row); renderStatusButtons(row); if (updateNotes) {{ suppressNotesAutosave = true; notesArea.value = row.qc_notes || ''; suppressNotesAutosave = false; }} renderQuicklook(row); preloadAdjacentImages(); }}
function scheduleAutosave() {{ if (suppressNotesAutosave) return; autosaveStatus.textContent = 'Saving notes...'; const requestId = ++autosaveCounter; clearTimeout(autosaveTimer); autosaveTimer = setTimeout(async () => {{ const row = getCurrentRow(); const body = new URLSearchParams({{ pwd_path: row.pwd_path, qc_status: row.qc_status, qc_notes: notesArea.value }}); try {{ await fetch('/update_notes', {{ method: 'POST', headers: {{ 'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8' }}, body }}); row.qc_notes = notesArea.value; if (requestId === autosaveCounter) autosaveStatus.textContent = 'Notes saved.'; }} catch (error) {{ autosaveStatus.textContent = 'Failed to save notes.'; }} }}, 400); }}
notesArea.addEventListener('input', scheduleAutosave); previousButton.addEventListener('click', () => {{ if (currentPosition > 0) {{ currentPosition -= 1; renderCurrentRow(); }} }}); nextButton.addEventListener('click', () => {{ if (currentPosition < filteredRowIndices.length - 1) {{ currentPosition += 1; renderCurrentRow(); }} }}); renderCurrentRow();
</script></body></html>
"""


def launch_web_app(
    config: QcConfig, host: str, port: int, open_browser: bool = True
) -> None:
    """Launch the local QC review app."""

    def app(environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET")
        path = environ.get("PATH_INFO", "/")
        query = urllib.parse.parse_qs(environ.get("QUERY_STRING", ""))
        if method == "GET" and path == "/qc_plot":
            return _serve_qc_plot(config, start_response, query)
        if method == "POST" and path in {"/update_notes", "/update_status"}:
            content_length = int(environ.get("CONTENT_LENGTH", "0") or "0")
            body = environ["wsgi.input"].read(content_length).decode("utf-8")
            form = urllib.parse.parse_qs(body)
            update_qc_entry(
                config,
                pwd_path=form.get("pwd_path", [""])[0],
                qc_status=form.get("qc_status", ["pending"])[0],
                qc_notes=form.get("qc_notes", [""])[0],
            )
            return _ok(start_response)
        page = _render_page(config, query)
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [page.encode("utf-8")]

    url = f"http://{host}:{port}"
    if open_browser:
        webbrowser.open(url)
    CONSOLE.log(f"Starting QC web app at {url}")
    with make_server(host, port, app) as server:
        server.serve_forever()
