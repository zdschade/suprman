"""Configuration parsing, validation and stylesheet rendering."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from suprman import config


class TempConfigTest(unittest.TestCase):
    """Base class that hands out throwaway config files."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def write_config(self, text: str, name: str = "suprman.conf") -> Path:
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        return path


class LoadTest(TempConfigTest):
    def test_missing_file_yields_defaults(self) -> None:
        cfg = config.load(self.tmp / "nope.conf")
        self.assertEqual(cfg.appearance.width, config.Appearance().width)
        self.assertEqual(cfg.behavior.max_results, config.Behavior().max_results)
        self.assertIsNone(cfg.path)

    def test_overrides_are_read(self) -> None:
        path = self.write_config(
            """
            [appearance]
            background = #101010
            opacity = 0.5
            width = 900
            [behavior]
            max_results = 42
            tldr = false
            man_sections = 1, 8
            """
        )
        cfg = config.load(path)
        self.assertEqual(cfg.appearance.background, "#101010")
        self.assertAlmostEqual(cfg.appearance.opacity, 0.5)
        self.assertEqual(cfg.appearance.width, 900)
        self.assertEqual(cfg.behavior.max_results, 42)
        self.assertFalse(cfg.behavior.tldr)
        self.assertEqual(cfg.behavior.section_list(), ("1", "8"))
        self.assertEqual(cfg.path, path)

    def test_unknown_keys_are_ignored(self) -> None:
        path = self.write_config("[appearance]\nnot_a_key = 1\n")
        cfg = config.load(path)
        self.assertEqual(cfg.appearance.background, config.Appearance().background)

    def test_broken_values_fall_back(self) -> None:
        path = self.write_config(
            """
            [appearance]
            background = rebeccapurple
            opacity = 3.5
            width = -4
            [behavior]
            max_results = zero
            layer_shell = sometimes
            """
        )
        cfg = config.load(path)
        defaults = config.Appearance()
        self.assertEqual(cfg.appearance.background, defaults.background)
        self.assertEqual(cfg.appearance.opacity, 1.0)
        self.assertEqual(cfg.appearance.width, defaults.width)
        self.assertEqual(cfg.behavior.max_results, config.Behavior().max_results)
        self.assertEqual(cfg.behavior.layer_shell, "auto")

    def test_malformed_file_does_not_raise(self) -> None:
        path = self.write_config("this is not an ini file\n")
        self.assertIsInstance(config.load(path), config.Config)

    def test_man_width_floor(self) -> None:
        path = self.write_config("[behavior]\nman_width = 10\n")
        self.assertEqual(config.load(path).behavior.man_width, 80)

    def test_boolean_spellings(self) -> None:
        for spelling in ("yes", "on", "1", "TRUE"):
            path = self.write_config(f"[behavior]\ntldr = {spelling}\n")
            self.assertTrue(config.load(path).behavior.tldr, spelling)
        for spelling in ("no", "off", "0", "False"):
            path = self.write_config(f"[behavior]\ntldr = {spelling}\n")
            self.assertFalse(config.load(path).behavior.tldr, spelling)


class ColourTest(unittest.TestCase):
    def test_hex_to_rgba(self) -> None:
        self.assertEqual(config.hex_to_rgba("#000000", 0.5), "rgba(0, 0, 0, 0.500)")
        self.assertEqual(config.hex_to_rgba("#fff", 1.0), "rgba(255, 255, 255, 1.000)")
        self.assertEqual(config.hex_to_rgba("#7aa2f780", 0.25), "rgba(122, 162, 247, 0.250)")


class FontTest(unittest.TestCase):
    def test_plain_description(self) -> None:
        spec = config.parse_font("Sans 11", config.FontSpec("Sans", 10))
        self.assertEqual((spec.family, spec.size, spec.weight), ("Sans", 11.0, 400))

    def test_weight_and_style(self) -> None:
        spec = config.parse_font("Cantarell Bold Italic 12", config.FontSpec("Sans", 10))
        self.assertEqual(spec.family, "Cantarell")
        self.assertEqual(spec.weight, 700)
        self.assertEqual(spec.style, "italic")
        self.assertEqual(spec.size, 12.0)

    def test_multi_word_family(self) -> None:
        spec = config.parse_font("DejaVu Sans Mono 9.5", config.FontSpec("Monospace", 10))
        self.assertEqual(spec.family, "DejaVu Sans Mono")
        self.assertEqual(spec.size, 9.5)

    def test_empty_falls_back(self) -> None:
        fallback = config.FontSpec("Sans", 9)
        self.assertEqual(config.parse_font("   ", fallback), fallback)

    def test_quoted_family_in_css(self) -> None:
        spec = config.FontSpec("DejaVu Sans Mono", 10)
        self.assertEqual(spec.declare("key")["key_family"], '"DejaVu Sans Mono"')


class StylesheetTest(unittest.TestCase):
    def test_every_placeholder_is_substituted(self) -> None:
        css = config.build_css(config.Config())
        self.assertNotIn("$", css)
        self.assertIn("rgba(11, 11, 16, 0.880)", css)
        self.assertIn("border-radius: 14px", css)

    def test_custom_appearance_reaches_the_stylesheet(self) -> None:
        cfg = config.Config(
            appearance=config.Appearance(accent="#ff0000", radius=4, font="Monospace 14")
        )
        css = config.build_css(cfg)
        self.assertIn("#ff0000", css)
        self.assertIn("border-radius: 4px", css)
        self.assertIn("font-size: 14pt", css)


class ExampleConfigTest(TempConfigTest):
    def test_example_config_parses_to_defaults(self) -> None:
        path = self.write_config(config.EXAMPLE_CONFIG)
        cfg = config.load(path)
        self.assertEqual(cfg.appearance, config.Appearance())
        self.assertEqual(cfg.behavior, config.Behavior())

    def test_docs_copy_matches_generated_example(self) -> None:
        docs = Path(__file__).resolve().parent.parent / "docs" / "config.example.conf"
        self.assertTrue(docs.is_file(), "docs/config.example.conf is missing")
        self.assertEqual(docs.read_text(encoding="utf-8"), config.EXAMPLE_CONFIG)

    def test_write_example_config_does_not_clobber(self) -> None:
        target = self.tmp / "suprman.conf"
        config.write_example_config(target)
        target.write_text("[appearance]\nwidth = 800\n", encoding="utf-8")
        config.write_example_config(target)
        self.assertIn("width = 800", target.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
