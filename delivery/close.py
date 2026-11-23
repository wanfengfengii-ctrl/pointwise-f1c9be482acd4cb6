from enum import StrEnum

from archive.catalog import Catalog
from delivery.ticket import load_ticket


class CloseResult(StrEnum):
    CLOSED = 'closed'
    ALREADY_CLOSED = 'already_closed'


def close_ticket(catalog: Catalog, ticket_id: int) -> CloseResult:
    with catalog.write_transaction() as conn:
        ticket = load_ticket(conn, ticket_id)
        if ticket.closed:
            return CloseResult.ALREADY_CLOSED
        conn.execute('UPDATE tickets SET closed = 1 WHERE ticket_id = ?', (ticket_id,))
        return CloseResult.CLOSED
