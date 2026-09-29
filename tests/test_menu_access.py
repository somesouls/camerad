import unittest

import db.menu_catalog as mc


class MenuAccessEmptyRoleTest(unittest.TestCase):
    def setUp(self):
        self.original_cache = mc._CACHE
        self.original_areas = mc._MENU_AREA
        mc._MENU_AREA = {key: "common" for key in mc.MENU_KEYS}

    def tearDown(self):
        mc._CACHE = self.original_cache
        mc._MENU_AREA = self.original_areas

    def test_explicit_empty_role_does_not_fall_back(self):
        mc._CACHE = {
            "loaded": True,
            "role_menus": {"limited": {mc._CONFIG_MARKER}},
            "user_menus": {},
        }
        self.assertTrue(mc.role_configured("limited"))
        self.assertFalse(mc.menu_allowed("limited", "m_sosmed_monitor"))
        self.assertTrue(mc.menu_allowed("limited", "m_studio"))
        info = mc.get_role_menus("limited")
        self.assertTrue(info["configured"])
        self.assertEqual(info["menus"], [])

    def test_unconfigured_role_still_uses_legacy_fallback(self):
        mc._CACHE = {"loaded": True, "role_menus": {}, "user_menus": {}}
        original = mc.usr.area_allowed
        mc.usr.area_allowed = lambda role, area, user_id=None: True
        try:
            self.assertTrue(mc.menu_allowed("legacy", "m_sosmed_monitor"))
        finally:
            mc.usr.area_allowed = original


if __name__ == "__main__":
    unittest.main()
