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

    def test_removes_unwrapped_official_audio_suffix(self):
        self.assertEqual(
            clean_imported_title("Indila - Love Story Official Audio", "Indila"),
            "Love Story",
        )

    def test_preserves_meaningful_parenthetical_title_details(self):
        self.assertEqual(
            clean_imported_title("Dusk Till Dawn (feat. Sia) - Radio Edit", "ZAYN/Sia"),
            "Dusk Till Dawn (feat. Sia) - Radio Edit",
        )

    def test_preserves_remix_and_edit_suffixes(self):
        self.assertEqual(
            clean_imported_title("Despacito - Remix", "Luis Fonsi/Daddy Yankee/Justin Bieber"),
            "Despacito - Remix",
        )


if __name__ == "__main__":
    unittest.main()
