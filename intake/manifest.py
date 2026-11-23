import json
from dataclasses import dataclass
from sqlite3 import Connection

from intake.record import SourceVolume


@dataclass(frozen=True)
class ManifestPage:
    page_index: int
    source_page: str
    identities: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class SourceManifest:
    volume_id: str
    initial_revision: int
    pages: tuple[ManifestPage, ...]


def build_manifest(source: SourceVolume, initial_revision: int) -> SourceManifest:
    pages = tuple(
        ManifestPage(index, page.page_id, tuple((item.reading_id, item.origin) for item in page.readings))
        for index, page in enumerate(source.pages)
    )
    return SourceManifest(source.source_id, initial_revision, pages)


def load_manifest(conn: Connection, volume_id: str) -> SourceManifest | None:
    header = conn.execute(
        'SELECT initial_revision FROM manifests WHERE volume_id = ?', (volume_id,)
    ).fetchone()
    if header is None:
        return None
    rows = conn.execute(
        'SELECT page_index, source_page, identities FROM manifest_pages '
        'WHERE volume_id = ? ORDER BY page_index',
        (volume_id,),
    )
    pages = tuple(
        ManifestPage(index, page_id, tuple(tuple(pair) for pair in json.loads(identities)))
        for index, page_id, identities in rows
    )
    return SourceManifest(volume_id, header[0], pages)


def save_manifest(conn: Connection, manifest: SourceManifest) -> None:
    conn.execute(
        'INSERT INTO manifests(volume_id, initial_revision) VALUES (?, ?)',
        (manifest.volume_id, manifest.initial_revision),
    )
    conn.executemany(
        'INSERT INTO manifest_pages(volume_id, page_index, source_page, identities) VALUES (?, ?, ?, ?)',
        (
            (manifest.volume_id, page.page_index, page.source_page, json.dumps(page.identities))
            for page in manifest.pages
        ),
    )


def same_source_material(manifest: SourceManifest, source: SourceVolume, archived_pages) -> bool:
    return (
        manifest == build_manifest(source, manifest.initial_revision)
        and tuple(archived_pages) == source.pages
    )


def matches_page_identity(expected: ManifestPage, archived) -> bool:
    return (
        archived.page_index == expected.page_index
        and archived.source_page == expected.source_page
        and tuple((item.reading_id, item.origin) for item in archived.readings) == expected.identities
    )
