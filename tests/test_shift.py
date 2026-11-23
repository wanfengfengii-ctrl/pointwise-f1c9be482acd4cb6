from archive.region import RegionsOccupiedError
from archive.version_map import load_current_revision
from calibration.job import recalibrate_volume
from delivery.close import CloseResult, close_ticket
from delivery.read_page import next_page
from delivery.ticket import open_ticket
from intake.initial_seal import seal_source
from reconcile.statement import PendingStatement, ShiftStatement, reconcile_ticket
from tests.fixtures import ArchiveTestCase, VOLUME, page_trace, page_values, source


class ShiftTest(ArchiveTestCase):
    def test_last_close_releases_region(self):
        seal_source(self.catalog, source())
        first = open_ticket(self.catalog, VOLUME)
        second = open_ticket(self.catalog, VOLUME)
        two = recalibrate_volume(self.catalog, VOLUME, 2)
        current = open_ticket(self.catalog, VOLUME)
        paths = sorted((self.root / 'payload').glob('*/*.json'))
        self.assertEqual({path.parent.name for path in paths}, {'a', 'b'})
        self.assertEqual(len(paths), 4)
        snapshot = {path: path.read_bytes() for path in paths}
        with self.assertRaises(RegionsOccupiedError):
            recalibrate_volume(self.catalog, VOLUME, 3)
        self.assertEqual(close_ticket(self.catalog, first.ticket_id), CloseResult.CLOSED)
        with self.assertRaises(RegionsOccupiedError):
            recalibrate_volume(self.catalog, VOLUME, 3)
        self.assertEqual({path: path.read_bytes() for path in paths}, snapshot)
        with self.catalog.read_transaction() as conn:
            self.assertEqual(load_current_revision(conn, VOLUME), two)
            self.assertEqual(conn.execute('SELECT count(*) FROM calibration_jobs').fetchone()[0], 1)
        self.assertEqual(page_values(self.collect(second)), [7, 11])
        self.assertEqual(page_values(self.collect(current)), [14, 22])
        self.assertEqual(close_ticket(self.catalog, second.ticket_id), CloseResult.CLOSED)
        three = recalibrate_volume(self.catalog, VOLUME, 3)
        self.assertNotEqual(two.region, three.region)
        newest = open_ticket(self.catalog, VOLUME)
        self.assertEqual(page_values(self.collect(newest)), [21, 33])
        self.assertEqual(reconcile_ticket(self.catalog, current.ticket_id).totals.total_um, 36)

    def test_statement_after_full_delivery(self):
        seal_source(self.catalog, source())
        ticket = open_ticket(self.catalog, VOLUME)
        next_page(self.catalog, ticket.ticket_id)
        self.assertIsInstance(reconcile_ticket(self.catalog, ticket.ticket_id), PendingStatement)
        self.assertEqual(page_values(self.collect(ticket)), [11])
        statement = reconcile_ticket(self.catalog, ticket.ticket_id)
        self.assertIsInstance(statement, ShiftStatement)
        self.assertEqual(statement.revision, ticket.revision)
        self.assertEqual([(item.reading_id, item.value_um) for item in statement.readings], [('A', 7), ('B', 11)])
        self.assertEqual((statement.totals.total_um, statement.totals.valid_count, statement.totals.missing_count),
                         (18, 2, 0))

    def test_missing_and_zero(self):
        material = source((0, None, 7))
        sealed = seal_source(self.catalog, material)
        ticket = open_ticket(self.catalog, VOLUME)
        del material
        reopened = self.reopen()
        self.assertEqual(page_trace(self.collect(ticket, reopened)), [
            [sealed.initial_revision, 0, 'S1', 0, 'A', 'pump/1', 0],
            [sealed.initial_revision, 1, 'S2', 0, 'B', 'pump/2', None],
            [sealed.initial_revision, 2, 'S3', 0, 'C', 'pump/3', 7],
        ])
        statement = reconcile_ticket(reopened, ticket.ticket_id)
        self.assertEqual([(item.origin, item.value_um) for item in statement.readings],
                         [('pump/1', 0), ('pump/2', None), ('pump/3', 7)])
        self.assertEqual((statement.totals.total_um, statement.totals.missing_count, statement.totals.valid_count),
                         (7, 1, 2))
