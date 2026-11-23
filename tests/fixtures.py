import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from archive.catalog import Catalog
from delivery.read_page import DeliveredPage, DeliveryComplete, next_page
from intake.record import SourcePage, SourceReading, SourceVolume


VOLUME = 'shift-Q17'


def source(values=(7, 11)):
    return SourceVolume(VOLUME, tuple(
        SourcePage(f'S{index + 1}', (SourceReading(chr(65 + index), value, f'pump/{index + 1}'),))
        for index, value in enumerate(values)
    ))


class ArchiveTestCase(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / 'station'
        self.catalog = self.reopen()

    def reopen(self, **hooks):
        return Catalog(self.root, **hooks).initialize()

    def collect(self, ticket, catalog=None):
        catalog = catalog or self.catalog
        pages = []
        while True:
            response = next_page(catalog, ticket.ticket_id)
            if isinstance(response, DeliveryComplete):
                self.assertEqual(response.ticket_id, ticket.ticket_id)
                self.assertEqual(response.volume_id, ticket.volume_id)
                self.assertEqual(response.revision, ticket.revision)
                return pages
            self.assertIsInstance(response, DeliveredPage)
            self.assertEqual(response.ticket_id, ticket.ticket_id)
            self.assertEqual(response.volume_id, ticket.volume_id)
            pages.append(response)


def page_values(pages):
    return [reading.value_um for result in pages for reading in result.page.readings]


def page_trace(pages):
    return [
        [result.revision, result.page.page_index, result.page.source_page, position,
         item.reading_id, item.origin, item.value_um]
        for result in pages for position, item in enumerate(result.page.readings)
    ]
