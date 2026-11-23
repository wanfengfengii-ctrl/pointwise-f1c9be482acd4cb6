import json
from pathlib import Path
from tempfile import TemporaryDirectory

from archive.catalog import Catalog
from archive.region import RegionsOccupiedError
from calibration.job import recalibrate_volume
from delivery.close import close_ticket
from delivery.read_page import DeliveryComplete, next_page
from delivery.ticket import open_ticket
from intake.initial_seal import seal_source
from reconcile.statement import PendingStatement, reconcile_ticket


def finish(catalog, ticket):
    pages = []
    while True:
        page = next_page(catalog, ticket.ticket_id)
        if isinstance(page, DeliveryComplete):
            if (page.ticket_id, page.revision) != (ticket.ticket_id, ticket.revision):
                raise AssertionError(f'unexpected delivery completion {page!r}')
            return pages
        pages.append(page)


def trace(pages):
    return [
        [page.revision, page.page.page_index, page.page.source_page, position,
         item.reading_id, item.origin, item.value_um]
        for page in pages for position, item in enumerate(page.page.readings)
    ]


def expect_busy(catalog, volume):
    try:
        recalibrate_volume(catalog, volume, 3)
    except RegionsOccupiedError:
        return
    raise AssertionError('both protected regions should be occupied')


def run_shift(root, material):
    catalog = Catalog(root).initialize()
    initial = seal_source(catalog, material)
    if seal_source(catalog, material) != initial:
        raise AssertionError('same materials must replay the initial receipt')
    volume = initial.volume_id
    old = open_ticket(catalog, volume)
    first = next_page(catalog, old.ticket_id)
    if not isinstance(reconcile_ticket(catalog, old.ticket_id), PendingStatement):
        raise AssertionError('a partial delivery must wait for reconciliation')

    def fail_second(region, index):
        if index == 1:
            raise OSError('sample second page unavailable for writing')

    try:
        recalibrate_volume(Catalog(root, before_write=fail_second).initialize(), volume, 2)
    except OSError as exc:
        if str(exc) != 'sample second page unavailable for writing':
            raise
    else:
        raise AssertionError('the interrupted candidate unexpectedly published')

    recovered = Catalog(root).initialize()
    probe = open_ticket(recovered, volume)
    after_failure = finish(recovered, probe)
    close_ticket(recovered, probe.ticket_id)
    paused = []

    def observe(region, index):
        if index == 0:
            observer = Catalog(root).initialize()
            ticket = open_ticket(observer, volume)
            paused.append((ticket, finish(observer, ticket)))

    two = recalibrate_volume(Catalog(root, after_write=observe).initialize(), volume, 2)
    if len(paused) != 1:
        raise AssertionError('the first written page was not observed')
    during, during_pages = paused[0]
    offline = Catalog(root).initialize()
    old_pages = [first] + finish(offline, old)
    current = open_ticket(offline, volume)
    current_pages = finish(offline, current)
    totals = [reconcile_ticket(offline, ticket.ticket_id).totals.total_um for ticket in (old, during, current)]
    expect_busy(offline, volume)
    close_ticket(offline, old.ticket_id)
    expect_busy(offline, volume)
    close_ticket(offline, during.ticket_id)
    three = recalibrate_volume(offline, volume, 3)
    later = open_ticket(offline, volume)
    later_pages = finish(offline, later)
    totals.append(reconcile_ticket(offline, later.ticket_id).totals.total_um)
    for ticket in (current, later):
        close_ticket(offline, ticket.ticket_id)

    if len({initial.initial_revision, two.revision, three.revision}) != 3:
        raise AssertionError('initial seal and both recalibrations must publish distinct revisions')
    original = [
        [initial.initial_revision, 0, 'sheet-A', 0, 'A', 'pump-A/inboard', 7],
        [initial.initial_revision, 1, 'sheet-B', 0, 'B', 'pump-B/outboard', 11],
    ]
    expected = {'after_failure': original, 'old': original, 'during': original, 'double': [
        [two.revision, 0, 'sheet-A', 0, 'A', 'pump-A/inboard', 14],
        [two.revision, 1, 'sheet-B', 0, 'B', 'pump-B/outboard', 22],
    ], 'triple': [
        [three.revision, 0, 'sheet-A', 0, 'A', 'pump-A/inboard', 21],
        [three.revision, 1, 'sheet-B', 0, 'B', 'pump-B/outboard', 33],
    ]}
    actual = {'after_failure': trace(after_failure), 'old': trace(old_pages), 'during': trace(during_pages),
              'double': trace(current_pages), 'triple': trace(later_pages)}
    if actual != expected or totals != [18, 18, 36, 54]:
        raise AssertionError(f'领票或班账与手算不符: pages={actual!r}, totals={totals!r}')
    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': 'cross_publish', 'input': {'source': material, 'multipliers': [2, 3]},
        'expected': expected, 'actual': actual, 'downstream_expected': [18, 18, 36, 54],
        'downstream_actual': totals,
        'code_paths': ['intake/initial_seal.py', 'calibration/job.py', 'archive/publication.py',
                       'delivery/read_page.py', 'delivery/close.py', 'reconcile/statement.py'],
        'oracle': '按样例的两个原页手算各倍率读值及微米合计，逐项对照测次、原页和出处',
    }, ensure_ascii=False))


def main():
    sample = Path(__file__).resolve().parent / 'samples' / 'pump_shift.json'
    material = json.loads(sample.read_text(encoding='utf-8'))
    with TemporaryDirectory() as directory:
        run_shift(Path(directory) / 'archive', material)


if __name__ == '__main__':
    main()
