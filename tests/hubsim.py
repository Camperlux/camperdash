"""Load the hub's code under CPython, for tests.

The hub runs MicroPython on hardware: its modules import machine, network,
bluetooth and friends, and main.py starts the whole hub the moment it is
imported. This stands in for the hardware and loads main.py *without* its
start-up block, so its functions - the alert rules, frost protection, the
Wi-Fi decisions - can be called directly.

    import hubsim
    hub = hubsim.load_main()          # a module: hub._alert_tick(), hub.state, ...
"""
import asyncio
import os
import sys
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PICO = os.path.join(REPO, "pico")
DISPLAY = os.path.join(REPO, "display")

# MicroPython's extras on the standard modules
_t0 = time.monotonic()
time.ticks_ms = lambda: int((time.monotonic() - _t0) * 1000)
time.ticks_diff = lambda a, b: a - b
time.ticks_add = lambda a, b: a + b
time.sleep_ms = lambda ms: time.sleep(ms / 1000)
asyncio.sleep_ms = lambda ms: asyncio.sleep(ms / 1000)
asyncio.wait_for_ms = lambda aw, ms: asyncio.wait_for(aw, ms / 1000)
import gc                                                        # noqa: E402
gc.mem_free = lambda: 300000
gc.mem_alloc = lambda: 100000


class _Anything:
    """Stands in for any hardware object: every attribute and call works and
    returns another stand-in, so code that only touches hardware runs."""
    def __init__(self, *a, **k):
        pass

    def __call__(self, *a, **k):
        return _Anything()

    def __getattr__(self, name):
        return _Anything()

    def __bool__(self):
        return False

    def __iter__(self):
        return iter(())


def _stub(name, **attrs):
    m = types.ModuleType(name)
    m.__getattr__ = lambda n: _Anything()
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


def hardware_stubs():
    # MicroPython's u-prefixed names for standard modules
    import binascii, errno, hashlib, json, random, select, socket, struct  # noqa: E401
    for u, std in (("uos", os), ("ujson", json), ("utime", time), ("ustruct", struct),
                   ("ubinascii", binascii), ("uhashlib", hashlib), ("uselect", select),
                   ("usocket", socket), ("uasyncio", asyncio), ("uerrno", errno),
                   ("urandom", random)):
        sys.modules.setdefault(u, std)
    for name in ("machine", "network", "bluetooth", "ntptime", "neopixel", "ucryptolib",
                 "micropython", "rp2", "onewire", "ds18x20"):
        if name not in sys.modules:
            _stub(name)
    sys.modules["micropython"].const = lambda v: v


def load_config(**overrides):
    """config.example.py as the config module, with overrides."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("config", os.path.join(PICO, "config.example.py"))
    cfg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cfg)
    for k, v in overrides.items():
        setattr(cfg, k, v)
    sys.modules["config"] = cfg
    return cfg


def load_main(workdir=None, **cfg_overrides):
    """pico/main.py as a module, up to (not including) its start-up block."""
    hardware_stubs()
    load_config(**cfg_overrides)
    if PICO not in sys.path:
        sys.path.insert(0, PICO)
    # its files (settings.json, logs) go to a scratch folder, not the repo
    if workdir:
        os.chdir(workdir)
    src = open(os.path.join(PICO, "main.py"), encoding="utf-8").read()
    cut = src.index("# Self-heal:")
    mod = types.ModuleType("hub_main")
    mod.__file__ = os.path.join(PICO, "main.py")
    exec(compile(src[:cut], mod.__file__, "exec"), mod.__dict__)
    return mod


def run(coro):
    """Run one coroutine to completion (for the hub's async functions)."""
    return asyncio.new_event_loop().run_until_complete(coro)
