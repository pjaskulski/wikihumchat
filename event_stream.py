"""Most SSE/WSGI: pętla asyncio działa w wątku obsługującym odpowiedź HTTP."""

import asyncio
from collections import deque
import json

from errors import ChatError


def encode_event(kind, data):
    return f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class ChatEventStream:
    def __init__(self, service, payload, release, finished, *, heartbeat_seconds=10):
        self.service = service
        self.payload = payload
        self.release = release
        self.finished = finished
        self.heartbeat_seconds = heartbeat_seconds
        self.events = deque(maxlen=64)
        self.iterator = self._iterate()
        self.closed = False
        self.closing = False

    def __iter__(self):
        return self.iterator

    async def _wait(self, task):
        # Operacje wykonują się w tej samej pętli: bez wątków tła i polling-u.
        wake = asyncio.create_task(self.ready.wait())
        try:
            await asyncio.wait({wake, task}, timeout=self.heartbeat_seconds,
                               return_when=asyncio.FIRST_COMPLETED)
        finally:
            wake.cancel()
            await asyncio.gather(wake, return_exceptions=True)

    def _iterate(self):
        code, result_status = "disconnected", 499
        try:
            # Runner anuluje zadanie i zamyka klientów także po GeneratorExit.
            with asyncio.Runner() as runner:
                self.ready = asyncio.Event()

                def progress(event):
                    self.events.append(event)
                    self.ready.set()

                task = runner.get_loop().create_task(
                    self.service.reply(self.payload, on_progress=progress),
                )
                # Pierwszy bajt dociera przed wywołaniem usług zewnętrznych.
                yield ": connected\n\n"
                while True:
                    if self.events:
                        yield encode_event("progress", self.events.popleft())
                        continue
                    if task.done():
                        try:
                            result = task.result()
                        except ChatError as error:
                            code, result_status = error.code, error.status
                            yield encode_event("error", {"error": {
                                "code": error.code, "message": error.message,
                            }, "status": error.status})
                        except Exception:
                            code, result_status = "internal_error", 500
                            yield encode_event("error", {"error": {
                                "code": code,
                                "message": "Wystąpił błąd aplikacji. Spróbuj ponownie.",
                            }, "status": 500})
                        else:
                            code, result_status = "ok", 200
                            yield encode_event("result", result)
                        break
                    self.ready.clear()
                    runner.run(self._wait(task))
                    if not self.events and not task.done():
                        yield ": keep-alive\n\n"
        except Exception:
            code, result_status = "internal_error", 500
            if not self.closing:
                yield encode_event("error", {"error": {
                    "code": code, "message": "Wystąpił błąd aplikacji. Spróbuj ponownie.",
                }, "status": 500})
        finally:
            self._finish(code, result_status)

    def _finish(self, code, status):
        if not self.closed:
            self.closed = True
            self.release()
            self.finished(code, status)

    def close(self):
        self.closing = True
        try:
            self.iterator.close()
        finally:
            # Response może być zamknięty przed rozpoczęciem iteracji.
            self._finish("disconnected", 499)
