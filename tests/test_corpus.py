import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401
from reportgen.corpus import load_corpus, parse_front_matter, split_document

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "corpus"


class FrontMatterTests(unittest.TestCase):
    def test_parses_metadata(self):
        meta, body = parse_front_matter("---\ntitle: Книга\nyear: 2019\n---\n# Заголовок\n")
        self.assertEqual(meta, {"title": "Книга", "year": "2019"})
        self.assertTrue(body.startswith("# Заголовок"))

    def test_absent_front_matter(self):
        meta, body = parse_front_matter("# Заголовок\n")
        self.assertEqual(meta, {})
        self.assertEqual(body, "# Заголовок\n")


class SplitTests(unittest.TestCase):
    def test_tracks_heading_path(self):
        doc = "# Книга\n\nВведение.\n\n## Глава 1\n\nТело.\n\n### Раздел\n\nЕщё тело.\n"
        paths = [path for path, _ in split_document(doc)]
        self.assertEqual(paths[-1], ["Книга", "Глава 1", "Раздел"])

    def test_long_section_is_split_with_overlap(self):
        paragraph = ("Абзац. " * 60).strip()
        doc = "# Книга\n\n" + "\n\n".join([paragraph] * 8)
        pieces = [text for _, text in split_document(doc)]
        self.assertGreater(len(pieces), 1)
        self.assertTrue(any(pieces[0].endswith(p) for p in [paragraph]))


class LoadCorpusTests(unittest.TestCase):
    def test_loads_examples_with_types(self):
        chunks = load_corpus(EXAMPLES)
        self.assertTrue(chunks)
        types = {chunk.doc_type for chunk in chunks}
        self.assertEqual(types, {"literature", "standards", "reports", "regulations"})

    def test_skips_files_in_root(self):
        chunks = load_corpus(EXAMPLES)
        self.assertFalse([c for c in chunks if c.doc_id == "README"])

    def test_indexed_text_contains_breadcrumbs(self):
        chunk = load_corpus(EXAMPLES)[0]
        self.assertIn(chunk.breadcrumbs, chunk.indexed_text)

    def test_citation_does_not_repeat_document_title(self):
        chunk = next(c for c in load_corpus(EXAMPLES) if len(c.title_path) > 1)
        title = chunk.meta["title"]
        self.assertTrue(chunk.citation.startswith(title))
        # Название документа встречается в ссылке ровно один раз.
        self.assertEqual(chunk.citation.count(title), 1)
        self.assertIn(chunk.title_path[1], chunk.citation)

    def test_citation_includes_page_when_known(self):
        chunk = load_corpus(EXAMPLES)[0]
        chunk.meta["page"] = 42
        self.assertIn("с. 42", chunk.citation)

    def test_citation_without_subsections(self):
        from reportgen.corpus import Chunk

        chunk = Chunk("d#0", "d", "literature", ["Книга"], "текст", {"title": "Книга"})
        self.assertEqual(chunk.citation, "Книга")

    def test_missing_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                load_corpus(Path(tmp) / "нет-такого")


if __name__ == "__main__":
    unittest.main()
