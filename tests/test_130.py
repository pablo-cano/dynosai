import json
import unittest
from dynosai_flow.version import __version__
from dynosai_flow.db import Database


class StableRelease0130Tests(unittest.TestCase):
    def test_stable_version(self):
        self.assertEqual(__version__, "0.13.0")

    def test_schema_remains_v6(self):
        self.assertEqual(Database.CURRENT_SCHEMA_VERSION, 6)

    def test_no_stable_release_schema_bump(self):
        # 0.13.0 is a promotion of the validated RC, not an architecture release.
        self.assertLessEqual(Database.CURRENT_SCHEMA_VERSION, 6)


if __name__ == "__main__":
    unittest.main()
