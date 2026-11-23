from dataclasses import dataclass
from sqlite3 import Connection

from delivery.receipts import DeliveryReceipt, record_delivery_receipt
from delivery.ticket import UnknownTicketError


@dataclass(frozen=True)
class TicketCursor:
    ticket_id: int
    next_index: int


def load_cursor(conn: Connection, ticket_id: int) -> TicketCursor:
    row = conn.execute('SELECT next_index FROM tickets WHERE ticket_id = ?', (ticket_id,)).fetchone()
    if row is None:
        raise UnknownTicketError(ticket_id)
    return TicketCursor(ticket_id, row[0])


def commit_page_delivery(conn: Connection, cursor: TicketCursor, receipt: DeliveryReceipt) -> None:
    if (receipt.ticket_id, receipt.page_index) != (cursor.ticket_id, cursor.next_index):
        raise ValueError('delivery receipt is for a different ticket or page')
    updated = conn.execute(
        'UPDATE tickets SET next_index = next_index + 1 '
        'WHERE ticket_id = ? AND next_index = ? AND closed = 0',
        (cursor.ticket_id, cursor.next_index),
    )
    if updated.rowcount != 1:
        raise RuntimeError('ticket was closed or moved before delivery committed')
    record_delivery_receipt(conn, receipt)
