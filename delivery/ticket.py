from dataclasses import dataclass
from sqlite3 import Connection

from archive.catalog import Catalog
from archive.version_map import load_current_revision


class UnknownTicketError(LookupError):
    pass


class ClosedTicketError(ValueError):
    pass


@dataclass(frozen=True)
class Ticket:
    ticket_id: int
    volume_id: str
    revision: int
    closed: bool = False


def open_ticket(catalog: Catalog, volume_id: str) -> Ticket:
    with catalog.write_transaction() as conn:
        location = load_current_revision(conn, volume_id)
        result = conn.execute(
            'INSERT INTO tickets(volume_id, revision) VALUES (?, ?)', (volume_id, location.revision)
        )
        return Ticket(result.lastrowid, volume_id, location.revision)


def load_ticket(conn: Connection, ticket_id: int) -> Ticket:
    if type(ticket_id) is not int or not 0 < ticket_id < 2**63:
        raise UnknownTicketError(ticket_id)
    row = conn.execute(
        'SELECT volume_id, revision, closed FROM tickets WHERE ticket_id = ?', (ticket_id,)
    ).fetchone()
    if row is None:
        raise UnknownTicketError(ticket_id)
    volume_id, revision, closed = row
    return Ticket(ticket_id, volume_id, revision, bool(closed))


def require_open_ticket(conn: Connection, ticket_id: int) -> Ticket:
    ticket = load_ticket(conn, ticket_id)
    if ticket.closed:
        raise ClosedTicketError(f'ticket {ticket_id} is closed')
    return ticket
