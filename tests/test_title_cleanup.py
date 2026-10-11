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

    def test_removes_live_version_suffix(self):
        self.assertEqual(
            clean_imported_title("Love Story - Live", "Indila"),
            "Love Story",
        )

    def test_removes_unwrapped_official_audio_suffix(self):
        self.assertEqual(
            clean_imported_title("Indila - Love Story Official Audio", "Indila"),
            "Love Story",
        )

    def test_removes_collaborators_and_version_details(self):
        self.assertEqual(
            clean_imported_title("Dusk Till Dawn (feat. Sia) - Radio Edit", "ZAYN/Sia"),
            "Dusk Till Dawn",
        )

    def test_removes_remix_suffix(self):
        self.assertEqual(
            clean_imported_title("Despacito - Remix", "Luis Fonsi/Daddy Yankee/Justin Bieber"),
            "Despacito",
        )

    def test_removes_youtube_details_after_title_and_featured_artists(self):
        self.assertEqual(
            clean_imported_title(
                "SUBEME LA RADIO (Official Video) ft. Descemer Bueno, Zion & Lennox",
                "Enrique Iglesias",
            ),
            "SUBEME LA RADIO",
        )


if __name__ == "__main__":
    unittest.main()
