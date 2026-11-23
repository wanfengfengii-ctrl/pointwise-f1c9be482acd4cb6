import json
from pathlib import Path
from tempfile import TemporaryDirectory

from archive.catalog import Catalog
from archive.region import REGION_IDS, RegionsOccupiedError
from archive.version_map import load_current_revision
from calibration.completeness import CalibrationCoverageError
from calibration.job import recalibrate_volume
from delivery.close import CloseResult, close_ticket
from delivery.cursor import load_cursor
from delivery.read_page import DeliveryComplete, next_page
from delivery.receipts import list_delivery_receipts
from delivery.ticket import open_ticket
from intake.initial_seal import SourceConflictError, seal_source
from reconcile.statement import PendingStatement, ShiftStatement, reconcile_ticket
from replay_fixture import change_raw, run_cut
from tests.fixtures import ArchiveTestCase, VOLUME, page_values, source

SAMPLE = Path(__file__).resolve().parent.parent / 'samples' / 'pump_shift.json'
SHIFT_VOLUME = 'QL-day-17'


def sample_material():
    return json.loads(SAMPLE.read_text(encoding='utf-8'))


def other_region(region):
    (other,) = set(REGION_IDS) - {region}
    return other


class ReplaySealTest(ArchiveTestCase):
    def test_replay_mode_and_material_conflicts(self):
        material = sample_material()
        receipt = seal_source(self.catalog, material, replay=True)
        self.assertEqual(seal_source(self.catalog, material, replay=True), receipt)
        with self.assertRaises(SourceConflictError):
            seal_source(self.catalog, material)
        changed = sample_material()
        changed['pages'][1]['readings'][0]['raw_um'] = 13
        with self.assertRaises(SourceConflictError):
            seal_source(self.catalog, changed, replay=True)
        with self.assertRaises(ValueError):
            seal_source(self.catalog, source())

    def test_replay_requires_fresh_terminal_and_enabled_volume(self):
        seal_source(self.catalog, source())
        with self.assertRaises(ValueError):
            open_ticket(self.catalog, VOLUME, replay=True)
        with self.assertRaises(ValueError):
            seal_source(self.catalog, sample_material(), replay=True)

    def test_same_material_reseal_survives_bad_current_page(self):
        material = sample_material()
        receipt = seal_source(self.catalog, material, replay=True)
        two = recalibrate_volume(self.catalog, SHIFT_VOLUME, 2)
        change_raw(self.catalog, two.region, 1, 19, 2)
        self.assertEqual(seal_source(self.catalog, material, replay=True), receipt)


class ReplayDeliveryTest(ArchiveTestCase):
    def prepare(self, catalog):
        """Seal the sample for replay, take page A on a replay ticket, publish
        the 2x revision, then corrupt current-region page B (raw 19, value 38)."""
        material = sample_material()
        initial = seal_source(catalog, material, replay=True)
        old = open_ticket(catalog, SHIFT_VOLUME, replay=True)
        first = next_page(catalog, old.ticket_id)
        two = recalibrate_volume(catalog, SHIFT_VOLUME, 2)
        change_raw(catalog, two.region, 1, 19, 2)
        return initial, old, first, two

    def test_reopen_continue_and_reconcile_across_region_reuse(self):
        initial, old, first, two = self.prepare(self.catalog)
        self.assertEqual(first.page.readings[0].value_um, 7)
        three = recalibrate_volume(self.catalog, SHIFT_VOLUME, 3)
        self.assertEqual(three.region, other_region(two.region))
        reopened = self.reopen()
        with reopened.read_transaction() as conn:
            self.assertEqual(load_current_revision(conn, SHIFT_VOLUME), three)

        continued = next_page(reopened, old.ticket_id)
        self.assertEqual((continued.revision, continued.page.page_index), (old.revision, 1))
        self.assertEqual(
            [(item.reading_id, item.origin, item.raw_um, item.value_um) for item in continued.page.readings],
            [('B', 'pump-B/outboard', 11, 11)])
        tail = next_page(reopened, old.ticket_id)
        self.assertIsInstance(tail, DeliveryComplete)
        self.assertEqual(next_page(reopened, old.ticket_id), tail)
        statement = reconcile_ticket(reopened, old.ticket_id)
        self.assertIsInstance(statement, ShiftStatement)
        self.assertEqual(
            [(item.reading_id, item.origin, item.raw_um, item.value_um) for item in statement.readings],
            [('A', 'pump-A/inboard', 7, 7), ('B', 'pump-B/outboard', 11, 11)])
        self.assertEqual(
            (statement.totals.total_um, statement.totals.valid_count, statement.totals.missing_count),
            (18, 2, 0))

        newest = open_ticket(reopened, SHIFT_VOLUME, replay=True)
        self.assertEqual(newest.revision, three.revision)
        self.assertEqual(page_values(self.collect(newest, reopened)), [21, 33])
        newest_statement = reconcile_ticket(reopened, newest.ticket_id)
        self.assertEqual([item.origin for item in newest_statement.readings],
                         ['pump-A/inboard', 'pump-B/outboard'])
        self.assertEqual(newest_statement.totals.total_um, 54)
        for ticket in (old, newest):
            self.assertEqual(close_ticket(reopened, ticket.ticket_id), CloseResult.CLOSED)

    def test_interrupted_recalibration_keeps_current_and_continues(self):
        for page_index in (0, 1):
            with self.subTest(page_index=page_index), TemporaryDirectory() as directory:
                root = Path(directory) / 'station'
                catalog = Catalog(root).initialize()
                initial, old, first, two = self.prepare(catalog)
                old_region = other_region(two.region)
                marker = run_cut(root, SHIFT_VOLUME, old_region, page_index)
                self.assertIn(f'{old_region}/{page_index}', marker)

                reopened = Catalog(root).initialize()
                with reopened.read_transaction() as conn:
                    self.assertEqual(load_current_revision(conn, SHIFT_VOLUME), two)
                continued = next_page(reopened, old.ticket_id)
                self.assertEqual(continued.revision, initial.initial_revision)
                self.assertEqual(
                    [(item.reading_id, item.origin, item.value_um) for item in continued.page.readings],
                    [('B', 'pump-B/outboard', 11)])
                statement = reconcile_ticket(reopened, old.ticket_id)
                self.assertEqual(statement.totals.total_um, 18)

                three = recalibrate_volume(reopened, SHIFT_VOLUME, 3)
                newest = open_ticket(reopened, SHIFT_VOLUME, replay=True)
                self.assertEqual(newest.revision, three.revision)
                self.assertEqual(page_values(self.collect(newest, reopened)), [21, 33])
                self.assertEqual(reconcile_ticket(reopened, newest.ticket_id).totals.total_um, 54)

    def test_bad_page_in_both_regions_blocks_publish_and_delivery(self):
        initial, old, first, two = self.prepare(self.catalog)
        change_raw(self.catalog, other_region(two.region), 1, 19, 1)
        with self.assertRaises((OSError, CalibrationCoverageError)):
            recalibrate_volume(self.catalog, SHIFT_VOLUME, 3)
        with self.catalog.read_transaction() as conn:
            self.assertEqual(load_current_revision(conn, SHIFT_VOLUME), two)
            self.assertEqual(conn.execute('SELECT count(*) FROM reservations').fetchone()[0], 0)
            before = (load_cursor(conn, old.ticket_id), list_delivery_receipts(conn, old.ticket_id))
        self.assertEqual(before[0].next_index, 1)
        with self.assertRaises(OSError):
            next_page(self.catalog, old.ticket_id)
        with self.catalog.read_transaction() as conn:
            self.assertEqual(
                (load_cursor(conn, old.ticket_id), list_delivery_receipts(conn, old.ticket_id)),
                before)
        self.assertIsInstance(reconcile_ticket(self.catalog, old.ticket_id), PendingStatement)

    def test_reconcile_pending_when_delivered_evidence_is_lost(self):
        material = sample_material()
        seal_source(self.catalog, material, replay=True)
        ticket = open_ticket(self.catalog, SHIFT_VOLUME, replay=True)
        self.assertEqual(page_values(self.collect(ticket)), [7, 11])
        two = recalibrate_volume(self.catalog, SHIFT_VOLUME, 2)
        change_raw(self.catalog, two.region, 1, 19, 2)
        change_raw(self.catalog, other_region(two.region), 1, 19, 1)
        self.assertIsInstance(reconcile_ticket(self.catalog, ticket.ticket_id), PendingStatement)

    def test_ordinary_ticket_still_protects_its_region(self):
        material = sample_material()
        initial = seal_source(self.catalog, material, replay=True)
        ordinary = open_ticket(self.catalog, SHIFT_VOLUME)
        replay_old = open_ticket(self.catalog, SHIFT_VOLUME, replay=True)
        self.assertEqual(page_values(self.collect(ordinary)), [7, 11])
        self.assertEqual(reconcile_ticket(self.catalog, ordinary.ticket_id).totals.total_um, 18)
        two = recalibrate_volume(self.catalog, SHIFT_VOLUME, 2)
        with self.assertRaises(RegionsOccupiedError):
            recalibrate_volume(self.catalog, SHIFT_VOLUME, 3)
        self.assertEqual(close_ticket(self.catalog, ordinary.ticket_id), CloseResult.CLOSED)
        three = recalibrate_volume(self.catalog, SHIFT_VOLUME, 3)
        self.assertNotEqual(three.region, two.region)
        self.assertEqual(replay_old.revision, initial.initial_revision)
        self.assertEqual(page_values(self.collect(replay_old)), [7, 11])
        self.assertEqual(reconcile_ticket(self.catalog, replay_old.ticket_id).totals.total_um, 18)
