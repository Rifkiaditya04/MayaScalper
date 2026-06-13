from __future__ import annotations

import unittest

from tests.harness import PROFILE_MODULES, build_suite, iter_test_ids


class TestTSPHarness(unittest.TestCase):
    def test_all_profile_contains_every_registered_module(self) -> None:
        suite = build_suite("all")
        ids = iter_test_ids(suite)

        self.assertGreater(len(ids), 0)
        for module_name in PROFILE_MODULES["all"]:
            self.assertTrue(
                any(test_id.startswith(module_name) for test_id in ids),
                msg=f"profile all missing tests from {module_name}",
            )

    def test_smoke_profile_is_subset_of_all(self) -> None:
        smoke_ids = set(iter_test_ids(build_suite("smoke")))
        all_ids = set(iter_test_ids(build_suite("all")))

        self.assertTrue(smoke_ids)
        self.assertTrue(smoke_ids.issubset(all_ids))

    def test_unknown_profile_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            build_suite("unknown-profile")


if __name__ == "__main__":
    unittest.main()
