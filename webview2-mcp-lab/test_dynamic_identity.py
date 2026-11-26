import unittest
from tab_identity import attach_tab, detach_tab, rebind_pages

class DynamicIdentityTest(unittest.TestCase):
    def setUp(self):
        self.binding = {"guest": "A", "guest-2": "B"}
        self.pages = [{"id": 11, "url": "same"}, {"id": 12, "url": "same"}, {"id": 13, "url": "bootstrap-C"}]
        self.ids = {11: "B", 12: "A", 13: "C"}

    def test_attach_preserves_original_identity(self):
        result = attach_tab(self.binding, "guest-3", "bootstrap-C", self.pages, self.ids)
        self.assertEqual(result, {**self.binding, "guest-3": "C"})
        self.assertEqual(rebind_pages(result, self.pages, self.ids), {"guest": 12, "guest-2": 11, "guest-3": 13})
        self.assertNotIn("guest-3", self.binding)

    def test_attach_rejects_duplicate_label(self):
        with self.assertRaisesRegex(RuntimeError, "already_bound"):
            attach_tab(self.binding, "guest", "bootstrap-C", self.pages, self.ids)

    def test_attach_rejects_unexpected_removal(self):
        with self.assertRaisesRegex(RuntimeError, "generation_changed"):
            attach_tab(self.binding, "guest-3", "bootstrap-C", self.pages[1:], {12: "A", 13: "C"})

    def test_attach_rejects_extra_page(self):
        with self.assertRaisesRegex(RuntimeError, "generation_changed"):
            attach_tab(self.binding, "guest-3", "bootstrap-C", self.pages + [{"id": 14, "url": "other"}], {**self.ids, 14: "D"})

    def test_attach_rejects_wrong_bootstrap(self):
        with self.assertRaisesRegex(RuntimeError, "bootstrap_not_found"):
            attach_tab(self.binding, "guest-3", "wrong", self.pages, self.ids)

    def test_attach_rejects_ambiguous_bootstrap(self):
        pages = [{**self.pages[0], "url": "bootstrap-C"}, *self.pages[1:]]
        with self.assertRaisesRegex(RuntimeError, "bootstrap_not_found"):
            attach_tab(self.binding, "guest-3", "bootstrap-C", pages, self.ids)

    def test_detach_confirms_exact_disappearance(self):
        binding = attach_tab(self.binding, "guest-3", "bootstrap-C", self.pages, self.ids)
        self.assertEqual(detach_tab(binding, "guest-3", self.pages[:2], {11: "B", 12: "A"}), self.binding)
        self.assertIn("guest-3", binding)

    def test_detach_rejects_wrong_disappearance(self):
        binding = {**self.binding, "guest-3": "C"}
        with self.assertRaisesRegex(RuntimeError, "generation_changed"):
            detach_tab(binding, "guest-3", self.pages[1:], {12: "A", 13: "C"})

    def test_detach_rejects_same_count_replacement(self):
        binding = {**self.binding, "guest-3": "C"}
        with self.assertRaisesRegex(RuntimeError, "generation_changed"):
            detach_tab(binding, "guest-3", self.pages[:2], {11: "B", 12: "D"})

    def test_detach_rejects_missing_label_and_last_tab(self):
        for binding, label in [(self.binding, "guest-3"), ({"guest": "A"}, "guest")]:
            with self.assertRaisesRegex(RuntimeError, "close_invalid"):
                detach_tab(binding, label, [], {})

if __name__ == "__main__":
    unittest.main()
