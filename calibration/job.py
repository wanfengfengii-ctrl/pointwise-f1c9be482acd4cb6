from dataclasses import dataclass

from archive.catalog import Catalog
from archive.publication import (
    abort_candidate,
    candidate_session,
    publish_candidate,
    reserve_candidate,
    write_candidate_page,
)
from archive.region import RegionReservation
from archive.version_map import RevisionLocation, load_current_revision
from calibration.completeness import verify_candidate_coverage
from calibration.convert import validate_multiplier
from calibration.page_plan import iter_calibrated_pages
from intake.manifest import load_manifest


@dataclass(frozen=True)
class CalibrationJob:
    volume_id: str
    multiplier: int
    base: RevisionLocation
    reservation: RegionReservation


def _change_state(conn, job, expected, desired):
    result = conn.execute(
        'UPDATE calibration_jobs SET state = ? WHERE revision = ? AND state = ?',
        (desired, job.reservation.revision, expected),
    )
    if result.rowcount != 1:
        raise RuntimeError(f'calibration cannot transition from {expected} to {desired}')


def recalibrate_volume(catalog: Catalog, volume_id: str, multiplier: int) -> RevisionLocation:
    multiplier = validate_multiplier(multiplier)
    with candidate_session(catalog):
        with catalog.write_transaction() as conn:
            base = load_current_revision(conn, volume_id)
            manifest = load_manifest(conn, volume_id)
            reservation = reserve_candidate(conn, volume_id, len(manifest.pages))
            job = CalibrationJob(volume_id, multiplier, base, reservation)
            conn.execute(
                "INSERT INTO calibration_jobs(revision, base_revision, multiplier, state) VALUES (?, ?, ?, 'writing')",
                (reservation.revision, base.revision, hex(job.multiplier)),
            )

        try:
            for page in iter_calibrated_pages(catalog, job.base, manifest, job.multiplier):
                write_candidate_page(catalog, job.reservation, page)
            verify_candidate_coverage(catalog, job.reservation, job.base, manifest)
            with catalog.write_transaction() as conn:
                _change_state(conn, job, 'writing', 'ready')

            def finalize(conn):
                if load_current_revision(conn, job.volume_id) != job.base:
                    raise RuntimeError('base revision changed before publication')
                _change_state(conn, job, 'ready', 'succeeded')

            return publish_candidate(catalog, job.reservation, finalize)
        except BaseException:
            with catalog.write_transaction() as conn:
                if abort_candidate(conn, job.reservation):
                    conn.execute(
                        "UPDATE calibration_jobs SET state = 'failed' WHERE revision = ?",
                        (job.reservation.revision,),
                    )
            raise
