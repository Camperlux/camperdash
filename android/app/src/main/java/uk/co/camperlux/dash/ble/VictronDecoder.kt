package uk.co.camperlux.dash.ble

import uk.co.camperlux.dash.model.VictronState
import javax.crypto.Cipher
import javax.crypto.spec.IvParameterSpec
import javax.crypto.spec.SecretKeySpec

/**
 * Victron "Instant Readout" AC charger (Blue Smart IP22) — passive BLE advert decode.
 * Manufacturer id 0x02E1. Verified against the victron-ble library.
 *
 * Advert value layout:
 *   [0:2] prefix 0x0010   [2:4] model id (LE)   [4] readout type
 *   [5:7] IV (LE)         [7] key-check (== key[0])   [8:] AES-CTR ciphertext
 * Decrypt: AES-128-CTR, key = 16-byte advertisement key, counter = IV as 16-byte LE.
 * Decrypted AC-charger record: [0]=state [1]=error, then 24-bit LE per output:
 *   voltage = bits0..12 (0.01 V), current = bits13..23 (0.1 A).
 */
object VictronDecoder {
    const val MANUFACTURER_ID = 0x02E1

    private val OP_MODES = mapOf(
        0 to "Off", 1 to "Low power", 2 to "Fault", 3 to "Bulk", 4 to "Absorption",
        5 to "Float", 6 to "Storage", 7 to "Equalise", 245 to "Starting up",
        247 to "Auto equalise", 252 to "External control"
    )
    private val MODELS = mapOf(0xA330 to "Blue Smart IP22 Charger 12/30")

    fun decode(raw: ByteArray, keyHex: String): VictronState? {
        if (raw.size < 10 || (raw[0].toInt() and 0xFF) != 0x10) return null
        val key = hexToBytes(keyHex)
        if (key.size != 16) return null
        val model = (raw[2].toInt() and 0xFF) or ((raw[3].toInt() and 0xFF) shl 8)
        val iv = (raw[5].toInt() and 0xFF) or ((raw[6].toInt() and 0xFF) shl 8)
        val ciphertext = raw.copyOfRange(8, raw.size)
        val ctr = ByteArray(16)
        ctr[0] = (iv and 0xFF).toByte(); ctr[1] = ((iv shr 8) and 0xFF).toByte()

        val pt = try {
            val cipher = Cipher.getInstance("AES/CTR/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, SecretKeySpec(key, "AES"), IvParameterSpec(ctr))
            cipher.doFinal(ciphertext)
        } catch (e: Exception) {
            return VictronState(connected = false, error = "decrypt failed")
        }
        if (pt.size < 5) return VictronState(connected = false, error = "short packet")

        val state = pt[0].toInt() and 0xFF
        val err = pt[1].toInt() and 0xFF
        val v24 = (pt[2].toInt() and 0xFF) or ((pt[3].toInt() and 0xFF) shl 8) or ((pt[4].toInt() and 0xFF) shl 16)
        val vRaw = v24 and 0x1FFF
        val cRaw = (v24 shr 13) and 0x7FF
        val voltage = if (vRaw == 0x1FFF) null else vRaw * 0.01
        val current = if (cRaw == 0x7FF) null else cRaw * 0.1
        val power = if (voltage != null && current != null) Math.round(voltage * current * 10) / 10.0 else null

        return VictronState(
            connected = true,
            model = MODELS[model] ?: "Victron %#06x".format(model),
            state = OP_MODES[state] ?: "State $state",
            voltage = voltage, current = current, powerW = power,
            error = if (err == 0) "No Error" else "Error $err"
        )
    }
}
