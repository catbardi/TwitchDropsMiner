from __future__ import annotations

import unittest

from utils import autocomplete_matches


class AutocompleteMatchesTests(unittest.TestCase):
    GAMES = {
        "Call of Duty: Warzone",
        "Warface",
        "Warframe",
        "World of Warcraft",
        "The War Within",
    }

    def test_matches_are_case_insensitive(self) -> None:
        self.assertIn("Warframe", autocomplete_matches(self.GAMES, "WARF"))

    def test_prefix_matches_are_listed_before_word_matches(self) -> None:
        matches = autocomplete_matches(self.GAMES, "war")
        self.assertEqual(matches[:2], ["Warface", "Warframe"])
        self.assertIn("Call of Duty: Warzone", matches[2:])

    def test_does_not_match_inside_a_word(self) -> None:
        choices = {"Star Wars: The Old Republic", "PUBG: BATTLEGROUNDS"}
        self.assertEqual(autocomplete_matches(choices, "pub"), ["PUBG: BATTLEGROUNDS"])

    def test_whitespace_is_ignored(self) -> None:
        self.assertEqual(autocomplete_matches(self.GAMES, "  warframe  "), ["Warframe"])

    def test_result_limit_is_respected(self) -> None:
        self.assertEqual(len(autocomplete_matches(self.GAMES, "war", limit=3)), 3)


if __name__ == "__main__":
    unittest.main()
