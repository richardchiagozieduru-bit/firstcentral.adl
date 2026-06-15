"""
identifier_utils.py
────────────────────
Best-effort, deterministic preservation and reconstruction of leading zeros
on key identifier columns (ACCOUNTNUMBER, CUSTOMERID, etc.) during Excel
ingestion.

Resolution methods (recorded in the returned audit counters):

    preserved              - cell was already a text/string value; kept as-is
    reconstructed_mask     - numeric cell + zero-pad format mask; rebuilt to
                             the mask width (e.g. "0000000000" → 10 digits)
    reconstructed_contract - numeric cell, no mask, but a configured fixed
                             length is set; left-pads to that width
    unresolved             - numeric cell, no mask, no contract; raw integer
                             string kept and flagged in log
    empty                  - null / blank input; returns empty string
"""

import re
import logging
import zipfile
import xml.etree.ElementTree as ET

import openpyxl

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Field contracts
# ---------------------------------------------------------------------------
# Columns whose leading zeros must be preserved.
# configured_length: set to an int if this field has a known fixed width
# (e.g. 10 for a 10-digit account number).  Leave None when no contract is
# known; the mask-based reconstruction will still fire if Excel provides one.
IDENTIFIER_CONTRACTS = {
    'ACCOUNTNUMBER':          {'configured_length': None},
    'CUSTOMERID':             {'configured_length': None},
    'CUSTOMERSACCOUNTNUMBER': {'configured_length': None},
    'CUSTOMERBRANCHCODE':     {'configured_length': None},
    'BRANCHCODE':             {'configured_length': None},
    'PREVIOUSACCOUNTNUMBER':  {'configured_length': None},
    'PREVIOUSCUSTOMERID':     {'configured_length': None},
    'PREVIOUSBRANCHCODE':     {'configured_length': None},
}

# Regex used to normalise column header names for matching
_COL_NORM_RE = re.compile(r'[^A-Z0-9]')
# Regex used to strip separators from number-format masks
_FMT_SEP_RE = re.compile(r'[-,. _]')


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalise_col(name: str) -> str:
    """Strip everything except uppercase letters and digits."""
    return _COL_NORM_RE.sub('', str(name).upper().strip())


def _mask_is_zero_pad(fmt: str):
    """
    Return the pad width if *fmt* is a zero-padding mask, otherwise None.

    Examples that match:  '0000000000', '000-000-0000', '00 000 00000'
    Examples that don't:  'General', '@', '#,##0.00', ''
    """
    if not fmt or fmt in ('General', '@', ''):
        return None
    # Remove common separators so '000-000-0000' → '0000000000'
    core = _FMT_SEP_RE.sub('', fmt)
    if re.match(r'^0+$', core):
        return len(core)
    return None


# Built-in Excel number-format IDs (subset; full list in ECMA-376 §18.8.30)
_BUILTIN_FORMATS = {
    0: 'General', 1: '0', 2: '0.00', 3: '#,##0', 4: '#,##0.00',
    9: '0%', 10: '0.00%', 11: '0.00E+00', 12: '# ?/?', 13: '# ??/??',
    14: 'mm-dd-yy', 49: '@',
}

# Cap debug output so unresolved diagnostics stay readable in production logs.
UNRESOLVED_DEBUG_SAMPLE_LIMIT = 25


def _load_style_table(file_path: str) -> dict:
    """
    Read ``xl/styles.xml`` directly from the xlsx ZIP archive and return a
    ``{style_id (int): number_format_string (str)}`` lookup table.

    This is a single small XML member read — no full workbook load.  Returns
    an empty dict if the file is not a ZIP (e.g. legacy .xls) or if the
    styles member is absent.
    """
    if not zipfile.is_zipfile(file_path):
        return {}
    try:
        with zipfile.ZipFile(file_path, 'r') as zf:
            if 'xl/styles.xml' not in zf.namelist():
                return {}
            with zf.open('xl/styles.xml') as fh:
                root = ET.parse(fh).getroot()
    except Exception as exc:
        logger.warning("[IDENTIFIER] Cannot parse xl/styles.xml: %s", exc)
        return {}

    # Detect namespace (present in all modern xlsx files)
    ns_match = re.match(r'\{(.+?)\}', root.tag)
    pfx = f"{{{ns_match.group(1)}}}" if ns_match else ''

    # Merge built-in formats with any custom numFmts defined in the workbook
    num_fmts = dict(_BUILTIN_FORMATS)
    num_fmts_el = root.find(f'{pfx}numFmts')
    if num_fmts_el is not None:
        for nf in num_fmts_el:
            try:
                num_fmts[int(nf.get('numFmtId', -1))] = nf.get('formatCode', '')
            except (ValueError, TypeError):
                pass

    # cellXfs: the indexed list of cell formats; position = style_id
    style_table = {}
    cell_xfs = root.find(f'{pfx}cellXfs')
    if cell_xfs is not None:
        for style_id, xf in enumerate(cell_xfs):
            try:
                fmt_id = int(xf.get('numFmtId', 0))
                style_table[style_id] = num_fmts.get(fmt_id, 'General')
            except (ValueError, TypeError):
                style_table[style_id] = 'General'

    return style_table


def _load_sheet_col_styles(file_path: str, sheet_name: str) -> dict:
    """
    Return ``{1-based column index: style_id}`` for columns that have a
    custom number format applied at the *column* level.

    When a user formats a whole column in Excel (e.g. selects column B and
    applies "0000000000"), individual cells do NOT get an ``s`` attribute in
    the XML — only the ``<col>`` element in the worksheet carries the style.
    This function reads that information so it can be used as a fallback when
    ``ReadOnlyCell.style_id`` is ``None``.

    Reads three tiny XML members from the ZIP:
      xl/workbook.xml           → sheet-name → r:id
      xl/_rels/workbook.xml.rels → r:id → worksheets/sheetN.xml path
      xl/worksheets/sheetN.xml  → <cols> section
    """
    if not zipfile.is_zipfile(file_path):
        return {}
    try:
        with zipfile.ZipFile(file_path, 'r') as zf:
            members = zf.namelist()

            # ── 1. workbook.xml: sheet name → relationship id ────────────────
            if 'xl/workbook.xml' not in members:
                return {}
            with zf.open('xl/workbook.xml') as fh:
                wb_root = ET.parse(fh).getroot()
            wb_ns  = re.match(r'\{(.+?)\}', wb_root.tag)
            wb_pfx = f"{{{wb_ns.group(1)}}}" if wb_ns else ''
            r_ns   = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'

            r_id = None
            sheets_el = wb_root.find(f'{wb_pfx}sheets')
            if sheets_el is not None:
                for s in sheets_el:
                    if s.get('name') == sheet_name:
                        r_id = s.get(f'{{{r_ns}}}id')
                        break
            if not r_id:
                return {}

            # ── 2. workbook.xml.rels: r:id → worksheet file path ─────────────
            rels_path = 'xl/_rels/workbook.xml.rels'
            if rels_path not in members:
                return {}
            with zf.open(rels_path) as fh:
                rels_root = ET.parse(fh).getroot()

            ws_path = None
            for rel in rels_root:
                if rel.get('Id') == r_id:
                    target = rel.get('Target', '')
                    # Target is relative to xl/
                    ws_path = (
                        f'xl/{target}' if not target.startswith('/')
                        else target.lstrip('/')
                    )
                    break
            if not ws_path or ws_path not in members:
                return {}

            # ── 3. worksheetN.xml: parse <cols> for column-level styles ──────
            with zf.open(ws_path) as fh:
                ws_root = ET.parse(fh).getroot()
            ws_ns  = re.match(r'\{(.+?)\}', ws_root.tag)
            ws_pfx = f"{{{ws_ns.group(1)}}}" if ws_ns else ''

            col_styles = {}
            cols_el = ws_root.find(f'{ws_pfx}cols')
            if cols_el is not None:
                for col_el in cols_el:
                    # Only honour columns that explicitly carry a custom format
                    if col_el.get('customFormat', '0') != '1':
                        continue
                    try:
                        sid     = int(col_el.get('style', 0))
                        col_min = int(col_el.get('min', 0))
                        col_max = int(col_el.get('max', 0))
                        for c in range(col_min, col_max + 1):
                            col_styles[c] = sid
                    except (ValueError, TypeError):
                        continue

            return col_styles

    except Exception as exc:
        logger.warning(
            "[IDENTIFIER] Cannot parse column styles from worksheet XML: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Core resolution logic
# ---------------------------------------------------------------------------

def resolve_identifier_cell(cell_value, number_format, configured_length=None):
    """
    Deterministically resolve a single identifier cell value.

    Parameters
    ----------
    cell_value : any
        Raw value from openpyxl (str, int, float, or None).
    number_format : str | None
        Excel number-format string for this cell (e.g. '0000000000').
        Pass None when unavailable.
    configured_length : int | None
        Known fixed width for the field.  Used as fallback when no mask
        is present.

    Returns
    -------
    (canonical: str, raw: str, method: str)
    """
    # ── Empty / null ────────────────────────────────────────────────────────
    if cell_value is None or str(cell_value).strip() in ('', 'None', 'nan', 'NaN'):
        return ('', '', 'empty')

    raw = str(cell_value)

    # ── Text cell: already correct, just strip whitespace ───────────────────
    if isinstance(cell_value, str):
        return (cell_value.strip(), raw, 'preserved')

    # ── Numeric cell ────────────────────────────────────────────────────────
    if isinstance(cell_value, (int, float)):
        # Convert to integer string; eliminates float noise (123456.0 → '123456')
        int_str = str(int(cell_value))

        # Priority 1: reconstruct from zero-pad format mask
        pad_width = _mask_is_zero_pad(number_format)
        if pad_width is not None:
            return (int_str.zfill(pad_width), raw, 'reconstructed_mask')

        # Priority 2: pad to configured contract length
        if configured_length is not None:
            return (int_str.zfill(configured_length), raw, 'reconstructed_contract')

        # No evidence of intended width — keep raw, flag for audit
        return (int_str, raw, 'unresolved')

    # ── Fallback for unexpected types ────────────────────────────────────────
    return (str(cell_value).strip(), raw, 'preserved')


# ---------------------------------------------------------------------------
# Workbook-level extraction
# ---------------------------------------------------------------------------

def extract_identifier_columns(file_path: str, sheet_name: str,
                                header_row_0indexed: int):
    """
    Extract identifier columns with format-aware leading-zero reconstruction.

    Strategy (avoids a full workbook load for large files):

    1. Parse ``xl/styles.xml`` directly from the xlsx ZIP — one small XML
       member, kilobytes — to build ``{style_id: number_format}``.
    2. Open the workbook with ``read_only=True`` (streaming, low memory).
    3. Per identifier cell, use ``cell.style_id`` to look up the format
       string from the pre-built table.

    Only the identifier columns are visited cell-by-cell; all other columns
    are skipped for performance.  A single streaming pass is sufficient for
    both normal-sized and large (chunked) sheets.

    Parameters
    ----------
    file_path : str
        Path to the Excel file.  ``.xlsb`` is not supported by openpyxl;
        an empty result is returned for that extension.
    sheet_name : str
    header_row_0indexed : int
        0-based header row index (as returned by ``find_header_row``).

    Returns
    -------
    id_values : dict
        ``{normalised_col_name: {data_row_0idx: canonical_str}}``
    audit : dict
        ``{'preserved': int, 'reconstructed_mask': int,
           'reconstructed_contract': int, 'unresolved': int}``
    """
    _empty_audit = {
        'preserved': 0, 'reconstructed_mask': 0,
        'reconstructed_contract': 0, 'unresolved': 0,
    }

    if file_path.lower().endswith('.xlsb'):
        logger.warning(
            "[IDENTIFIER] .xlsb format: openpyxl cannot read number_format; "
            "leading-zero reconstruction from format mask is unavailable "
            "for sheet '%s'.", sheet_name,
        )
        return {}, dict(_empty_audit)

    # ── Step 1: build style lookups from ZIP (no full workbook load) ───────
    style_table     = _load_style_table(file_path)
    # Column-level default styles: fallback for cells with no cell-level s attr
    col_def_styles  = _load_sheet_col_styles(file_path, sheet_name)

    # ── Step 2: streaming read with read_only=True ───────────────────────────
    try:
        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    except Exception as exc:
        logger.warning(
            "[IDENTIFIER] Cannot open workbook for identifier extraction: %s", exc)
        return {}, dict(_empty_audit)

    try:
        ws = wb[sheet_name]
        header_row_1indexed = header_row_0indexed + 1

        # ── Locate identifier columns in the header row ──────────────────────
        header_cells = list(
            ws.iter_rows(min_row=header_row_1indexed,
                         max_row=header_row_1indexed)
        )[0]

        col_index_map = {}  # {normalised_col_name: 0-based col index}
        for idx, cell in enumerate(header_cells):
            if cell.value is None:
                continue
            norm = _normalise_col(cell.value)
            if norm in IDENTIFIER_CONTRACTS:
                col_index_map[norm] = idx

        if not col_index_map:
            logger.info(
                "[IDENTIFIER] No identifier columns found in sheet '%s' "
                "(header row %d).", sheet_name, header_row_1indexed,
            )
            return {}, dict(_empty_audit)

        logger.info(
            "[IDENTIFIER] Identifier columns detected in '%s': %s",
            sheet_name, list(col_index_map.keys()),
        )

        # ── Step 3: extract values using cell.number_format with fallback ────
        result = {col: {} for col in col_index_map}
        audit = dict(_empty_audit)
        unresolved_by_column = {col: 0 for col in col_index_map}
        unresolved_samples = []
        data_start_row = header_row_1indexed + 1

        for data_row_idx, row in enumerate(
                ws.iter_rows(min_row=data_start_row)):
            for col_name, col_idx in col_index_map.items():
                if col_idx >= len(row):
                    continue
                cell = row[col_idx]
                # In read-only mode, ReadOnlyCell exposes number_format directly
                # in many files, even when style_id is not available.
                cell_number_format = getattr(cell, 'number_format', None)
                cell_sid = getattr(cell, 'style_id', None)
                sid = cell_sid
                if cell_number_format not in (None, ''):
                    number_format = cell_number_format
                    style_source = 'cell_number_format'
                else:
                    # Fallback: style_id lookup, then column-default style.
                    style_source = 'cell_style'
                    if sid is None:
                        sid = col_def_styles.get(col_idx + 1)  # col_idx is 0-based
                        style_source = 'column_style' if sid is not None else 'none'
                    number_format = style_table.get(sid) if sid is not None else None
                contract = IDENTIFIER_CONTRACTS.get(col_name, {})
                canonical, raw, method = resolve_identifier_cell(
                    cell.value,
                    number_format,
                    contract.get('configured_length'),
                )
                result[col_name][data_row_idx] = canonical
                if method != 'empty':
                    audit[method] = audit.get(method, 0) + 1
                if method == 'unresolved':
                    unresolved_by_column[col_name] = (
                        unresolved_by_column.get(col_name, 0) + 1
                    )
                    if len(unresolved_samples) < UNRESOLVED_DEBUG_SAMPLE_LIMIT:
                        unresolved_samples.append({
                            'excel_row': data_start_row + data_row_idx,
                            'column': col_name,
                            'raw': raw,
                            'style_source': style_source,
                            'cell_number_format': cell_number_format,
                            'cell_style_id': cell_sid,
                            'resolved_style_id': sid,
                            'number_format': number_format,
                        })

        logger.info(
            "[IDENTIFIER] '%s' audit — preserved: %d, "
            "reconstructed_mask: %d, reconstructed_contract: %d, "
            "unresolved: %d",
            sheet_name,
            audit['preserved'], audit['reconstructed_mask'],
            audit['reconstructed_contract'], audit['unresolved'],
        )
        if audit['unresolved'] > 0:
            logger.warning(
                "[IDENTIFIER] '%s': %d identifier cell(s) could not be "
                "reconstructed (numeric value, no zero-mask, no configured "
                "length). Raw integer string kept.",
                sheet_name, audit['unresolved'],
            )
            logger.info(
                "[IDENTIFIER DEBUG] '%s' unresolved distribution by column: %s",
                sheet_name, unresolved_by_column,
            )
            for sample in unresolved_samples:
                logger.info(
                    "[IDENTIFIER DEBUG] sheet='%s' row=%s col='%s' raw='%s' "
                    "style_source=%s cell_number_format=%r "
                    "cell_style_id=%s resolved_style_id=%s "
                    "number_format=%r",
                    sheet_name,
                    sample['excel_row'],
                    sample['column'],
                    sample['raw'],
                    sample['style_source'],
                    sample['cell_number_format'],
                    sample['cell_style_id'],
                    sample['resolved_style_id'],
                    sample['number_format'],
                )

        return result, audit

    finally:
        wb.close()


# ---------------------------------------------------------------------------
# DataFrame-level helpers
# ---------------------------------------------------------------------------

def apply_identifier_preservation(df, file_path: str, sheet_name: str,
                                   header_row_0indexed: int):
    """
    Patch identifier columns in *df* using format-aware extraction.

    Call this **after** ``pd.read_excel`` + ``astype(str)`` and **before**
    ``preprocess_sheet_columns``.  Column headers in the DataFrame may still
    be raw (un-normalised) at this point; the function handles the mapping.

    Parameters
    ----------
    df : pd.DataFrame
        All columns should already be str type.
    file_path, sheet_name, header_row_0indexed
        Passed through to ``extract_identifier_columns``.

    Returns
    -------
    (patched_df, audit_dict)
    """
    id_values, audit = extract_identifier_columns(
        file_path, sheet_name, header_row_0indexed)
    if not id_values:
        return df, audit

    # Build normalised_name → actual df column name mapping
    df_col_map = {}
    for col in df.columns:
        norm = _normalise_col(col)
        if norm in IDENTIFIER_CONTRACTS:
            df_col_map[norm] = col

    for col_name, row_map in id_values.items():
        if col_name not in df_col_map:
            continue
        actual_col = df_col_map[col_name]
        for df_row_idx, canonical in row_map.items():
            if df_row_idx < len(df):
                df.at[df_row_idx, actual_col] = canonical

    logger.info(
        "[IDENTIFIER] Identifier preservation applied to DataFrame "
        "for sheet '%s'.", sheet_name,
    )
    return df, audit


def patch_chunk_identifier_columns(chunk_df, id_values: dict,
                                    global_row_start: int):
    """
    Patch identifier columns in one chunk DataFrame.

    Parameters
    ----------
    chunk_df : pd.DataFrame
        Chunk with all columns already converted to str.
    id_values : dict
        Output of ``extract_identifier_columns`` — keyed by normalised
        column name, values are ``{data_row_0idx: canonical_str}``.
    global_row_start : int
        0-based index of the first row in this chunk within the full
        data range (i.e. offset from the first data row in the sheet).

    Returns
    -------
    chunk_df (modified in-place and returned)
    """
    if not id_values:
        return chunk_df

    # Build normalised_name → actual df column name mapping for this chunk
    df_col_map = {}
    for col in chunk_df.columns:
        norm = _normalise_col(col)
        if norm in IDENTIFIER_CONTRACTS:
            df_col_map[norm] = col

    for col_name, row_map in id_values.items():
        if col_name not in df_col_map:
            continue
        actual_col = df_col_map[col_name]
        for local_idx in range(len(chunk_df)):
            global_idx = global_row_start + local_idx
            if global_idx in row_map:
                chunk_df.at[local_idx, actual_col] = row_map[global_idx]

    return chunk_df
