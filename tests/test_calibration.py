import json

from archive.version_map import load_current_revision
from calibration.job import recalibrate_volume
from delivery.close import close_ticket
from delivery.read_page import next_page
from delivery.ticket import open_ticket
from intake.initial_seal import seal_source
from reconcile.statement import reconcile_ticket
from tests.fixtures import ArchiveTestCase, VOLUME, page_trace, page_values, source


class CalibrationTest(ArchiveTestCase):
    def test_rates_use_raw(self):
        seal_source(self.catalog, source())
        two = recalibrate_volume(self.catalog, VOLUME, 2)
        ticket = open_ticket(self.catalog, VOLUME)
        self.assertEqual(page_trace(self.collect(ticket)), [
            [two.revision, 0, 'S1', 0, 'A', 'pump/1', 14],
            [two.revision, 1, 'S2', 0, 'B', 'pump/2', 22],
        ])
        close_ticket(self.catalog, ticket.ticket_id)
        three = recalibrate_volume(self.catalog, VOLUME, 3)
        ticket = open_ticket(self.catalog, VOLUME)
        self.assertEqual(page_trace(self.collect(ticket)), [
            [three.revision, 0, 'S1', 0, 'A', 'pump/1', 21],
            [three.revision, 1, 'S2', 0, 'B', 'pump/2', 33],
        ])
        self.assertEqual(reconcile_ticket(self.catalog, ticket.ticket_id).totals.total_um, 54)

    def test_tickets_across_publish(self):
        initial = seal_source(self.catalog, source())
        old = open_ticket(self.catalog, VOLUME)
        head = next_page(self.catalog, old.ticket_id)
        paused = []

        def pause(region, index):
            if index == 0:
                observer = self.reopen()
                during = open_ticket(observer, VOLUME)
                paused.append((during, self.collect(during, observer)))

        published = recalibrate_volume(self.reopen(after_write=pause), VOLUME, 2)
        self.assertEqual(len(paused), 1)
        self.assertNotEqual(initial.initial_revision, published.revision)
        reopened = self.reopen()
        during, during_pages = paused[0]
        after = open_ticket(reopened, VOLUME)
        actual = {
            'old': page_trace([head] + self.collect(old, reopened)),
            'during': page_trace(during_pages),
            'after': page_trace(self.collect(after, reopened)),
        }
        old_trace = [
            [initial.initial_revision, 0, 'S1', 0, 'A', 'pump/1', 7],
            [initial.initial_revision, 1, 'S2', 0, 'B', 'pump/2', 11],
        ]
        expected = {'old': old_trace, 'during': old_trace, 'after': [
            [published.revision, 0, 'S1', 0, 'A', 'pump/1', 14],
            [published.revision, 1, 'S2', 0, 'B', 'pump/2', 22],
        ]}
        self.assertEqual(actual, expected, '领票的版次、页值或原测出处不符')
        self.assertEqual([old.revision, during.revision, after.revision],
                         [initial.initial_revision, initial.initial_revision, published.revision])
        actual_totals = [reconcile_ticket(reopened, ticket.ticket_id).totals.total_um
                         for ticket in (old, during, after)]
        self.assertEqual(actual_totals, [18, 18, 36])
        print('POINTWISE_SCENARIO ' + json.dumps({
            'case_id': 'cross_publish', 'input': {'raw_um': [7, 11], 'multiplier': 2, 'pause_after': 0},
            'expected': expected, 'actual': actual, 'downstream_expected': [18, 18, 36],
            'downstream_actual': actual_totals,
            'code_paths': ['calibration/job.py', 'archive/publication.py',
                           'delivery/read_page.py', 'reconcile/statement.py'],
            'oracle': '原页 A=7 B=11，原版继续使用原值，新版逐项乘二后分别手算合计',
        }, ensure_ascii=False))

    def test_failed_second_page(self):
        initial = seal_source(self.catalog, source())
        old = open_ticket(self.catalog, VOLUME)
        self.assertEqual(page_values([next_page(self.catalog, old.ticket_id)]), [7])
        written = []

        def fail(region, index):
            if index == 1:
                raise OSError('second page is unwritable')

        def track(region, index):
            written.append(index)

        with self.assertRaisesRegex(OSError, 'second page is unwritable'):
            recalibrate_volume(self.reopen(before_write=fail, after_write=track), VOLUME, 2)
        self.assertEqual(written, [0])
        reopened = self.reopen()
        with reopened.read_transaction() as conn:
            self.assertEqual(load_current_revision(conn, VOLUME).revision, initial.initial_revision)
            failed_revision, state = conn.execute('SELECT revision, state FROM calibration_jobs').fetchone()
            self.assertEqual(state, 'failed')
            self.assertEqual(conn.execute('SELECT count(*) FROM reservations').fetchone()[0], 0)
        self.assertEqual(page_trace(self.collect(old, reopened)), [
            [initial.initial_revision, 1, 'S2', 0, 'B', 'pump/2', 11],
        ])
        still_current = open_ticket(reopened, VOLUME)
        self.assertEqual(page_values(self.collect(still_current, reopened)), [7, 11])
        retry = recalibrate_volume(reopened, VOLUME, 2)
        self.assertNotEqual(retry.revision, failed_revision)
        current = open_ticket(reopened, VOLUME)
        self.assertEqual(current.revision, retry.revision)
        self.assertEqual(page_values(self.collect(current, reopened)), [14, 22])
