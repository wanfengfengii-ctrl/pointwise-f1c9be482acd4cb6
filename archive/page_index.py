from dataclasses import dataclass
from sqlite3 import Connection


@dataclass(frozen=True)
class WrittenPage:
    revision: int
    page_index: int
    fingerprint: str


def record_written_page(conn: Connection, page: WrittenPage) -> None:
    conn.execute(
        'INSERT INTO candidate_pages(revision, page_index, fingerprint) VALUES (?, ?, ?)',
        (page.revision, page.page_index, page.fingerprint),
    )


def list_written_pages(conn: Connection, revision: int) -> tuple[WrittenPage, ...]:
    rows = conn.execute(
        'SELECT revision, page_index, fingerprint FROM candidate_pages '
        'WHERE revision = ? ORDER BY page_index',
        (revision,),
    )
    return tuple(WrittenPage(*row) for row in rows)


def forget_written_pages(conn: Connection, revision: int) -> None:
    conn.execute('DELETE FROM candidate_pages WHERE revision = ?', (revision,))
