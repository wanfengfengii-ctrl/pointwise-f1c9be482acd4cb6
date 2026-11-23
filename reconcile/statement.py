from dataclasses import dataclass

from archive.catalog import Catalog
from archive.page_io import read_payload_page
from archive.replay_basis import (
    find_trusted_page,
    load_replay_basis,
    rebuild_page,
    replay_ticket_multiplier,
)
from archive.version_map import RevisionUnavailableError, load_revision
from delivery.cursor import load_cursor
from delivery.receipts import list_delivery_receipts
from delivery.ticket import require_open_ticket
from intake.manifest import load_manifest
from reconcile.coverage import CoverageError, check_coverage
from reconcile.provenance import ProvenanceError, ReadingDetail, check_provenance
from reconcile.total import Totals, sum_readings


@dataclass(frozen=True)
class PendingStatement:
    ticket_id: int
    revision: int
    reason: str


@dataclass(frozen=True)
class ShiftStatement:
    ticket_id: int
    volume_id: str
    revision: int
    readings: tuple[ReadingDetail, ...]
    totals: Totals


def reconcile_ticket(catalog: Catalog, ticket_id: int) -> PendingStatement | ShiftStatement:
    # Keep close and region reuse out until all reads for this statement are complete.
    with catalog.write_transaction() as conn:
        ticket = require_open_ticket(conn, ticket_id)
        manifest = load_manifest(conn, ticket.volume_id)
        cursor = load_cursor(conn, ticket_id)
        receipts = list_delivery_receipts(conn, ticket_id)
        details = []
        try:
            check_coverage(manifest, ticket.revision, cursor, receipts)
            multiplier = replay_ticket_multiplier(conn, ticket_id)
            if multiplier is None:
                location = load_revision(conn, ticket.revision)
                if location.page_count != len(manifest.pages) or location.volume_id != ticket.volume_id:
                    raise CoverageError('revision does not match the ticket manifest')

                def supply(page_index):
                    return read_payload_page(catalog, location.region, page_index)
            else:
                bases = load_replay_basis(conn, ticket.volume_id)
                if bases is None or len(bases) != len(manifest.pages):
                    raise CoverageError('replay ticket lost its verification basis')

                def supply(page_index):
                    trusted = find_trusted_page(catalog, ticket.volume_id, bases, page_index)
                    return rebuild_page(trusted, multiplier)
            for expected, receipt in zip(manifest.pages, receipts, strict=True):
                details.extend(check_provenance(expected, receipt, supply(expected.page_index)))
        except (CoverageError, ProvenanceError, RevisionUnavailableError, OSError) as exc:
            return PendingStatement(ticket_id, ticket.revision, str(exc))
        readings = tuple(details)
        return ShiftStatement(ticket_id, ticket.volume_id, ticket.revision, readings, sum_readings(readings))
