"""Probe the JP diesel heater controller ('Intrepid', AA:BB:CC:DD:EE:FF).

Uses WinRT directly: full GATT discovery hangs this module (Windows then drops
the link), but per-UUID discovery of service 0xfff0 works fine.

Protocol (from the JP Heater app, com.example.pw_iot2):
  service 0xfff0, write 0xfff2, notify 0xfff1
  frame  = 01 <cmd> 0C <12 payload bytes> <crc lo> <crc hi>   (17 bytes)
  crc    = Modbus CRC-16 (poly 0xA001, init 0xFFFF) over first 15 bytes
  cmd 03 handshake  -> reply[9]==0xAA, [3]=product code, [4]=firmware
  cmd 05 status     -> [3]=mode(1 temp,2 power,3 fan,else off) [4]=set °C
                       [5]=level [7]==0xAB timer [8]=fan types
                       [9..11]=total run time  [14]=run state
  cmd 06 sensors    -> [3]=supply V*5  [4]=temp+50  [13]=error code  [14]=alarm/fault
  cmd 08 faults
"""
import asyncio
import time
from uuid import UUID

from winrt.windows.devices.bluetooth import (BluetoothCacheMode, BluetoothConnectionStatus,
                                             BluetoothLEDevice)
from winrt.windows.devices.bluetooth.genericattributeprofile import (
    GattClientCharacteristicConfigurationDescriptorValue as CCCD,
    GattCommunicationStatus, GattSession, GattWriteOption)
from winrt.windows.storage.streams import DataReader, DataWriter

ADDR = 0x5C5310BC585E
SVC = UUID("0000fff0-0000-1000-8000-00805f9b34fb")
NOTIFY = UUID("0000fff1-0000-1000-8000-00805f9b34fb")
WRITE = UUID("0000fff2-0000-1000-8000-00805f9b34fb")

RUN_STATE = {0: "Idle", 1: "Self-check", 2: "Ignition", 3: "Stable combustion",
             5: "Fuel priming", 6: "Ventilation", 7: "Standby (intermittent)",
             8: "Shutting down", 10: "Immediate stop"}


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def frame(cmd: int, payload: bytes = b"") -> bytes:
    body = bytes([0x01, cmd, 0x0C]) + payload.ljust(12, b"\x00")
    c = crc16(body)
    return body + bytes([c & 0xFF, c >> 8])


def to_buffer(b: bytes):
    w = DataWriter(); w.write_bytes(b); return w.detach_buffer()


def from_buffer(buf) -> bytes:
    r = DataReader.from_buffer(buf); out = bytearray(buf.length); r.read_bytes(out); return bytes(out)


rx = bytearray()
got = []
t0 = time.time()


def on_value(_c, args):
    data = from_buffer(args.characteristic_value)
    print(f"  [{time.time()-t0:6.2f}s] <- {data.hex(' ')}")
    rx.extend(data)
    while len(rx) >= 17:
        if rx[0] != 0x01:
            del rx[0]; continue
        f = bytes(rx[:17]); del rx[:17]
        ok = crc16(f[:15]) == (f[15] | f[16] << 8)
        got.append((f, ok))
        print(f"        frame cmd={f[1]:#04x} crc={'ok' if ok else 'BAD'}")


async def send(wchar, cmd, wait=2.0, want=None):
    got.clear()
    try:
        st = await wchar.write_value_with_option_async(to_buffer(frame(cmd)), GattWriteOption.WRITE_WITHOUT_RESPONSE)
        print(f"  [{time.time()-t0:6.2f}s] -> cmd {cmd:#04x}: {frame(cmd).hex(' ')}  (write {'ok' if st==GattCommunicationStatus.SUCCESS else int(st)})")
    except OSError as e:
        print(f"  [{time.time()-t0:6.2f}s] -> cmd {cmd:#04x}: write failed: {str(e)[:50]}")
        return None
    end = time.monotonic() + wait
    while time.monotonic() < end:
        for f, ok in got:
            if ok and (want is None or f[1] == want):
                return f
        await asyncio.sleep(0.05)
    return None


async def main():
    dev = await BluetoothLEDevice.from_bluetooth_address_async(ADDR)
    print(f"device {dev.name}")
    link = lambda: "CONNECTED" if dev.connection_status == BluetoothConnectionStatus.CONNECTED else "down"
    dev.add_connection_status_changed(lambda d, _: print(f"  [{time.time()-t0:6.2f}s] ** link {link()}"))
    # Windows tears an LE link down when no GATT op is in flight unless a session
    # asks it to keep the connection up (Android holds the GATT open by default).
    session = await GattSession.from_device_id_async(dev.bluetooth_device_id)
    session.maintain_connection = True
    print("GattSession maintain_connection=True")
    # The module drops the link if discovery is prodded too much: one call for the
    # service, ONE call for all its characteristics, retrying from scratch on failure.
    nchar = wchar = None
    for attempt in range(1, 9):
        try:
            r = await dev.get_gatt_services_for_uuid_with_cache_mode_async(SVC, BluetoothCacheMode.UNCACHED)
            if r.status != GattCommunicationStatus.SUCCESS or not r.services.size:
                print(f"  try {attempt}: service lookup status={int(r.status)} (1=Unreachable)")
                await asyncio.sleep(3); continue
            svc = r.services.get_at(0)
            cr = await svc.get_characteristics_with_cache_mode_async(BluetoothCacheMode.UNCACHED)
            if cr.status != GattCommunicationStatus.SUCCESS:
                print(f"  try {attempt}: characteristics status={int(cr.status)}")
                await asyncio.sleep(3); continue
            chars = {str(c.uuid): c for c in cr.characteristics}
            print(f"  try {attempt}: service ok, characteristics: {[u[4:8] for u in chars]}")
            nchar, wchar = chars.get(str(NOTIFY)), chars.get(str(WRITE))
            if nchar and wchar:
                break
        except OSError as e:
            print(f"  try {attempt}: {str(e)[:70]}")
        await asyncio.sleep(3)
    if not (nchar and wchar):
        print("could not get fff1/fff2 after retries"); return
    print(f"chars ok: notify props={int(nchar.characteristic_properties)} write props={int(wchar.characteristic_properties)}")
    nchar.add_value_changed(on_value)
    print(f"  MTU (max_pdu_size) = {session.max_pdu_size}, link {link()}")
    # Descriptor discovery (needed for a CCCD write) hangs this module and drops
    # the link, so first see whether it notifies without the CCCD being set.
    print("handshake WITHOUT CCCD write ...")
    hs = None
    for _ in range(4):
        hs = await send(wchar, 0x03, wait=2.0, want=0x03)
        if hs and hs[9] == 0xAA:
            break
    if not hs:
        print(f"  no reply without CCCD (link {link()}); trying descriptor discovery with 8s timeout ...")
        try:
            dr = await asyncio.wait_for(nchar.get_descriptors_with_cache_mode_async(BluetoothCacheMode.UNCACHED), 8)
            print(f"  descriptors: status={int(dr.status)} n={dr.descriptors.size if int(dr.status)==0 else '-'} link {link()}")
            if int(dr.status) == 0:
                for d in dr.descriptors:
                    print("    desc", d.uuid, "handle", d.attribute_handle)
                res = await nchar.write_client_characteristic_configuration_descriptor_with_result_async(CCCD.NOTIFY)
                print(f"  CCCD write: status={int(res.status)} link {link()}")
                for _ in range(4):
                    hs = await send(wchar, 0x03, wait=2.0, want=0x03)
                    if hs and hs[9] == 0xAA:
                        break
        except asyncio.TimeoutError:
            print(f"  descriptor discovery timed out (link {link()})")
        except OSError as e:
            print(f"  descriptor path failed: {str(e)[:60]} (link {link()})")
    if not hs:
        print("No handshake reply."); return
    print(f"\nHANDSHAKE: product code={hs[3]}  firmware={hs[4]}  marker={hs[9]:#04x}  raw={hs.hex(' ')}")
    fam = ("air heater (QiNuan)" if 5 <= hs[3] <= 12 or 18 <= hs[3] <= 25 or hs[3] in (44, 45)
           else "water heater (ShuiNuan)" if 1 <= hs[3] <= 4 or 13 <= hs[3] <= 17
           else "CR12" if 26 <= hs[3] <= 30 else "CR14/CR17/other")
    print(f"  family: {fam}")

    st = await send(wchar, 0x05, wait=2.5, want=0x05)
    if st:
        mode = {1: "temperature", 2: "power level", 3: "fan"}.get(st[3], f"off ({st[3]})")
        print(f"\nSTATUS: mode={mode}  set={st[4]}°C  level={st[5]}  timer={'on' if st[7]==0xAB else 'off'}  "
              f"fan_types={st[8]:#04x}  run_time={(st[10]<<8)|st[9]}h {st[11]}m  "
              f"run_state={st[14]} ({RUN_STATE.get(st[14],'?')})\n  raw payload: {st[3:15].hex(' ')}")
    se = await send(wchar, 0x06, wait=2.5, want=0x06)
    if se:
        print(f"\nSENSORS: supply={se[3]/5:.1f} V  temp={se[4]-50} °C  error_code={se[13]}  flag={se[14]}"
              f"\n  raw payload: {se[3:15].hex(' ')}")
    fl = await send(wchar, 0x08, wait=2.5, want=0x08)
    if fl:
        print(f"\nFAULTS raw payload: {fl[3:15].hex(' ')}")

    print("\nListening 6s for unsolicited frames ..."); got.clear(); await asyncio.sleep(6)
    await nchar.write_client_characteristic_configuration_descriptor_async(CCCD.NONE)
    dev.close()


if __name__ == "__main__":
    asyncio.run(main())
