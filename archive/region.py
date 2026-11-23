from dataclasses import dataclass
from pathlib import Path
from sqlite3 import Connection


REGION_IDS = ('a', 'b')


class RegionsOccupiedError(RuntimeError):
    pass


@dataclass(frozen=True)
class RegionReservation:
    region: str
    revision: int


def region_path(root: Path, region: str) -> Path:
    if region not in REGION_IDS:
        raise ValueError(f'unknown payload region {region!r}')
    return root / 'payload' / region


def reserve_region(conn: Connection, revision: int, protected) -> RegionReservation:
    reserved = {row[0] for row in conn.execute('SELECT region FROM reservations')}
    unavailable = set(protected) | reserved
    for region in REGION_IDS:
        if region not in unavailable:
            conn.execute('INSERT INTO reservations(region, revision) VALUES (?, ?)', (region, revision))
            return RegionReservation(region, revision)
    raise RegionsOccupiedError('both payload regions are in use')


def release_reservation(conn: Connection, reservation: RegionReservation) -> bool:
    result = conn.execute(
        'DELETE FROM reservations WHERE region = ? AND revision = ?',
        (reservation.region, reservation.revision),
    )
    return result.rowcount == 1
