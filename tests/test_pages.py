"""Показ страниц документа картинками.

PDF в окне предпросмотра не открывался вовсе: встроенное окно стоит в
песочнице и с запретом «default-src 'none'» — чужой файл в своей странице
это чужой код в своей странице. Встроенный просмотрщик браузера тоже код, и
запрет глушил его. Человек нажимал на справку-объективку, получал пустой
серый прямоугольник со словами «This page has been blocked by Chromium», ждал
и шёл скачивать файл — отсюда и «долгое скачивание справки».

Снимать запрет нельзя: PDF умеет исполнять свой код. Поэтому страницу рисует
сервер и отдаёт картинкой.
"""

import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401
from fastapi.testclient import TestClient

from reportgen.config import Settings
from reportgen.web.app import create_app
from reportgen.web.pages import (
    DPI,
    PageRenderError,
    is_renderable,
    page_count,
    render_page,
)

ROOT = Path(__file__).resolve().parents[1]
FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf")

PNG_HEAD = b"\x89PNG\r\n\x1a\n"


def make_pdf(path: Path, pages: int = 3, password: str = "") -> Path:
    import pymupdf

    doc = pymupdf.open()
    for number in range(pages):
        page = doc.new_page(width=595, height=842)
        if FONT.is_file():
            page.insert_font(fontname="rus", fontfile=str(FONT))
            page.insert_text((60, 100), f"Страница {number + 1} справки",
                             fontname="rus", fontsize=14)
        else:                                    # pragma: no cover
            page.insert_text((60, 100), f"Page {number + 1}", fontsize=14)
    if password:
        doc.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256,
                 owner_pw=password, user_pw=password)
    else:
        doc.save(str(path))
    doc.close()
    return path


class KindTests(unittest.TestCase):
    def test_what_is_shown_by_pages(self):
        for name in ("справка.pdf", "СКАН.TIFF", "kniga.tif"):
            self.assertTrue(is_renderable(name), name)

    def test_what_is_not(self):
        # DOCX и архив страницами не показать; DjVu MuPDF не открывает.
        for name in ("prikaz.docx", "arhiv.zip", "kniga.djvu", "foto.png"):
            self.assertFalse(is_renderable(name), name)


@unittest.skipUnless(FONT.is_file(), "нет шрифта с кириллицей для сборки PDF")
class RenderTests(unittest.TestCase):
    def setUp(self):
        try:
            import pymupdf                       # noqa: F401
        except ImportError:                      # pragma: no cover
            self.skipTest("нет pymupdf")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = Path(self._tmp.name)
        self.pdf = make_pdf(self.folder / "spravka.pdf", pages=3)
        self.cache = self.folder / "kesh"

    def test_a_page_comes_back_as_a_png(self):
        data = render_page(self.pdf, 1)
        self.assertTrue(data.startswith(PNG_HEAD), "это не PNG")
        self.assertGreater(len(data), 1000)

    def test_every_page_can_be_asked_for(self):
        self.assertEqual(3, page_count(self.pdf))
        for number in (1, 2, 3):
            self.assertTrue(render_page(self.pdf, number).startswith(PNG_HEAD))

    def test_the_pages_are_different(self):
        """Иначе листалка листала бы одну и ту же картинку."""
        first = render_page(self.pdf, 1)
        second = render_page(self.pdf, 2)
        self.assertNotEqual(first, second)

    def test_a_page_that_is_not_there_says_so(self):
        with self.assertRaises(PageRenderError) as caught:
            render_page(self.pdf, 9)
        self.assertIn("3 страниц", str(caught.exception))

    def test_page_zero_is_refused(self):
        with self.assertRaises(PageRenderError):
            render_page(self.pdf, 0)

    def test_a_file_that_is_not_shown_by_pages_says_so(self):
        other = self.folder / "prikaz.docx"
        other.write_bytes(b"PK\x03\x04")
        with self.assertRaises(PageRenderError) as caught:
            render_page(other, 1)
        self.assertIn("страницами", str(caught.exception))

    def test_a_missing_file_says_so(self):
        with self.assertRaises(PageRenderError) as caught:
            render_page(self.folder / "нет.pdf", 1)
        self.assertIn("не найден", str(caught.exception))

    def test_a_password_protected_file_tells_what_to_do(self):
        locked = make_pdf(self.folder / "zakryto.pdf", pages=1, password="тайна")
        self.assertEqual(0, page_count(locked))
        with self.assertRaises(PageRenderError) as caught:
            render_page(locked, 1)
        self.assertIn("паролем", str(caught.exception))

    def test_a_rendered_page_is_kept_and_not_drawn_twice(self):
        """Справку смотрят не один раз, а рисовать её каждый раз незачем."""
        first = render_page(self.pdf, 1, cache_root=self.cache)
        files = list(self.cache.rglob("*.png"))
        self.assertEqual(1, len(files), files)
        # Подменяем содержимое кэша — если вторая отрисовка идёт из него,
        # вернётся подмена, а не заново нарисованная страница.
        files[0].write_bytes(PNG_HEAD + b"podmena")
        again = render_page(self.pdf, 1, cache_root=self.cache)
        self.assertEqual(PNG_HEAD + b"podmena", again)
        self.assertNotEqual(first, again)

    def test_a_replaced_file_is_drawn_afresh(self):
        """Справку заменили новой — старая картинка не должна всплыть."""
        render_page(self.pdf, 1, cache_root=self.cache)
        old = list(self.cache.rglob("*.png"))[0]
        old.write_bytes(PNG_HEAD + b"staraya")
        make_pdf(self.pdf, pages=2)              # тот же путь, другой файл
        fresh = render_page(self.pdf, 1, cache_root=self.cache)
        self.assertNotEqual(PNG_HEAD + b"staraya", fresh)
        self.assertTrue(fresh.startswith(PNG_HEAD))

    def test_the_page_is_big_enough_to_read(self):
        """При мелкой отрисовке страница, растянутая по ширине, мылится."""
        import pymupdf

        with pymupdf.open(str(self.pdf)) as document:
            pixmap = document[0].get_pixmap(dpi=DPI)
        self.assertGreaterEqual(pixmap.width, 1100)

    def test_a_tiff_scan_is_shown_too(self):
        """Ни один браузер TIFF не показывает, а сканы в отделе такие."""
        try:
            from PIL import Image
        except ImportError:                      # pragma: no cover
            self.skipTest("нет Pillow")
        scan = self.folder / "skan.tiff"
        Image.new("RGB", (1000, 1400), "white").save(str(scan), format="TIFF")
        self.assertEqual(1, page_count(scan))
        self.assertTrue(render_page(scan, 1).startswith(PNG_HEAD))


@unittest.skipUnless(FONT.is_file(), "нет шрифта с кириллицей для сборки PDF")
class ThroughTheWebTests(unittest.TestCase):
    """Страницы через сеть: с правами, с числом страниц и с честной ошибкой."""

    def setUp(self):
        try:
            import pymupdf                       # noqa: F401
        except ImportError:                      # pragma: no cover
            self.skipTest("нет pymupdf")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        folder = Path(self._tmp.name)
        settings = Settings.load(
            data_dir=str(folder), db_path=str(folder / "p.db"),
            auth_enabled=True, templates_dir=str(ROOT / "templates"))
        self.app = create_app(settings)
        self.client = TestClient(self.app)
        repos = self.app.state.repos
        self.chief = repos.users.create(
            login="chief", password="proverka123", role="owner",
            full_name="Ковалёв Андрей Сергеевич")
        self.engineer = repos.users.create(
            login="zhukov", password="proverka123", role="engineer",
            full_name="Жуков Пётр Ильич")
        docs = folder / "people" / str(self.chief.id)
        docs.mkdir(parents=True, exist_ok=True)
        self.pdf = make_pdf(docs / "Справка-объективка.pdf", pages=3)
        self.item = repos.person_files.add(
            user_id=self.chief.id, name=self.pdf.name, path=str(self.pdf),
            size=self.pdf.stat().st_size, kind="profile",
            uploaded_by=self.chief.id)
        self.login("chief")

    def login(self, who: str) -> None:
        answer = self.client.post("/api/auth/login",
                                  json={"login": who, "password": "proverka123"})
        self.assertEqual(200, answer.status_code, answer.text)

    def url(self, extra: str = "") -> str:
        return (f"/api/users/{self.chief.id}/files/{self.item.id}{extra}")

    def test_the_listing_says_how_many_pages(self):
        """Без этого окно не напишет «страница 1 из 4» и не найдёт следующую."""
        answer = self.client.get(f"/api/users/{self.chief.id}/files")
        self.assertEqual(200, answer.status_code, answer.text)
        item = answer.json()["files"][0]
        self.assertEqual(3, item["pages"])
        self.assertNotIn("path", item, "путь на диске уходит человеку")

    def test_a_page_comes_as_a_picture(self):
        answer = self.client.get(self.url("?page=2"))
        self.assertEqual(200, answer.status_code, answer.text)
        self.assertEqual("image/png", answer.headers["content-type"])
        self.assertTrue(answer.content.startswith(PNG_HEAD))
        self.assertIn("nosniff", answer.headers.get("x-content-type-options", ""))

    def test_without_a_page_the_file_itself_comes(self):
        answer = self.client.get(self.url())
        self.assertEqual(200, answer.status_code)
        self.assertTrue(answer.content.startswith(b"%PDF"))

    def test_a_page_that_is_not_there_is_a_plain_404(self):
        answer = self.client.get(self.url("?page=99"))
        self.assertEqual(404, answer.status_code)
        self.assertIn("страниц", answer.json()["error"])

    def test_a_stranger_gets_no_page_either(self):
        """Показ страницами не должен стать лазейкой в чужие документы."""
        self.login("zhukov")
        answer = self.client.get(self.url("?page=1"))
        self.assertEqual(403, answer.status_code, answer.text)

    def test_a_page_is_not_drawn_twice(self):
        first = self.client.get(self.url("?page=1"))
        self.assertEqual(200, first.status_code)
        kept = list((Path(self.app.state.settings.data_dir) / "kesh")
                    .rglob("*.png"))
        self.assertTrue(kept, "нарисованная страница не сохранилась")
        again = self.client.get(self.url("?page=1"))
        self.assertEqual(first.content, again.content)


@unittest.skipUnless(FONT.is_file(), "нет шрифта с кириллицей для сборки PDF")
class LibrarySourceTests(unittest.TestCase):
    """Подлинник документа библиотеки — страницами, не скачиванием.

    Кнопка «Открыть исходный файл» на деле скачивала: сервер отдаёт файл
    вложением. Посмотреть страницу стандарта, не таща его на диск, было
    нечем — а именно за этим её и жмут.
    """

    def setUp(self):
        try:
            import pymupdf                       # noqa: F401
        except ImportError:                      # pragma: no cover
            self.skipTest("нет pymupdf")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        folder = Path(self._tmp.name)
        library = folder / "biblioteka" / "literature"
        library.mkdir(parents=True)
        self.pdf = make_pdf(library / "tom.pdf", pages=4)
        settings = Settings.load(
            data_dir=str(folder), db_path=str(folder / "p.db"), auth_enabled=True,
            library_dir=str(folder / "biblioteka"),
            templates_dir=str(ROOT / "templates"))
        self.app = create_app(settings)
        self.client = TestClient(self.app)
        repos = self.app.state.repos
        repos.users.create(login="chief", password="proverka123", role="owner",
                           full_name="Ковалёв Андрей Сергеевич")
        from reportgen.corpus import Chunk

        document = repos.documents.upsert(
            doc_id="literature/tom", doc_type="literature", title="Том",
            source_path=str(self.pdf), sha256="a" * 64, domain="satellite")
        repos.chunks.replace_for_document(document, [
            Chunk(chunk_id="literature/tom#0000", doc_id="literature/tom",
                  doc_type="literature", title_path=["Глава 4"],
                  text="Занимаемая полоса частот измеряется методом.")])
        answer = self.client.post("/api/auth/login",
                                  json={"login": "chief", "password": "proverka123"})
        self.assertEqual(200, answer.status_code, answer.text)

    def test_the_window_learns_how_many_pages_the_original_has(self):
        """Без этого числа окно не покажет «страница 1 из 4»."""
        answer = self.client.get("/api/library/literature%2Ftom/text")
        self.assertEqual(200, answer.status_code, answer.text)
        self.assertEqual(4, answer.json()["source_pages"])

    def test_a_page_of_the_original_comes_as_a_picture(self):
        answer = self.client.get("/api/library/literature%2Ftom/file?page=2")
        self.assertEqual(200, answer.status_code, answer.text)
        self.assertEqual("image/png", answer.headers["content-type"])
        self.assertTrue(answer.content.startswith(PNG_HEAD))

    def test_the_file_itself_still_downloads(self):
        answer = self.client.get("/api/library/literature%2Ftom/file")
        self.assertEqual(200, answer.status_code)
        self.assertTrue(answer.content.startswith(b"%PDF"))

    def test_a_page_that_is_not_there_is_a_plain_404(self):
        answer = self.client.get("/api/library/literature%2Ftom/file?page=99")
        self.assertEqual(404, answer.status_code)

    def test_a_document_without_a_readable_original_says_zero(self):
        """DOCX страницами не показать — окно об этом и не заикнётся."""
        repos = self.app.state.repos
        other = Path(self.app.state.settings.library_dir) / "literature" / "prikaz.docx"
        other.write_bytes(b"PK\x03\x04")
        repos.documents.upsert(
            doc_id="literature/prikaz", doc_type="literature", title="Приказ",
            source_path=str(other), sha256="b" * 64, domain="satellite")
        answer = self.client.get("/api/library/literature%2Fprikaz/text")
        self.assertEqual(0, answer.json()["source_pages"])


if __name__ == "__main__":
    unittest.main()
