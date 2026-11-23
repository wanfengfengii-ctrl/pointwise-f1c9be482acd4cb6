from delivery.cursor import TicketCursor
from delivery.receipts import DeliveryReceipt
from intake.manifest import SourceManifest


class CoverageError(ValueError):
    pass


def check_coverage(
    manifest: SourceManifest,
    revision: int,
    cursor: TicketCursor,
    receipts: tuple[DeliveryReceipt, ...],
) -> None:
    expected = tuple(page.page_index for page in manifest.pages)
    if cursor.next_index != len(expected):
        raise CoverageError('ticket has not delivered the complete volume')
    if tuple(receipt.page_index for receipt in receipts) != expected:
        raise CoverageError('delivery receipts do not cover the source pages in order')
    if any((receipt.ticket_id, receipt.revision) != (cursor.ticket_id, revision) for receipt in receipts):
        raise CoverageError('delivery receipt is from another ticket or revision')
