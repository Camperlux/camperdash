# The name of the place the van is at, from its GPS position: "Near Hawes".
#
# OpenStreetMap's reverse geocoder (Nominatim). Its usage policy asks for an
# application that says who it is, at most a request a second, and results
# kept rather than asked for again - so this is called only when the forecast
# is re-fetched for a new position (main.py: the van 10 km from the last one,
# no more than every 10 minutes), and never while parked. Nothing about the
# owner is sent: only the position, rounded to about 100 m.
#
# HTTPS (the service requires it), which the RP2350 hub has the memory for.

import asyncio
import json

HOST = "nominatim.openstreetmap.org"
AGENT = "Camperlux-van-hub/1.0"
_MAX = 8192                   # a reverse lookup is about 1-2 KB

# the most local useful name first
_KEYS = ("village", "town", "city", "hamlet", "suburb", "municipality", "locality",
         "county", "state")


def _name(d):
    a = d.get("address") or {}
    for k in _KEYS:
        if a.get(k):
            return a[k]
    return (d.get("name") or "").strip() or None


async def lookup(lat, lon):
    """The place's name, or None. Raises OSError on a network failure."""
    import ssl
    r = w = None
    try:
        r, w = await asyncio.wait_for(asyncio.open_connection(HOST, 443, ssl=True), 20)
        w.write(("GET /reverse?format=jsonv2&zoom=14&accept-language=en&lat=%.3f&lon=%.3f "
                 "HTTP/1.1\r\nHost: %s\r\nUser-Agent: %s\r\nConnection: close\r\n\r\n"
                 % (lat, lon, HOST, AGENT)).encode())
        await asyncio.wait_for(w.drain(), 15)
        raw = b""
        while len(raw) < _MAX:
            part = await asyncio.wait_for(r.read(1024), 15)
            if not part:
                break
            raw += part
    finally:
        if w is not None:
            try:
                w.close()
                await w.wait_closed()
            except Exception:
                pass
    head, _, body = raw.partition(b"\r\n\r\n")
    if b" 200" not in head.split(b"\r\n")[0]:
        raise OSError("place lookup said: %s" % head.split(b"\r\n")[0])
    if b"chunked" in head.lower():
        out = b""
        while body:
            line, _, rest = body.partition(b"\r\n")
            try:
                n = int(line.split(b";")[0], 16)
            except ValueError:
                break
            if n == 0:
                break
            out += rest[:n]
            body = rest[n + 2:]
        body = out
    return _name(json.loads(body))
