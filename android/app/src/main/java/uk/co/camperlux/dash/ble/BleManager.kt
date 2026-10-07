package uk.co.camperlux.dash.ble

import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothManager
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanResult
import android.content.Context
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import uk.co.camperlux.dash.model.*
import java.util.UUID

private fun uuid16(short: String) = UUID.fromString("0000$short-0000-1000-8000-00805f9b34fb")
private val CCCD = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")

@SuppressLint("MissingPermission")
class BleManager(private val ctx: Context) {

    companion object {
        const val BMS_ADDR = "AA:BB:CC:DD:EE:FF"
        const val RENOGY_ADDR = "AA:BB:CC:DD:EE:FF"
        const val VICTRON_ADDR = "AA:BB:CC:DD:EE:FF"
        // Secret - decrypts this charger's adverts. Supply it at build time
        // (local.properties / BuildConfig) rather than committing it.
        const val VICTRON_KEY = ""
        const val VICTRON_STALE_MS = 30_000L

        val JBD_SVC = uuid16("ff00"); val JBD_NOTIFY = uuid16("ff01"); val JBD_WRITE = uuid16("ff02")
        val REN_WSVC = uuid16("ffd0"); val REN_WRITE = uuid16("ffd1")
        val REN_NSVC = uuid16("fff0"); val REN_NOTIFY = uuid16("fff1")
    }

    private val adapter: BluetoothAdapter? =
        (ctx.getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager)?.adapter

    private val _state = MutableStateFlow(VanState())
    val state: StateFlow<VanState> = _state

    private var loopJob: Job? = null
    private var scanning = false
    private var victron = VictronState()
    private var victronSeen = 0L
    private var emaCurrent: Double? = null

    fun start(scope: CoroutineScope) {
        if (loopJob != null) return
        startVictronScan()
        loopJob = scope.launch(Dispatchers.IO) {
            while (isActive) {
                val bms = withTimeoutOrNull(15_000) { pollBms() } ?: BatteryState()
                val ren = withTimeoutOrNull(15_000) { pollRenogy() } ?: RenogyState()
                publish(bms, ren)
                delay(3_000)
            }
        }
    }

    fun stop() {
        loopJob?.cancel(); loopJob = null
        if (scanning) { adapter?.bluetoothLeScanner?.stopScan(scanCb); scanning = false }
    }

    // ---- Victron passive scan -------------------------------------------
    private val scanCb = object : ScanCallback() {
        override fun onScanResult(callbackType: Int, result: ScanResult) {
            if (result.device?.address != VICTRON_ADDR) return
            val data = result.scanRecord?.getManufacturerSpecificData(VictronDecoder.MANUFACTURER_ID) ?: return
            VictronDecoder.decode(data, VICTRON_KEY)?.let {
                if (it.connected) { victron = it; victronSeen = System.currentTimeMillis() }
            }
        }
    }

    private fun startVictronScan() {
        val scanner = adapter?.bluetoothLeScanner ?: return
        try { scanner.startScan(scanCb); scanning = true } catch (_: Exception) {}
    }

    private fun victronSnapshot(): VictronState {
        val fresh = victron.connected && System.currentTimeMillis() - victronSeen < VICTRON_STALE_MS
        return if (victron.connected && !fresh) victron.copy(connected = false, stale = true) else victron
    }

    // ---- GATT polling ---------------------------------------------------
    private suspend fun pollBms(): BatteryState? {
        // JBD often ignores the first command and Android connects can hit GATT 133,
        // so retry the whole exchange a couple of times.
        repeat(3) {
            val g = GattConn(ctx, adapter, BMS_ADDR)
            try {
                if (g.connect() && g.enableNotify(JBD_SVC, JBD_NOTIFY)) {
                    delay(350)  // let the BMS settle after notifications are enabled
                    val basic = g.request(JBD_SVC, JBD_WRITE, Jbd.CMD_BASIC, 0x03)
                    val cells = g.request(JBD_SVC, JBD_WRITE, Jbd.CMD_CELLS, 0x04)
                    if (basic != null || cells != null)
                        return Jbd.decode(basic?.let { Jbd.payloadOf(it) }, cells?.let { Jbd.payloadOf(it) })
                }
            } finally { g.close() }
            delay(700)  // let the BLE stack tear down before the next attempt
        }
        return null
    }

    private suspend fun pollRenogy(): RenogyState? {
        val g = GattConn(ctx, adapter, RENOGY_ADDR)
        try {
            if (!g.connect()) return null
            if (!g.enableNotify(REN_NSVC, REN_NOTIFY)) return null
            val frame = g.requestModbus(REN_WSVC, REN_WRITE,
                RenogyModbus.readCmd(RenogyModbus.LIVE_REG, RenogyModbus.LIVE_COUNT))
            val regs = frame?.let { RenogyModbus.regsOf(it) } ?: return null
            if (regs.size < 34) return null
            return RenogyModbus.decode(regs)
        } finally { g.close() }
    }

    // ---- derive + publish ----------------------------------------------
    private fun publish(bms: BatteryState, ren: RenogyState) {
        val vic = victronSnapshot()
        // smoothed current for time estimates
        val cur = bms.current
        if (bms.connected && cur != null) {
            emaCurrent = emaCurrent?.let { it + 0.3 * (cur - it) } ?: cur
        }
        var loadA: Double? = null; var loadW: Double? = null
        if (bms.connected && cur != null) {
            var chargeIn = 0.0; var haveCharger = false
            if (ren.connected && ren.chargeA != null) { chargeIn += ren.chargeA; haveCharger = true }
            if (vic.connected && vic.current != null) { chargeIn += vic.current; haveCharger = true }
            if (haveCharger) {
                loadA = maxOf(0.0, chargeIn - cur)
                loadW = bms.voltage?.let { Math.round(loadA!! * it * 10) / 10.0 }
            }
        }
        var tte: Double? = null; var ttf: Double? = null
        val ema = emaCurrent
        if (ema != null && bms.residualAh != null) {
            if (ema < -0.1) tte = bms.residualAh / -ema
            else if (ema > 0.1 && bms.nominalAh != null) ttf = (bms.nominalAh - bms.residualAh) / ema
        }
        _state.value = VanState(
            battery = bms, renogy = ren, victron = vic,
            derived = Derived(loadA, loadW, tte, ttf, ema)
        )
    }
}

/** Minimal coroutine wrapper over one GATT connection using the legacy characteristic API. */
@SuppressLint("MissingPermission")
private class GattConn(
    private val ctx: Context,
    private val adapter: BluetoothAdapter?,
    private val address: String,
) {
    private var gatt: BluetoothGatt? = null
    private val connected = CompletableDeferred<Boolean>()
    private val services = CompletableDeferred<Boolean>()
    private val descWritten = Channel<Boolean>(Channel.CONFLATED)
    private val notifications = Channel<ByteArray>(Channel.UNLIMITED)

    private val cb = object : BluetoothGattCallback() {
        override fun onConnectionStateChange(g: BluetoothGatt, status: Int, newState: Int) {
            if (newState == BluetoothGatt.STATE_CONNECTED) {
                if (!connected.isCompleted) connected.complete(true)
                g.discoverServices()
            } else if (newState == BluetoothGatt.STATE_DISCONNECTED) {
                if (!connected.isCompleted) connected.complete(false)
                if (!services.isCompleted) services.complete(false)
            }
        }
        override fun onServicesDiscovered(g: BluetoothGatt, status: Int) {
            if (!services.isCompleted) services.complete(status == BluetoothGatt.GATT_SUCCESS)
        }
        @Suppress("DEPRECATION")
        override fun onCharacteristicChanged(g: BluetoothGatt, c: BluetoothGattCharacteristic) {
            c.value?.let { notifications.trySend(it.copyOf()) }
        }
        override fun onDescriptorWrite(g: BluetoothGatt, d: BluetoothGattDescriptor, status: Int) {
            descWritten.trySend(status == BluetoothGatt.GATT_SUCCESS)
        }
    }

    suspend fun connect(): Boolean {
        val dev = try { adapter?.getRemoteDevice(address) } catch (e: Exception) { null } ?: return false
        gatt = dev.connectGatt(ctx, false, cb, BluetoothDevice_TRANSPORT_LE)
        if (withTimeoutOrNull(12_000) { connected.await() } != true) return false
        return withTimeoutOrNull(8_000) { services.await() } == true
    }

    @Suppress("DEPRECATION")
    suspend fun enableNotify(service: UUID, ch: UUID): Boolean {
        val g = gatt ?: return false
        val c = g.getService(service)?.getCharacteristic(ch) ?: return false
        g.setCharacteristicNotification(c, true)
        val d = c.getDescriptor(CCCD) ?: return true  // some stacks don't expose the CCCD
        d.value = BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
        g.writeDescriptor(d)
        return withTimeoutOrNull(4_000) { descWritten.receive() } == true
    }

    @Suppress("DEPRECATION")
    private fun write(service: UUID, ch: UUID, data: ByteArray): Boolean {
        val g = gatt ?: return false
        val c = g.getService(service)?.getCharacteristic(ch) ?: return false
        c.writeType = BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE
        c.value = data
        return g.writeCharacteristic(c)
    }

    /** Write a command, await a JBD frame whose cmd matches; resend if the BMS stays silent. */
    suspend fun request(service: UUID, ch: UUID, cmd: ByteArray, wantCmd: Int): ByteArray? {
        val buf = ArrayList<Byte>()
        repeat(4) {
            while (notifications.tryReceive().isSuccess) { /* drain stale */ }
            buf.clear()
            if (write(service, ch, cmd)) {
                val res = withTimeoutOrNull(1_300) {
                    var result: ByteArray? = null
                    while (result == null) {
                        buf.addAll(notifications.receive().toList())
                        for (f in Jbd.parseFrames(buf)) {
                            if ((f[1].toInt() and 0xFF) == wantCmd && (f[2].toInt() and 0xFF) == 0x00) { result = f; break }
                        }
                    }
                    result
                }
                if (res != null) return res
            }
            delay(200)
        }
        return null
    }

    /** Write a Modbus read and await the reassembled reply frame. */
    suspend fun requestModbus(service: UUID, ch: UUID, cmd: ByteArray): ByteArray? {
        while (notifications.tryReceive().isSuccess) { }
        val buf = ArrayList<Byte>()
        if (!write(service, ch, cmd)) return null
        return withTimeoutOrNull(3_000) {
            var result: ByteArray? = null
            while (result == null) {
                buf.addAll(notifications.receive().toList())
                if (buf.size >= 3 && (buf[1].toInt() and 0xFF) == 0x03) {
                    val need = 3 + (buf[2].toInt() and 0xFF) + 2
                    if (buf.size >= need) result = ByteArray(need) { buf[it] }
                }
            }
            result
        }
    }

    fun close() { try { gatt?.disconnect(); gatt?.close() } catch (_: Exception) {}; gatt = null }
}

// Constant pulled out to avoid an import clash with the annotated class name.
private const val BluetoothDevice_TRANSPORT_LE = 2
