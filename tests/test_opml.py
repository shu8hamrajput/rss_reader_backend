import xml.etree.ElementTree as ET
from unittest.mock import AsyncMock, patch

from .conftest import make_category, make_feed

_OPML = """<?xml version="1.0"?>
<opml version="1.0">
  <head><title>Test feeds</title></head>
  <body>
    <outline text="Top Level Feed" title="Top Level Feed" type="rss" xmlUrl="https://example.com/top.xml"/>
    <outline text="Tech" title="Tech">
      <outline text="Tech Feed" title="Tech Feed" type="rss" xmlUrl="https://example.com/tech.xml"/>
    </outline>
  </body>
</opml>
"""


def _upload(client, auth_headers, body: str = _OPML):
    return client.post(
        "/api/v1/opml/import",
        files={"file": ("feeds.opml", body.encode(), "application/xml")},
        headers=auth_headers,
    )


def test_import_opml_creates_feeds_and_category(client, db_session, user, auth_headers):
    with patch("app.routers.opml.refresh_feed", new_callable=AsyncMock, return_value=0):
        resp = _upload(client, auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["added"] == 2
    assert body["skipped"] == 0
    assert body["failed"] == 0

    from app.models import Category, Feed
    cat = db_session.query(Category).filter(Category.user_id == user.id, Category.name == "Tech").first()
    assert cat is not None
    tech_feed = db_session.query(Feed).filter(Feed.url == "https://example.com/tech.xml").first()
    assert cat in tech_feed.categories


def test_import_opml_skips_existing_subscription(client, db_session, user, auth_headers):
    make_feed(db_session, user, url="https://example.com/top.xml")

    with patch("app.routers.opml.refresh_feed", new_callable=AsyncMock, return_value=0):
        resp = _upload(client, auth_headers)

    body = resp.json()
    assert body["skipped"] == 1
    assert body["added"] == 1


def test_import_opml_records_failures(client, auth_headers):
    with patch(
        "app.routers.opml.refresh_feed",
        new_callable=AsyncMock,
        side_effect=[RuntimeError("boom"), 0],
    ):
        resp = _upload(client, auth_headers)

    body = resp.json()
    assert body["added"] == 1
    assert body["failed"] == 1
    assert len(body["errors"]) == 1
    assert "boom" in body["errors"][0]


def test_import_opml_invalid_xml(client, auth_headers):
    resp = _upload(client, auth_headers, body="not xml at all <<<")
    assert resp.status_code == 422


def test_import_opml_missing_body(client, auth_headers):
    resp = _upload(client, auth_headers, body='<?xml version="1.0"?><opml version="1.0"><head/></opml>')
    assert resp.status_code == 422


def test_import_opml_blocks_xxe(client, auth_headers):
    body = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE opml [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        '<opml version="1.0"><head><title>&xxe;</title></head><body/></opml>'
    )
    resp = _upload(client, auth_headers, body=body)
    assert resp.status_code == 422


def test_import_opml_rejects_oversized_file(client, auth_headers):
    padding = " " * (5 * 1024 * 1024 + 1)
    body = f"<?xml version=\"1.0\"?><opml version=\"1.0\"><!--{padding}--><body/></opml>"
    resp = _upload(client, auth_headers, body=body)
    assert resp.status_code == 413


def test_export_opml(client, db_session, user, auth_headers):
    cat = make_category(db_session, user, name="News")
    categorized = make_feed(db_session, user, title="Categorized Feed", url="https://example.com/cat.xml")
    categorized.categories.append(cat)
    uncategorized = make_feed(db_session, user, title="Uncategorized Feed", url="https://example.com/uncat.xml")
    db_session.commit()

    resp = client.get("/api/v1/opml/export", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.headers["content-disposition"] == "attachment; filename=feeds.opml"

    root = ET.fromstring(resp.content)
    body = root.find("body")

    folders = [o for o in body.findall("outline") if o.get("xmlUrl") is None]
    assert len(folders) == 1
    folder = folders[0]
    assert folder.get("text") == "News"
    assert [o.get("xmlUrl") for o in folder.findall("outline")] == [categorized.url]

    top_level_urls = [o.get("xmlUrl") for o in body.findall("outline") if o.get("xmlUrl")]
    assert top_level_urls == [uncategorized.url]
