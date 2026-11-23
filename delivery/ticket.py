from dataclasses import dataclass
from sqlite3 import Connection

from archive.catalog import Catalog
from archive.replay_basis import is_replay_volume, published_multiplier, record_replay_ticket
from archive.version_map import load_current_revision
from intake.manifest import load_manifest


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
    replay: bool = False


def open_ticket(catalog: Catalog, volume_id: str, replay: bool = False) -> Ticket:
    with catalog.write_transaction() as conn:
        location = load_current_revision(conn, volume_id)
        multiplier = None
        if replay:
            if not is_replay_volume(conn, volume_id):
                raise ValueError(f'volume {volume_id} is not sealed for replay')
            manifest = load_manifest(conn, volume_id)
            multiplier = published_multiplier(conn, manifest, location.revision)
        result = conn.execute(
            'INSERT INTO tickets(volume_id, revision) VALUES (?, ?)', (volume_id, location.revision)
        )
        ticket_id = result.lastrowid
        if multiplier is None:
            return Ticket(ticket_id, volume_id, location.revision)
        record_replay_ticket(conn, ticket_id, multiplier)
        return Ticket(ticket_id, volume_id, location.revision, replay=True)


def load_ticket(conn: Connection, ticket_id: int) -> Ticket:
    if type(ticket_id) is not int or not 0 < ticket_id < 2**63:
        raise UnknownTicketError(ticket_id)
    row = conn.execute(
        'SELECT volume_id, revision, closed FROM tickets WHERE ticket_id = ?', (ticket_id,)
    ).fetchone()
    if row is None:
        raise UnknownTicketError(ticket_id)
    volume_id, revision, closed = row
    replay = conn.execute(
        'SELECT 1 FROM replay_tickets WHERE ticket_id = ?', (ticket_id,)
    ).fetchone() is not None
    return Ticket(ticket_id, volume_id, revision, bool(closed), replay)


def require_open_ticket(conn: Connection, ticket_id: int) -> Ticket:
    ticket = load_ticket(conn, ticket_id)
    if ticket.closed:
        raise ClosedTicketError(f'ticket {ticket_id} is closed')
    return ticket
