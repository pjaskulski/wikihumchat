"""Ograniczenia ruchu w pamięci jednego procesu, współdzielone przez wątki."""

from collections import OrderedDict, deque
from contextlib import contextmanager
from hashlib import blake2b
from ipaddress import ip_address, ip_network
from math import ceil
from secrets import token_bytes
from threading import Lock
from time import monotonic

from errors import ChatError


def normalize_ip(value):
    if not isinstance(value, str) or "%" in value:
        raise ValueError
    address = ip_address(value)
    if address.version == 6:
        return address.ipv4_mapped or address
    return address


class TrafficControl:
    def __init__(self, settings, *, clock=monotonic):
        self.settings = settings
        self.clock = clock
        self._lock = Lock()
        self._clients = OrderedDict()
        self._active = 0
        self._salt = token_bytes(32)
        self._trusted = tuple(
            ip_network(value.strip(), strict=False)
            for value in settings.trusted_proxy_cidrs.split(",") if value.strip()
        )

    def client_address(self, peer, forwarded_for):
        try:
            address = normalize_ip(peer)
        except ValueError:
            # Brak poprawnego REMOTE_ADDR nie może generować nowych tożsamości.
            return "unknown"

        def trusted(candidate):
            return any(candidate in network for network in self._trusted)

        if not trusted(address) or not forwarded_for:
            return str(address)
        # Ogranicz rozmiar danych analizowanych z nagłówka proxy.
        if len(forwarded_for) > 2048:
            return str(address)
        parts = forwarded_for.split(",")
        if len(parts) > 20:
            return str(address)
        try:
            chain = [normalize_ip(value.strip()) for value in parts]
        except ValueError:
            return str(address)
        # Proxy musi dopisywać rzeczywisty adres nadawcy lub nadpisywać XFF.
        for candidate in reversed(chain):
            address = candidate
            if not trusted(candidate):
                break
        return str(address)

    @contextmanager
    def admit(self, peer, forwarded_for=None):
        address = self.client_address(peer or "", forwarded_for)
        identity = blake2b(address.encode(), key=self._salt, digest_size=20).digest()
        with self._lock:
            now = self.clock()
            cutoff = now - self.settings.rate_limit_window_seconds
            # Kolejność ostatniego przyjętego żądania pozwala usuwać wygasłe
            # wpisy bez skanowania całej mapy przy każdym żądaniu.
            while self._clients:
                first = next(iter(self._clients.values()))
                if first[-1] > cutoff:
                    break
                self._clients.popitem(last=False)
            bucket = self._clients.get(identity)
            if bucket is None:
                if len(self._clients) >= self.settings.rate_limit_max_clients:
                    retry = max(1, ceil(next(iter(self._clients.values()))[-1] - cutoff))
                    raise ChatError(
                        "limit_capacity", "Usługa jest zajęta. Spróbuj ponownie później.",
                        503, retry_after=retry,
                    )
                bucket = self._clients[identity] = deque()
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.settings.rate_limit_requests:
                retry = max(1, ceil(bucket[0] - cutoff))
                raise ChatError(
                    "rate_limit", f"Przekroczono limit zapytań. Spróbuj ponownie za {retry} s.",
                    429, retry_after=retry,
                )
            # Także błędy wejścia i zajętość zużywają limit prób z danego IP.
            bucket.append(now)
            self._clients.move_to_end(identity)
            if self._active >= self.settings.chat_max_concurrent_requests:
                raise ChatError(
                    "service_busy", "Wszystkie miejsca obsługi są zajęte. Spróbuj ponownie za 1 s.",
                    503, retry_after=1,
                )
            self._active += 1
        try:
            yield
        finally:
            with self._lock:
                self._active -= 1
