import io
import zipfile

from app.models import Highlight

from .conftest import make_article, make_feed


def test_export_markdown_vault(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    noted = make_article(db_session, feed, title="Noted Article", article_note="Remember this")
    highlighted = make_article(db_session, feed, title="Highlighted Article")
    db_session.add(Highlight(user_id=user.id, article_id=highlighted.id, start_pos=0, end_pos=10, text="snippet text"))
    db_session.commit()
    make_article(db_session, feed, title="Plain Article")

    resp = client.get("/api/v1/export/markdown", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.headers["content-disposition"] == "attachment; filename=knowledge-vault.zip"

    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    assert len(names) == 2

    note_entry = next(n for n in names if n.startswith(f"{noted.id}-"))
    note_content = zf.read(note_entry).decode()
    assert 'title: "Noted Article"' in note_content
    assert "## Notes" in note_content
    assert "Remember this" in note_content

    highlight_entry = next(n for n in names if n.startswith(f"{highlighted.id}-"))
    highlight_content = zf.read(highlight_entry).decode()
    assert "## Highlights" in highlight_content
    assert "snippet text" in highlight_content
