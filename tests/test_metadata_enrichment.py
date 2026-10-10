import json
import unittest
from unittest.mock import MagicMock, patch

from app.api.playlists import lookup_cover_art


class CatalogMetadataTests(unittest.TestCase):
    def mock_catalog_response(self, results):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({"results": results}).encode()
        return response

    @patch("app.api.playlists.urlopen")
    def test_lookup_returns_recording_metadata_for_a_title_only_match(self, urlopen):
        urlopen.return_value = self.mock_catalog_response([{
            "trackName": "Dandelions",
            "artistName": "Ruth B.",
            "collectionName": "Safe Haven",
            "primaryGenreName": "Pop",
            "artworkUrl100": "https://example.test/100x100bb.jpg",
        }])

        result = lookup_cover_art("Dandelions", None)

        self.assertEqual(result["title"], "Dandelions")
        self.assertEqual(result["artist"], "Ruth B.")
        self.assertEqual(result["album"], "Safe Haven")
        self.assertEqual(result["genre"], "Pop")
        self.assertEqual(result["cover_url"], "https://example.test/600x600bb.jpg")

    @patch("app.api.playlists.urlopen")
    def test_title_only_lookup_rejects_a_weak_match(self, urlopen):
        urlopen.return_value = self.mock_catalog_response([{
            "trackName": "Dance",
            "artistName": "Unknown Artist",
            "artworkUrl100": "https://example.test/100x100bb.jpg",
        }])

        self.assertEqual(lookup_cover_art("Dandelions", None), {})

    @patch("app.api.playlists.urlopen")
    def test_short_title_is_enriched_when_artist_is_a_strong_match(self, urlopen):
        urlopen.return_value = self.mock_catalog_response([{
            "trackName": "GO",
            "artistName": "The Kid LAROI, Juice WRLD",
            "collectionName": "F*CK LOVE 3: OVER YOU",
        }])

        result = lookup_cover_art("GO", "Juice WRLD")

        self.assertEqual(result["title"], "GO")
        self.assertEqual(result["artist"], "The Kid LAROI, Juice WRLD")


if __name__ == "__main__":
    unittest.main()
