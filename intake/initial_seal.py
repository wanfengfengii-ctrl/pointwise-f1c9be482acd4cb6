from archive.catalog import Catalog
from archive.page_codec import make_initial_page
from archive.page_io import read_source_page
from archive.publication import (
    abort_candidate,
    candidate_session,
    publish_candidate,
    reserve_candidate,
    write_candidate_page,
)
from archive.replay_basis import (
    is_replay_volume,
    load_replay_basis,
    mark_replay_volume,
    save_replay_basis,
    source_page_basis,
)
from archive.version_map import load_current_revision
from intake.manifest import SourceManifest, build_manifest, load_manifest, same_source_material, save_manifest
from intake.validate import validate_source


class SourceConflictError(ValueError):
    pass


def _replay_bases(source):
    return tuple(
        source_page_basis(source.source_id, index, page)
        for index, page in enumerate(source.pages)
    )


def _same_replay_material(conn, manifest: SourceManifest, source) -> bool:
    return (
        manifest == build_manifest(source, manifest.initial_revision)
        and load_replay_basis(conn, source.source_id) == _replay_bases(source)
    )


def seal_source(catalog: Catalog, material, replay: bool = False) -> SourceManifest:
    source = validate_source(material)
    with candidate_session(catalog):
        with catalog.read_transaction() as conn:
            existing = load_manifest(conn, source.source_id)
            if existing is not None:
                if is_replay_volume(conn, source.source_id) != bool(replay):
                    raise SourceConflictError(f'replay mode conflict for {source.source_id}')
                if replay:
                    if _same_replay_material(conn, existing, source):
                        return existing
                    raise SourceConflictError(f'conflicting material for {source.source_id}')
                current = load_current_revision(conn, source.source_id)
                raw_pages = (
                    read_source_page(catalog, current.region, index)
                    for index in range(current.page_count)
                )
                if same_source_material(existing, source, raw_pages):
                    return existing
                raise SourceConflictError(f'conflicting material for {source.source_id}')
            if replay:
                if conn.execute('SELECT 1 FROM manifests LIMIT 1').fetchone() is not None:
                    raise ValueError('replay sealing requires a new single-volume terminal')
            elif conn.execute('SELECT 1 FROM replay_volumes LIMIT 1').fetchone() is not None:
                raise ValueError('a replay terminal hosts a single volume')

        with catalog.write_transaction() as conn:
            reservation = reserve_candidate(conn, source.source_id, len(source.pages))

        try:
            manifest = build_manifest(source, reservation.revision)
            for index, page in enumerate(source.pages):
                write_candidate_page(catalog, reservation, make_initial_page(index, page))
            if replay:
                bases = _replay_bases(source)

                def finalize(conn):
                    save_manifest(conn, manifest)
                    mark_replay_volume(conn, source.source_id)
                    save_replay_basis(conn, source.source_id, bases)
            else:
                def finalize(conn):
                    save_manifest(conn, manifest)

            publish_candidate(catalog, reservation, finalize)
        except BaseException:
            with catalog.write_transaction() as conn:
                abort_candidate(conn, reservation)
            raise
        return manifest
