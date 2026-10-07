package uk.co.camperlux.dash.ble

import uk.co.camperlux.dash.model.RenogyState

/**
 * Renogy DC-DC / MPPT charger via BT-2 module — Modbus RTU over BLE. Port of renogy.py.
 * Write 0xffd1, notify 0xfff1. Device id 0xFF. Live block 0x0100..0x0122.
 */
object RenogyModbus {
    const val DEVICE_ID = 0xFF
    const val LIVE_REG = 0x0100
    const val LIVE_COUNT = 35

    private val CHARGE_STATES = mapOf(
        0 to "Not charging", 1 to "Activated", 2 to "MPPT", 3 to "Equalising",
        4 to "Boost", 5 to "Float", 6 to "Current limiting"
    )
    private val FAULT_BITS = mapOf(
        16 to "Charge MOSFET short", 17 to "Anti-reverse MOSFET short",
        18 to "Solar reversed", 19 to "Solar over-voltage", 20 to "Solar counter-current",
        21 to "PV over-voltage", 22 to "PV short", 23 to "PV over-power",
        24 to "Ambient over-temp", 25 to "Controller over-temp", 26 to "Load over-power",
        27 to "Load short circuit", 28 to "Battery under-voltage",
        29 to "Battery over-voltage", 30 to "Battery over-discharge"
    )

    fun crc16(data: ByteArray, len: Int = data.size): Int {
        var crc = 0xFFFF
        for (i in 0 until len) {
            crc = crc xor (data[i].toInt() and 0xFF)
            repeat(8) { crc = if (crc and 1 != 0) (crc shr 1) xor 0xA001 else crc shr 1 }
        }
        return crc
    }

    fun readCmd(reg: Int, count: Int, devId: Int = DEVICE_ID): ByteArray {
        val body = byteArrayOf(
            devId.toByte(), 0x03,
            (reg shr 8).toByte(), (reg and 0xFF).toByte(),
            (count shr 8).toByte(), (count and 0xFF).toByte()
        )
        val crc = crc16(body)
        return body + byteArrayOf((crc and 0xFF).toByte(), (crc shr 8).toByte())
    }

    /** Validate a 0x03 reply frame and return its registers, or null. */
    fun regsOf(frame: ByteArray): IntArray? {
        if (frame.size < 5 || (frame[1].toInt() and 0xFF) != 0x03) return null
        val nbytes = frame[2].toInt() and 0xFF
        if (frame.size < 3 + nbytes + 2) return null
        val gotCrc = (frame[3 + nbytes].toInt() and 0xFF) or ((frame[3 + nbytes + 1].toInt() and 0xFF) shl 8)
        if (crc16(frame, 3 + nbytes) != gotCrc) return null
        return IntArray(nbytes / 2) { ((frame[3 + 2 * it].toInt() and 0xFF) shl 8) or (frame[4 + 2 * it].toInt() and 0xFF) }
    }

    private fun temp(b: Int) = if (b and 0x80 != 0) -(b and 0x7F) else b

    fun decode(r: IntArray): RenogyState {
        val faultsWord = (r[33] shl 16) or (if (r.size > 34) r[34] else 0)
        val faults = FAULT_BITS.filter { (faultsWord and (1 shl it.key)) != 0 }.values.toList()
        val altA = r[5] / 100.0
        val solA = r[8] / 100.0
        val source = when {
            altA > 0.05 && solA > 0.05 -> "Alternator + Solar"
            altA > 0.05 -> "Alternator"
            solA > 0.05 -> "Solar"
            else -> "None"
        }
        val bv = r[1] / 10.0
        val ca = r[2] / 100.0
        return RenogyState(
            connected = true,
            batteryV = bv, chargeA = ca, chargeW = Math.round(bv * ca * 10) / 10.0,
            solarV = r[7] / 10.0, solarA = solA, solarW = r[9],
            altV = r[4] / 10.0, altA = altA, altW = r[6],
            state = CHARGE_STATES[r[32] and 0xFF] ?: "Unknown",
            source = source,
            ctrlTempC = temp(r[3] shr 8), battTempC = temp(r[3] and 0xFF),
            todayChgWh = r[19], todayChgAh = r[17],
            faults = faults
        )
    }
}
