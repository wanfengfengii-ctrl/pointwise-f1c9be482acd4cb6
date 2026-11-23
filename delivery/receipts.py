from dataclasses import dataclass
from sqlite3 import Connection

from archive.page_codec import ArchivedPage, payload_fingerprint


@dataclass(frozen=True)
class DeliveryReceipt:
    ticket_id: int
    revision: int
    page_index: int
    fingerprint: str


def page_fingerprint(page: ArchivedPage) -> str:
    return payload_fingerprint(page)


def make_delivery_receipt(ticket_id: int, revision: int, page: ArchivedPage) -> DeliveryReceipt:
    return DeliveryReceipt(ticket_id, revision, page.page_index, page_fingerprint(page))


def record_delivery_receipt(conn: Connection, receipt: DeliveryReceipt) -> None:
    conn.execute(
        'INSERT INTO delivery_receipts(ticket_id, revision, page_index, fingerprint) VALUES (?, ?, ?, ?)',
        (receipt.ticket_id, receipt.revision, receipt.page_index, receipt.fingerprint),
    )


def list_delivery_receipts(conn: Connection, ticket_id: int) -> tuple[DeliveryReceipt, ...]:
    rows = conn.execute(
        'SELECT ticket_id, revision, page_index, fingerprint FROM delivery_receipts '
        'WHERE ticket_id = ? ORDER BY page_index',
        (ticket_id,),
    )
    return tuple(DeliveryReceipt(*row) for row in rows)
