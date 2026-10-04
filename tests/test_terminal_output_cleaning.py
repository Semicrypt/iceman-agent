import unittest

from iceman.presentation import _terminal_text


class TerminalOutputCleaningTests(unittest.TestCase):
    def test_ansi_is_removed_and_rich_markup_is_escaped(self):
        value = "\x1b[32mSuccess!\x1b[0m *.py[cod]"
        cleaned = _terminal_text(value)
        self.assertNotIn("\x1b", cleaned)
        self.assertIn("Success!", cleaned)
        self.assertIn(r"\[cod]", cleaned)


if __name__ == "__main__":
    unittest.main()
