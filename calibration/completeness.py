from archive.catalog import Catalog
from archive.page_io import read_source_page
from archive.publication import checked_candidate_pages
from archive.region import RegionReservation
from archive.version_map import RevisionLocation
from intake.manifest import SourceManifest, matches_page_identity


class CalibrationCoverageError(RuntimeError):
    pass


def verify_candidate_coverage(
    catalog: Catalog,
    reservation: RegionReservation,
    base: RevisionLocation,
    manifest: SourceManifest,
    read_original=None,
) -> None:
    if base.page_count != len(manifest.pages) or base.volume_id != manifest.volume_id:
        raise CalibrationCoverageError('base revision does not match the source manifest')
    if read_original is None:
        def read_original(page_index):
            return read_source_page(catalog, base.region, page_index)
    with catalog.read_transaction() as conn:
        candidates = checked_candidate_pages(catalog, conn, reservation)
        for expected, page in zip(manifest.pages, candidates, strict=True):
            if not matches_page_identity(expected, page):
                raise CalibrationCoverageError(f'candidate page {page.page_index} differs from the source manifest')
            original = read_original(expected.page_index)
            original_readings = tuple((item.reading_id, item.origin, item.raw_um) for item in original.readings)
            candidate_readings = tuple((item.reading_id, item.origin, item.raw_um) for item in page.readings)
            if original.page_id != expected.source_page or candidate_readings != original_readings:
                raise CalibrationCoverageError(f'candidate page {page.page_index} changed original measurements')
