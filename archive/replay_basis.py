"""Replay verification basis and trusted original sources.

A replay-enabled volume seals a per-page verification basis into the catalog:
the digest binds the volume, the page identity, the in-page reading order, the
original raw values (or missing marks) and the provenance. The original
measurements themselves keep living only in the two payload regions; the
catalog basis is what judges whether a physical page is a trusted source.
"""
import json
from dataclasses import replace
from hashlib import sha256
from sqlite3 import Connection

from archive.catalog import Catalog
from archive.page_codec import ArchivedPage
from archive.page_io import PayloadReadError, read_payload_page
from archive.region import REGION_IDS
from intake.manifest import SourceManifest
from intake.record import SourcePage, SourceReading


class TrustedSourceError(OSError):
    pass


def _basis(volume_id: str, page_index: int, source_page: str, readings) -> str:
    document = {
        'volume_id': volume_id,
        'page_index': page_index,
        'source_page': source_page,
        'readings': [
            [item.reading_id, item.origin, None if item.raw_um is None else hex(item.raw_um)]
            for item in readings
        ],
    }
    payload = json.dumps(document, ensure_ascii=True, separators=(',', ':'), sort_keys=True).encode('ascii')
    return sha256(payload).hexdigest()


def source_page_basis(volume_id: str, page_index: int, page: SourcePage) -> str:
    return _basis(volume_id, page_index, page.page_id, page.readings)


def archived_page_basis(volume_id: str, page: ArchivedPage) -> str:
    return _basis(volume_id, page.page_index, page.source_page, page.readings)


def mark_replay_volume(conn: Connection, volume_id: str) -> None:
    conn.execute('INSERT INTO replay_volumes(volume_id) VALUES (?)', (volume_id,))


def is_replay_volume(conn: Connection, volume_id: str) -> bool:
    row = conn.execute('SELECT 1 FROM replay_volumes WHERE volume_id = ?', (volume_id,)).fetchone()
    return row is not None


def save_replay_basis(conn: Connection, volume_id: str, bases) -> None:
    conn.executemany(
        'INSERT INTO replay_pages(volume_id, page_index, basis) VALUES (?, ?, ?)',
        ((volume_id, index, basis) for index, basis in enumerate(bases)),
    )


def load_replay_basis(conn: Connection, volume_id: str) -> tuple[str, ...] | None:
    if not is_replay_volume(conn, volume_id):
        return None
    rows = conn.execute(
        'SELECT basis FROM replay_pages WHERE volume_id = ? ORDER BY page_index', (volume_id,)
    )
    return tuple(row[0] for row in rows)


def record_replay_ticket(conn: Connection, ticket_id: int, multiplier: int) -> None:
    conn.execute(
        'INSERT INTO replay_tickets(ticket_id, multiplier) VALUES (?, ?)',
        (ticket_id, hex(multiplier)),
    )


def replay_ticket_multiplier(conn: Connection, ticket_id: int) -> int | None:
    row = conn.execute(
        'SELECT multiplier FROM replay_tickets WHERE ticket_id = ?', (ticket_id,)
    ).fetchone()
    return None if row is None else int(row[0], 16)


def published_multiplier(conn: Connection, manifest: SourceManifest, revision: int) -> int:
    if revision == manifest.initial_revision:
        return 1
    row = conn.execute(
        "SELECT multiplier FROM calibration_jobs WHERE revision = ? AND state = 'succeeded'",
        (revision,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f'no succeeded calibration recorded for revision {revision}')
    return int(row[0], 16)


def find_trusted_page(catalog: Catalog, volume_id: str, bases: tuple[str, ...], page_index: int) -> ArchivedPage:
    expected = bases[page_index]
    for region in REGION_IDS:
        try:
            page = read_payload_page(catalog, region, page_index)
        except PayloadReadError:
            continue
        if archived_page_basis(volume_id, page) == expected:
            return page
    raise TrustedSourceError(f'no trustworthy source remains for page {page_index}')


def trusted_source_page(catalog: Catalog, volume_id: str, bases: tuple[str, ...], page_index: int) -> SourcePage:
    page = find_trusted_page(catalog, volume_id, bases, page_index)
    return SourcePage(
        page.source_page,
        tuple(SourceReading(item.reading_id, item.raw_um, item.origin) for item in page.readings),
    )


def rebuild_page(page: ArchivedPage, multiplier: int) -> ArchivedPage:
    return replace(page, readings=tuple(
        replace(item, value_um=None if item.raw_um is None else item.raw_um * multiplier)
        for item in page.readings
    ))
