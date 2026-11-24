"""Appearance token contracts independent of Qt."""
import unittest

from dubsync.gui.appearance import appearance_colors, normalize_appearance


def luminance(hex_color):
    rgb = [int(hex_color[i:i+2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in rgb]
    return sum(c * w for c, w in zip(linear, (.2126, .7152, .0722)))


def contrast(a, b):
    first, second = sorted((luminance(a), luminance(b)))
    return (second + .05) / (first + .05)


class AppearanceTests(unittest.TestCase):
    def test_invalid_preferences_default_to_light(self):
        for value in (None, [], {}, 42, "sepia"):
            self.assertEqual(normalize_appearance(value), "light")

    def test_semantic_text_contrast(self):
        for mode in ("light", "dark"):
            for high in (False, True):
                colors = appearance_colors(mode, high)
                for fg, bg in (("text", "canvas"), ("text", "surface"),
                               ("text", "selection"), ("muted", "surface"),
                               ("accent", "canvas"), ("ink", "accent"),
                               ("warning", "raised"), ("danger", "surface"),
                               ("success", "canvas")):
                    with self.subTest(mode=mode, high=high, fg=fg, bg=bg):
                        self.assertGreaterEqual(contrast(colors[fg], colors[bg]), 4.5)

    def test_palette_isolation(self):
        colors = appearance_colors("dark")
        colors["text"] = "#000000"
        self.assertNotEqual(appearance_colors("dark")["text"], colors["text"])
        self.assertEqual(set(appearance_colors("dark")), set(appearance_colors("light", True)))


if __name__ == "__main__":
    unittest.main()
