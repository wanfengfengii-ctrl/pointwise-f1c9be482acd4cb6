from contextlib import contextmanager
from sqlite3 import Connection

from archive.catalog import Catalog
from archive.page_codec import ArchivedPage, payload_fingerprint
from archive.page_index import WrittenPage, forget_written_pages, list_written_pages, record_written_page
from archive.page_io import read_payload_page, write_payload_page
from archive.region import RegionReservation, release_reservation, reserve_region
from archive.version_map import detach_revision, list_current_revisions, set_current_revision, store_revision


class InvalidReservationError(RuntimeError):
    pass


class IncompleteCandidateError(RuntimeError):
    pass


@contextmanager
def candidate_session(catalog: Catalog):
    # With the writer lock held, any persisted reservation belongs to an interrupted writer.
    with catalog.writer_guard():
        with catalog.write_transaction() as conn:
            conn.execute(
                "UPDATE calibration_jobs SET state = 'failed' WHERE state IN ('writing', 'ready') "
                'AND revision IN (SELECT revision FROM reservations)'
            )
            conn.execute('DELETE FROM reservations')
        yield


def reserve_candidate(conn: Connection, volume_id: str, page_count: int) -> RegionReservation:
    protected = {location.region for location in list_current_revisions(conn)}
    for (region,) in conn.execute(
        'SELECT DISTINCT r.region FROM tickets AS t JOIN revisions AS r USING (revision) '
        'WHERE t.closed = 0'
    ):
        if region is None:
            raise InvalidReservationError('an open ticket has no payload region')
        protected.add(region)
    result = conn.execute(
        'INSERT INTO revisions(volume_id, page_count) VALUES (?, ?)', (volume_id, page_count)
    )
    reservation = reserve_region(conn, result.lastrowid, protected)
    detach_revision(conn, reservation.region)
    return reservation


def _candidate_page_count(conn, reservation):
    row = conn.execute(
        'SELECT page_count FROM reservations JOIN revisions USING (revision) '
        'WHERE reservations.region = ? AND revision = ?',
        (reservation.region, reservation.revision),
    ).fetchone()
    if row is None:
        raise InvalidReservationError('candidate does not own this reservation')
    return row[0]


def write_candidate_page(catalog: Catalog, reservation: RegionReservation, page: ArchivedPage) -> None:
    # Called inside candidate_session. Keep the I/O and pause callbacks outside directory transactions.
    with catalog.read_transaction() as conn:
        count = _candidate_page_count(conn, reservation)
        exists = conn.execute(
            'SELECT 1 FROM candidate_pages WHERE revision = ? AND page_index = ?',
            (reservation.revision, page.page_index),
        ).fetchone()
        if not 0 <= page.page_index < count or exists is not None:
            raise IncompleteCandidateError('unexpected or repeated candidate page')
    write_payload_page(catalog, reservation.region, page)
    with catalog.write_transaction() as conn:
        _candidate_page_count(conn, reservation)
        record_written_page(conn, WrittenPage(reservation.revision, page.page_index, payload_fingerprint(page)))
    if catalog.after_write is not None:
        catalog.after_write(reservation.region, page.page_index)


def checked_candidate_pages(catalog: Catalog, conn: Connection, reservation: RegionReservation):
    count = _candidate_page_count(conn, reservation)
    written = list_written_pages(conn, reservation.revision)
    if tuple(page.page_index for page in written) != tuple(range(count)):
        raise IncompleteCandidateError('candidate written page set does not cover the volume')
    for entry in written:
        page = read_payload_page(catalog, reservation.region, entry.page_index)
        if payload_fingerprint(page) != entry.fingerprint:
            raise IncompleteCandidateError('candidate payload differs from the page that was written')
        yield page


def publish_candidate(catalog: Catalog, reservation: RegionReservation, finalize=None):
    # The writer guard keeps the verified candidate unchanged until commit.
    with catalog.read_transaction() as conn:
        for _ in checked_candidate_pages(catalog, conn, reservation):
            pass
    with catalog.write_transaction() as conn:
        _candidate_page_count(conn, reservation)
        location = store_revision(conn, reservation)
        if finalize is not None:
            finalize(conn)
        set_current_revision(conn, location)
        release_reservation(conn, reservation)
        return location


def abort_candidate(conn: Connection, reservation: RegionReservation) -> bool:
    owner = conn.execute('SELECT revision FROM reservations WHERE region = ?', (reservation.region,)).fetchone()
    if owner is None or owner[0] != reservation.revision:
        return False
    forget_written_pages(conn, reservation.revision)
    return release_reservation(conn, reservation)
