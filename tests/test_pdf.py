import io

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from paperdesk.database import Database
from paperdesk.pdf import PdfImportError, extract_pdf, import_pdf


def pdf_bytes(text="A research paper published in 2024. DOI: 10.1234/example.", *, encrypted=False, blank=False):
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    if not blank:
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})
        })
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 50 700 Td ({text}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    writer.add_metadata({"/Title": "Measured progress", "/Author": "Jane Doe"})
    if encrypted:
        writer.encrypt("password")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_extracts_metadata_and_text():
    paper = extract_pdf(pdf_bytes())
    assert paper.title == "Measured progress"
    assert paper.authors == "Jane Doe"
    assert paper.year == 2024
    assert paper.doi == "10.1234/example"
    assert "A research paper" in paper.pages[0]


def test_import_is_managed_deduplicated_and_restores_missing_copy(tmp_path):
    db = Database(tmp_path / "library.sqlite3")
    source = tmp_path / "original.pdf"
    data = pdf_bytes()
    source.write_bytes(data)
    paper_id = import_pdf(source, db, tmp_path / "papers")
    source.rename(tmp_path / "renamed.pdf")
    copied = tmp_path / "papers" / f"{db.get_paper(paper_id)['sha256']}.pdf"
    assert copied.read_bytes() == data
    copied.unlink()
    assert import_pdf(tmp_path / "renamed.pdf", db, tmp_path / "papers") == paper_id
    assert copied.read_bytes() == data
    assert len(db.list_papers()) == 1
    assert not list((tmp_path / "papers").glob("*.tmp"))


@pytest.mark.parametrize("data,message", [
    (b"broken", "Unable to read"),
    (pdf_bytes(encrypted=True), "password-protected"),
    (pdf_bytes(blank=True), "OCR"),
])
def test_actionable_document_failures(data, message):
    with pytest.raises(PdfImportError, match=message):
        extract_pdf(data)


def test_enforces_document_limits(monkeypatch):
    import paperdesk.pdf as module
    monkeypatch.setattr(module, "MAX_PDF_BYTES", 4)
    with pytest.raises(PdfImportError, match="50 MiB"):
        extract_pdf(b"12345")
    monkeypatch.setattr(module, "MAX_PDF_BYTES", 100_000)
    monkeypatch.setattr(module, "MAX_TEXT_CHARACTERS", 3)
    with pytest.raises(PdfImportError, match="two-million"):
        extract_pdf(pdf_bytes())
    monkeypatch.setattr(module, "MAX_TEXT_CHARACTERS", 2_000_000)
    monkeypatch.setattr(module, "MAX_PAGES", 0)
    with pytest.raises(PdfImportError, match="page import limit"):
        extract_pdf(pdf_bytes())


def test_absent_metadata_uses_filename_without_inventing_authors():
    reader_data = pdf_bytes(text="A study with no explicit publication date")
    from pypdf import PdfReader
    writer = PdfWriter()
    writer.add_page(PdfReader(io.BytesIO(reader_data)).pages[0])
    output = io.BytesIO()
    writer.write(output)
    paper = extract_pdf(output.getvalue(), "Meaningful_filename.pdf")
    assert paper.title == "Meaningful filename"
    assert paper.authors == ""
    assert paper.year is None
    assert paper.doi == ""


def test_failed_import_does_not_create_library_entry(tmp_path):
    source = tmp_path / "scan.pdf"
    source.write_bytes(pdf_bytes(blank=True))
    db = Database(tmp_path / "library.sqlite3")
    with pytest.raises(PdfImportError):
        import_pdf(source, db, tmp_path / "papers")
    assert db.list_papers() == []


def test_missing_pdf_is_actionable(tmp_path):
    db = Database(tmp_path / "library.sqlite3")
    with pytest.raises(PdfImportError, match="Cannot open"):
        import_pdf(tmp_path / "missing.pdf", db, tmp_path / "papers")
