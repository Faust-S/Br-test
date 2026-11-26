"""Regression for the exact W067.1 accessibility evidence."""
import unittest
from snapshot_refs import uid

# The six lines below preserve the actual IDs and names from 03-human-input.txt.
CAPTURE = """## Latest page snapshot
uid=1_0 RootWebArea "IWXA browser fixture"
  uid=1_2 "Human entry"
  uid=1_3 textbox "Human entry" focusable focused value="Human-167"
  uid=1_4 "Agent entry"
  uid=1_5 textbox "Agent entry"
  uid=1_6 button "Save by agent"
  uid=1_7 button "Finish by human"
"""

class SnapshotRefsTest(unittest.TestCase):
    def test_selects_textbox_not_static_label(self):
        self.assertEqual(uid(CAPTURE,"textbox","Agent entry"),"1_5")
    def test_selects_human_textbox(self):
        self.assertEqual(uid(CAPTURE,"textbox","Human entry"),"1_3")
    def test_selects_button(self):
        self.assertEqual(uid(CAPTURE,"button","Save by agent"),"1_6")
    def test_label_alone_not_actionable(self):
        with self.assertRaisesRegex(RuntimeError,"found 0"):
            uid('uid=1_4 "Agent entry"',"textbox","Agent entry")
    def test_exact_name_not_substring(self):
        with self.assertRaisesRegex(RuntimeError,"found 0"):
            uid(CAPTURE,"textbox","Agent")
    def test_value_not_confused_with_accessible_name(self):
        with self.assertRaisesRegex(RuntimeError,"found 0"):
            uid('uid=2_1 textbox "Other" value="Agent entry"',"textbox","Agent entry")
    def test_duplicate_accessible_target_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError,"found 2"):
            uid(CAPTURE+'\nuid=2_5 textbox "Agent entry"',"textbox","Agent entry")
    def test_wrong_role_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError,"found 0"):
            uid(CAPTURE,"button","Agent entry")
    def test_invalid_role(self):
        with self.assertRaises(ValueError):
            uid(CAPTURE,"textbox .*","Agent entry")

if __name__ == "__main__":
    unittest.main()
