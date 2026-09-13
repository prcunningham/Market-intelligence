import pandas as pd

from market_intel import pdf_download


class FakeResponse:
    def __init__(self, status_code, content=b"", headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}


class FakeSession:
    def __init__(self, responses):
        # {url: FakeResponse} or {url: Exception}
        self.responses = responses
        self.calls = []

    def get(self, url, timeout=None):
        self.calls.append(url)
        result = self.responses.get(url)
        if isinstance(result, Exception):
            raise result
        return result


def test_download_pdf_saves_file_on_200(tmp_path):
    url = "https://www.accessdata.fda.gov/cdrh_docs/pdf19/K193503.pdf"
    session = FakeSession({url: FakeResponse(200, b"%PDF-1.4 fake content",
                                              {"Content-Type": "application/pdf"})})
    dest = tmp_path / "K193503.pdf"
    status = pdf_download.download_pdf(url, dest, session=session)
    assert status == "downloaded"
    assert dest.read_bytes() == b"%PDF-1.4 fake content"


def test_download_pdf_404_is_not_found_not_error(tmp_path):
    url = "https://www.accessdata.fda.gov/cdrh_docs/pdf19/K999999.pdf"
    session = FakeSession({url: FakeResponse(404)})
    status = pdf_download.download_pdf(url, tmp_path / "K999999.pdf", session=session)
    assert status == "not_found"
    assert not (tmp_path / "K999999.pdf").exists()


def test_download_pdf_200_but_not_actually_a_pdf_is_error(tmp_path):
    # FDA sometimes serves an HTML error/redirect page with a 200 status.
    url = "https://www.accessdata.fda.gov/cdrh_docs/pdf19/K000000.pdf"
    session = FakeSession({url: FakeResponse(200, b"<html>not found</html>",
                                              {"Content-Type": "text/html"})})
    status = pdf_download.download_pdf(url, tmp_path / "K000000.pdf", session=session)
    assert status == "error"
    assert not (tmp_path / "K000000.pdf").exists()


def test_download_510k_summaries_handles_missing_url(tmp_path):
    listing = pd.DataFrame([
        {"k_number": "K111111", "device_name": "Foo", "applicant": "Acme", "summary_pdf_url": None},
    ])
    result = pdf_download.download_510k_summaries(listing, tmp_path)
    assert result.iloc[0]["status"] == "no_url"
    assert pd.isna(result.iloc[0]["path"])


def test_download_510k_summaries_downloads_each_row(tmp_path):
    url1 = "https://www.accessdata.fda.gov/cdrh_docs/pdf19/K193503.pdf"
    url2 = "https://www.accessdata.fda.gov/cdrh_docs/pdf5/K052737.pdf"
    session = FakeSession({
        url1: FakeResponse(200, b"%PDF one", {"Content-Type": "application/pdf"}),
        url2: FakeResponse(404),
    })
    listing = pd.DataFrame([
        {"k_number": "K193503", "device_name": "A", "applicant": "Acme", "summary_pdf_url": url1},
        {"k_number": "K052737", "device_name": "B", "applicant": "Beta", "summary_pdf_url": url2},
    ])
    result = pdf_download.download_510k_summaries(listing, tmp_path, session=session)

    row1 = result[result["k_number"] == "K193503"].iloc[0]
    assert row1["status"] == "downloaded"
    assert (tmp_path / "K193503.pdf").exists()

    row2 = result[result["k_number"] == "K052737"].iloc[0]
    assert row2["status"] == "not_found"
    assert pd.isna(row2["path"])
