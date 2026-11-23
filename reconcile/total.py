from dataclasses import dataclass


@dataclass(frozen=True)
class Totals:
    total_um: int
    valid_count: int
    missing_count: int


def sum_readings(readings) -> Totals:
    total_um = 0
    valid_count = 0
    missing_count = 0
    for reading in readings:
        if reading.value_um is None:
            missing_count += 1
        else:
            valid_count += 1
            total_um += reading.value_um
    return Totals(total_um, valid_count, missing_count)
