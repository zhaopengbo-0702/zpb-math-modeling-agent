#!/usr/bin/env python3
"""Export validated table data to standalone LaTeX tables and one Excel workbook."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import sys
import tempfile


class TableExportError(ValueError):
    """Input data or output paths do not satisfy the table export contract."""


TEX_ESCAPE = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$",
              "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}",
              "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def text_value(value: object, location: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise TableExportError(f"{location} must be a {'possibly empty ' if allow_empty else 'nonempty '}string.")
    if CONTROL.search(value) or len(value) > 32767:
        raise TableExportError(f"{location} contains an illegal control character or exceeds Excel's cell text limit.")
    try:
        value.encode("utf-8")
    except UnicodeError as error:
        raise TableExportError(f"{location} contains invalid Unicode.") from error
    return value


def validate_spec(spec: object) -> list[dict]:
    if not isinstance(spec, dict) or not isinstance(spec.get("tables"), list) or not spec["tables"]:
        raise TableExportError("The specification must contain a nonempty tables array.")
    normalized, ids, labels, sheets = [], set(), set(), set()
    for index, table in enumerate(spec["tables"]):
        where = f"tables[{index}]"
        if not isinstance(table, dict):
            raise TableExportError(f"{where} must be an object.")
        identifier = table.get("id")
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", identifier) or len(identifier) > 64:
            raise TableExportError(f"{where}.id must be a lowercase ASCII slug, at most 64 characters.")
        if identifier in ids:
            raise TableExportError(f"Duplicate table id: {identifier}")
        ids.add(identifier)
        caption = text_value(table.get("caption"), where + ".caption")
        header_note = text_value(table.get("header_note", ""), where + ".header_note", allow_empty=True)
        if header_note:
            text_value(caption + "\n" + header_note, where + ".caption and header_note")
        label = table.get("label", f"tab:{identifier}")
        if not isinstance(label, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9:._-]*", label):
            raise TableExportError(f"{where}.label contains unsafe LaTeX label characters.")
        if label in labels:
            raise TableExportError(f"Duplicate LaTeX label: {label}")
        labels.add(label)
        sheet = text_value(table.get("sheet", identifier[:31]), where + ".sheet")
        if len(sheet) > 31 or re.search(r"[\[\]:*?/\\]", sheet) or sheet.startswith("'") or sheet.endswith("'"):
            raise TableExportError(f"{where}.sheet must have at most 31 characters and no Excel-reserved characters.")
        if sheet.casefold() in sheets:
            raise TableExportError(f"Duplicate worksheet name (case-insensitive): {sheet}")
        sheets.add(sheet.casefold())
        raw_columns = table.get("columns")
        if not isinstance(raw_columns, list) or not raw_columns:
            raise TableExportError(f"{where}.columns must be a nonempty array.")
        if len(raw_columns) > 8:
            raise TableExportError(f"{where} has more than eight columns; split the table instead of shrinking its font.")
        columns, keys = [], set()
        for column_index, column in enumerate(raw_columns):
            column_where = f"{where}.columns[{column_index}]"
            if not isinstance(column, dict):
                raise TableExportError(f"{column_where} must be an object.")
            key = text_value(column.get("key"), column_where + ".key")
            if key in keys:
                raise TableExportError(f"Duplicate column key in {identifier}: {key}")
            keys.add(key)
            decimals = column.get("decimals", 4)
            if isinstance(decimals, bool) or not isinstance(decimals, int) or not 0 <= decimals <= 15:
                raise TableExportError(f"{column_where}.decimals must be an integer from 0 to 15.")
            normalized_column = {"key": key, "label": text_value(column.get("label"), column_where + ".label"),
                                 "unit": text_value(column.get("unit", ""), column_where + ".unit", allow_empty=True),
                                 "decimals": decimals}
            if "width_cm" in column:
                width = column["width_cm"]
                if (isinstance(width, bool) or not isinstance(width, (int, float)) or width <= 0
                        or (isinstance(width, float) and not math.isfinite(width))):
                    raise TableExportError(f"{column_where}.width_cm must be a positive finite number.")
                normalized_column["width_cm"] = width
            columns.append(normalized_column)
        rows = table.get("rows")
        if not isinstance(rows, list) or not rows:
            raise TableExportError(f"{where}.rows must be a nonempty array.")
        if len(rows) > 1048574:
            raise TableExportError(f"{where}.rows exceeds Excel's row limit after the caption and header.")
        for row_index, row in enumerate(rows):
            row_where = f"{where}.rows[{row_index}]"
            if not isinstance(row, dict) or set(row) != keys:
                missing = sorted(keys - set(row)) if isinstance(row, dict) else sorted(keys)
                extra = sorted(set(row) - keys) if isinstance(row, dict) else []
                raise TableExportError(f"{row_where} must contain exactly the column keys; missing={missing}, extra={extra}.")
            for key, value in row.items():
                if value is None:
                    continue
                if isinstance(value, str):
                    text_value(value, row_where + "." + key, allow_empty=True)
                elif isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise TableExportError(f"{row_where}.{key} must be a number, string or null.")
                elif isinstance(value, int) and abs(value) > 2 ** 53:
                    raise TableExportError(f"{row_where}.{key} exceeds exact Excel integer precision; encode identifiers as strings.")
                elif not math.isfinite(value):
                    raise TableExportError(f"{row_where}.{key} must be finite; NaN and infinity are not permitted.")
        normalized.append({"id": identifier, "caption": caption, "header_note": header_note,
                           "label": label, "sheet": sheet,
                           "columns": columns, "rows": rows, "source_pointer": f"/tables/{index}"})
    return normalized


def latex_text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    return "".join(r"\newline{}" if character == "\n" else TEX_ESCAPE.get(character, character) for character in value)


def latex_cell(value: str, *, paragraph: bool = False, alignment: str = "l") -> str:
    escaped = latex_text(value)
    if not paragraph and r"\newline{}" in escaped:
        return r"\shortstack[" + alignment + "]{" + escaped.replace(r"\newline{}", r"\\") + "}"
    return escaped


def display_value(value: object, decimals: int) -> str:
    if value is None:
        return "---"
    if isinstance(value, str):
        return value
    return f"{value:.{decimals}f}"


def column_header(column: dict) -> str:
    return column["label"] + (" / " + column["unit"] if column["unit"] else "")


def visual_width(value: str) -> int:
    return max((sum(2 if ord(character) > 127 else 1 for character in line) for line in value.splitlines()), default=0)


def latex_table(table: dict) -> str:
    specifications, alignments = [], []
    for column in table["columns"]:
        values = [row[column["key"]] for row in table["rows"]]
        numeric = all(value is None or isinstance(value, (int, float)) for value in values)
        alignments.append("r" if numeric else "l")
        if "width_cm" in column:
            width = format(Decimal(str(column["width_cm"])), "f")
            alignment = "raggedleft" if numeric else "raggedright"
            specifications.append(rf">{{\{alignment}\arraybackslash}}p{{{width}cm}}")
        else:
            specifications.append("r" if numeric else "l")
    specification = "".join(specifications)
    rows = []
    for row in table["rows"]:
        rows.append(" & ".join(latex_cell(display_value(row[column["key"]], column["decimals"]),
                                          paragraph="width_cm" in column, alignment=alignment)
                               for column, alignment in zip(table["columns"], alignments)) + r" \\")
    note = ([rf"\multicolumn{{{len(table['columns'])}}}{{c}}{{" + latex_cell(table["header_note"], alignment="c") + r"} \\"]
            if table["header_note"] else [])
    return "\n".join([
        "% Requires booktabs; explicit paragraph widths additionally require array.",
        r"\begin{table}[!htbp]", r"\centering\small", r"\caption{" + latex_text(table["caption"]) + "}",
        r"\label{" + table["label"] + "}",
        r"\begin{tabular}{" + specification + "}", r"\toprule", *note,
        " & ".join(latex_cell(column_header(column), paragraph="width_cm" in column, alignment=alignment)
                   for column, alignment in zip(table["columns"], alignments)) + r" \\",
        r"\midrule", *rows, r"\bottomrule", r"\end{tabular}", r"\end{table}", ""])


def workbook_file(tables: list[dict], destination: Path) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    workbook = Workbook()
    workbook.remove(workbook.active)
    black = "000000"
    white = "FFFFFF"
    rule = Side(style="thin", color=black)
    for table in tables:
        sheet = workbook.create_sheet(table["sheet"])
        count = len(table["columns"])
        sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=count)
        caption_text = table["caption"] + ("\n" + table["header_note"] if table["header_note"] else "")
        caption = sheet.cell(1, 1, caption_text)
        caption.data_type = "s"
        caption.font = Font(name="Microsoft YaHei", size=12, bold=True, color=black)
        caption.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        sheet.row_dimensions[1].height = 46 if table["header_note"] else 30
        sheet.row_dimensions[2].height = 30
        sheet.freeze_panes = "B3"
        sheet.sheet_view.showGridLines = False
        for index, column in enumerate(table["columns"], 1):
            header = sheet.cell(2, index, column_header(column))
            header.data_type = "s"
            header.font = Font(name="Microsoft YaHei", size=11, bold=True, color=black)
            header.fill = PatternFill(fill_type="solid", fgColor=white)
            header.border = Border(top=rule, bottom=rule)
            header.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            texts = [column_header(column)] + [display_value(row[column["key"]], column["decimals"]) for row in table["rows"]]
            sheet.column_dimensions[header.column_letter].width = max(12, min(36, max(visual_width(value) for value in texts) + 2))
        for row_index, row in enumerate(table["rows"], 3):
            sheet.row_dimensions[row_index].height = 20
            for column_index, column in enumerate(table["columns"], 1):
                value = row[column["key"]]
                cell = sheet.cell(row_index, column_index, value)
                cell.font = Font(name="Microsoft YaHei", size=11, color=black)
                cell.fill = PatternFill(fill_type="solid", fgColor=white)
                cell.alignment = Alignment(horizontal="left" if isinstance(value, str) else "right",
                                           vertical="center", wrap_text=isinstance(value, str))
                if isinstance(value, str):
                    cell.data_type = "s"
                elif value is not None:
                    cell.number_format = "0" + ("." + "0" * column["decimals"] if column["decimals"] else "")
                if row_index == len(table["rows"]) + 2:
                    cell.border = Border(bottom=rule)
        sheet.print_options.horizontalCentered = True
        sheet.page_setup.orientation = "landscape" if count > 5 else "portrait"
        sheet.page_setup.fitToWidth = 1
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.print_title_rows = "1:2"
    workbook.save(destination)
    workbook.close()


def digest_file(path: Path) -> dict:
    payload = path.read_bytes()
    return {"path": path.name, "sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)}


def publish(staged: Path, output: Path, names: list[str]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    backups = {}
    published = []
    try:
        for name in names:
            final = output / name
            if final.exists():
                backup = staged / ("previous-" + name)
                shutil.copy2(final, backup)
                backups[name] = backup
            (staged / name).replace(final)
            published.append(name)
    except BaseException:
        for name in reversed(published):
            if name in backups:
                backups[name].replace(output / name)
            else:
                (output / name).unlink(missing_ok=True)
        raise


def export_tables(source: Path, output: Path, *, overwrite: bool = False) -> dict:
    source = source.expanduser().resolve(strict=True)
    raw = source.read_bytes()
    source_hash = hashlib.sha256(raw).hexdigest()

    def reject_constant(value: str):
        raise TableExportError(f"Nonfinite JSON number is not permitted: {value}")

    try:
        tables = validate_spec(json.loads(raw.decode("utf-8-sig"), parse_constant=reject_constant))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise TableExportError(f"Invalid UTF-8 JSON specification: {error}") from error
    output = output.expanduser().resolve()
    if output.exists() and not output.is_dir():
        raise TableExportError("--output must name a directory.")
    if output.exists() and any(output.iterdir()) and not overwrite:
        raise TableExportError("Output directory is not empty; use a new directory or --overwrite for generated files.")
    names = [table["id"] + ".tex" for table in tables] + ["tables.xlsx", "table-manifest.json"]
    for name in names:
        final = output / name
        if final.resolve() == source:
            raise TableExportError("An output file would replace the source specification.")
        if final.is_symlink() or (hasattr(final, "is_junction") and final.is_junction()):
            raise TableExportError(f"Output cannot be a symlink or junction: {name}")
        if final.exists() and not final.is_file():
            raise TableExportError(f"Output filename is occupied by a directory: {name}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".zpb-tables-", dir=output.parent) as temporary:
        staged = Path(temporary)
        for table in tables:
            (staged / (table["id"] + ".tex")).write_text(latex_table(table), encoding="utf-8")
        workbook_file(tables, staged / "tables.xlsx")
        outputs = [digest_file(staged / name) for name in names if name != "table-manifest.json"]
        manifest = {
            "schema_version": 1, "created_utc": datetime.now(timezone.utc).isoformat(),
            "source_spec": {"file": source.name, "sha256": source_hash, "size_bytes": len(raw)},
            "tables": [{"id": table["id"], "caption": table["caption"], "header_note": table["header_note"],
                        "label": table["label"],
                        "sheet": table["sheet"], "row_count": len(table["rows"]),
                        "column_count": len(table["columns"]),
                        "source": {"spec": source.name, "json_pointer": table["source_pointer"]},
                        "latex": table["id"] + ".tex", "workbook": "tables.xlsx",
                        "columns": table["columns"]} for table in tables],
            "outputs": outputs,
            "numeric_storage": "Source numbers are not rounded before Excel export; decimals control display formatting.",
        }
        (staged / "table-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if hashlib.sha256(source.read_bytes()).hexdigest() != source_hash:
            raise TableExportError("Source specification changed during export; no generated files were published.")
        publish(staged, output, names)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true", help="Replace only this run's generated files, preserving unrelated files.")
    args = parser.parse_args(argv)
    try:
        report = export_tables(args.spec, args.output, overwrite=args.overwrite)
    except (TableExportError, OSError, ImportError, ValueError) as error:
        parser.exit(1, f"Table export failed: {error}\n")
    print(f"Exported {len(report['tables'])} standalone LaTeX tables and tables.xlsx to {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    from _console import utf8_console
    utf8_console()
    sys.exit(main())
