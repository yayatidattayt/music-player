import unittest

from app.api.playlists import clean_imported_title


class ImportedTitleCleanupTests(unittest.TestCase):
    def test_removes_exact_artist_prefix(self):
        self.assertEqual(
            clean_imported_title("Indila - Love Story (Official Music Video)", "Indila"),
            "Love Story",
        )

    def test_removes_artist_prefix_when_youtube_adds_topic_suffix(self):
        self.assertEqual(
            clean_imported_title("Indila - Love Story", "Indila Topic"),
            "Love Story",
        )

    def test_keeps_hyphenated_title_when_artist_does_not_match(self):
        self.assertEqual(
            clean_imported_title("Love Story - Live", "Indila"),
            "Love Story - Live",
        )


if __name__ == "__main__":
    unittest.main()
