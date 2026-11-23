from dataclasses import replace

from archive.catalog import Catalog
from archive.page_io import read_payload_page
from archive.version_map import RevisionLocation
from calibration.convert import convert_reading
from intake.manifest import SourceManifest


def iter_calibrated_pages(catalog: Catalog, base: RevisionLocation, manifest: SourceManifest, multiplier: int, read_page=None):
    if read_page is None:
        def read_page(page_index):
            return read_payload_page(catalog, base.region, page_index)
    for expected in manifest.pages:
        page = read_page(expected.page_index)
        yield replace(page, readings=tuple(convert_reading(item, multiplier) for item in page.readings))
