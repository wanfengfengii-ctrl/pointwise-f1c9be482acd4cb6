from archive.version_map import load_current_revision
from delivery.ticket import open_ticket
from intake.initial_seal import SourceConflictError, seal_source
from tests.fixtures import ArchiveTestCase, VOLUME, page_trace, source


class SealTest(ArchiveTestCase):
    def test_initial_and_replay(self):
        initial = seal_source(self.catalog, source())
        self.assertEqual(seal_source(self.reopen(), source()), initial)
        self.assertEqual(initial.volume_id, VOLUME)
        self.assertEqual([page.source_page for page in initial.pages], ['S1', 'S2'])
        ticket = open_ticket(self.catalog, VOLUME)
        self.assertEqual(ticket.revision, initial.initial_revision)
        self.assertEqual(page_trace(self.collect(ticket)), [
            [initial.initial_revision, 0, 'S1', 0, 'A', 'pump/1', 7],
            [initial.initial_revision, 1, 'S2', 0, 'B', 'pump/2', 11],
        ])
        with self.catalog.read_transaction() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM manifests').fetchone()[0], 1)

    def test_conflicting_source(self):
        receipt = seal_source(self.catalog, source())
        with self.assertRaises(SourceConflictError):
            seal_source(self.catalog, source((7, 13)))
        reopened = self.reopen()
        self.assertEqual(seal_source(reopened, source()), receipt)
        with reopened.read_transaction() as conn:
            self.assertEqual(load_current_revision(conn, VOLUME).revision, receipt.initial_revision)
        ticket = open_ticket(reopened, VOLUME)
        self.assertEqual(page_trace(self.collect(ticket, reopened)), [
            [receipt.initial_revision, 0, 'S1', 0, 'A', 'pump/1', 7],
            [receipt.initial_revision, 1, 'S2', 0, 'B', 'pump/2', 11],
        ])
