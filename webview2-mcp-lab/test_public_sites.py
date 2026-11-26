import unittest
from public_sites import page_catalog, validate_public_page, validate_public_snapshot

class PublicSiteCriteriaTest(unittest.TestCase):
    def record(self,url="https://github.com/ChromeDevTools/chrome-devtools-mcp",id=1):
        return {"id":id,"title":"chrome-devtools-mcp","url":url,"selected":True}
    def result(self,*records):
        return {"structuredContent":{"pages":list(records)}}
    def test_original_structured_target(self):
        record=self.record()
        self.assertEqual(validate_public_page(self.result(record),1,{"github.com"}),record)
    def test_reject_extra_page_and_wrong_target(self):
        for records in [(self.record(id=2),),(self.record(),self.record(id=2))]:
            with self.assertRaisesRegex(RuntimeError,"not_original_visible_target"):
                validate_public_page(self.result(*records),1,{"github.com"})
    def test_reject_nonmatching_origin(self):
        for url in ("https://github.com.evil/","http://github.com/","https://user@github.com/","https://github.com:444/"):
            with self.assertRaisesRegex(RuntimeError,"origin_mismatch"):
                validate_public_page(self.result(self.record(url)),1,{"github.com"})
    def test_no_title_or_text_guess_for_catalog(self):
        with self.assertRaisesRegex(RuntimeError,"structured_pages_missing"):
            page_catalog({"content":[{"type":"text","text":"1: Github (https://github.com)"}]})
    def test_requires_actual_accessible_content(self):
        snapshot='uid=1_0 RootWebArea "GitHub chrome-devtools-mcp"\n'+"\n".join(f"  uid=1_{n} link" for n in range(1,6))
        self.assertEqual(validate_public_snapshot(snapshot,("chrome-devtools-mcp",)),6)
        with self.assertRaisesRegex(RuntimeError,"expected_content_missing"):
            validate_public_snapshot(snapshot,("IWXA",))
        with self.assertRaisesRegex(RuntimeError,"accessibility_content_missing"):
            validate_public_snapshot('uid=1_0 RootWebArea "chrome-devtools-mcp"',("chrome-devtools-mcp",))
if __name__=="__main__":unittest.main()
