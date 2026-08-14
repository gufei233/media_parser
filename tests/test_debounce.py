import importlib.util
import pathlib
import unittest
from unittest.mock import patch


ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("media_parser_debounce", ROOT / "debounce.py")
debounce_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(debounce_module)
Debouncer = debounce_module.Debouncer


class DebouncerTests(unittest.TestCase):
    def test_release_link_allows_immediate_retry(self):
        debouncer = Debouncer(300)

        self.assertFalse(debouncer.hit_link("session", "https://example.invalid/a"))
        self.assertTrue(debouncer.hit_link("session", "https://example.invalid/a"))

        debouncer.release_link("session", "https://example.invalid/a")

        self.assertFalse(debouncer.hit_link("session", "https://example.invalid/a"))

    def test_old_failure_cannot_release_new_reservation(self):
        debouncer = Debouncer(1)
        link = "https://example.invalid/a"
        with patch.object(debounce_module.time, "time", return_value=100.0):
            hit, old_reservation = debouncer.reserve_link("session", link)
        self.assertFalse(hit)

        with patch.object(debounce_module.time, "time", return_value=102.0):
            hit, new_reservation = debouncer.reserve_link("session", link)
        self.assertFalse(hit)
        self.assertNotEqual(old_reservation, new_reservation)

        debouncer.release_link("session", link, old_reservation)

        self.assertEqual(
            debouncer.link_cache["session"][link], new_reservation
        )

    def test_release_link_is_scoped_and_idempotent(self):
        debouncer = Debouncer(300)
        debouncer.hit_link("session", "https://example.invalid/a")
        debouncer.hit_link("session", "https://example.invalid/b")
        debouncer.hit_link("other", "https://example.invalid/a")

        debouncer.release_link("session", "https://example.invalid/a")
        debouncer.release_link("session", "https://example.invalid/a")

        self.assertFalse(debouncer.hit_link("session", "https://example.invalid/a"))
        self.assertTrue(debouncer.hit_link("session", "https://example.invalid/b"))
        self.assertTrue(debouncer.hit_link("other", "https://example.invalid/a"))


if __name__ == "__main__":
    unittest.main()
