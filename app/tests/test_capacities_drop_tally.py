"""Drop-tally wording: what a silent admission drop now reports.

Every assertion here is about the *shape* of the diagnostic, not about any
object's content: the tally may name a type label, closed reason codes, and a
count, and nothing else.
"""

from __future__ import annotations

import unittest

import capacities_adapter as ca


class DropTallyWarningTests(unittest.TestCase):
    def test_singular_and_plural_nouns(self):
        one = ca.drop_tally_warning("Project", {ca.DROP_REASON_FUTURE_DATE: 1})
        two = ca.drop_tally_warning("Project", {ca.DROP_REASON_FUTURE_DATE: 2})

        self.assertIn("1 object dropped", one)
        self.assertIn("2 objects dropped", two)
        self.assertIn(ca.DROP_REASON_FUTURE_DATE, one)

    def test_nothing_counted_is_the_empty_string(self):
        self.assertEqual(ca.drop_tally_warning("Project", {}), "")
        self.assertEqual(
            ca.drop_tally_warning("Project", {ca.DROP_REASON_FUTURE_DATE: 0}), ""
        )

    def test_zero_count_codes_are_not_listed(self):
        warning = ca.drop_tally_warning(
            "Project",
            {ca.DROP_REASON_FUTURE_DATE: 2, ca.DROP_REASON_EXCLUDED: 0},
        )

        self.assertNotIn(ca.DROP_REASON_EXCLUDED, warning)

    def test_codes_are_ordered_by_count_then_name(self):
        warning = ca.drop_tally_warning(
            "Project",
            {
                ca.DROP_REASON_EXCLUDED: 1,
                ca.DROP_REASON_CLOSED_STATUS: 3,
                ca.DROP_REASON_FUTURE_DATE: 1,
            },
        )

        self.assertLess(
            warning.index(ca.DROP_REASON_CLOSED_STATUS),
            warning.index(ca.DROP_REASON_FUTURE_DATE),
        )
        self.assertLess(
            warning.index(ca.DROP_REASON_FUTURE_DATE),
            warning.index(ca.DROP_REASON_EXCLUDED),
        )

    def test_the_line_is_bounded(self):
        counts = {f"reason-{index}": 1 for index in range(9)}

        warning = ca.drop_tally_warning("Project", counts)

        self.assertIn("and 3 more", warning)
        self.assertEqual(warning.count("reason-"), ca.MAX_DROP_REASONS_PER_LINE)


class RetainedScopeWarningTests(unittest.TestCase):
    def test_names_the_type_the_count_and_the_remedy(self):
        warning = ca.retained_scope_warning(
            "Purchase", {ca.DROP_REASON_STRUCTURE_UNMAPPED: 4}
        )

        self.assertIn("Purchase", warning)
        self.assertIn("4 objects", warning)
        self.assertIn("retained", warning)
        self.assertIn("not refreshed", warning)
        self.assertIn(ca.DROP_REASON_STRUCTURE_UNMAPPED, warning)

    def test_nothing_retained_is_the_empty_string(self):
        self.assertEqual(ca.retained_scope_warning("Purchase", {}), "")


class StructureNotReadWarningTests(unittest.TestCase):
    def test_says_the_type_was_never_listed(self):
        warning = ca.structure_not_read_warning(
            "Project", ca.DROP_REASON_STRUCTURE_NOT_ELIGIBLE
        )

        self.assertIn("Project", warning)
        self.assertIn("not read", warning)
        self.assertIn("never listed", warning)
        self.assertIn(ca.DROP_REASON_STRUCTURE_NOT_ELIGIBLE, warning)


class StructureLabelTests(unittest.TestCase):
    """The label lookup must survive every payload shape the provider returns."""

    def test_a_mapping_keyed_by_id(self):
        structures = {"custom-project": {"id": "custom-project", "title": "Project"}}

        self.assertEqual(ca.structure_label(structures, "custom-project"), "Project")

    def test_a_list_of_rows(self):
        structures = [
            {"id": "custom-project", "title": "Project"},
            {"id": "custom-print", "title": "Print"},
        ]

        self.assertEqual(ca.structure_label(structures, "custom-print"), "Print")

    def test_a_payload_wrapping_the_rows(self):
        structures = {"structures": [{"id": "custom-project", "name": "Project"}]}

        self.assertEqual(ca.structure_label(structures, "custom-project"), "Project")

    def test_an_unknown_or_titledless_structure_falls_back_to_the_id(self):
        self.assertEqual(ca.structure_label([], "custom-project"), "custom-project")
        self.assertEqual(
            ca.structure_label([{"id": "custom-project"}], "custom-project"),
            "custom-project",
        )


if __name__ == "__main__":
    unittest.main()
