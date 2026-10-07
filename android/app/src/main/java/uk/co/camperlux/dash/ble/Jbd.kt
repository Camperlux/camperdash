package uk.co.camperlux.dash.ble

import uk.co.camperlux.dash.model.BatteryState

/**
 * Fogstar / JBD (Xiaoxiang) BMS protocol — port of bms.py.
 * Service 0xff00, notify 0xff01, write 0xff02.
 * Frame: DD <cmd> <status> <len> <payload...> <chk_hi> <chk_lo> 77
 */
object Jbd {
    val CMD_BASIC = hexToBytes("DDA50300FFFD77")
    val CMD_CELLS = hexToBytes("DDA50400FFFC77")

    private val PROTECTION_BITS = listOf(
        "Cell overvoltage", "Cell undervoltage", "Pack overvoltage",
        "Pack undervoltage", "Charge over-temp", "Charge under-temp",
        "Discharge over-temp", "Discharge under-temp", "Charge overcurrent",
        "Discharge overcurrent", "Short circuit", "IC error", "MOSFET locked"
    )

    private fun u16(b: ByteArray, i: Int) = ((b[i].toInt() and 0xFF) shl 8) or (b[i + 1].toInt() and 0xFF)
    private fun s16(b: ByteArray, i: Int): Int {
        val v = u16(b, i); return if (v >= 0x8000) v - 0x10000 else v
    }

    /** Extract complete DD..77 frames from a rolling buffer; returns frames and leftover. */
    fun parseFrames(buf: MutableList<Byte>): List<ByteArray> {
        val out = ArrayList<ByteArray>()
        while (buf.size >= 7 && (buf[0].toInt() and 0xFF) == 0xDD) {
            val plen = buf[3].toInt() and 0xFF
            val total = 4 + plen + 3
            if (buf.size < total) break
            out.add(ByteArray(total) { buf[it] })
            repeat(total) { buf.removeAt(0) }
        }
        if (buf.isNotEmpty() && (buf[0].toInt() and 0xFF) != 0xDD) buf.clear()
        return out
    }

    /** Merge a 0x03 basic-info payload and optional 0x04 cell payload into a BatteryState. */
    fun decode(basic: ByteArray?, cells: ByteArray?): BatteryState {
        var st = BatteryState(connected = true)
        if (basic != null && basic.size >= 23) {
            val ntc = basic[22].toInt() and 0xFF
            val temps = ArrayList<Double>()
            for (i in 0 until ntc) {
                val idx = 23 + 2 * i
                if (idx + 1 < basic.size) temps.add(Math.round((u16(basic, idx) / 10.0 - 273.15) * 10) / 10.0)
            }
            val prot = u16(basic, 16)
            val faults = PROTECTION_BITS.filterIndexed { n, _ -> (prot and (1 shl n)) != 0 }
            val fet = basic[20].toInt() and 0xFF
            val voltage = u16(basic, 0) / 100.0
            val current = s16(basic, 2) / 100.0
            val prod = u16(basic, 10)
            st = st.copy(
                voltage = voltage,
                current = current,
                powerW = Math.round(voltage * current * 10) / 10.0,
                residualAh = u16(basic, 4) / 100.0,
                nominalAh = u16(basic, 6) / 100.0,
                cycles = u16(basic, 8),
                soc = basic[19].toInt() and 0xFF,
                chargeFet = (fet and 0x01) != 0,
                dischargeFet = (fet and 0x02) != 0,
                tempsC = temps,
                faults = faults,
                prodDate = "%04d-%02d-%02d".format(2000 + (prod shr 9), (prod shr 5) and 0x0f, prod and 0x1f)
            )
        }
        if (cells != null && cells.size >= 2) {
            val n = cells.size / 2
            val mv = (0 until n).map { u16(cells, 2 * it) }
            if (mv.isNotEmpty()) st = st.copy(cellsMv = mv, cellDeltaMv = mv.max() - mv.min())
        }
        return st
    }

    fun payloadOf(frame: ByteArray): ByteArray {
        val plen = frame[3].toInt() and 0xFF
        return frame.copyOfRange(4, 4 + plen)
    }
}

fun hexToBytes(s: String): ByteArray {
    val clean = s.replace(" ", "")
    return ByteArray(clean.length / 2) { ((clean.substring(it * 2, it * 2 + 2)).toInt(16)).toByte() }
}
