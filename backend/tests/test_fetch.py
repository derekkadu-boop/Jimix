import httpx
import pytest

from app.fetch import _file_name


@pytest.mark.parametrize(
    ("disposition", "url", "expected"),
    [
        ('attachment; filename="Hinweis%20E-Vergabe.pdf"; filename*=UTF-8\'\'Hinweis%20E-Vergabe.pdf',
         "https://p.example/d/x", "Hinweis E-Vergabe.pdf"),
        ('attachment; filename="a.pdf"; filename*=UTF-8\'\'Zus%C3%A4tzlich.pdf', "https://p.example/d/x",
         "Zusätzlich.pdf"),
        ("attachment; filename*=iso-8859-1'de'Gr%FC%DFe.pdf", "https://p.example/d/x", "Grüße.pdf"),
        ('attachment; filename="plain name.pdf"', "https://p.example/d/x", "plain name.pdf"),
        ("", "https://p.example/d/LV%20Komplett.pdf", "LV Komplett.pdf"),
        ("", "https://p.example/", "document"),
    ],
)
def test_file_name_prefers_extended_parameter_and_decodes(disposition, url, expected):
    resp = httpx.Response(200, headers={"content-disposition": disposition}, request=httpx.Request("GET", url))
    assert _file_name(resp) == expected
