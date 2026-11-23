from dataclasses import dataclass
from sqlite3 import Connection

from archive.region import RegionReservation


class UnknownVolumeError(LookupError):
    pass


class RevisionUnavailableError(LookupError):
    pass


@dataclass(frozen=True)
class RevisionLocation:
    revision: int
    volume_id: str
    region: str
    page_count: int


_CURRENT = (
    'SELECT r.revision, r.volume_id, r.region, r.page_count '
    'FROM current_revisions AS c JOIN revisions AS r USING (revision)'
)


def load_current_revision(conn: Connection, volume_id: str) -> RevisionLocation:
    row = conn.execute(_CURRENT + ' WHERE c.volume_id = ?', (volume_id,)).fetchone()
    if row is None:
        raise UnknownVolumeError(volume_id)
    return _location(row)


def _location(row):
    if row is None or row[2] is None:
        raise RevisionUnavailableError('revision has no readable payload location')
    return RevisionLocation(*row)


def load_revision(conn: Connection, revision: int) -> RevisionLocation:
    row = conn.execute(
        'SELECT revision, volume_id, region, page_count FROM revisions WHERE revision = ?',
        (revision,),
    ).fetchone()
    return _location(row)


def list_current_revisions(conn: Connection) -> tuple[RevisionLocation, ...]:
    return tuple(_location(row) for row in conn.execute(_CURRENT + ' ORDER BY c.volume_id'))


def store_revision(conn: Connection, reservation: RegionReservation) -> RevisionLocation:
    result = conn.execute(
        'UPDATE revisions SET region = ? WHERE revision = ? AND region IS NULL',
        (reservation.region, reservation.revision),
    )
    if result.rowcount != 1:
        raise RevisionUnavailableError('candidate revision cannot take a location')
    return load_revision(conn, reservation.revision)


def set_current_revision(conn: Connection, location: RevisionLocation) -> None:
    conn.execute(
        'INSERT INTO current_revisions(volume_id, revision) VALUES (?, ?) '
        'ON CONFLICT(volume_id) DO UPDATE SET revision = excluded.revision',
        (location.volume_id, location.revision),
    )


def detach_revision(conn: Connection, region: str) -> None:
    conn.execute('UPDATE revisions SET region = NULL WHERE region = ?', (region,))
