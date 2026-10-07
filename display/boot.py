# Runs first on every start, and on every wake from deep sleep. Shut down from
# the screen, the display wakes for a moment every minute to see whether it has
# been plugged in to charge: if not, shutdown.check() puts it straight back to
# sleep and main.py never runs. See shutdown.py.
import machine

if machine.reset_cause() == machine.DEEPSLEEP_RESET:
    try:
        import shutdown
        shutdown.check()
    except Exception as e:                   # never let this keep the display off
        print("shutdown check:", e)

# Updates over the air (ota.py): a new version is on trial until main.py
# confirms it; one that will not start is put back. See otaboot.py.
try:
    import otaboot
    otaboot.boot()
except Exception as e:
    print("ota boot:", e)
