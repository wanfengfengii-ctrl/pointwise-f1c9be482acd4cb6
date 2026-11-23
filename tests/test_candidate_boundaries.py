from archive.catalog import ArchiveBusyError
from archive.page_codec import make_initial_page
from archive.page_index import list_written_pages
from archive.page_io import read_payload_page
from archive.publication import (
    IncompleteCandidateError, abort_candidate, candidate_session, publish_candidate,
    reserve_candidate, write_candidate_page,
)
from archive.version_map import load_current_revision
from calibration.convert import InvalidMultiplierError
from calibration.job import recalibrate_volume
from delivery.ticket import open_ticket
from intake.initial_seal import seal_source
from reconcile.statement import reconcile_ticket
from tests.fixtures import ArchiveTestCase, VOLUME, page_values, source


class CandidateBoundaryTest(ArchiveTestCase):
    def test_invalid_multipliers_do_not_write(self):
        initial = seal_source(self.catalog, source())
        writes = []
        instrumented = self.reopen(before_write=lambda region, index: writes.append((region, index)))
        for rate in (0, -1, True, 1.5, '2', None):
            with self.subTest(rate=rate), self.assertRaises(InvalidMultiplierError):
                recalibrate_volume(instrumented, VOLUME, rate)
        self.assertEqual(writes, [])
        with self.catalog.read_transaction() as conn:
            self.assertEqual(load_current_revision(conn, VOLUME).revision, initial.initial_revision)
            self.assertEqual(conn.execute('SELECT count(*) FROM calibration_jobs').fetchone()[0], 0)

    def test_calibration_preserves_missing_and_effective_zero(self):
        seal_source(self.catalog, source((0, None, -7)))
        revised = recalibrate_volume(self.catalog, VOLUME, 3)
        ticket = open_ticket(self.catalog, VOLUME)
        self.assertEqual(ticket.revision, revised.revision)
        self.assertEqual(page_values(self.collect(ticket)), [0, None, -21])
        statement = reconcile_ticket(self.catalog, ticket.ticket_id)
        self.assertEqual([item.raw_um for item in statement.readings], [0, None, -7])
        self.assertEqual([item.origin for item in statement.readings], ['pump/1', 'pump/2', 'pump/3'])
        self.assertEqual((statement.totals.total_um, statement.totals.missing_count), (-21, 1))

    def test_incomplete_candidate_rejects_stale_physical_page(self):
        initial = seal_source(self.catalog, source())
        abandoned_regions = []

        def fail(region, index):
            if index == 1:
                abandoned_regions.append(region)
                raise OSError('interrupted candidate')

        with self.assertRaisesRegex(OSError, 'interrupted candidate'):
            recalibrate_volume(self.reopen(before_write=fail), VOLUME, 2)
        catalog = self.reopen()
        with candidate_session(catalog):
            with catalog.write_transaction() as conn:
                reservation = reserve_candidate(conn, VOLUME, 2)
                self.assertEqual(list_written_pages(conn, reservation.revision), ())
            self.assertEqual(abandoned_regions, [reservation.region])
            stale = read_payload_page(catalog, reservation.region, 0)
            self.assertEqual(stale.readings[0].value_um, 14)
            write_candidate_page(catalog, reservation, make_initial_page(1, source().pages[1]))
            with catalog.read_transaction() as conn:
                self.assertEqual([item.page_index for item in list_written_pages(conn, reservation.revision)], [1])
            with self.assertRaisesRegex(IncompleteCandidateError, 'does not cover'):
                publish_candidate(catalog, reservation)
            observer = open_ticket(catalog, VOLUME)
            self.assertEqual(observer.revision, initial.initial_revision)
            self.assertEqual(page_values(self.collect(observer, catalog)), [7, 11])
            with catalog.write_transaction() as conn:
                self.assertTrue(abort_candidate(conn, reservation))
            with catalog.write_transaction() as conn:
                new_reservation = reserve_candidate(conn, VOLUME, 2)
                self.assertNotEqual(new_reservation.revision, reservation.revision)
                self.assertFalse(abort_candidate(conn, reservation))
                self.assertTrue(abort_candidate(conn, new_reservation))
        retry = recalibrate_volume(catalog, VOLUME, 3)
        freshest = open_ticket(catalog, VOLUME)
        self.assertEqual(freshest.revision, retry.revision)
        self.assertEqual(page_values(self.collect(freshest, catalog)), [21, 33])

    def test_competing_writer_cannot_reclaim_active_reservation(self):
        initial = seal_source(self.catalog, source())
        observations = []

        def probe(region, index):
            if index == 0:
                contender = self.reopen()
                with self.assertRaises(ArchiveBusyError):
                    recalibrate_volume(contender, VOLUME, 3)
                observer = open_ticket(contender, VOLUME)
                observations.append((observer.revision, page_values(self.collect(observer, contender))))

        published = recalibrate_volume(self.reopen(after_write=probe), VOLUME, 2)
        self.assertEqual(observations, [(initial.initial_revision, [7, 11])])
        ticket = open_ticket(self.catalog, VOLUME)
        self.assertEqual(ticket.revision, published.revision)
        self.assertEqual(page_values(self.collect(ticket)), [14, 22])
