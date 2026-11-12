from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier, Event, Lock

import pytest

from config import Settings
from errors import ChatError
from traffic_control import TrafficControl


def settings(**values):
    return replace(Settings(model_api_key="secret"), **values)


def test_sliding_window_expires_individual_attempts_and_rounds_retry_up():
    now = [0.0]
    guard = TrafficControl(settings(rate_limit_requests=2, rate_limit_window_seconds=10),
                           clock=lambda: now[0])
    with guard.admit("192.0.2.1"):
        pass
    now[0] = 5
    with guard.admit("192.0.2.1"):
        pass
    now[0] = 9.5
    with pytest.raises(ChatError) as error:
        with guard.admit("192.0.2.1"):
            pytest.fail("Przekroczony limit nie może udzielić dostępu")
    assert error.value.status == 429
    assert error.value.retry_after == 1
    now[0] = 10
    with guard.admit("192.0.2.1"):
        pass
    with pytest.raises(ChatError) as error:
        with guard.admit("192.0.2.1"):
            pass
    assert error.value.retry_after == 5


def test_client_capacity_does_not_evict_live_limits_and_recovers_after_expiry():
    now = [0]
    guard = TrafficControl(settings(rate_limit_requests=1, rate_limit_max_clients=1),
                           clock=lambda: now[0])
    with guard.admit("192.0.2.1"):
        pass
    with pytest.raises(ChatError) as error:
        with guard.admit("192.0.2.2"):
            pass
    assert error.value.code == "limit_capacity"
    assert error.value.status == 503
    with pytest.raises(ChatError) as error:
        with guard.admit("192.0.2.1"):
            pass
    assert error.value.code == "rate_limit"
    now[0] = 60
    with guard.admit("192.0.2.2"):
        pass


def test_exception_releases_concurrency_slot():
    guard = TrafficControl(settings(chat_max_concurrent_requests=1))
    with pytest.raises(RuntimeError):
        with guard.admit("192.0.2.1"):
            raise RuntimeError("failure")
    with guard.admit("192.0.2.2"):
        pass


def test_parallel_admission_never_exceeds_limit():
    guard = TrafficControl(settings(chat_max_concurrent_requests=2, rate_limit_requests=20))
    start = Barrier(7)
    release = Event()
    rejected = Event()
    lock = Lock()
    counts = {"active": 0, "peak": 0, "rejected": 0}

    def worker():
        start.wait(timeout=5)
        try:
            with guard.admit("192.0.2.1"):
                with lock:
                    counts["active"] += 1
                    counts["peak"] = max(counts["peak"], counts["active"])
                assert release.wait(timeout=5)
                with lock:
                    counts["active"] -= 1
        except ChatError as error:
            assert error.code == "service_busy"
            with lock:
                counts["rejected"] += 1
                if counts["rejected"] == 4:
                    rejected.set()

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(worker) for _ in range(6)]
        start.wait(timeout=5)
        try:
            assert rejected.wait(timeout=5)
        finally:
            release.set()
        for future in futures:
            future.result(timeout=5)
    assert counts == {"active": 0, "peak": 2, "rejected": 4}


@pytest.mark.parametrize("trusted,peer,forwarded,expected", [
    ("", "192.0.2.1", "198.51.100.1", "192.0.2.1"),
    ("10.0.0.0/8", "192.0.2.1", "198.51.100.1", "192.0.2.1"),
    ("10.0.0.0/8", "10.0.0.1", "198.51.100.1", "198.51.100.1"),
    ("10.0.0.0/8", "10.0.0.1", "192.0.2.99, 198.51.100.1, 10.0.0.2", "198.51.100.1"),
    ("10.0.0.0/8", "10.0.0.1", "not-an-ip", "10.0.0.1"),
    ("10.0.0.0/8", "10.0.0.1", "fe80::1%zone", "10.0.0.1"),
    ("::1/128", "::1", "2001:db8:0:0::1", "2001:db8::1"),
    ("", "::ffff:192.0.2.1", None, "192.0.2.1"),
    ("", "", "198.51.100.1", "unknown"),
])
def test_proxy_trust_and_ip_normalization(trusted, peer, forwarded, expected):
    guard = TrafficControl(settings(trusted_proxy_cidrs=trusted))
    assert guard.client_address(peer, forwarded) == expected


def test_ipv4_mapped_alias_does_not_get_an_additional_quota():
    guard = TrafficControl(settings(rate_limit_requests=1))
    with guard.admit("::ffff:192.0.2.1"):
        pass
    with pytest.raises(ChatError) as error:
        with guard.admit("192.0.2.1"):
            pass
    assert error.value.code == "rate_limit"
