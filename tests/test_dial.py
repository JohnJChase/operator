"""Dial decoder checks."""

from operator_os.dial import DialDecoder, pulses_to_digit


def test_digit_zero_is_ten_pulses():
    assert pulses_to_digit(10) == 0


def test_digit_two():
    assert pulses_to_digit(2) == 2


def test_digit_two_does_not_split_into_ones():
    """Two pulses in one burst must decode as 2, not 1 then 1."""
    d = DialDecoder(digit_done_ms=700)
    d.pulse(0)
    d.pulse(60)  # mid-burst gap well under digit_done_ms
    assert d.poll(100) is None
    assert d.poll(400) is None
    assert d.poll(760) == 2
    assert d.poll(2000) is None


def test_no_leading_pulse_suppression():
    d = DialDecoder(digit_done_ms=100)
    d.pulse(0)  # first pulse counts
    assert d.poll(150) == 1
