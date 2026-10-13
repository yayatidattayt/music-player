import unittest

from app.api.playlists import combine_analysis_matches, parse_public_analysis, song_search_variants


class SongAnalysisMatchingTests(unittest.TestCase):
    def test_public_parser_accepts_both_bpm_formats(self):
        self.assertEqual(parse_public_analysis("BPM: 120 · Key: C# minor"), (120.0, "C# minor"))
        self.assertEqual(parse_public_analysis("120 BPM in A major"), (120.0, "A major"))

    def test_consensus_handles_half_time_reports(self):
        result = combine_analysis_matches([
            {"bpm": 120, "key": "C major"},
            {"bpm": 60, "key": "C major"},
        ])
        self.assertEqual(result["bpm"], 120.0)
        self.assertEqual(result["key"], "C major")

    def test_search_variants_remove_video_branding(self):
        variants = song_search_variants(
            "Elley Duhé - MIDDLE OF THE NIGHT (Official Video)",
            "Elley Duhé Topic",
        )
        self.assertTrue(any("MIDDLE OF THE NIGHT" in variant for variant in variants))
        self.assertFalse(any("Official Video" in variant for variant in variants[:2]))


if __name__ == "__main__":
    unittest.main()
