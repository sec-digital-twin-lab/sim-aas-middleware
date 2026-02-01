"""Time and period parsing utilities."""

from __future__ import annotations

from typing import Optional


def parse_period(period: str) -> Optional[int]:
    """Parse period string into hours.

    Args:
        period: Period string in format '<number><unit>' where unit is:
                - 'h': hours
                - 'd': days (24 hours)
                - 'w': weeks (168 hours)

    Returns:
        Number of hours, or None if format is invalid

    Examples:
        >>> parse_period('24h')
        24
        >>> parse_period('7d')
        168
        >>> parse_period('1w')
        168
        >>> parse_period('invalid')
        None
    """
    if not period or len(period) < 2:
        return None

    try:
        unit = period[-1:].lower()
        number = int(period[:-1])

        multiplier = {'h': 1, 'd': 24, 'w': 7 * 24}
        if unit not in multiplier:
            return None

        return number * multiplier[unit]

    except (ValueError, KeyError):
        return None
