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
from archive.version_map import load_current_revision
from intake.manifest import SourceManifest, build_manifest, load_manifest, same_source_material, save_manifest
from intake.validate import validate_source


class SourceConflictError(ValueError):
    pass


def seal_source(catalog: Catalog, material) -> SourceManifest:
    source = validate_source(material)
    with candidate_session(catalog):
        with catalog.read_transaction() as conn:
            existing = load_manifest(conn, source.source_id)
            if existing is not None:
                current = load_current_revision(conn, source.source_id)
                raw_pages = (
                    read_source_page(catalog, current.region, index)
                    for index in range(current.page_count)
                )
                if same_source_material(existing, source, raw_pages):
                    return existing
                raise SourceConflictError(f'conflicting material for {source.source_id}')

        with catalog.write_transaction() as conn:
            reservation = reserve_candidate(conn, source.source_id, len(source.pages))

        try:
            manifest = build_manifest(source, reservation.revision)
            for index, page in enumerate(source.pages):
                write_candidate_page(catalog, reservation, make_initial_page(index, page))

            def finalize(conn):
                save_manifest(conn, manifest)

            publish_candidate(catalog, reservation, finalize)
        except BaseException:
            with catalog.write_transaction() as conn:
                abort_candidate(conn, reservation)
            raise
        return manifest
