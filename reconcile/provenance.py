from dataclasses import dataclass

from archive.page_codec import ArchivedPage
from delivery.receipts import DeliveryReceipt, page_fingerprint
from intake.manifest import ManifestPage, matches_page_identity


class ProvenanceError(ValueError):
    pass


@dataclass(frozen=True)
class ReadingDetail:
    page_index: int
    source_page: str
    position: int
    reading_id: str
    origin: str
    raw_um: int | None
    value_um: int | None


def check_provenance(
    expected: ManifestPage, receipt: DeliveryReceipt, page: ArchivedPage
) -> tuple[ReadingDetail, ...]:
    if receipt.page_index != page.page_index or receipt.fingerprint != page_fingerprint(page):
        raise ProvenanceError(f'page {page.page_index} differs from the delivered page')
    if not matches_page_identity(expected, page):
        raise ProvenanceError(f'page {page.page_index} differs from the source provenance')
    return tuple(
        ReadingDetail(page.page_index, page.source_page, position, item.reading_id,
                      item.origin, item.raw_um, item.value_um)
        for position, item in enumerate(page.readings)
    )
