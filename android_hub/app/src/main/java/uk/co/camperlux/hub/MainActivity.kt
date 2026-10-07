package uk.co.camperlux.hub

import android.app.Activity
import android.app.AlertDialog
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.app.PendingIntent
import android.content.pm.PackageInstaller
import android.provider.Settings
import android.graphics.Color
import android.graphics.Typeface
import android.net.ConnectivityManager
import android.net.LinkProperties
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.provider.MediaStore
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.webkit.URLUtil
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import java.io.File
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.HttpURLConnection
import java.net.Inet4Address
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.NetworkInterface
import java.net.SocketTimeoutException
import java.net.URL
import kotlin.concurrent.thread

/**
 * Finds the van hub on the WiFi network and shows its web pages, exactly as a
 * browser pointed at the hub would - but without having to know its address.
 *
 * Finding it: the router gives the hub whatever address it likes, and the Pico
 * W does not answer mDNS, so the app broadcasts "camperdash?" on UDP 50505 and
 * the hub replies (see DISCOVERY_PORT in the hub's main.py). If nothing
 * answers, the last address that worked and the hub's own hotspot address are
 * tried directly.
 *
 * Staying on WiFi: the hub's hotspot has no internet, and Android quietly sends
 * traffic for such a network out over mobile data instead. The app binds itself
 * to the WiFi network, so the pages, the discovery broadcast and every request
 * the pages make go where the hub is.
 *
 * The hub's address is remembered for each network it was found on (by the
 * network's address range, which needs no location permission), so coming
 * back to a network tries the right address first.
 *
 * This phone as the hotspot: when the hub has joined the phone's own hotspot,
 * Android does not count the phone as being on WiFi at all, and a broadcast
 * would go out over mobile data. So with no WiFi, the hotspot's own interface
 * is looked for and the hub searched for on that.
 *
 * Not found anywhere: the app offers Android's WiFi panel to join the hub's
 * own hotspot, by name (learned from the hub while connected). Android does
 * the joining; the app never sees or keeps the password.
 */
class MainActivity : Activity() {

    companion object {
        private const val DISCOVERY_PORT = 50505
        // the hub's hotspot: 192.168.44.1 now, clear of the home network's range;
        // 192.168.4.1 on a hub that has not been moved off its old address
        private val HOTSPOT_ADDRESSES = listOf("192.168.44.1", "192.168.4.1")
        private const val WATCH_MS = 10_000L
        private const val PREFS = "hub"
        private const val KEY_LAST = "last_host"
        private const val KEY_MANUAL = "manual_host"
        private const val KEY_DECLINED = "declined_update"   // a version not wanted: not offered again
        private const val KEY_AP = "hub_ap_ssid"             // the hub's own hotspot, by name
        private const val KEY_ON = "host@"                   // + a network's range: the hub's address on it
        private const val ACTION_INSTALLED = "uk.co.camperlux.hub.INSTALLED"

        private val BG = Color.parseColor("#0F0E12")
        private val TXT = Color.parseColor("#EFE9E0")
        private val MUTED = Color.parseColor("#9A8F9E")
        private val BRAND = Color.parseColor("#D3A94A")
        private val PANEL2 = Color.parseColor("#201D26")
    }

    private lateinit var web: WebView
    private lateinit var panel: LinearLayout
    private lateinit var status: TextView
    private lateinit var spinner: ProgressBar
    private lateinit var buttons: LinearLayout

    private val cm by lazy { getSystemService(ConnectivityManager::class.java) }
    private var wifi: Network? = null
    private var hubHost: String? = null
    private var searching = false
    private var pageShown = false
    private var loadFailures = 0     // failed page loads in a row, to stop a retry loop
    private var loadFailed = false   // the current load failed: never show its error page
    private var misses = 0           // watchdog checks in a row the hub did not answer
    private var watching = false
    private var tether: String? = null  // this phone's own hotspot's range, while the hub is on it

    // ---- set-up -----------------------------------------------------------

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = FrameLayout(this).apply { setBackgroundColor(BG) }

        web = WebView(this).apply {
            setBackgroundColor(BG)
            visibility = View.INVISIBLE
            settings.javaScriptEnabled = true
            // the pages keep small preferences (chosen tab, layout) in localStorage
            settings.domStorageEnabled = true
            // Without a WebChromeClient, confirm() silently returns false - and
            // every heater command on the hub's pages asks confirm() first.
            webChromeClient = WebChromeClient()
            webViewClient = HubClient()
            setDownloadListener { url, _, disposition, mime, _ ->
                val name = URLUtil.guessFileName(url, disposition, mime)
                // the app itself, from the hub: install it rather than file it
                if (mime == "application/vnd.android.package-archive" || name.endsWith(".apk"))
                    install(url)
                else
                    download(url, name)
            }
        }
        root.addView(web, FrameLayout.LayoutParams(-1, -1))

        panel = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setPadding(dp(32), dp(32), dp(32), dp(32))
        }
        panel.addView(TextView(this).apply {
            text = getString(R.string.app_name)
            setTextColor(TXT)
            textSize = 26f
            typeface = Typeface.DEFAULT_BOLD
            gravity = Gravity.CENTER
        })
        spinner = ProgressBar(this).apply { isIndeterminate = true }
        panel.addView(spinner, LinearLayout.LayoutParams(dp(40), dp(40)).apply {
            topMargin = dp(24); bottomMargin = dp(16)
        })
        status = TextView(this).apply {
            setTextColor(MUTED)
            textSize = 16f
            gravity = Gravity.CENTER
        }
        panel.addView(status)
        buttons = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER
            visibility = View.GONE
        }
        buttons.addView(button("Try again") { findHub() })
        buttons.addView(button("Join hub hotspot") { joinHotspot() })
        buttons.addView(button("Enter address") { askForAddress() })
        panel.addView(buttons, LinearLayout.LayoutParams(-2, -2).apply { topMargin = dp(24) })
        root.addView(panel, FrameLayout.LayoutParams(-1, -1))

        // Android 15 draws apps under the status and navigation bars; keep the
        // pages clear of them.
        root.setOnApplyWindowInsetsListener { v, insets ->
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                val b = insets.getInsets(android.view.WindowInsets.Type.systemBars())
                v.setPadding(b.left, b.top, b.right, b.bottom)
            } else {
                @Suppress("DEPRECATION")
                v.setPadding(insets.systemWindowInsetLeft, insets.systemWindowInsetTop,
                    insets.systemWindowInsetRight, insets.systemWindowInsetBottom)
            }
            insets
        }
        setContentView(root)

        showStatus("Looking for WiFi…", busy = true)
        watchWifi()
    }

    private fun watchWifi() {
        val req = NetworkRequest.Builder()
            .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
            .build()
        cm.registerNetworkCallback(req, object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) {
                runOnUiThread {
                    wifi = network
                    cm.bindProcessToNetwork(network)
                    findHub()
                }
            }

            override fun onLost(network: Network) {
                runOnUiThread {
                    if (network == wifi) {
                        wifi = null
                        cm.bindProcessToNetwork(null)
                        findHub()            // perhaps on this phone's own hotspot
                    }
                }
            }
        })
        // nothing arrives if there is no WiFi at all, so say so after a moment
        web.postDelayed({ if (wifi == null && hubHost == null) findHub() }, 3000)
    }

    override fun onResume() {
        super.onResume()
        watching = true
        web.postDelayed(watchdog, WATCH_MS)
        // The hub may have moved while the app was in the background: check the
        // one we know still answers, and look again if not.
        val h = hubHost ?: return
        thread {
            if (!probe(h)) runOnUiThread { findHub(web.url) }
        }
    }

    override fun onPause() {
        super.onPause()
        watching = false
        web.removeCallbacks(watchdog)
    }

    /**
     * While the app is on screen: every 10 s, does the hub still answer? The
     * page's own requests failing is only shown by the page ("failed to
     * fetch"), and the page stays loaded from the old address - so a hub that
     * was swapped, restarted onto a new address, or out of range and back
     * again was never looked for. Two misses in a row and it is looked for
     * afresh, keeping the page the user was on. With no hub found at all, it
     * keeps looking rather than stopping at "No hub found".
     */
    private val watchdog: Runnable = object : Runnable {
        override fun run() {
            if (!watching) return
            val h = hubHost
            when {
                searching -> {}
                wifi == null && tether == null -> if (h == null) findHub()
                h == null || !pageShown -> findHub(web.url?.takeIf { it.startsWith("http") })
                else -> thread {
                    runOnUiThread { keepKey() }
                    val ok = probe(h)
                    runOnUiThread {
                        misses = if (ok) 0 else misses + 1
                        if (misses >= 2 && hubHost == h && !searching) {
                            misses = 0
                            findHub(web.url)
                        }
                    }
                }
            }
            if (watching) web.postDelayed(this, WATCH_MS)
        }
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (pageShown && web.canGoBack()) web.goBack() else super.onBackPressed()
    }

    // ---- finding the hub --------------------------------------------------

    /** Look for the hub, then open [reopen] on it (the dashboard by default):
     *  on the WiFi network the phone is on, or else on its own hotspot. */
    private fun findHub(reopen: String? = null) {
        if (searching) return
        val net = wifi
        searching = true
        showStatus("Finding the hub…", busy = true)
        thread {
            val prefs = getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            val manual = prefs.getString(KEY_MANUAL, null)
            var range: String? = null
            var found: String? = null
            var onTether: String? = null
            if (net != null) {
                range = cm.getLinkProperties(net)?.let { rangeOf(it) }
                found = discover(net)
                    ?: (listOfNotNull(manual, range?.let { prefs.getString(KEY_ON + it, null) },
                        prefs.getString(KEY_LAST, null)) + HOTSPOT_ADDRESSES)
                        .distinct().firstOrNull { probe(it) }
            } else {
                // no WiFi: is the hub on this phone's own hotspot?
                for ((addr, ia) in hotspotInterfaces()) {
                    val r = rangeOf(addr, ia.networkPrefixLength.toInt())
                    found = discoverOn(addr, ia.broadcast)
                        ?: listOfNotNull(manual, prefs.getString(KEY_ON + r, null))
                            .distinct().firstOrNull { probe(it) }
                    if (found != null) {
                        range = r; onTether = r; break
                    }
                }
            }
            runOnUiThread {
                searching = false
                tether = onTether
                if (found == null) {
                    val ap = prefs.getString(KEY_AP, null)
                    val join = if (ap != null) "\n\nOr join the hub's own hotspot, \"$ap\"."
                               else "\n\nOr join the hub's own hotspot."
                    showStatus((if (net == null) "Not on WiFi, and no hub on this phone's hotspot."
                                else "No hub found on this network.\n" +
                                     "Is it powered, and on the same WiFi as this phone?") + join,
                        busy = false)
                } else {
                    prefs.edit().apply {
                        putString(KEY_LAST, found)
                        if (range != null) putString(KEY_ON + range, found)
                    }.apply()
                    open(found, reopen)
                }
            }
        }
    }

    /** A network's address range, as "192.168.1.0/24": the key its hub
     *  address is remembered under. */
    private fun rangeOf(lp: LinkProperties): String? {
        val la = lp.linkAddresses.firstOrNull { it.address is Inet4Address } ?: return null
        return rangeOf(la.address, la.prefixLength)
    }

    private fun rangeOf(a: InetAddress, bits: Int): String {
        val ip = a.address
        val v = ((ip[0].toInt() and 255) shl 24) or ((ip[1].toInt() and 255) shl 16) or
                ((ip[2].toInt() and 255) shl 8) or (ip[3].toInt() and 255)
        val n = v and (if (bits == 0) 0 else (-1 shl (32 - bits)))
        return "${(n ushr 24) and 255}.${(n ushr 16) and 255}.${(n ushr 8) and 255}.${n and 255}/$bits"
    }

    /** This phone's own hotspot, if it is running: its address on each
     *  hotspot interface. Mobile data, VPNs and the like are left out by name. */
    private fun hotspotInterfaces(): List<Pair<InetAddress, java.net.InterfaceAddress>> = try {
        NetworkInterface.getNetworkInterfaces().toList()
            .filter { ni -> ni.isUp && !ni.isLoopback &&
                listOf("ap", "swlan", "softap", "wlan").any { ni.name.startsWith(it) } }
            .flatMap { ni -> ni.interfaceAddresses
                .filter { it.address is Inet4Address && it.address.isSiteLocalAddress && it.broadcast != null }
                .map { it.address to it } }
    } catch (_: Exception) {
        emptyList()
    }

    /** Discovery on this phone's hotspot: a broadcast from its own address there. */
    private fun discoverOn(local: InetAddress, bcast: InetAddress): String? = try {
        DatagramSocket(InetSocketAddress(local, 0)).use { s ->
            s.broadcast = true
            s.soTimeout = 1200
            val q = "camperdash?".toByteArray()
            val buf = ByteArray(64)
            var hit: String? = null
            for (i in 0 until 3) {
                s.send(DatagramPacket(q, q.size, bcast, DISCOVERY_PORT))
                try {
                    val p = DatagramPacket(buf, buf.size)
                    s.receive(p)
                    if (String(buf, 0, p.length).startsWith("camperdash hub")) {
                        hit = p.address.hostAddress
                        break
                    }
                } catch (_: SocketTimeoutException) {
                }
            }
            hit
        }
    } catch (_: Exception) {
        null
    }

    /** Android's own WiFi panel, to join the hub's hotspot: the app names it,
     *  Android does the joining (and asks for the password the first time). */
    private fun joinHotspot() {
        val ap = getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY_AP, null)
        Toast.makeText(this, if (ap != null) "Choose \"$ap\"" else "Choose the hub's hotspot",
            Toast.LENGTH_LONG).show()
        try {
            startActivity(Intent(if (Build.VERSION.SDK_INT >= 29) Settings.Panel.ACTION_WIFI
                                 else Settings.ACTION_WIFI_SETTINGS))
        } catch (_: Exception) {
            startActivity(Intent(Settings.ACTION_WIFI_SETTINGS))
        }
    }

    /** Note the hub's hotspot name, for joinHotspot() on a day it is not found. */
    private fun learnHotspot(host: String) = thread {
        try {
            val c = URL("http://$host/api/data").openConnection() as HttpURLConnection
            c.connectTimeout = 3000; c.readTimeout = 6000
            val t = c.inputStream.bufferedReader().use { it.readText() }
            Regex("\"wifi_ap_ssid\"\\s*:\\s*\"([^\"]+)\"").find(t)?.groupValues?.get(1)?.let {
                getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(KEY_AP, it).apply()
            }
        } catch (_: Exception) {
        }
    }

    /** Broadcast "camperdash?" and return the address that answers. */
    private fun discover(net: Network): String? {
        val targets = mutableListOf(InetAddress.getByName("255.255.255.255"))
        // Some access points drop the all-ones broadcast but pass the subnet's
        // own broadcast address, so send to both.
        cm.getLinkProperties(net)?.let { subnetBroadcast(it)?.let(targets::add) }
        return try {
            DatagramSocket().use { s ->
                net.bindSocket(s)
                s.broadcast = true
                s.soTimeout = 1200
                val q = "camperdash?".toByteArray()
                val buf = ByteArray(64)
                repeat(3) {
                    for (t in targets) s.send(DatagramPacket(q, q.size, t, DISCOVERY_PORT))
                    try {
                        val p = DatagramPacket(buf, buf.size)
                        s.receive(p)
                        if (String(buf, 0, p.length).startsWith("camperdash hub"))
                            return p.address.hostAddress
                    } catch (_: SocketTimeoutException) {
                    }
                }
                null
            }
        } catch (_: Exception) {
            null
        }
    }

    private fun subnetBroadcast(lp: LinkProperties): InetAddress? {
        val la = lp.linkAddresses.firstOrNull { it.address is Inet4Address } ?: return null
        val ip = la.address.address
        val bits = la.prefixLength
        val v = ((ip[0].toInt() and 255) shl 24) or ((ip[1].toInt() and 255) shl 16) or
                ((ip[2].toInt() and 255) shl 8) or (ip[3].toInt() and 255)
        val mask = if (bits == 0) 0 else (-1 shl (32 - bits))
        val b = v or mask.inv()
        return InetAddress.getByAddress(byteArrayOf(
            (b ushr 24).toByte(), (b ushr 16).toByte(), (b ushr 8).toByte(), b.toByte()))
    }

    /** Is there a hub at [host]? Checked on its small levelling endpoint, and
     *  by content - anything else on port 80 (a router, say) is not it. */
    private fun probe(host: String): Boolean = try {
        val c = URL("http://$host/api/level").openConnection() as HttpURLConnection
        c.connectTimeout = 2500
        c.readTimeout = 2500
        val ok = c.responseCode == 200 &&
                c.inputStream.bufferedReader().use { it.readText() }.contains("\"level\"")
        c.disconnect()
        ok
    } catch (_: Exception) {
        false
    }

    // ---- showing it -------------------------------------------------------

    private fun open(host: String, reopen: String?) {
        hubHost = host
        // keep the page the user was on if the hub merely moved address
        val path = reopen?.let { Uri.parse(it) }?.let { u ->
            (u.path ?: "/") + (u.query?.let { "?$it" } ?: "")
        } ?: "/"
        showStatus("Opening the hub at $host…", busy = true)
        pageShown = false
        loadFailed = false
        web.loadUrl("http://$host$path")
    }

    private inner class HubClient : WebViewClient() {
        override fun shouldOverrideUrlLoading(view: WebView, req: WebResourceRequest): Boolean {
            if (req.url.host == hubHost) return false          // the hub's own pages
            // anything else (a weather credit, say) goes to the phone's browser
            try {
                startActivity(Intent(Intent.ACTION_VIEW, req.url))
            } catch (_: Exception) {
            }
            return true
        }

        override fun onPageFinished(view: WebView, url: String) {
            if (!searching && !loadFailed && Uri.parse(url).host == hubHost) {
                loadFailures = 0
                pageShown = true
                web.visibility = View.VISIBLE
                panel.visibility = View.GONE
                offerUpdate()
                keepKey()
                hubHost?.let { learnHotspot(it) }
            }
        }

        override fun onReceivedError(view: WebView, req: WebResourceRequest, err: WebResourceError) {
            // Only a failed page matters; the pages' own background requests
            // failing is shown by the pages themselves.
            if (req.isForMainFrame) {
                loadFailed = true
                web.visibility = View.INVISIBLE
                pageShown = false
                // A hub that answers discovery but will not serve its pages
                // would otherwise have us searching and reloading for ever.
                if (++loadFailures >= 3) {
                    loadFailures = 0
                    showStatus("The hub was found but its pages did not load " +
                        "(${err.description}).", busy = false)
                } else {
                    findHub(req.url.toString())
                }
            }
        }
    }

    // ---- downloads (the level log) ----------------------------------------

    // ---- updating itself from the hub ---------------------------------------
    // The hub carries the app (tools/build_mpy.py puts it there, with apk.json
    // saying which version). When it has a newer one than this, say so once;
    // and the download button in the hub's Settings installs rather than
    // files it. Android's own PackageInstaller does the installing - no
    // library needed - and always asks the user to confirm.

    private var offered = false

    private fun installedVersion(): String = try {
        packageManager.getPackageInfo(packageName, 0).versionName ?: ""
    } catch (_: Exception) {
        ""
    }

    /** "1.10" is newer than "1.9": compared number by number. */
    private fun newer(a: String, b: String): Boolean {
        val x = a.split(".").map { it.toIntOrNull() ?: 0 }
        val y = b.split(".").map { it.toIntOrNull() ?: 0 }
        for (i in 0 until maxOf(x.size, y.size)) {
            val p = x.getOrElse(i) { 0 }
            val q = y.getOrElse(i) { 0 }
            if (p != q) return p > q
        }
        return false
    }

    /** The hub's password key, as the page keeps it once the hub has been
     *  unlocked here, kept for the Android Auto view (car/HubCar.kt), which
     *  has no page of its own to ask with. */
    private fun keepKey() {
        web.evaluateJavascript("(window.hubKey && window.hubKey()) || ''") { v ->
            val k = v?.trim('"') ?: ""
            if (k.length == 64 && k.all { it in "0123456789abcdef" })
                getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                    .putString(uk.co.camperlux.hub.car.Hub.KEY_TOKEN, k).apply()
        }
    }

    private fun offerUpdate() {
        if (offered) return
        offered = true
        val host = hubHost ?: return
        thread {
            val hub = try {
                val c = URL("http://$host/apk.json").openConnection() as HttpURLConnection
                c.connectTimeout = 3000; c.readTimeout = 3000
                val t = c.inputStream.bufferedReader().use { it.readText() }
                Regex("\"version\"\\s*:\\s*\"([^\"]+)\"").find(t)?.groupValues?.get(1)
            } catch (_: Exception) {
                null
            } ?: return@thread
            val mine = installedVersion()
            val prefs = getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            if (!newer(hub, mine) || prefs.getString(KEY_DECLINED, null) == hub) return@thread
            runOnUiThread {
                AlertDialog.Builder(this)
                    .setTitle("Update the Camperlux app?")
                    .setMessage("The hub has version $hub of this app; this is $mine.")
                    .setPositiveButton("Update") { _, _ -> install("http://$host/camperlux.apk") }
                    .setNegativeButton("Not now") { _, _ -> }
                    .setNeutralButton("Skip this version") { _, _ ->
                        prefs.edit().putString(KEY_DECLINED, hub).apply()
                    }
                    .show()
            }
        }
    }

    private fun install(url: String) {
        // Android's one-off permission for this app to install apps
        if (!packageManager.canRequestPackageInstalls()) {
            Toast.makeText(this, "Allow Camperlux to install apps, then tap Update again",
                Toast.LENGTH_LONG).show()
            try {
                startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                    Uri.parse("package:$packageName")))
            } catch (_: Exception) {
            }
            return
        }
        Toast.makeText(this, "Downloading the update…", Toast.LENGTH_SHORT).show()
        thread {
            val err = try {
                val pi = packageManager.packageInstaller
                val params = PackageInstaller.SessionParams(
                    PackageInstaller.SessionParams.MODE_FULL_INSTALL)
                params.setAppPackageName(packageName)
                val id = pi.createSession(params)
                pi.openSession(id).use { s ->
                    val c = URL(url).openConnection() as HttpURLConnection
                    c.connectTimeout = 5000; c.readTimeout = 30000
                    c.inputStream.use { input ->
                        s.openWrite("camperlux.apk", 0, -1).use { out ->
                            input.copyTo(out)
                            s.fsync(out)
                        }
                    }
                    // the result comes back to this activity (onNewIntent)
                    val flags = PendingIntent.FLAG_UPDATE_CURRENT or
                        (if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0)
                    val pending = PendingIntent.getActivity(this, 1,
                        Intent(this, MainActivity::class.java).setAction(ACTION_INSTALLED), flags)
                    s.commit(pending.intentSender)
                }
                null
            } catch (e: Exception) {
                e.message ?: e.toString()
            }
            if (err != null) runOnUiThread {
                Toast.makeText(this, "Update failed: $err", Toast.LENGTH_LONG).show()
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        if (intent.action != ACTION_INSTALLED) return
        when (intent.getIntExtra(PackageInstaller.EXTRA_STATUS, -999)) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                // Android's "Do you want to update this app?" - the user decides
                @Suppress("DEPRECATION")
                val confirm = intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT)
                if (confirm != null) startActivity(confirm)
            }
            PackageInstaller.STATUS_SUCCESS ->
                Toast.makeText(this, "Updated", Toast.LENGTH_SHORT).show()
            else -> Toast.makeText(this, "Not updated: " +
                (intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE) ?: "cancelled"),
                Toast.LENGTH_LONG).show()
        }
    }

    private fun download(url: String, name: String) {
        Toast.makeText(this, "Downloading $name…", Toast.LENGTH_SHORT).show()
        thread {
            val msg = try {
                val bytes = (URL(url).openConnection() as HttpURLConnection).run {
                    connectTimeout = 5000; readTimeout = 20000
                    inputStream.use { it.readBytes() }
                }
                save(name, bytes)
                "Saved $name to Downloads"
            } catch (e: Exception) {
                "Download failed: ${e.message}"
            }
            runOnUiThread { Toast.makeText(this, msg, Toast.LENGTH_LONG).show() }
        }
    }

    private fun save(name: String, bytes: ByteArray) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val values = ContentValues().apply {
                put(MediaStore.Downloads.DISPLAY_NAME, name)
                put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS)
            }
            val uri = contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
                ?: error("could not create the file")
            contentResolver.openOutputStream(uri)!!.use { it.write(bytes) }
        } else {
            @Suppress("DEPRECATION")
            val dir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
            File(dir, name).writeBytes(bytes)
        }
    }

    // ---- small UI helpers -------------------------------------------------

    private fun showStatus(text: String, busy: Boolean) {
        panel.visibility = View.VISIBLE
        if (!pageShown) web.visibility = View.INVISIBLE
        status.text = text
        spinner.visibility = if (busy) View.VISIBLE else View.GONE
        buttons.visibility = if (busy) View.GONE else View.VISIBLE
    }

    private fun askForAddress() {
        val prefs = getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val input = EditText(this).apply {
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
            hint = "e.g. 192.168.4.187"
            setText(prefs.getString(KEY_MANUAL, "") ?: "")
        }
        AlertDialog.Builder(this)
            .setTitle("Hub address")
            .setMessage("Only needed if the hub is not found by itself. Leave empty to clear.")
            .setView(input)
            .setPositiveButton("Connect") { _, _ ->
                val v = input.text.toString().trim()
                    .removePrefix("http://").trimEnd('/')
                prefs.edit().apply {
                    if (v.isEmpty()) remove(KEY_MANUAL) else putString(KEY_MANUAL, v)
                }.apply()
                findHub()
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun button(label: String, onClick: () -> Unit) = Button(this).apply {
        text = label
        isAllCaps = false
        setTextColor(TXT)
        setBackgroundColor(PANEL2)
        setPadding(dp(18), dp(10), dp(18), dp(10))
        layoutParams = LinearLayout.LayoutParams(-2, -2).apply { setMargins(dp(6), 0, dp(6), 0) }
        setOnClickListener { onClick() }
    }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()
}
