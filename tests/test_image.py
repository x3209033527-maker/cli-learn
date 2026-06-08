import base64
import tempfile
import unittest
from pathlib import Path

from paicli_py.image import ImageInputExpander, parse_image_references


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


class ImageInputTest(unittest.TestCase):
    def test_parse_image_references(self):
        refs = parse_image_references("inspect @image:img/a.png and @image:b.webp")

        self.assertEqual(["img/a.png", "b.webp"], [ref.path for ref in refs])

    def test_expander_loads_local_image_and_adds_summary_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "img.png").write_bytes(PNG_1X1)

            text, images = ImageInputExpander(root).expand("describe @image:img.png")

            self.assertIn("<images>", text)
            self.assertIn("img.png (image/png", text)
            self.assertEqual(1, len(images))
            self.assertEqual("image/png", images[0].mime_type)
            self.assertTrue(images[0].data_url().startswith("data:image/png;base64,"))

    def test_expander_reports_missing_or_unsupported_images_without_attaching(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "note.txt").write_text("not image", encoding="utf-8")

            text, images = ImageInputExpander(root).expand("see @image:missing.png @image:note.txt")

            self.assertEqual([], images)
            self.assertIn("missing.png (error:", text)
            self.assertIn("unsupported image type", text)

    def test_expander_rejects_paths_outside_project_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            text, images = ImageInputExpander(root).expand("see @image:../outside.png")

            self.assertEqual([], images)
            self.assertIn("path escapes project root", text)


if __name__ == "__main__":
    unittest.main()
