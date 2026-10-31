"""TDD gate for ``tools/capacities_csv_diff.py``.

The Capacities "Assigned" query is the human definition of what TDTB should
load, but Capacities exposes no API for query *results* — only the in-app CSV
export. These tests pin the export parser (BOM, quoting, tolerant column
detection), the title normalization and duplicate collapsing, the
missing/extra/mismatch classification, the JSON shape, and the 0/1/2/3 exit
codes. No network: the plan-inputs loader is injected.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools"))

import capacities_csv_diff as diffmod  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL = REPO_ROOT / "tools" / "capacities_csv_diff.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_csv(path: Path, text: str, *, bom: bool = False) -> Path:
    data = ("\ufeff" + text).encode("utf-8") if bom else text.encode("utf-8")
    path.write_bytes(data)
    return path


def _plan_inputs(rows: list[dict]) -> dict:
    return {"digest": {"assigned": rows}}


def _cap_row(
    name: str,
    structure_id: str = "e16f8eeb-1d66-4f21-8d3a-adaeff0a75be",
    reasons: list[str] | None = None,
    source: str = "capacities",
) -> dict:
    return {
        "name": name,
        "source": source,
        "capacities_structure_id": structure_id,
        "capacities_assignment": {
            "mode": "auto",
            "reasons": reasons if reasons is not None else ["auto-status-active"],
        },
    }


def _run(tmp_path: Path, csv_text: str, rows: list[dict], *,
         bom: bool = False, extra_args: list[str] = ()) -> tuple[int, str, str]:
    path = _write_csv(tmp_path / "query.csv", csv_text, bom=bom)
    payload = _plan_inputs(rows)
    out, err = [], []

    argv = [str(path), *extra_args]

    class _Out:
        def write(self, text: str) -> int:
            out.append(text)
            return len(text)

        def flush(self) -> None:
            return None

    class _Err:
        def write(self, text: str) -> int:
            err.append(text)
            return len(text)

        def flush(self) -> None:
            return None

    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = _Out(), _Err()
    try:
        code = diffmod.main(
            argv,
            load_plan_inputs=lambda url: payload,
            load_structure_titles=lambda url: {"e16f8eeb-1d66-4f21-8d3a-adaeff0a75be": "Event"},
        )
    finally:
        sys.stdout, sys.stderr = old_out, old_err
    return code, "".join(out), "".join(err)


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------

class TestReadQueryCsv:
    def test_bom_and_quoted_cells_parse(self, tmp_path):
        text = (
            'Title,Type,Notes\n'
            '"Halloween, decorations",Event,"multi\nline note"\n'
            'Simple,Task,\n'
        )
        path = _write_csv(tmp_path / "q.csv", text, bom=True)
        rows, counters = diffmod.read_query_csv(str(path))
        assert [row["title"] for row in rows] == ["Halloween, decorations", "Simple"]
        assert [row["type"] for row in rows] == ["Event", "Task"]
        assert counters["rows"] == 2
        assert counters["has_type"] == 1

    def test_headers_name_and_object_type_recognized(self, tmp_path):
        text = 'Name,Object Type\nHalloween,Event\n'
        path = _write_csv(tmp_path / "q.csv", text)
        rows, counters = diffmod.read_query_csv(str(path))
        assert rows == [{"title": "Halloween", "type": "Event"}]
        assert counters["has_type"] == 1

    def test_extra_columns_blank_lines_and_no_type_column(self, tmp_path):
        text = 'object,Created\n\nGarage Buildout,2026-10-01\n\n,2026-10-02\n'
        path = _write_csv(tmp_path / "q.csv", text)
        rows, counters = diffmod.read_query_csv(str(path))
        assert rows == [{"title": "Garage Buildout", "type": None}]
        assert counters["has_type"] == 0
        assert counters["blank"] == 1

    def test_whitespace_titles_trimmed_and_empty_ignored(self, tmp_path):
        text = 'Title\n"  Spaced  "\n   \n'
        path = _write_csv(tmp_path / "q.csv", text)
        rows, _counters = diffmod.read_query_csv(str(path))
        assert rows == [{"title": "Spaced", "type": None}]

    def test_duplicates_collapsed_and_counted(self, tmp_path):
        text = 'Title,Type\nHalloween,Event\n  halloween ,Event\nHALLOWEEN,Task\n'
        path = _write_csv(tmp_path / "q.csv", text)
        rows, counters = diffmod.read_query_csv(str(path))
        assert rows == [{"title": "Halloween", "type": "Event"}]
        assert counters["duplicates"] == 2

    def test_missing_title_column_raises_with_headers(self, tmp_path):
        path = _write_csv(tmp_path / "q.csv", "Foo,Bar\n1,2\n")
        with pytest.raises(diffmod.CsvError) as excinfo:
            diffmod.read_query_csv(str(path))
        message = str(excinfo.value)
        assert "'Foo'" in message and "'Bar'" in message


# ---------------------------------------------------------------------------
# Diff classification
# ---------------------------------------------------------------------------

class TestBuildDiff:
    def test_matched_missing_extra_and_normalization(self):
        csv_rows = [
            {"title": "Halloween", "type": "Event"},      # normalized match
            {"title": "  Multi   Space ", "type": "Task"},  # whitespace-collapsed match
            {"title": "Not In App", "type": "Task"},
            {"title": "not in app", "type": "Task"},      # duplicate collapse happens upstream
        ]
        tdtb_rows = [
            {"title": "HALLOWEEN", "type": "Event", "reason": "source-assigned"},
            {"title": "multi space", "type": "Task", "reason": "auto-status-active"},
            {"title": "Loaded Only", "type": "Project", "reason": "auto-status-active"},
        ]
        # collapse the duplicate the way read_query_csv does
        unique = [csv_rows[0], csv_rows[1], csv_rows[2]]
        result = diffmod.build_diff(unique, tdtb_rows, {"has_type": 1, "duplicates": 1})

        assert result["summary"] == {"csv": 3, "tdtb": 3, "matched": 2, "duplicates": 1}
        assert [row["title"] for row in result["missing"]] == ["Not In App"]
        assert [row["title"] for row in result["extra"]] == ["Loaded Only"]
        assert result["extra"][0]["reason"] == "auto-status-active"
        assert result["mismatched"] == []

    def test_type_mismatch_is_not_a_match(self):
        csv_rows = [{"title": "Buy paint", "type": "Purchase"}]
        tdtb_rows = [{"title": "Buy paint", "type": "Task", "reason": "auto-status-active"}]
        result = diffmod.build_diff(csv_rows, tdtb_rows, {"has_type": 1})

        assert result["summary"]["matched"] == 0
        assert result["mismatched"] == [
            {"title": "Buy paint", "csv_type": "Purchase", "tdtb_type": "Task"}
        ]
        assert result["missing"] == []
        assert result["extra"] == []

    def test_type_comparison_is_casefolded(self):
        csv_rows = [{"title": "Halloween", "type": "event"}]
        tdtb_rows = [{"title": "Halloween", "type": "Event", "reason": "r"}]
        result = diffmod.build_diff(csv_rows, tdtb_rows, {"has_type": 1})
        assert result["summary"]["matched"] == 1
        assert result["mismatched"] == []

    def test_missing_type_on_either_side_still_matches(self):
        csv_rows = [{"title": "Halloween", "type": None}]
        tdtb_rows = [{"title": "Halloween", "type": "Event", "reason": "r"}]
        result = diffmod.build_diff(csv_rows, tdtb_rows, {"has_type": 0})
        assert result["summary"]["matched"] == 1
        assert result["mismatched"] == []


# ---------------------------------------------------------------------------
# Loaded rows
# ---------------------------------------------------------------------------

class TestLoadedRows:
    def test_only_capacities_source_rows_are_kept(self):
        payload = _plan_inputs([
            _cap_row("Kept"),
            _cap_row("Todoist row", source="todoist"),
            {"name": "No source", "capacities_structure_id": "x"},
        ])
        rows = diffmod.loaded_rows(payload, {"e16f8eeb-1d66-4f21-8d3a-adaeff0a75be": "Event"})
        assert [row["title"] for row in rows] == ["Kept"]
        assert rows[0]["type"] == "Event"
        assert rows[0]["reason"] == "auto-status-active"

    def test_unknown_structure_id_shortens_to_eight_chars(self):
        rows = diffmod.loaded_rows(_plan_inputs([_cap_row("X")]), {})
        assert rows[0]["type"] == "e16f8eeb"

    def test_missing_assignment_reason_is_none(self):
        payload = _plan_inputs([
            {"name": "Bare", "source": "Capacities", "capacities_structure_id": "RootTask"},
        ])
        rows = diffmod.loaded_rows(payload, {"RootTask": "Task"})
        assert rows[0]["reason"] is None

    def test_malformed_payload_yields_no_rows(self):
        assert diffmod.loaded_rows({}, {}) == []
        assert diffmod.loaded_rows({"digest": {"assigned": "nope"}}, {}) == []


# ---------------------------------------------------------------------------
# CLI contract
# ---------------------------------------------------------------------------

class TestCli:
    def test_exit_0_when_everything_matches(self, tmp_path):
        code, out, _err = _run(
            tmp_path,
            "Title,Type\nHalloween,Event\n",
            [_cap_row("halloween")],
        )
        assert code == 0
        assert out.strip() == "CSV 1 · TDTB 1 · matched 1"

    def test_exit_1_when_missing(self, tmp_path):
        code, out, _err = _run(
            tmp_path,
            "Title,Type\nHalloween,Event\nNot A Real Object,Task\n",
            [_cap_row("Halloween")],
        )
        assert code == 1
        assert "In the query but not loaded" in out
        assert "Not A Real Object  [Task]" in out
        assert "Loaded but not in the query" not in out

    def test_exit_1_when_extra(self, tmp_path):
        code, out, _err = _run(
            tmp_path,
            "Title,Type\nHalloween,Event\n",
            [_cap_row("Halloween"), _cap_row("Stray", reasons=["source-assigned"])],
        )
        assert code == 1
        assert "Loaded but not in the query" in out
        assert "Stray  [Event]  source-assigned" in out

    def test_type_mismatch_section_rendered(self, tmp_path):
        code, out, _err = _run(
            tmp_path,
            "Title,Type\nHalloween,Task\n",
            [_cap_row("Halloween")],
        )
        assert code == 1
        assert "Type mismatches" in out
        assert "Halloween  CSV: Task · TDTB: Event" in out

    def test_exit_2_when_no_title_column(self, tmp_path):
        code, out, err = _run(tmp_path, "Foo,Bar\n1,2\n", [_cap_row("Halloween")])
        assert code == 2
        assert out == ""
        assert "no title column" in err
        assert "Foo" in err
        assert len(err.strip().splitlines()) == 1

    def test_exit_2_when_csv_missing(self, tmp_path):
        class _Null:
            def write(self, text: str) -> int:
                return len(text)

            def flush(self) -> None:
                return None

        old = sys.stderr
        sys.stderr = _Null()
        try:
            code = diffmod.main(
                [str(tmp_path / "absent.csv")],
                load_plan_inputs=lambda url: _plan_inputs([]),
                load_structure_titles=lambda url: {},
            )
        finally:
            sys.stderr = old
        assert code == 2

    def test_json_shape(self, tmp_path):
        code, out, _err = _run(
            tmp_path,
            "Title,Type\nHalloween,Event\nGone,Task\n",
            [_cap_row("Halloween"), _cap_row("Stray")],
            extra_args=["--json"],
        )
        assert code == 1
        payload = json.loads(out)
        assert set(payload) == {"summary", "missing", "extra", "mismatched"}
        assert payload["summary"] == {"csv": 2, "tdtb": 2, "matched": 1, "duplicates": 0}
        assert payload["missing"] == [{"title": "Gone", "type": "Task"}]
        assert payload["extra"] == [
            {"title": "Stray", "type": "Event", "reason": "auto-status-active"}
        ]
        assert payload["mismatched"] == []

    def test_unreachable_server_exits_3_without_traceback(self, tmp_path):
        path = _write_csv(tmp_path / "q.csv", "Title\nHalloween\n")

        class _Err:
            def __init__(self) -> None:
                self.parts: list[str] = []

            def write(self, text: str) -> int:
                self.parts.append(text)
                return len(text)

            def flush(self) -> None:
                return None

        def _boom(url: str) -> dict:
            raise diffmod.ServerError("capacities_csv_diff: cannot read /plan-inputs: refused")

        err = _Err()
        old_err = sys.stderr
        sys.stderr = err
        try:
            code = diffmod.main([str(path)], load_plan_inputs=_boom)
        finally:
            sys.stderr = old_err
        text = "".join(err.parts)
        assert code == 3
        assert "refused" in text
        assert "Traceback" not in text
        assert len(text.strip().splitlines()) == 1

    def test_unexpected_loader_exception_also_exits_3(self, tmp_path):
        path = _write_csv(tmp_path / "q.csv", "Title\nHalloween\n")

        class _Null:
            def write(self, text: str) -> int:
                return len(text)

            def flush(self) -> None:
                return None

        def _boom(url: str) -> dict:
            raise OSError("connection refused")

        old = sys.stderr
        sys.stderr = _Null()
        try:
            code = diffmod.main([str(path)], load_plan_inputs=_boom)
        finally:
            sys.stderr = old
        assert code == 3

    def test_structure_title_failure_falls_back_to_short_id(self, tmp_path):
        path = _write_csv(tmp_path / "q.csv", "Title\nNothing\n")

        class _Sink:
            def __init__(self) -> None:
                self.parts: list[str] = []

            def write(self, text: str) -> int:
                self.parts.append(text)
                return len(text)

            def flush(self) -> None:
                return None

        def _boom(url: str) -> dict:
            raise OSError("settings route unavailable")

        out = _Sink()
        old_out = sys.stdout
        sys.stdout = out
        try:
            code = diffmod.main(
                [str(path)],
                load_plan_inputs=lambda url: _plan_inputs([_cap_row("Stray")]),
                load_structure_titles=_boom,
            )
        finally:
            sys.stdout = old_out
        text = "".join(out.parts)
        assert code == 1
        assert "Stray  [e16f8eeb]" in text
