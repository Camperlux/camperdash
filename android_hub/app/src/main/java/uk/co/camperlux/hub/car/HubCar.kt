package uk.co.camperlux.hub.car

// The hub on the van's own screen, through Android Auto.
//
// Android Auto shows no web pages, only its own driver-safe templates, so this
// is a small separate view of the same hub: the van's state as a few rows, the
// switches as big buttons, and the heater on and off. It reads the same
// /api/data the pages do, and switches through the same password-protected
// paths, with the key the phone app keeps once the hub has been unlocked on
// the phone (MainActivity saves it). The hub is reached over the van's WiFi
// even when the phone's own traffic goes over mobile data.
//
// An app from outside the Play Store appears in Android Auto only with its
// developer setting "Unknown sources" on.

import android.content.Context
import android.content.Intent
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.os.Handler
import android.os.Looper
import androidx.car.app.CarAppService
import androidx.car.app.CarContext
import androidx.car.app.CarToast
import androidx.car.app.Screen
import androidx.car.app.Session
import androidx.car.app.model.Action
import androidx.car.app.model.ActionStrip
import androidx.car.app.model.CarIcon
import androidx.car.app.model.GridItem
import androidx.car.app.model.GridTemplate
import androidx.car.app.model.ItemList
import androidx.car.app.model.ListTemplate
import androidx.car.app.model.MessageTemplate
import androidx.car.app.model.Pane
import androidx.car.app.model.PaneTemplate
import androidx.car.app.model.Row
import androidx.car.app.model.Template
import androidx.car.app.validation.HostValidator
import androidx.core.graphics.drawable.IconCompat
import androidx.lifecycle.DefaultLifecycleObserver
import androidx.lifecycle.LifecycleOwner
import org.json.JSONObject
import uk.co.camperlux.hub.R
import java.net.HttpURLConnection
import java.net.URL
import kotlin.concurrent.thread

class HubCarService : CarAppService() {
    // Any Android Auto host may show it: it is a debug-signed app for one van,
    // not one published to the Play Store with a list of trusted hosts.
    override fun createHostValidator(): HostValidator = HostValidator.ALLOW_ALL_HOSTS_VALIDATOR

    override fun onCreateSession(): Session = object : Session() {
        override fun onCreateScreen(intent: Intent): Screen = StatusScreen(carContext)
    }
}

/** Talking to the hub: its address and key as the phone app left them. */
object Hub {
    private const val PREFS = "hub"           // MainActivity's
    private const val KEY_LAST = "last_host"
    const val KEY_TOKEN = "hub_key"

    @Volatile var data: JSONObject? = null
    @Volatile var error: String? = null

    fun host(ctx: Context): String =
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY_LAST, null) ?: "192.168.4.1"

    fun token(ctx: Context): String? =
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY_TOKEN, null)

    /** The van's WiFi, so the hub is reached even when mobile data is the default. */
    private fun wifi(ctx: Context): Network? {
        val cm = ctx.getSystemService(ConnectivityManager::class.java)
        @Suppress("DEPRECATION")
        return cm.allNetworks.firstOrNull {
            cm.getNetworkCapabilities(it)?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true
        }
    }

    private fun open(ctx: Context, path: String): HttpURLConnection {
        val url = URL("http://${host(ctx)}$path")
        val c = (wifi(ctx)?.openConnection(url) ?: url.openConnection()) as HttpURLConnection
        c.connectTimeout = 4000
        c.readTimeout = 6000
        token(ctx)?.let { c.setRequestProperty("X-Token", it) }
        return c
    }

    fun refresh(ctx: Context) {
        try {
            val c = open(ctx, "/api/data")
            data = JSONObject(c.inputStream.bufferedReader().use { it.readText() })
            error = null
        } catch (e: Exception) {
            error = "Cannot reach the hub at ${host(ctx)}"
        }
    }

    /** POST; the hub's answer, or throws with why not. */
    fun post(ctx: Context, path: String, body: JSONObject): JSONObject {
        val c = open(ctx, path)
        c.requestMethod = "POST"
        c.doOutput = true
        c.setRequestProperty("Content-Type", "application/json")
        c.outputStream.use { it.write(body.toString().toByteArray()) }
        if (c.responseCode == 403)
            throw Exception("Unlock the hub in the phone app first")
        val r = JSONObject(c.inputStream.bufferedReader().use { it.readText() })
        if (r.optBoolean("ok", true) == false) throw Exception(r.optString("error", "refused"))
        return r
    }
}

/** A screen that keeps itself up to date while it is showing. */
abstract class LiveScreen(ctx: CarContext, private val everyMs: Long = 10_000) : Screen(ctx) {
    private val main = Handler(Looper.getMainLooper())
    private var running = false
    private val tick = object : Runnable {
        override fun run() {
            if (!running) return
            reload()
            main.postDelayed(this, everyMs)
        }
    }

    init {
        lifecycle.addObserver(object : DefaultLifecycleObserver {
            override fun onStart(owner: LifecycleOwner) {
                running = true
                main.post(tick)
            }

            override fun onStop(owner: LifecycleOwner) {
                running = false
                main.removeCallbacks(tick)
            }
        })
    }

    fun reload() {
        thread {
            Hub.refresh(carContext)
            main.post { invalidate() }
        }
    }

    /** Send a change; say what happened; then show the result. */
    fun send(path: String, body: JSONObject, done: String) {
        thread {
            val msg = try {
                Hub.post(carContext, path, body)
                done
            } catch (e: Exception) {
                e.message ?: "Not sent"
            }
            Hub.refresh(carContext)
            main.post {
                CarToast.makeText(carContext, msg, CarToast.LENGTH_SHORT).show()
                invalidate()
            }
        }
    }

    fun unreachable(): Template? {
        if (Hub.data != null || Hub.error == null) return null
        return MessageTemplate.Builder(Hub.error + ".\nIs the phone on the van's WiFi?")
            .setTitle("Camperlux")
            .setHeaderAction(Action.APP_ICON)
            .addAction(Action.Builder().setTitle("Try again").setOnClickListener { reload() }.build())
            .build()
    }

    fun icon(res: Int): CarIcon = CarIcon.Builder(IconCompat.createWithResource(carContext, res)).build()
}

private fun JSONObject.num(key: String): Double? = if (has(key) && !isNull(key)) optDouble(key) else null

private fun f0(v: Double?) = v?.let { "%.0f".format(it) } ?: "-"

class StatusScreen(ctx: CarContext) : LiveScreen(ctx) {
    override fun onGetTemplate(): Template {
        unreachable()?.let { return it }
        val d = Hub.data
        val list = ItemList.Builder()
        if (d == null) {
            return ListTemplate.Builder().setTitle("Camperlux").setHeaderAction(Action.APP_ICON)
                .setLoading(true).build()
        }
        // the house battery
        val cur = d.num("current") ?: 0.0
        val batt = if (d.optBoolean("connected")) Row.Builder()
            .setTitle("Battery ${f0(d.num("soc"))}%")
            .addText("%.2f V · %s".format(d.num("voltage") ?: 0.0,
                when {
                    cur > 0.05 -> "charging %.1f A".format(cur)
                    cur < -0.05 -> "using %.1f A".format(-cur)
                    else -> "resting"
                }))
            .setImage(icon(R.drawable.car_battery)).build()
        else Row.Builder().setTitle("Battery").addText("No reading from the battery")
            .setImage(icon(R.drawable.car_battery)).build()
        list.addItem(batt)
        // what is charging it
        val r = d.optJSONObject("renogy")
        val parts = mutableListOf<String>()
        if (r != null && r.optBoolean("connected")) {
            parts += "Solar ${f0(r.num("solar_w"))} W"
            parts += "Alternator ${f0(r.num("alt_w"))} W"
        }
        parts += if (d.optBoolean("mains_live")) "Hook-up on" else "No hook-up"
        list.addItem(Row.Builder().setTitle("Charging").addText(parts.joinToString(" · "))
            .setImage(icon(R.drawable.car_bolt)).build())
        // the heater: tap for its screen
        val h = d.optJSONObject("heater")
        val heat = if (h != null && (h.optBoolean("connected") || h.optBoolean("remembered")))
            (if (h.optBoolean("on")) "On · ${h.optString("mode", "")}" else "Off") +
                (h.num("air_temp_c")?.let { " · air %.0f°C".format(it) } ?: "")
        else "Not connected"
        list.addItem(Row.Builder().setTitle("Heater").addText(heat)
            .setImage(icon(R.drawable.car_flame)).setBrowsable(true)
            .setOnClickListener { screenManager.push(HeaterScreen(carContext)) }.build())
        // the switches: tap for them
        val sw = d.optJSONObject("display")?.optJSONArray("switches")
        val on = (0 until (sw?.length() ?: 0)).count { sw!!.optBoolean(it) }
        list.addItem(Row.Builder().setTitle("Switches").addText("$on of ${sw?.length() ?: 0} on")
            .setImage(icon(R.drawable.car_switch)).setBrowsable(true)
            .setOnClickListener { screenManager.push(SwitchesScreen(carContext)) }.build())
        // anything wrong
        val al = d.optJSONArray("alerts")
        val names = (0 until (al?.length() ?: 0)).map { al!!.optJSONObject(it)?.optString("title") ?: "" }
        list.addItem(Row.Builder().setTitle("Alerts")
            .addText(if (names.isEmpty()) "None" else names.joinToString(", "))
            .setImage(icon(R.drawable.car_alert)).build())
        return ListTemplate.Builder()
            .setTitle("Camperlux")
            .setHeaderAction(Action.APP_ICON)
            .setSingleList(list.build())
            .setActionStrip(ActionStrip.Builder().addAction(
                Action.Builder().setTitle("Refresh").setOnClickListener { reload() }.build()).build())
            .build()
    }
}

class SwitchesScreen(ctx: CarContext) : LiveScreen(ctx) {
    override fun onGetTemplate(): Template {
        unreachable()?.let { return it }
        val d = Hub.data ?: return GridTemplate.Builder().setTitle("Switches")
            .setHeaderAction(Action.BACK).setLoading(true).build()
        val sw = d.optJSONObject("display")?.optJSONArray("switches")
        val names = d.optJSONArray("switch_names")
        val grid = ItemList.Builder()
        for (i in 0 until (sw?.length() ?: 0)) {
            val on = sw!!.optBoolean(i)
            val name = names?.optString(i) ?: "Switch ${i + 1}"
            grid.addItem(GridItem.Builder()
                .setTitle(name)
                .setText(if (on) "On" else "Off")
                .setImage(icon(if (on) R.drawable.car_toggle_on else R.drawable.car_toggle_off))
                .setOnClickListener {
                    send("/api/switches", JSONObject().put("i", i).put("on", !on),
                        "$name ${if (on) "off" else "on"}")
                }
                .build())
        }
        return GridTemplate.Builder()
            .setTitle(if (d.optInt("relays") > 0) "Switches" else "Switches (not wired)")
            .setHeaderAction(Action.BACK)
            .setSingleList(grid.build())
            .build()
    }
}

class HeaterScreen(ctx: CarContext) : LiveScreen(ctx) {
    override fun onGetTemplate(): Template {
        unreachable()?.let { return it }
        val d = Hub.data ?: return PaneTemplate.Builder(Pane.Builder().setLoading(true).build())
            .setTitle("Heater").setHeaderAction(Action.BACK).build()
        val h = d.optJSONObject("heater") ?: JSONObject()
        val target = (h.num("set_air_c") ?: 20.0).toInt().coerceIn(8, 30)
        val pane = Pane.Builder()
            .addRow(Row.Builder().setTitle(if (h.optBoolean("on")) "On · ${h.optString("mode", "")}" else "Off")
                .addText(if (h.optBoolean("connected")) "Connected" else "Last known - not connected now")
                .build())
            .addRow(Row.Builder().setTitle("Temperatures")
                .addText("Air ${f0(h.num("air_temp_c"))}°C · water ${f0(h.num("water_temp_c"))}°C").build())
            .addAction(Action.Builder().setTitle("Heat to $target°C").setOnClickListener {
                send("/api/heater/cmd", JSONObject().put("action", "air").put("temp", target)
                    .put("water", 1).put("level", 1).put("energy", "auto"), "Heater starting")
            }.build())
            .addAction(Action.Builder().setTitle("Off").setOnClickListener {
                send("/api/heater/cmd", JSONObject().put("action", "off").put("temp", target)
                    .put("water", 1).put("level", 1).put("energy", "auto"), "Heater stopping")
            }.build())
            .build()
        return PaneTemplate.Builder(pane).setTitle("Heater").setHeaderAction(Action.BACK).build()
    }
}
