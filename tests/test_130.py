import unittest
from dynosai_flow.version import __version__
from dynosai_flow.db import Database


class StableReleaseCompatibilityTests(unittest.TestCase):
    def test_release_version(self):
        self.assertEqual(__version__, "0.15.0")

    def test_schema_remains_v6(self):
        self.assertEqual(Database.CURRENT_SCHEMA_VERSION, 6)

    def test_product_patch_does_not_bump_authority_schema(self):
        # 0.15.0 keeps the established schema-v6 authority model; harness artifacts stay in runtime files.
        self.assertLessEqual(Database.CURRENT_SCHEMA_VERSION, 6)


if __name__ == "__main__":
    unittest.main()
