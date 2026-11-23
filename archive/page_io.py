import os

from archive.catalog import Catalog
from archive.page_codec import ArchivedPage, decode_page, encode_page
from archive.region import region_path
from intake.record import SourcePage, SourceReading


class PayloadReadError(OSError):
    pass


def _page_path(catalog, region, page_index):
    if type(page_index) is not int or page_index < 0:
        raise ValueError('invalid payload page index')
    return region_path(catalog.root, region) / f'{page_index}.json'


def write_payload_page(catalog: Catalog, region: str, page: ArchivedPage) -> None:
    path = _page_path(catalog, region, page.page_index)
    payload = encode_page(page)
    if catalog.before_write is not None:
        catalog.before_write(region, page.page_index)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('wb') as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def read_payload_page(catalog: Catalog, region: str, page_index: int) -> ArchivedPage:
    path = _page_path(catalog, region, page_index)
    if catalog.before_read is not None:
        catalog.before_read(region, page_index)
    try:
        page = decode_page(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise PayloadReadError(f'cannot read payload {region}/{page_index}: {exc}') from exc
    if page.page_index != page_index:
        raise PayloadReadError(f'wrong page identity in payload {region}/{page_index}')
    return page


def read_source_page(catalog: Catalog, region: str, page_index: int) -> SourcePage:
    page = read_payload_page(catalog, region, page_index)
    return SourcePage(
        page.source_page,
        tuple(SourceReading(item.reading_id, item.raw_um, item.origin) for item in page.readings),
    )
