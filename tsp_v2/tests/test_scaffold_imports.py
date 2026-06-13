from __future__ import annotations

import unittest

from tsp_v2 import app
from tsp_v2 import enums
from tsp_v2 import models


class ScaffoldImportTests(unittest.TestCase):
    def test_scaffold_modules_import(self) -> None:
        self.assertIsNotNone(app)
        self.assertEqual(enums.GovernorState.NORMAL.value, "NORMAL")
        self.assertIsNotNone(models.RuntimeState)


if __name__ == "__main__":
    unittest.main()
