import unittest
from tab_identity import bind_bootstrap,rebind_pages,stable_tab_id

class TabIdentityTest(unittest.TestCase):
    def setUp(self):
        self.urls={"guest":"http://fixture/tab-a","guest-2":"http://fixture/tab-b"}
        self.pages=[{"id":1,"url":self.urls["guest"]},{"id":2,"url":self.urls["guest-2"]}]
        self.ids={1:"stable-A",2:"stable-B"}
        self.binding=bind_bootstrap(self.urls,self.pages,self.ids)
    def test_initial_identity_from_unique_bootstrap(self):
        self.assertEqual(self.binding,{"guest":"stable-A","guest-2":"stable-B"})
    def test_same_address_after_navigation_and_reconnect(self):
        pages=[{"id":7,"url":"https://example.com/"},{"id":8,"url":"https://example.com/"}]
        self.assertEqual(rebind_pages(self.binding,pages,{7:"stable-B",8:"stable-A"}),{"guest":8,"guest-2":7})
    def test_missing_native_tab_does_not_guess(self):
        with self.assertRaisesRegex(RuntimeError,"generation_changed"):
            rebind_pages(self.binding,[{"id":7,"url":"https://example.com/"}],{7:"stable-A"})
    def test_duplicate_original_identity_rejected(self):
        with self.assertRaisesRegex(RuntimeError,"not_unique"):
            rebind_pages(self.binding,self.pages,{1:"stable-A",2:"stable-A"})
    def test_wrong_bootstrap_rejected(self):
        with self.assertRaisesRegex(RuntimeError,"bootstrap_not_found"):
            bind_bootstrap({"guest":"http://fixture/other","guest-2":self.urls["guest-2"]},self.pages,self.ids)
    def test_original_structured_tab_id_only(self):
        self.assertEqual(stable_tab_id({"structuredContent":{"tabId":"abc"}}),"abc")
        with self.assertRaisesRegex(RuntimeError,"tab_id_missing"):
            stable_tab_id({"content":[{"type":"text","text":"Tab ID: abc"}]})
if __name__=="__main__":unittest.main()
