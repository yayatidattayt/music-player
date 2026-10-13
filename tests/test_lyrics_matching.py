import unittest

from app.api.playlists import (
    lyric_artist_candidates,
    lyric_candidates,
    normalize_lyric_text,
)


class LyricsMatchingTests(unittest.TestCase):
    def test_normalization_handles_accents_and_punctuation(self):
        self.assertEqual(normalize_lyric_text("Beyoncé — Halo!"), "beyonce halo")

    def test_title_candidates_strip_youtube_labels(self):
        candidates = lyric_candidates(
            "Elley Duhé - MIDDLE OF THE NIGHT (Official Video)",
            "Elley Duhé Topic",
        )
        self.assertEqual(candidates[0], "MIDDLE OF THE NIGHT")
        self.assertIn("Elley Duhé - MIDDLE OF THE NIGHT", candidates)

    def test_artist_candidates_split_collaborators_but_keep_combined_first(self):
        candidates = lyric_artist_candidates("ZAYN/Sia Topic")
        self.assertEqual(candidates[0], "ZAYN/Sia")
        self.assertIn("ZAYN", candidates)
        self.assertIn("Sia", candidates)


if __name__ == "__main__":
    unittest.main()
