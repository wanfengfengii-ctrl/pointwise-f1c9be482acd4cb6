from dataclasses import replace

from archive.page_codec import ArchivedReading


class InvalidMultiplierError(ValueError):
    pass


def validate_multiplier(multiplier) -> int:
    if type(multiplier) is not int or multiplier <= 0:
        raise InvalidMultiplierError('multiplier must be a positive integer')
    return multiplier


def convert_reading(reading: ArchivedReading, multiplier: int) -> ArchivedReading:
    value = None if reading.raw_um is None else reading.raw_um * multiplier
    return replace(reading, value_um=value)
