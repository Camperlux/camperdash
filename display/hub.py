# Talks to the van hub over HTTP, without blocking the screen.
#
# urequests would stall everything - touch, drawing - for as long as the hub
# takes to answer, and a heater command can take a minute while the hub retries
# over Bluetooth. This uses asyncio streams instead, so the page keeps
# responding while it waits.

import asyncio
import json
import time


class HubError(Exception):
    pass


async def _request(host, method, path, body=None, token="", timeout_ms=8000):
    port = 80
    if ":" in host:                          # "address:port", for testing
        host, port = host.split(":", 1)
        port = int(port)

    async def go():
        reader, writer = await asyncio.open_connection(host, port)
        try:
            head = "%s %s HTTP/1.0\r\nHost: %s\r\nConnection: close\r\n" % (method, path, host)
            data = b""
            if body is not None:
                data = json.dumps(body).encode()
                head += "Content-Type: application/json\r\nContent-Length: %d\r\n" % len(data)
            if token:
                head += "X-Token: %s\r\n" % token
            writer.write(head.encode() + b"\r\n" + data)
            await writer.drain()
            raw = b""
            while True:
                chunk = await reader.read(2048)
                if not chunk:
                    break
                raw += chunk
            return raw
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    try:
        raw = await asyncio.wait_for_ms(go(), timeout_ms)
    except asyncio.TimeoutError:
        raise HubError("timed out")
    except OSError as e:
        raise HubError("no connection (%s)" % e)
    sep = raw.find(b"\r\n\r\n")
    if sep < 0:
        raise HubError("bad response")
    status = raw[:raw.find(b"\r\n")].split(b" ")
    code = int(status[1]) if len(status) > 1 else 0
    try:
        obj = json.loads(raw[sep + 4:])
    except ValueError:
        raise HubError("not the hub (HTTP %d)" % code)
    if code == 403:
        raise HubError("the hub wants its password")
    if code != 200:
        raise HubError((obj.get("error") if isinstance(obj, dict) else None)
                       or "HTTP %d" % code)
    return obj


DISCOVERY_PORT = 50505            # must match DISCOVERY_PORT in the hub's main.py


async def discover(timeout_ms=1500):
    """Broadcast "camperdash?" and return the address of the hub that answers,
    or None. The router moves the hub about and it does not answer mDNS, so
    this is how the display finds it on whatever network both are on."""
    import socket
    import time
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except (AttributeError, OSError):
            pass
        s.setblocking(False)
        s.sendto(b"camperdash?", ("255.255.255.255", DISCOVERY_PORT))
        t0 = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), t0) < timeout_ms:
            try:
                data, addr = s.recvfrom(64)
            except OSError:
                await asyncio.sleep_ms(50)
                continue
            if data.startswith(b"camperdash hub"):
                return addr[0]
    except OSError:
        pass
    finally:
        s.close()
    return None


class Hub:
    """Finds the hub and remembers where it was. Order of trying: the address
    that last worked, then a discovery broadcast, then the configured
    addresses (the hub's own hotspot, 192.168.4.1)."""

    def __init__(self, hosts, token=""):
        self.hosts = list(hosts)
        self.token = token
        self.host = None
        self._search_at = None       # ticks_ms before which not to search again
        self._misses = 0             # requests in a row the known hub did not answer
        self.fail_since = None       # ticks_ms requests started failing; None while answering

    async def get(self, path, timeout_ms=6000):
        """GET from the hub, finding it first if need be. Notes how long
        requests have been failing, for the display to judge whether the hub is
        on another network: no requests (a game, say) is not a failure."""
        try:
            obj = await self._get(path, timeout_ms)
        except HubError:
            if self.fail_since is None:
                self.fail_since = time.ticks_ms()
            raise
        self.fail_since = None
        return obj

    async def _get(self, path, timeout_ms):
        err = None
        tried = []
        if self.host:
            tried.append(self.host)
            try:
                obj = await _request(self.host, "GET", path, timeout_ms=timeout_ms)
                self._misses = 0
                return obj
            except HubError as e:
                err = e
                # One slow answer is not a lost hub: it is busy on Bluetooth
                # for a second or two at a time. Only a run of misses is worth
                # a search - and until one finds it elsewhere, the address
                # that worked is kept, so the next request tries it again.
                self._misses += 1
                if self._misses < 3:
                    raise
        # Searching means a broadcast and requests to the fallback addresses -
        # on the home network 192.168.4.1 is the router. Several tasks ask
        # every few seconds, so search at most every 20 s, not on every miss.
        if self._search_at is not None and time.ticks_diff(self._search_at, time.ticks_ms()) > 0:
            raise err or HubError("hub not found")
        self._search_at = time.ticks_add(time.ticks_ms(), 20000)
        found = await discover()
        for h in ([found] if found else []) + self.hosts:
            if h in tried:
                continue
            tried.append(h)
            try:
                obj = await _request(h, "GET", path, timeout_ms=timeout_ms)
                if self.host != h:
                    print("hub found at", h, "(discovered)" if h == found else "")
                self.host = h
                self._search_at = None
                self._misses = 0
                return obj
            except HubError as e:
                err = e
        raise err or HubError("hub not found")

    async def get_private(self, path, timeout_ms=6000):
        """A GET that carries the API token: for what the hub keeps behind it."""
        if not self.host:
            await self.get("/api/level")          # cheapest way to find the hub
        return await _request(self.host, "GET", path, token=self.token,
                              timeout_ms=timeout_ms)

    async def post(self, path, body, timeout_ms=5000):
        if not self.host:
            await self.get("/api/level")          # cheapest way to find the hub
        return await _request(self.host, "POST", path, body, self.token,
                              timeout_ms=timeout_ms)

    async def heater(self, body):
        """Send a heater command. The hub may retry over Bluetooth for about a
        minute before it gives up, so this waits a generous 90 s."""
        if not self.host:
            await self.get("/api/level")          # cheapest way to find the hub
        return await _request(self.host, "POST", "/api/heater/cmd", body,
                              self.token, timeout_ms=90000)
