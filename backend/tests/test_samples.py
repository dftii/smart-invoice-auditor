"""Sample invoices offered by GET /api/v1/samples ("probar con un ejemplo")."""

from pathlib import Path

import pytest

from app.core.config import Settings, get_settings
from app.services.samples import CATALOGUE
from tests.test_api import API
from tests.test_ocr_text import TESSERACT_SAMPLE_07, upload_with_ocr

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


def test_lists_every_catalogue_sample_with_its_metadata(client):
    response = client.get(f"{API}/samples")
    assert response.status_code == 200
    body = response.json()
    assert [item["filename"] for item in body] == [entry.filename for entry in CATALOGUE]
    first = body[0]
    assert first["title"] == "Factura correcta"
    assert first["media_type"] == "application/pdf"
    assert first["size_bytes"] == (SAMPLES / first["filename"]).stat().st_size
    assert {item["expected_status"] for item in body} == {"VALIDATED", "DISCREPANCY"}
    image = next(item for item in body if item["filename"].endswith(".png"))
    assert image["media_type"] == "image/png"


@pytest.mark.parametrize("entry", CATALOGUE, ids=lambda entry: entry.filename)
def test_downloads_each_sample_unchanged(client, entry):
    response = client.get(f"{API}/samples/{entry.filename}")
    assert response.status_code == 200
    assert response.content == (SAMPLES / entry.filename).read_bytes()
    assert response.headers["content-type"].startswith("application/pdf" if entry.filename.endswith(".pdf") else "image/png")
    assert entry.filename in response.headers["content-disposition"]


@pytest.mark.parametrize(
    "name",
    ["no_existe.pdf", "..%2F.env.example", "..%2Fapp%2Fmain.py", "%2E%2E%2Frequirements.txt"],
)
def test_only_catalogue_files_are_served(client, name):
    assert client.get(f"{API}/samples/{name}").status_code == 404


def test_a_file_in_the_folder_but_not_in_the_catalogue_is_not_served(client, tmp_path):
    (tmp_path / "secreto.pdf").write_bytes(b"%PDF-1.4 privado")
    client.app.dependency_overrides[get_settings] = lambda: Settings(samples_dir=tmp_path)
    assert client.get(f"{API}/samples/secreto.pdf").status_code == 404
    assert client.get(f"{API}/samples").json() == []


def test_missing_files_are_left_out_of_the_list(client, tmp_path):
    keep = CATALOGUE[2].filename
    (tmp_path / keep).write_bytes((SAMPLES / keep).read_bytes())
    client.app.dependency_overrides[get_settings] = lambda: Settings(samples_dir=tmp_path)
    assert [item["filename"] for item in client.get(f"{API}/samples").json()] == [keep]
    assert client.get(f"{API}/samples/{CATALOGUE[0].filename}").status_code == 404


@pytest.mark.parametrize("entry", CATALOGUE, ids=lambda entry: entry.filename)
def test_each_sample_produces_the_audit_result_it_promises(client, entry):
    content = client.get(f"{API}/samples/{entry.filename}").content
    media_type = "image/png" if entry.filename.endswith(".png") else "application/pdf"
    # The image has no text layer: the browser sends its OCR text, as the dashboard does in Modo Demo.
    ocr_text = TESSERACT_SAMPLE_07 if media_type == "image/png" else None
    response = upload_with_ocr(client, content, entry.filename, media_type, ocr_text)
    assert response.status_code == 201, response.text
    assert response.json()["audit"]["status"] == entry.expected_status.value
    assert response.json()["invoice"]["is_synthetic"] is False
