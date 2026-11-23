from dataclasses import dataclass

from archive.catalog import Catalog
from archive.page_codec import ArchivedPage
from archive.page_io import read_payload_page
from archive.version_map import load_revision
from delivery.cursor import commit_page_delivery, load_cursor
from delivery.receipts import make_delivery_receipt
from delivery.ticket import require_open_ticket


@dataclass(frozen=True)
class DeliveredPage:
    ticket_id: int
    volume_id: str
    revision: int
    page: ArchivedPage


@dataclass(frozen=True)
class DeliveryComplete:
    ticket_id: int
    volume_id: str
    revision: int
    page_count: int


def next_page(catalog: Catalog, ticket_id: int) -> DeliveredPage | DeliveryComplete:
    with catalog.write_transaction() as conn:
        ticket = require_open_ticket(conn, ticket_id)
        cursor = load_cursor(conn, ticket_id)
        location = load_revision(conn, ticket.revision)
        if cursor.next_index == location.page_count:
            return DeliveryComplete(ticket_id, ticket.volume_id, location.revision, location.page_count)
        if cursor.next_index > location.page_count:
            raise RuntimeError('ticket cursor is beyond the end of the revision')
        page = read_payload_page(catalog, location.region, cursor.next_index)
        receipt = make_delivery_receipt(ticket_id, location.revision, page)
        commit_page_delivery(conn, cursor, receipt)
        return DeliveredPage(ticket_id, ticket.volume_id, location.revision, page)
