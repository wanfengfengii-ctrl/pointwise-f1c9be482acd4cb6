from dataclasses import replace

from archive.version_map import RevisionUnavailableError, UnknownVolumeError, load_revision
from calibration.job import recalibrate_volume
from delivery.ticket import open_ticket
from intake.initial_seal import SourceConflictError, seal_source
from intake.record import SourcePage, SourceReading, SourceVolume
from intake.validate import SourceValidationError
from tests.fixtures import ArchiveTestCase, VOLUME, page_trace, page_values, source


class IntakeBoundaryTest(ArchiveTestCase):
    def test_source_order_and_page_boundaries(self):
        material = SourceVolume(VOLUME, (
            SourcePage('P9', (SourceReading('M9', 4, 'R/09'), SourceReading('M2', 0, 'R/02'))),
            SourcePage('P2', (SourceReading('M1', None, 'R/01'),)),
        ))
        receipt = seal_source(self.catalog, material)
        self.assertEqual(seal_source(self.catalog, material), receipt)
        self.assertEqual([page.identities for page in receipt.pages],
                         [(('M9', 'R/09'), ('M2', 'R/02')), (('M1', 'R/01'),)])
        actual = page_trace(self.collect(open_ticket(self.catalog, VOLUME)))
        self.assertEqual(actual, [
            [receipt.initial_revision, 0, 'P9', 0, 'M9', 'R/09', 4],
            [receipt.initial_revision, 0, 'P9', 1, 'M2', 'R/02', 0],
            [receipt.initial_revision, 1, 'P2', 0, 'M1', 'R/01', None],
        ])

    def test_invalid_source_rejected_before_any_write(self):
        valid = source()
        one, two = valid.pages
        bad_id = replace(two, readings=(replace(two.readings[0], reading_id='A'),))
        for invalid in (
            replace(valid, pages=()),
            replace(valid, pages=(one, replace(two, readings=()))),
            replace(valid, pages=(one, bad_id)),
            replace(valid, pages=(one, replace(two, page_id=one.page_id))),
            source((7, True)), source((7, 1.5)), {'source_id': VOLUME, 'pages': {}},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(SourceValidationError):
                seal_source(self.catalog, invalid)
        self.assertFalse((self.root / 'payload').exists())
        with self.catalog.read_transaction() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM revisions').fetchone()[0], 0)
        with self.assertRaises(UnknownVolumeError):
            open_ticket(self.catalog, VOLUME)

    def test_replay_after_recalibration_and_reuse(self):
        material = source()
        receipt = seal_source(self.catalog, material)
        recalibrate_volume(self.catalog, VOLUME, 2)
        current = recalibrate_volume(self.catalog, VOLUME, 3)
        reopened = self.reopen()
        with reopened.read_transaction() as conn, self.assertRaises(RevisionUnavailableError):
            load_revision(conn, receipt.initial_revision)
        self.assertEqual(seal_source(reopened, material), receipt)
        first, second = material.pages
        changed_origin = replace(first, readings=(replace(first.readings[0], origin='new/point'),))
        for changed in (replace(material, pages=(changed_origin, second)),
                        replace(material, pages=(second, first)), source((7, 13))):
            with self.subTest(changed=changed), self.assertRaises(SourceConflictError):
                seal_source(reopened, changed)
        ticket = open_ticket(reopened, VOLUME)
        self.assertEqual(ticket.revision, current.revision)
        self.assertEqual(page_values(self.collect(ticket, reopened)), [21, 33])

    def test_failed_initial_seal_is_invisible_and_can_retry(self):
        def fail(region, index):
            if index == 1:
                raise OSError('initial second-page failure')

        with self.assertRaisesRegex(OSError, 'initial second-page failure'):
            seal_source(self.reopen(before_write=fail), source())
        reopened = self.reopen()
        with self.assertRaises(UnknownVolumeError):
            open_ticket(reopened, VOLUME)
        with reopened.read_transaction() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM manifests').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT count(*) FROM candidate_pages').fetchone()[0], 0)
        retry = seal_source(reopened, source())
        ticket = open_ticket(reopened, VOLUME)
        self.assertEqual(ticket.revision, retry.initial_revision)
        self.assertEqual(page_values(self.collect(ticket, reopened)), [7, 11])
