"""
Covers ios_bot/hub_sync_signal.py -- the request/clear flag that lets a
team/match/tournament/player write ask the Hub sync scheduler to run on its
next tick instead of waiting out the full polling interval.
"""
from conftest import load_module_from_file


def _new_signal():
    return load_module_from_file("ios_bot/hub_sync_signal.py")


def test_no_pending_request_by_default():
    signal = _new_signal()
    assert signal.has_pending_request() is False
    assert signal.pending_reason() == ""


def test_request_sets_pending_and_reason():
    signal = _new_signal()
    signal.request_hub_sync_soon("team changed")
    assert signal.has_pending_request() is True
    assert signal.pending_reason() == "team changed"


def test_clear_resets_pending_and_reason():
    signal = _new_signal()
    signal.request_hub_sync_soon("match changed")
    signal.clear_pending_request()
    assert signal.has_pending_request() is False
    assert signal.pending_reason() == ""


def test_later_request_overwrites_earlier_reason():
    signal = _new_signal()
    signal.request_hub_sync_soon("team changed")
    signal.request_hub_sync_soon("player changed")
    assert signal.pending_reason() == "player changed"
