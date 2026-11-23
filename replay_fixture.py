import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from archive.catalog import Catalog
from archive.page_io import read_payload_page, write_payload_page
from archive.region import RegionsOccupiedError
from archive.version_map import load_current_revision
from calibration.job import recalibrate_volume
from delivery.close import close_ticket
from delivery.read_page import next_page
from delivery.ticket import open_ticket
from intake.initial_seal import seal_source
from reconcile.statement import reconcile_ticket

SAMPLE = Path(__file__).resolve().parent / "samples" / "pump_shift.json"
CUT_EXIT = 73


def change_raw(catalog, region, page_index, raw_um, multiplier):
    page = read_payload_page(catalog, region, page_index)
    (item,) = page.readings
    changed = replace(item, raw_um=raw_um, value_um=raw_um * multiplier)
    write_payload_page(catalog, region, replace(page, readings=(changed,)))


def _cut_marker(region, page_index):
    return f"replay-fixture candidate-written {region}/{page_index}"


def _writer(root, volume_id, target_region, page_index):
    def after_write(region, index):
        if (region, index) == (target_region, page_index):
            print(_cut_marker(region, index), flush=True)
            os._exit(CUT_EXIT)

    cat = Catalog(root, after_write=after_write).initialize()
    recalibrate_volume(cat, volume_id, 3)
    raise AssertionError("writer returned before the requested interruption")


def run_cut(root, volume_id, region, page_index):
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "_writer",
               str(root), volume_id, region, str(page_index)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    marker = _cut_marker(region, page_index)
    if result.returncode != CUT_EXIT or marker not in result.stdout.splitlines():
        raise AssertionError(f"interruption not reached: {result.returncode}, "
                             f"stdout={result.stdout!r}, stderr={result.stderr!r}")
    return marker


def _v1_case(root, page_index):
    material = json.loads(SAMPLE.read_text(encoding="utf-8"))
    cat = Catalog(root).initialize()
    initial = seal_source(cat, material)
    volume = initial.volume_id
    with cat.read_transaction() as conn:
        old_region = load_current_revision(conn, volume).region
    old = open_ticket(cat, volume)
    first = next_page(cat, old.ticket_id)
    two = recalibrate_volume(cat, volume, 2)
    change_raw(cat, two.region, 1, 19, 2)
    try:
        recalibrate_volume(cat, volume, 3)
    except RegionsOccupiedError:
        pass
    else:
        raise AssertionError("ordinary ticket failed to protect its region")
    second = next_page(cat, old.ticket_id)
    total = reconcile_ticket(cat, old.ticket_id).totals.total_um
    values = [page.page.readings[0].value_um for page in (first, second)]
    if (values, total) != ([7, 11], 18):
        raise AssertionError(f"ordinary ticket changed: {values}, {total}")
    close_ticket(cat, old.ticket_id)
    marker = run_cut(root, volume, old_region, page_index)

    reopened = Catalog(root).initialize()
    with reopened.read_transaction() as conn:
        current = load_current_revision(conn, volume)
    if current != two:
        raise AssertionError("interrupted candidate became current")
    raw_after_cut = read_payload_page(reopened, old_region, 1).readings[0].raw_um
    if raw_after_cut != (11 if page_index == 0 else 19):
        raise AssertionError(f"unexpected V1 raw page after interruption: {raw_after_cut}")
    recalibrate_volume(reopened, volume, 3)
    newest = open_ticket(reopened, volume)
    retried = [next_page(reopened, newest.ticket_id).page.readings[0].value_um for _ in values]
    if retried != [21, 57]:
        raise AssertionError(f"unexpected V1 control values: {retried}")
    return {"cut": marker, "ordinary_total_um": total, "old_region_b_raw_um": raw_after_cut,
            "retried_um": retried}


def main():
    if len(sys.argv) == 6 and sys.argv[1] == "_writer":
        _, _, root, volume_id, region, page_index = sys.argv
        _writer(root, volume_id, region, int(page_index))
    elif len(sys.argv) == 1:
        with TemporaryDirectory() as directory:
            cases = [_v1_case(Path(directory) / str(index), index) for index in (0, 1)]
        for case in cases:
            print("REPLAY_V1_CONTROL " + json.dumps(case))
    else:
        raise SystemExit("usage: python3 -B -m replay_fixture")


if __name__ == "__main__":
    main()
