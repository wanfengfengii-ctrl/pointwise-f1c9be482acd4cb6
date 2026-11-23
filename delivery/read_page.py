from dataclasses import dataclass

from archive.catalog import Catalog
from archive.page_codec import ArchivedPage
from archive.page_io import read_payload_page
from archive.replay_basis import (
    find_trusted_page,
    load_replay_basis,
    rebuild_page,
    replay_ticket_multiplier,
)
from archive.version_map import load_revision
from delivery.cursor import commit_page_delivery, load_cursor
from delivery.receipts import make_delivery_receipt
from delivery.ticket import require_open_ticket
from intake.manifest import load_manifest


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
        multiplier = replay_ticket_multiplier(conn, ticket_id)
        if multiplier is None:
            location = load_revision(conn, ticket.revision)
            revision, page_count = location.revision, location.page_count

            def supply(page_index):
                return read_payload_page(catalog, location.region, page_index)
        else:
            manifest = load_manifest(conn, ticket.volume_id)
            bases = load_replay_basis(conn, ticket.volume_id)
            if bases is None or len(bases) != len(manifest.pages):
                raise RuntimeError('replay ticket lost its verification basis')
            revision, page_count = ticket.revision, len(manifest.pages)

            def supply(page_index):
                trusted = find_trusted_page(catalog, ticket.volume_id, bases, page_index)
                return rebuild_page(trusted, multiplier)
        if cursor.next_index == page_count:
            return DeliveryComplete(ticket_id, ticket.volume_id, revision, page_count)
        if cursor.next_index > page_count:
            raise RuntimeError('ticket cursor is beyond the end of the revision')
        page = supply(cursor.next_index)
        receipt = make_delivery_receipt(ticket_id, revision, page)
        commit_page_delivery(conn, cursor, receipt)
        return DeliveredPage(ticket_id, ticket.volume_id, revision, page)
