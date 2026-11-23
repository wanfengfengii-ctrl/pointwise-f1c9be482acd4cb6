from dataclasses import dataclass

from archive.catalog import Catalog
from archive.page_io import read_payload_page
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
            location = load_revision(conn, ticket.revision)
            if location.page_count != len(manifest.pages) or location.volume_id != ticket.volume_id:
                raise CoverageError('revision does not match the ticket manifest')
            for expected, receipt in zip(manifest.pages, receipts, strict=True):
                page = read_payload_page(catalog, location.region, expected.page_index)
                details.extend(check_provenance(expected, receipt, page))
        except (CoverageError, ProvenanceError, RevisionUnavailableError, OSError) as exc:
            return PendingStatement(ticket_id, ticket.revision, str(exc))
        readings = tuple(details)
        return ShiftStatement(ticket_id, ticket.volume_id, ticket.revision, readings, sum_readings(readings))
