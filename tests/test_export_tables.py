"""Verify shared table data, native Excel precision and safe text exports."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("zpb_tables", ROOT / "scripts/export_tables.py")
tables = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tables)


def table_spec() -> dict:
    return {"tables": [{"id": "result-q1", "caption": "问题一的精确结果 & 检验", "label": "tab:result-q1", "sheet": "问题一结果",
                        "columns": [{"key": "name", "label": "测点"},
                                    {"key": "value", "label": "含水率", "unit": "kg/kg"}],
                        "rows": [{"name": "中心", "value": 1.23456789},
                                 {"name": "域外", "value": None}]}]}


def save_spec(base: Path, spec: dict) -> Path:
    path = base / "中文表数据.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return path


class TableExportTests(unittest.TestCase):
    def test_precision_number_format_null_and_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = save_spec(base, table_spec())
            output = base / "表格"
            report = tables.export_tables(source, output)
            workbook = load_workbook(output / "tables.xlsx", data_only=False)
            sheet = workbook["问题一结果"]
            self.assertEqual(sheet["B3"].value, 1.23456789)
            self.assertEqual(sheet["B3"].number_format, "0.0000")
            self.assertIsNone(sheet["B4"].value)
            self.assertEqual(sheet["B2"].value, "含水率 / kg/kg")
            workbook.close()
            tex = (output / "result-q1.tex").read_text(encoding="utf-8")
            self.assertIn("1.2346", tex)
            self.assertIn("---", tex)
            self.assertIn(r"\centering\small", tex)
            self.assertIn(r"\begin{tabular}{lr}", tex)
            self.assertIn("测点 & 含水率 / kg/kg", tex)
            self.assertNotIn(r"\textbf", tex)
            self.assertNotIn(r"\linewidth", tex)
            self.assertNotIn(r"\arraystretch", tex)
            self.assertNotIn("tabularx", tex)
            self.assertNotIn(r"\resizebox", tex)
            self.assertEqual(report["source_spec"]["sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
            for entry in report["outputs"]:
                payload = (output / entry["path"]).read_bytes()
                self.assertEqual(len(payload), entry["size_bytes"])
                self.assertEqual(hashlib.sha256(payload).hexdigest(), entry["sha256"])

    def test_chinese_special_characters_and_formula_like_text_remain_literal(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            spec = table_spec()
            literal = "中文 & % _ # { } $ \\ ~ ^\n第二行"
            spec["tables"][0]["rows"] = [{"name": literal, "value": "=SUM(1,2)"},
                                           {"name": "错误码是文本", "value": "#N/A"}]
            source = save_spec(base, spec)
            output = base / "tables"
            tables.export_tables(source, output)
            workbook = load_workbook(output / "tables.xlsx", data_only=False)
            sheet = workbook["问题一结果"]
            self.assertEqual(sheet["A3"].value, literal)
            self.assertEqual(sheet["B3"].value, "=SUM(1,2)")
            self.assertEqual(sheet["B3"].data_type, "s")
            self.assertEqual(sheet["B4"].data_type, "s")
            workbook.close()
            tex = (output / "result-q1.tex").read_text(encoding="utf-8")
            for escaped in (r"\&", r"\%", r"\_", r"\#", r"\{", r"\}", r"\$", r"\textbackslash{}", r"\textasciitilde{}", r"\textasciicircum{}", r"\shortstack[l]{"):
                self.assertIn(escaped, tex)
            self.assertIn(r"\\第二行}", tex)

    def test_six_column_spatiotemporal_table_matches_final_paper_style(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            table = {"id": "q4-moisture", "caption": "收缩域干燥过程的含水率 / kg/kg",
                     "sheet": "问题四含水率", "header_note": "径向距离 / cm；表面列对应当前半径",
                     "columns": [{"key": "time", "label": "时间", "unit": "h", "decimals": 0},
                                 {"key": "center", "label": "0"},
                                 {"key": "r05", "label": "0.5"},
                                 {"key": "r10", "label": "1.0"},
                                 {"key": "surface", "label": "表面"},
                                 {"key": "radius", "label": "半径", "unit": "cm"}],
                     "rows": [{"time": 6, "center": 1.716412345, "r05": 1.534912345,
                               "r10": 1.021212345, "surface": 0.421512345, "radius": 1.374012345},
                              {"time": 12, "center": 0.734012345, "r05": 0.651612345,
                               "r10": 0.406812345, "surface": 0.166612345, "radius": 1.248012345}]}
            output = base / "tables"
            report = tables.export_tables(save_spec(base, {"tables": [table]}), output)
            tex = (output / "q4-moisture.tex").read_text(encoding="utf-8")
            self.assertIn(r"\begin{tabular}{rrrrrr}", tex)
            self.assertIn(r"\toprule" + "\n" + r"\multicolumn{6}{c}{径向距离 / cm；表面列对应当前半径} \\" + "\n"
                          + "时间 / h & 0 & 0.5 & 1.0 & 表面 & 半径 / cm", tex)
            self.assertIn(r"6 & 1.7164 & 1.5349 & 1.0212 & 0.4215 & 1.3740 \\", tex)
            for forbidden in ("tabularx", r"\linewidth", r"\textbf", r"\arraystretch", "p{"):
                self.assertNotIn(forbidden, tex)
            self.assertEqual(report["tables"][0]["header_note"], table["header_note"])
            self.assertTrue(all("width_cm" not in column for column in report["tables"][0]["columns"]))
            workbook = load_workbook(output / "tables.xlsx", data_only=False)
            sheet = workbook["问题四含水率"]
            self.assertEqual(sheet["A1"].value, table["caption"] + "\n" + table["header_note"])
            self.assertEqual(str(next(iter(sheet.merged_cells.ranges))), "A1:F1")
            self.assertEqual(sheet["A2"].value, "时间 / h")
            self.assertEqual(sheet["F2"].value, "半径 / cm")
            for row_index, row in enumerate(table["rows"], 3):
                for column_index, column in enumerate(table["columns"], 1):
                    self.assertEqual(sheet.cell(row_index, column_index).value, row[column["key"]])
            workbook.close()

    def test_header_note_is_escaped_and_explicit_widths_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            spec = table_spec()
            table = spec["tables"][0]
            table["header_note"] = "说明 & 适用域_1"
            table["columns"][0]["width_cm"] = 2.5
            table["columns"][1]["width_cm"] = 2e-5
            table["rows"][0]["name"] = "第一行\n第二行"
            output = base / "tables"
            report = tables.export_tables(save_spec(base, spec), output)
            tex = (output / "result-q1.tex").read_text(encoding="utf-8")
            self.assertIn(r"\multicolumn{2}{c}{说明 \& 适用域\_1}", tex)
            self.assertIn(r">{\raggedright\arraybackslash}p{2.5cm}", tex)
            self.assertIn(r">{\raggedleft\arraybackslash}p{0.00002cm}", tex)
            self.assertIn(r"第一行\newline{}第二行", tex)
            self.assertNotIn("2e-05cm", tex)
            self.assertEqual(report["tables"][0]["columns"][0]["width_cm"], 2.5)
            self.assertEqual(report["tables"][0]["columns"][1]["width_cm"], 2e-5)

    def test_invalid_width_and_header_note_are_rejected(self):
        for value in (True, False, 0, -1, "2.5", None, float("nan"), float("inf"), float("-inf")):
            with self.subTest(width=value):
                spec = table_spec()
                spec["tables"][0]["columns"][0]["width_cm"] = value
                with self.assertRaisesRegex(tables.TableExportError, "positive finite"):
                    tables.validate_spec(spec)
        for value in (None, 5, "invalid\x00note", "中" * 32767):
            with self.subTest(note_type=type(value).__name__):
                spec = table_spec()
                spec["tables"][0]["header_note"] = value
                with self.assertRaises(tables.TableExportError):
                    tables.validate_spec(spec)

    def test_multiple_tables_have_independent_tex_and_worksheets(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            spec = table_spec()
            second = {"id": "validation", "caption": "收敛检验", "label": "tab:validation", "sheet": "验证",
                      "columns": [{"key": "n", "label": "区间数", "decimals": 0}], "rows": [{"n": 80}]}
            spec["tables"].append(second)
            output = base / "tables"
            tables.export_tables(save_spec(base, spec), output)
            workbook = load_workbook(output / "tables.xlsx")
            self.assertEqual(workbook.sheetnames, ["问题一结果", "验证"])
            self.assertEqual(workbook["验证"]["A3"].number_format, "0")
            workbook.close()
            self.assertTrue((output / "result-q1.tex").is_file())
            self.assertTrue((output / "validation.tex").is_file())

    def test_validation_failure_never_overwrites_existing_files(self):
        for mutation in ("duplicate", "missing", "nan", "inf", "huge-int", "wide"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                spec = table_spec()
                if mutation == "duplicate":
                    spec["tables"].append(spec["tables"][0].copy())
                elif mutation == "missing":
                    del spec["tables"][0]["rows"][0]["value"]
                elif mutation in {"nan", "inf"}:
                    spec["tables"][0]["rows"][0]["value"] = float(mutation)
                elif mutation == "huge-int":
                    spec["tables"][0]["rows"][0]["value"] = 10 ** 400
                else:
                    spec["tables"][0]["columns"] = [{"key": f"c{i}", "label": f"列{i}"} for i in range(9)]
                output = base / "tables"
                output.mkdir()
                original = output / "result-q1.tex"
                original.write_text("preserve the final earlier table")
                with self.assertRaises(tables.TableExportError):
                    tables.export_tables(save_spec(base, spec), output, overwrite=True)
                self.assertEqual(original.read_text(), "preserve the final earlier table")
                self.assertFalse((output / "tables.xlsx").exists())

    def test_sheet_length_and_case_insensitive_collision_fail(self):
        for mutation in ("length", "collision"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                spec = table_spec()
                if mutation == "length":
                    spec["tables"][0]["sheet"] = "中" * 32
                else:
                    first = spec["tables"][0]
                    first["sheet"] = "Results"
                    second = json.loads(json.dumps(first))
                    second.update(id="other", label="tab:other", sheet="results")
                    spec["tables"].append(second)
                with self.assertRaises(tables.TableExportError):
                    tables.export_tables(save_spec(base, spec), base / "tables")
                self.assertFalse((base / "tables").exists())

    def test_nonempty_output_requires_overwrite_and_preserves_other_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = save_spec(base, table_spec())
            output = base / "tables"
            output.mkdir()
            unrelated = output / "my-notes.txt"
            unrelated.write_text("keep unrelated work")
            with self.assertRaises(tables.TableExportError):
                tables.export_tables(source, output)
            tables.export_tables(source, output, overwrite=True)
            self.assertEqual(unrelated.read_text(), "keep unrelated work")
            self.assertTrue((output / "tables.xlsx").exists())

    def test_safe_ids_labels_and_excel_integer_precision(self):
        for field, value in (("id", "../escape"), ("label", r"tab:bad}\input{file")):
            with self.subTest(field=field):
                spec = table_spec()
                spec["tables"][0][field] = value
                with self.assertRaises(tables.TableExportError):
                    tables.validate_spec(spec)
        for value in (12345678901234567890, 10 ** 400):
            with self.subTest(value_digits=len(str(value))):
                spec = table_spec()
                spec["tables"][0]["rows"][0]["value"] = value
                with self.assertRaisesRegex(tables.TableExportError, "Excel integer precision"):
                    tables.validate_spec(spec)


if __name__ == "__main__":
    unittest.main()
