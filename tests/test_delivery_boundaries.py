from dataclasses import replace

from archive.page_io import read_payload_page, write_payload_page
from archive.region import RegionsOccupiedError
from archive.version_map import UnknownVolumeError, load_revision
from calibration.job import recalibrate_volume
from delivery.close import CloseResult, close_ticket
from delivery.cursor import load_cursor
from delivery.read_page import DeliveryComplete, next_page
from delivery.receipts import list_delivery_receipts, page_fingerprint
from delivery.ticket import ClosedTicketError, UnknownTicketError, open_ticket
from intake.initial_seal import seal_source
from intake.manifest import load_manifest
from reconcile.coverage import CoverageError, check_coverage
from reconcile.statement import PendingStatement, reconcile_ticket
from tests.fixtures import ArchiveTestCase, VOLUME, page_trace, page_values, source


class DeliveryBoundaryTest(ArchiveTestCase):
    def setUp(self):
        super().setUp()
        self.initial = seal_source(self.catalog, source())

    def test_unknown_volume_creates_no_ticket(self):
        with self.assertRaises(UnknownVolumeError):
            open_ticket(self.catalog, 'unsealed')
        with self.catalog.read_transaction() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM tickets').fetchone()[0], 0)
        for operation in (close_ticket, next_page, reconcile_ticket):
            with self.subTest(operation=operation), self.assertRaises(UnknownTicketError):
                operation(self.catalog, 987)

    def test_read_failure_keeps_cursor_and_receipt(self):
        ticket = open_ticket(self.catalog, VOLUME)
        next_page(self.catalog, ticket.ticket_id)
        with self.catalog.read_transaction() as conn:
            before = (load_cursor(conn, ticket.ticket_id), list_delivery_receipts(conn, ticket.ticket_id))
        self.assertEqual(before[0].next_index, 1)
        self.assertEqual(len(before[1]), 1)

        def fail(region, index):
            if index == 1:
                raise OSError('temporarily unreadable second page')

        with self.assertRaisesRegex(OSError, 'temporarily unreadable second page'):
            next_page(self.reopen(before_read=fail), ticket.ticket_id)
        reopened = self.reopen()
        with reopened.read_transaction() as conn:
            self.assertEqual((load_cursor(conn, ticket.ticket_id), list_delivery_receipts(conn, ticket.ticket_id)),
                             before)
        self.assertEqual(page_trace(self.collect(ticket, reopened)), [
            [ticket.revision, 1, 'S2', 0, 'B', 'pump/2', 11],
        ])
        self.assertEqual(reconcile_ticket(reopened, ticket.ticket_id).totals.total_um, 18)

    def test_tail_is_stable_and_completed_ticket_holds_region(self):
        old = open_ticket(self.catalog, VOLUME)
        self.assertEqual(page_values(self.collect(old)), [7, 11])
        recalibrate_volume(self.catalog, VOLUME, 2)
        with self.assertRaises(RegionsOccupiedError):
            recalibrate_volume(self.catalog, VOLUME, 3)
        reopened = self.reopen()
        tail = DeliveryComplete(old.ticket_id, VOLUME, old.revision, 2)
        self.assertEqual(next_page(reopened, old.ticket_id), tail)
        self.assertEqual(next_page(reopened, old.ticket_id), tail)
        with reopened.read_transaction() as conn:
            self.assertEqual(load_cursor(conn, old.ticket_id).next_index, 2)
            self.assertEqual(len(list_delivery_receipts(conn, old.ticket_id)), 2)
        self.assertEqual(close_ticket(reopened, old.ticket_id), CloseResult.CLOSED)
        self.assertEqual(close_ticket(reopened, old.ticket_id), CloseResult.ALREADY_CLOSED)
        with self.assertRaises(ClosedTicketError):
            next_page(reopened, old.ticket_id)
        with self.assertRaises(ClosedTicketError):
            reconcile_ticket(reopened, old.ticket_id)
        updated = recalibrate_volume(reopened, VOLUME, 3)
        ticket = open_ticket(reopened, VOLUME)
        self.assertEqual(ticket.revision, updated.revision)
        self.assertEqual(page_values(self.collect(ticket, reopened)), [21, 33])

    def test_coverage_rejects_missing_duplicate_out_of_order_and_wrong_revision(self):
        ticket = open_ticket(self.catalog, VOLUME)
        self.collect(ticket)
        different = recalibrate_volume(self.catalog, VOLUME, 2)
        with self.catalog.read_transaction() as conn:
            cursor = load_cursor(conn, ticket.ticket_id)
            receipts = list_delivery_receipts(conn, ticket.ticket_id)
            manifest = load_manifest(conn, VOLUME)
        first, second = receipts
        for invalid in ((first,), (first, first), (second, first),
                        (first, replace(second, revision=different.revision))):
            with self.subTest(invalid=invalid), self.assertRaises(CoverageError):
                check_coverage(manifest, ticket.revision, cursor, invalid)
        with self.catalog.write_transaction() as conn:
            conn.execute('UPDATE delivery_receipts SET revision = ? WHERE ticket_id = ? AND page_index = 1',
                         (different.revision, ticket.ticket_id))
        self.assertIsInstance(reconcile_ticket(self.catalog, ticket.ticket_id), PendingStatement)
        with self.catalog.write_transaction() as conn:
            conn.execute('DELETE FROM delivery_receipts WHERE ticket_id = ? AND page_index = 1',
                         (ticket.ticket_id,))
        self.assertIsInstance(reconcile_ticket(self.catalog, ticket.ticket_id), PendingStatement)

    def test_provenance_mismatch_even_if_receipt_digest_matches(self):
        ticket = open_ticket(self.catalog, VOLUME)
        self.collect(ticket)
        with self.catalog.read_transaction() as conn:
            location = load_revision(conn, ticket.revision)
        page = read_payload_page(self.catalog, location.region, 0)
        changed = replace(page, readings=(replace(page.readings[0], origin='unrelated/probe'),))
        write_payload_page(self.catalog, location.region, changed)
        pending = reconcile_ticket(self.catalog, ticket.ticket_id)
        self.assertIsInstance(pending, PendingStatement)
        self.assertIn('delivered page', pending.reason)
        with self.catalog.write_transaction() as conn:
            conn.execute('UPDATE delivery_receipts SET fingerprint = ? WHERE ticket_id = ? AND page_index = 0',
                         (page_fingerprint(changed), ticket.ticket_id))
        pending = reconcile_ticket(self.catalog, ticket.ticket_id)
        self.assertIsInstance(pending, PendingStatement)
        self.assertIn('source provenance', pending.reason)

    def test_offline_resume_and_all_missing(self):
        material = replace(source((None, None)), source_id='all-missing')
        receipt = seal_source(self.catalog, material)
        old = open_ticket(self.catalog, 'all-missing')
        first = next_page(self.catalog, old.ticket_id)
        del material
        reopened = self.reopen()
        self.assertEqual(page_trace([first] + self.collect(old, reopened)), [
            [receipt.initial_revision, 0, 'S1', 0, 'A', 'pump/1', None],
            [receipt.initial_revision, 1, 'S2', 0, 'B', 'pump/2', None],
        ])
        statement = reconcile_ticket(reopened, old.ticket_id)
        self.assertEqual(statement, reconcile_ticket(reopened, old.ticket_id))
        self.assertEqual((statement.totals.total_um, statement.totals.missing_count, statement.totals.valid_count),
                         (0, 2, 0))
