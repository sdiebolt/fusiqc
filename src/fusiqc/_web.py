"""Small local QC review web app."""

from __future__ import annotations

import html
import json
import mimetypes
import urllib.parse
import webbrowser
from pathlib import Path
from wsgiref.simple_server import make_server

import pandas as pd
from rich.console import Console

from fusiqc._config import QcConfig
from fusiqc._dataset import PwdRecording, get_session_label_from_pwd_path
from fusiqc._qc import (
    QC_PANELS,
    QC_PANELS_BY_DATATYPE,
    get_qc_plot_paths,
    load_qc_table,
    update_qc_entry,
)

QC_STATUSES = ("pending", "good", "bad")
QC_DATATYPES = ("fusi", "angio")
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


def _render_filter_chips(status_filter: str, counts: dict[str, int]) -> str:
    labels = (("pending", "Pending"), ("good", "Good"), ("bad", "Bad"), ("all", "All"))
    chips = []
    for value, label in labels:
        active_class = "active" if value == status_filter else ""
        chips.append(
            f'<button type="button" class="filter-chip {active_class}" data-filter-kind="status" data-filter-value="{value}">{label} <span>{counts[value]}</span></button>'
        )
    return "".join(chips)


def _render_datatype_chips(datatype_filter: str, counts: dict[str, int]) -> str:
    labels = (("all", "All datatypes"), ("fusi", "fUSI"), ("angio", "Angio"))
    chips = []
    for value, label in labels:
        active_class = "active" if value == datatype_filter else ""
        chips.append(
            f'<button type="button" class="filter-chip {active_class}" data-filter-kind="datatype" data-filter-value="{value}">{label} <span>{counts[value]}</span></button>'
        )
    return "".join(chips)


def _render_empty_page(config: QcConfig) -> str:
    return f"""
<!doctype html>
<html><head><meta charset="utf-8" /><meta name="viewport" content="width=device-width, initial-scale=1" />
<title>fUSIQC</title><link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css">
<style>
:root {{ --pico-font-size: 95%; --panel-bg: #0e1420; --panel-border: #243247; }}
main {{ max-width: 1400px; margin: 0 auto; padding: 1.5rem 1rem 3rem; }}
.muted {{ color: var(--pico-muted-color); }}
</style></head>
<body><main><header><h1>fUSIQC</h1><p class="muted">Quality control annotations saved at <code>{html.escape(str(config.tsv_path))}</code></p></header>
<article><h2>No recordings found.</h2><p class="muted">Run <code>fusiqc</code> to refresh the QC table.</p></article></main></body></html>
"""


def _serve_qc_plot(
    config: QcConfig, start_response, query: dict[str, list[str]]
) -> list[bytes]:
    pwd_path = query.get("pwd_path", [""])[0]
    panel = query.get("panel", ["mean_power_doppler"])[0]
    datatype = query.get("datatype", ["fusi"])[0]
    allowed_panels = set(QC_PANELS_BY_DATATYPE.get(datatype, QC_PANELS))
    if not pwd_path or panel not in allowed_panels:
        start_response("404 Not Found", [("Content-Type", "text/plain; charset=utf-8")])
        return [b"QC plot not found."]
    recording = Path(pwd_path)
    rec = PwdRecording(
        pwd_path=recording,
        session_label=get_session_label_from_pwd_path(recording),
        subject=recording.parts[-4].removeprefix("sub-"),
        session=recording.parts[-3].removeprefix("ses-"),
        task="",
        run="",
        datatype=datatype,
    )
    plot_path = get_qc_plot_paths(config, rec)[panel]
    figures_root = config.figures_dir.resolve()
    if not plot_path.exists() or not plot_path.resolve().is_relative_to(figures_root):
        start_response("404 Not Found", [("Content-Type", "text/plain; charset=utf-8")])
        return [b"QC plot not found."]
    mime_type = mimetypes.guess_type(plot_path.name)[0] or "image/png"
    start_response("200 OK", [("Content-Type", mime_type)])
    return [plot_path.read_bytes()]


def _render_page(config: QcConfig, query: dict[str, list[str]]) -> str:
    table = load_qc_table(config)
    if table.empty:
        return _render_empty_page(config)
    status_filter = query.get("status", [""])[0] or "pending"
    if status_filter not in (*QC_STATUSES, "all"):
        status_filter = "pending"
    datatype_filter = query.get("datatype", [""])[0] or "all"
    if datatype_filter not in (*QC_DATATYPES, "all"):
        datatype_filter = "all"
    status_scope = (
        table
        if datatype_filter == "all"
        else table.loc[table["datatype"] == datatype_filter]
    )
    status_counts = {
        "pending": int((status_scope["qc_status"] == "pending").sum()),
        "good": int((status_scope["qc_status"] == "good").sum()),
        "bad": int((status_scope["qc_status"] == "bad").sum()),
        "all": int(len(status_scope)),
    }
    datatype_counts = {
        "fusi": int((table["datatype"] == "fusi").sum()),
        "angio": int((table["datatype"] == "angio").sum()),
        "all": int(len(table)),
    }
    filter_mask = pd.Series(True, index=table.index)
    if status_filter != "all":
        filter_mask &= table["qc_status"] == status_filter
    if datatype_filter != "all":
        filter_mask &= table["datatype"] == datatype_filter
    filtered_indices = table.index[filter_mask].to_list()
    initial_index = int(query.get("index", ["0"])[0])
    initial_index = (
        max(0, min(initial_index, len(filtered_indices) - 1)) if filtered_indices else 0
    )
    initial_panel = query.get("panel", [QC_PANELS[0]])[0]
    if initial_panel not in QC_PANELS:
        initial_panel = QC_PANELS[0]
    status_chips = _render_filter_chips(status_filter, status_counts)
    datatype_chips = _render_datatype_chips(datatype_filter, datatype_counts)
    rows_json = json.dumps(table.to_dict(orient="records"))
    filtered_indices_json = json.dumps(filtered_indices)
    panel_labels_json = json.dumps(
        {
            "mean_power_doppler": "Power Doppler",
            "cv": "CV",
            "carpet": "Carpet plot",
            "dvars": "DVARS",
        }
    )
    panels_by_datatype_json = json.dumps(QC_PANELS_BY_DATATYPE)
    metadata_fields_json = json.dumps(METADATA_FIELDS)
    initial_filter_json = json.dumps(status_filter)
    initial_datatype_filter_json = json.dumps(datatype_filter)
    initial_index_json = json.dumps(initial_index)
    initial_panel_json = json.dumps(initial_panel)
    tsv_path_display = html.escape(str(config.tsv_path))
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
.filter-chip {{ padding: 0.45rem 0.8rem; border-radius: 0.35rem; text-decoration: none; background: var(--pico-muted-border-color); color: inherit; border: none; cursor: pointer; font: inherit; }}
.filter-chip.active {{ background: var(--pico-primary); color: white; }}
.filter-chip span {{ opacity: 0.8; margin-left: 0.35rem; }}
.nav-links {{ display: flex; gap: 1.15rem; align-items: center; flex-wrap: wrap; }}
.nav-group {{ display: flex; gap: 0.75rem; align-items: center; flex-wrap: nowrap; }}
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
.status-button.undo {{ flex: 0 0 auto; font-weight: 400; }}
.status-button.undo:disabled {{ opacity: 0.4; }}
.metadata-table th {{ width: 36%; }}
textarea {{ min-height: 150px; }}
.muted {{ color: var(--pico-muted-color); }}
.autosave-status {{ min-height: 1.2rem; font-size: 0.9rem; }}
.index-nav {{ display: inline-flex; align-items: center; gap: 0.35rem; flex: 0 0 auto; white-space: nowrap; }}
.index-input {{ width: 3.5rem !important; min-width: 3.5rem !important; max-width: 3.5rem !important; flex: 0 0 auto !important; box-sizing: border-box !important; text-align: center; margin: 0 !important; padding: 0.35rem 0.3rem !important; -moz-appearance: textfield; }}
.jump-wrap {{ position: relative; flex: 0 1 20rem; }}
.jump-input {{ width: 100%; margin: 0; padding: 0.35rem 0.6rem; }}
.jump-results {{ position: absolute; top: 100%; left: 0; right: 0; margin-top: 0.25rem; background: var(--panel-bg); border: 1px solid var(--panel-border); border-radius: 0.35rem; max-height: 240px; overflow-y: auto; z-index: 20; }}
.jump-result {{ padding: 0.45rem 0.7rem; cursor: pointer; color: white; display: flex; justify-content: space-between; gap: 0.5rem; }}
.jump-result .jump-result-tag {{ font-size: 0.8rem; opacity: 0.7; flex: 0 0 auto; }}
.jump-result:hover, .jump-result.active {{ background: rgba(255,255,255,0.12); }}
@media (max-width: 980px) {{ .controls-stack {{ grid-template-columns: 1fr; }} }}
</style></head>
<body><main><header><h1>fUSIQC</h1><p class="muted">Quality control annotations saved at <code>{tsv_path_display}</code></p></header>
<div class="toolbar"><div class="filter-bar">{datatype_chips}</div><div class="filter-bar">{status_chips}</div></div>
<div class="toolbar"><div class="jump-wrap"><input type="text" id="jump-input" class="jump-input" placeholder="Jump to recording..." autocomplete="off" /><div id="jump-results" class="jump-results" hidden></div></div>
<div class="nav-links nav-group"><span class="index-nav"><input type="number" id="index-input" class="index-input" min="1" /><span id="index-total" class="muted"></span></span><button id="previous-button" type="button" class="secondary outline session-nav-button">Previous</button><button id="next-button" type="button" class="secondary outline session-nav-button">Next</button></div></div>
<div class="layout"><article class="quicklook-panel"><div><h2 id="session-title" style="margin-bottom: 0.25rem; color: white;"></h2></div><div id="quicklook-container" class="quicklook-container"></div><div class="viewer-footer"><div id="panel-tabs" class="panel-tabs"></div></div></article>
<section class="controls-stack"><article><label><strong>QC status</strong></label><div id="status-buttons" class="status-buttons"></div><label for="qc_notes"><strong>Notes</strong></label><textarea id="qc_notes" name="qc_notes"></textarea><div id="autosave-status" class="autosave-status muted"></div></article><article><table id="metadata-table" class="metadata-table"></table></article></section></div></main>
<script>
const qcRows = {rows_json}; let currentFilter = {initial_filter_json}; let currentDatatypeFilter = {initial_datatype_filter_json}; let currentPanel = {initial_panel_json}; let filteredRowIndices = {filtered_indices_json}; let currentPosition = {initial_index_json};
const panelLabels = {panel_labels_json}; const panelsByDatatype = {panels_by_datatype_json}; const metadataFields = {metadata_fields_json};
const undoStack = [];
const notesArea = document.getElementById('qc_notes'); const autosaveStatus = document.getElementById('autosave-status'); const quicklookContainer = document.getElementById('quicklook-container'); const metadataTable = document.getElementById('metadata-table'); const statusButtonsContainer = document.getElementById('status-buttons'); const previousButton = document.getElementById('previous-button'); const nextButton = document.getElementById('next-button'); const indexInput = document.getElementById('index-input'); const indexTotal = document.getElementById('index-total'); const sessionTitle = document.getElementById('session-title'); const panelTabs = document.getElementById('panel-tabs'); const jumpInput = document.getElementById('jump-input'); const jumpResults = document.getElementById('jump-results'); let autosaveTimer = null; let autosaveCounter = 0; let suppressNotesAutosave = false;
function filterIndices() {{ return qcRows.flatMap((row, idx) => {{ if (currentFilter !== 'all' && row.qc_status !== currentFilter) return []; if (currentDatatypeFilter !== 'all' && row.datatype !== currentDatatypeFilter) return []; return [idx]; }}); }}
function getCurrentRow() {{ return qcRows[filteredRowIndices[currentPosition]]; }}
function panelsForRow(row) {{ return panelsByDatatype[row.datatype] || Object.keys(panelLabels); }}
function qcPlotUrl(pwdPath, panel, datatype) {{ return `/qc_plot?pwd_path=${{encodeURIComponent(pwdPath)}}&panel=${{encodeURIComponent(panel)}}&datatype=${{encodeURIComponent(datatype)}}`; }}
function updateUrl() {{ const params = new URLSearchParams({{ status: currentFilter, datatype: currentDatatypeFilter, index: String(currentPosition), panel: currentPanel }}); window.history.replaceState(null, '', `/?${{params.toString()}}`); }}
function renderFilterCounts() {{ const statusScope = qcRows.filter((row) => currentDatatypeFilter === 'all' || row.datatype === currentDatatypeFilter); const statusCounts = {{ pending: 0, good: 0, bad: 0, all: statusScope.length }}; const datatypeCounts = {{ fusi: 0, angio: 0, all: qcRows.length }}; statusScope.forEach((row) => {{ if (row.qc_status in statusCounts) statusCounts[row.qc_status] += 1; }}); qcRows.forEach((row) => {{ if (row.datatype in datatypeCounts) datatypeCounts[row.datatype] += 1; }}); document.querySelectorAll('.filter-chip').forEach((chip) => {{ const kind = chip.dataset.filterKind; const value = chip.dataset.filterValue; const counts = kind === 'datatype' ? datatypeCounts : statusCounts; const active = kind === 'datatype' ? currentDatatypeFilter : currentFilter; chip.classList.toggle('active', value === active); const span = chip.querySelector('span'); if (span && value in counts) span.textContent = counts[value]; }}); }}
function renderPanelTabs(row) {{ const panels = panelsForRow(row); if (!panels.includes(currentPanel)) currentPanel = panels[0]; panelTabs.innerHTML = panels.map((panel) => `<button type="button" class="panel-tab ${{panel === currentPanel ? 'active' : ''}}" data-panel="${{panel}}">${{panelLabels[panel]}}</button>`).join(''); panelTabs.querySelectorAll('[data-panel]').forEach((button) => {{ button.addEventListener('click', () => {{ currentPanel = button.dataset.panel; renderCurrentRow(false); }}); }}); }}
function renderMetadata(row) {{ metadataTable.innerHTML = metadataFields.map((field) => `<tr><th>${{field}}</th><td>${{row[field] ?? ''}}</td></tr>`).join(''); }}
function searchableText(row) {{ return [row.session_label, row.subject, row.session, row.task, row.run].filter(Boolean).join(' '); }}
function fuzzySubsequenceScore(token, text) {{ let tokenIndex = 0; let score = 0; let consecutive = 0; for (let textIndex = 0; textIndex < text.length && tokenIndex < token.length; textIndex++) {{ if (text[textIndex] === token[tokenIndex]) {{ tokenIndex += 1; consecutive += 1; score += consecutive; }} else {{ consecutive = 0; }} }} return tokenIndex === token.length ? score : -1; }}
function fuzzyScore(query, text) {{ const tokens = query.toLowerCase().split(/\\s+/).filter(Boolean); const lowerText = text.toLowerCase(); if (tokens.length === 0) return -1; let total = 0; for (const token of tokens) {{ const score = fuzzySubsequenceScore(token, lowerText); if (score < 0) return -1; total += score; }} return total; }}
let jumpMatches = []; let jumpActiveIndex = -1;
function renderJumpResults() {{ if (jumpMatches.length === 0) {{ jumpResults.hidden = true; jumpResults.innerHTML = ''; return; }} jumpResults.innerHTML = jumpMatches.map((match, idx) => {{ const row = match.row; return `<div class="jump-result ${{idx === jumpActiveIndex ? 'active' : ''}}" data-position="${{match.position}}"><span>${{row.session_label}}</span><span class="jump-result-tag">${{row.datatype}}</span></div>`; }}).join(''); jumpResults.hidden = false; jumpResults.querySelectorAll('[data-position]').forEach((element) => {{ element.addEventListener('mousedown', (event) => {{ event.preventDefault(); jumpToPosition(parseInt(element.dataset.position, 10)); closeJumpResults(); }}); }}); }}
function closeJumpResults() {{ jumpMatches = []; jumpActiveIndex = -1; jumpResults.hidden = true; jumpResults.innerHTML = ''; jumpInput.value = ''; }}
function updateJumpMatches() {{ const query = jumpInput.value.trim(); if (!query) {{ jumpMatches = []; jumpActiveIndex = -1; jumpResults.hidden = true; jumpResults.innerHTML = ''; return; }} const scored = filteredRowIndices.map((idx, position) => ({{ position, row: qcRows[idx], score: fuzzyScore(query, searchableText(qcRows[idx])) }})).filter((match) => match.score >= 0); scored.sort((a, b) => b.score - a.score); jumpMatches = scored.slice(0, 8); jumpActiveIndex = jumpMatches.length > 0 ? 0 : -1; renderJumpResults(); }}
jumpInput.addEventListener('input', updateJumpMatches);
jumpInput.addEventListener('focus', updateJumpMatches);
jumpInput.addEventListener('keydown', (event) => {{ if (event.key === 'ArrowDown') {{ event.preventDefault(); if (jumpMatches.length > 0) {{ jumpActiveIndex = (jumpActiveIndex + 1) % jumpMatches.length; renderJumpResults(); }} }} else if (event.key === 'ArrowUp') {{ event.preventDefault(); if (jumpMatches.length > 0) {{ jumpActiveIndex = (jumpActiveIndex - 1 + jumpMatches.length) % jumpMatches.length; renderJumpResults(); }} }} else if (event.key === 'Enter') {{ event.preventDefault(); if (jumpActiveIndex >= 0 && jumpMatches[jumpActiveIndex]) {{ jumpToPosition(jumpMatches[jumpActiveIndex].position); closeJumpResults(); }} }} else if (event.key === 'Escape') {{ closeJumpResults(); }} }});
jumpInput.addEventListener('blur', () => {{ setTimeout(closeJumpResults, 100); }});
document.querySelectorAll('.filter-chip').forEach((chip) => {{ chip.addEventListener('click', () => {{ const kind = chip.dataset.filterKind; const value = chip.dataset.filterValue; if (kind === 'datatype') currentDatatypeFilter = value; else currentFilter = value; filteredRowIndices = filterIndices(); currentPosition = 0; renderCurrentRow(); }}); }});
async function postStatus(row, status, notes) {{ await fetch('/update_status', {{ method: 'POST', headers: {{ 'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8' }}, body: new URLSearchParams({{ pwd_path: row.pwd_path, qc_status: status, qc_notes: notes }}) }}); }}
async function updateStatus(nextStatus) {{ const row = getCurrentRow(); undoStack.push({{ rowIndex: qcRows.indexOf(row), prevStatus: row.qc_status, prevNotes: row.qc_notes }}); await postStatus(row, nextStatus, notesArea.value); row.qc_status = nextStatus; row.qc_notes = notesArea.value; filteredRowIndices = filterIndices(); const currentRowIndex = qcRows.indexOf(row); const nextFilteredPosition = filteredRowIndices.indexOf(currentRowIndex); currentPosition = nextFilteredPosition !== -1 ? nextFilteredPosition : Math.min(currentPosition, Math.max(filteredRowIndices.length - 1, 0)); renderCurrentRow(); }}
async function undoLastChange() {{ const change = undoStack.pop(); if (!change) return; const row = qcRows[change.rowIndex]; await postStatus(row, change.prevStatus, change.prevNotes); row.qc_status = change.prevStatus; row.qc_notes = change.prevNotes; filteredRowIndices = filterIndices(); const restoredPosition = filteredRowIndices.indexOf(change.rowIndex); currentPosition = restoredPosition !== -1 ? restoredPosition : Math.min(currentPosition, Math.max(filteredRowIndices.length - 1, 0)); renderCurrentRow(); }}
function renderStatusButtons(row) {{ const statusHtml = ['good', 'bad', 'pending'].map((status) => `<button type="button" class="status-button ${{status}} ${{row && row.qc_status === status ? 'selected' : ''}}" data-status="${{status}}" ${{row ? '' : 'disabled'}}>${{status.charAt(0).toUpperCase() + status.slice(1)}}</button>`).join(''); const undoHtml = `<button type="button" class="status-button undo secondary outline" data-action="undo" ${{undoStack.length === 0 ? 'disabled' : ''}}>Undo</button>`; statusButtonsContainer.innerHTML = statusHtml + undoHtml; statusButtonsContainer.querySelectorAll('[data-status]').forEach((button) => {{ button.addEventListener('click', async () => {{ await updateStatus(button.dataset.status); }}); }}); const undoButtonElement = statusButtonsContainer.querySelector('[data-action="undo"]'); if (undoButtonElement) undoButtonElement.addEventListener('click', () => {{ undoLastChange(); }}); }}
function preloadAdjacentImages() {{ [currentPosition - 1, currentPosition + 1].forEach((position) => {{ if (position < 0 || position >= filteredRowIndices.length) return; const adjacentRow = qcRows[filteredRowIndices[position]]; const image = new Image(); image.src = qcPlotUrl(adjacentRow.pwd_path, currentPanel, adjacentRow.datatype); }}); }}
function renderQuicklook(row) {{ const img = document.createElement('img'); img.src = qcPlotUrl(row.pwd_path, currentPanel, row.datatype); img.alt = panelLabels[currentPanel]; img.onerror = () => {{ quicklookContainer.innerHTML = '<div class="quicklook-empty">No QC plot available for this recording.</div>'; }}; quicklookContainer.innerHTML = ''; quicklookContainer.appendChild(img); }}
function renderCurrentRow(updateNotes = true) {{ updateUrl(); renderFilterCounts(); const row = filteredRowIndices.length > 0 ? getCurrentRow() : null; if (row) {{ renderPanelTabs(row); indexInput.max = String(filteredRowIndices.length); indexInput.value = String(currentPosition + 1); indexTotal.textContent = ` / ${{filteredRowIndices.length}}`; previousButton.disabled = currentPosition === 0; nextButton.disabled = currentPosition >= filteredRowIndices.length - 1; sessionTitle.textContent = row.session_label; renderMetadata(row); if (updateNotes) {{ suppressNotesAutosave = true; notesArea.value = row.qc_notes || ''; suppressNotesAutosave = false; }} renderQuicklook(row); preloadAdjacentImages(); }} else {{ panelTabs.innerHTML = ''; indexInput.value = ''; indexInput.max = '0'; indexTotal.textContent = ' / 0'; previousButton.disabled = true; nextButton.disabled = true; sessionTitle.textContent = 'No recordings'; metadataTable.innerHTML = ''; quicklookContainer.innerHTML = '<div class="quicklook-empty">No recordings match the current filters.</div>'; if (updateNotes) {{ suppressNotesAutosave = true; notesArea.value = ''; suppressNotesAutosave = false; }} }} renderStatusButtons(row); }}
function scheduleAutosave() {{ if (suppressNotesAutosave) return; autosaveStatus.textContent = 'Saving notes...'; const requestId = ++autosaveCounter; clearTimeout(autosaveTimer); autosaveTimer = setTimeout(async () => {{ const row = getCurrentRow(); const body = new URLSearchParams({{ pwd_path: row.pwd_path, qc_status: row.qc_status, qc_notes: notesArea.value }}); try {{ await fetch('/update_notes', {{ method: 'POST', headers: {{ 'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8' }}, body }}); row.qc_notes = notesArea.value; if (requestId === autosaveCounter) autosaveStatus.textContent = 'Notes saved.'; }} catch (error) {{ autosaveStatus.textContent = 'Failed to save notes.'; }} }}, 400); }}
function jumpToPosition(nextPosition) {{ if (filteredRowIndices.length === 0) return; const clamped = Math.max(0, Math.min(nextPosition, filteredRowIndices.length - 1)); if (clamped === currentPosition) {{ renderCurrentRow(); return; }} currentPosition = clamped; renderCurrentRow(); }}
notesArea.addEventListener('input', scheduleAutosave);
previousButton.addEventListener('click', () => jumpToPosition(currentPosition - 1));
nextButton.addEventListener('click', () => jumpToPosition(currentPosition + 1));
indexInput.addEventListener('change', () => {{ const value = parseInt(indexInput.value, 10); if (Number.isNaN(value)) {{ renderCurrentRow(); return; }} jumpToPosition(value - 1); }});
renderCurrentRow();
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
