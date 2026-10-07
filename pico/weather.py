# Forecast cache for the hub.
#
# The dashboard used to fetch open-meteo straight from the browser, which meant
# the *viewing device* needed internet.  On the van's own hotspot there isn't
# any, so the weather page simply failed.  The hub now fetches the forecast
# itself whenever it has a route out and keeps the last good copy on flash, so
# the page still has something to show on the hotspot - clearly labelled with
# how old it is.
#
# The response is stored raw and served back untouched: no parsing on the Pico,
# so memory use stays flat no matter how big the forecast gets.

import asyncio
import time

HOST = "api.open-meteo.com"
FILE = "weather.json"
META = "weather.meta"
_MAX = 24576          # a sane ceiling; the usual reply is under 8 KB


def _path(lat, lon):
    return ("/v1/forecast?latitude=%s&longitude=%s"
            "&current=temperature_2m,weather_code,wind_speed_10m"
            "&daily=weather_code,temperature_2m_max,temperature_2m_min,"
            "precipitation_sum,wind_speed_10m_max,sunrise,sunset"
            "&hourly=temperature_2m,weather_code,precipitation_probability,"
            "wind_speed_10m"
            "&timezone=auto&forecast_days=7" % (lat, lon))


async def _readline(r):
    return await asyncio.wait_for(r.readline(), 15)


async def _body_to(r, f, chunked, length):
    """Write the response body to an open file, de-chunking if we have to."""
    total = 0
    if chunked:
        while True:
            line = (await _readline(r)).strip()
            if not line:
                continue
            try:
                n = int(line.split(b";")[0], 16)
            except ValueError:
                raise OSError("bad chunk header")
            if n == 0:
                break
            while n > 0:
                part = await asyncio.wait_for(r.read(min(n, 512)), 15)
                if not part:
                    raise OSError("connection closed mid-chunk")
                f.write(part)
                total += len(part)
                n -= len(part)
                if total > _MAX:
                    raise OSError("forecast too large")
            await _readline(r)          # trailing CRLF
    else:
        remaining = length if length is not None else _MAX
        while remaining > 0:
            part = await asyncio.wait_for(r.read(min(remaining, 512)), 15)
            if not part:
                break
            f.write(part)
            total += len(part)
            remaining -= len(part)
    return total


async def fetch(lat, lon):
    """Fetch the forecast and replace the cache. Returns bytes stored."""
    r = w = None
    tmp = FILE + ".tmp"
    try:
        r, w = await asyncio.wait_for(asyncio.open_connection(HOST, 80), 15)
        w.write(b"GET " + _path(lat, lon).encode() + b" HTTP/1.1\r\n")
        w.write(b"Host: " + HOST.encode() + b"\r\n")
        w.write(b"User-Agent: IntrepidVan\r\nConnection: close\r\n\r\n")
        await asyncio.wait_for(w.drain(), 15)

        status = await _readline(r)
        if b"200" not in status:
            raise OSError("forecast service said: %s" % status.strip())
        chunked = False
        length = None
        while True:
            h = await _readline(r)
            if h in (b"\r\n", b"\n", b""):
                break
            hl = h.decode().lower()
            if hl.startswith("transfer-encoding:") and "chunked" in hl:
                chunked = True
            elif hl.startswith("content-length:"):
                try:
                    length = int(hl.split(":", 1)[1].strip())
                except ValueError:
                    length = None

        with open(tmp, "wb") as f:
            n = await _body_to(r, f, chunked, length)
        if n < 100:
            raise OSError("forecast reply was empty")

        import os
        try:
            os.remove(FILE)
        except OSError:
            pass
        os.rename(tmp, FILE)
        with open(META, "w") as f:
            f.write("%d,%s,%s" % (int(time.time()), lat, lon))
        return n
    finally:
        if w is not None:
            try:
                w.close()
                await w.wait_closed()
            except Exception:
                pass


def cached_meta():
    """(fetched_epoch, lat, lon) for the stored forecast, or None."""
    try:
        with open(META) as f:
            parts = f.read().split(",")
        return int(parts[0]), parts[1], parts[2]
    except (OSError, ValueError, IndexError):
        return None
