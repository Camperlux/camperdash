"""CamperDash server — polls the Fogstar BMS and Renogy DC-DC charger over BLE,
sends fire-and-forget commands to the diesel heater, and serves a live dashboard.

Run:  python server.py
Open: http://localhost:5180   (or http://<this-pc-ip>:5180 from your phone)
"""
import asyncio
import os
import threading
import time

from flask import Flask, jsonify, request, send_from_directory

from bms import BMSPoller
from heater import HeaterCommander
from renogy import RenogyPoller
from stats import SessionStats
from victron import VictronMonitor

HERE = os.path.dirname(os.path.abspath(__file__))
FRESH_S = 15.0  # readings older than this aren't combined into derived values

STATIC = os.path.join(HERE, "..", "static")      # the dashboard pages, at the top of the repo
app = Flask(__name__, static_folder=STATIC, static_url_path="")
bms = BMSPoller()
renogy = RenogyPoller()
victron = VictronMonitor()
heater = HeaterCommander()
stats = SessionStats(os.path.join(HERE, "stats.json"))
bms.on_update = stats.update

ble_loop: asyncio.AbstractEventLoop | None = None
_loop_ready = threading.Event()


def _ble_thread():
    global ble_loop
    ble_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(ble_loop)
    _loop_ready.set()
    ble_loop.run_until_complete(asyncio.gather(bms.run(period=3.0),
                                               renogy.run(period=3.0),
                                               victron.run()))


def _fresh(s):
    return s.get("connected") and s.get("updated") and time.time() - s["updated"] < FRESH_S


def _derived(b, r, v):
    """True load = total charge in minus the battery's net current (all +ve into
    the battery): load = renogy_in + victron_in - battery_net. Needs the battery
    plus at least one fresh charger to be meaningful."""
    if not _fresh(b) or b.get("current") is None:
        return {}
    charge_in = 0.0
    have_charger = False
    if _fresh(r) and r.get("charge_a") is not None:
        charge_in += r["charge_a"]; have_charger = True
    if v.get("connected") and v.get("current") is not None:
        charge_in += v["current"]; have_charger = True
    if not have_charger:
        return {}
    load_a = max(0.0, charge_in - b["current"])
    return {"load_a": round(load_a, 2), "load_w": round(load_a * b["voltage"], 1)}


@app.route("/")
def index():
    return send_from_directory(STATIC, "flow.html")


@app.route("/details")
def details():
    return send_from_directory(STATIC, "index.html")


@app.route("/heater")
def heater_page():
    return send_from_directory(STATIC, "heater.html")


@app.route("/weather")
def weather():
    return send_from_directory(STATIC, "weather.html")


@app.route("/settings")
def settings_page():
    # Served for layout work only - the settings API lives on the Pico hub.
    return send_from_directory(STATIC, "settings.html")


@app.route("/api/data")
def data():
    b, r, v = dict(bms.state), dict(renogy.state), victron.snapshot()
    b["stats"] = stats.summary(b)
    return jsonify({**b, "renogy": r, "victron": v, "derived": _derived(b, r, v),
                    "heater": {"last": heater.last, "busy": heater.lock.locked()}})


@app.route("/api/history")
def history():
    return jsonify(stats.series())


@app.route("/api/heater/cmd", methods=["POST"])
def heater_cmd():
    body = request.get_json(force=True, silent=True) or {}
    action = str(body.get("action", ""))
    if action == "read":
        return jsonify({"ok": False, "error": "read-back isn't available on the PC link — use the Pico hub"}), 400
    if action not in ("off", "air", "water", "combi", "vent", "time"):
        return jsonify({"ok": False, "error": "unknown action"}), 400
    if heater.lock.locked():
        return jsonify({"ok": False, "error": "a heater command is already in progress"}), 409
    _loop_ready.wait(5)
    fut = asyncio.run_coroutine_threadsafe(
        heater.run(action, body.get("temp"), body.get("water"), body.get("level"), body.get("energy")),
        ble_loop)
    try:
        return jsonify(fut.result(timeout=45))
    except Exception as e:  # noqa: BLE001 - surface to the UI rather than 500
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"[:120]}), 504


if __name__ == "__main__":
    threading.Thread(target=_ble_thread, daemon=True).start()
    app.run(host="0.0.0.0", port=5180, threaded=True)
