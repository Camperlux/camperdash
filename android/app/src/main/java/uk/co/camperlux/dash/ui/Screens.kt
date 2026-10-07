package uk.co.camperlux.dash.ui

import androidx.compose.animation.core.*
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathMeasure
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import uk.co.camperlux.dash.model.*
import kotlin.math.hypot
import kotlin.math.roundToInt

private fun Double?.w() = if (this == null) "–" else "${this.roundToInt()} W"
private fun Double?.f(dp: Int, unit: String) =
    if (this == null) "–" else "%.${dp}f%s".format(this, unit)

@Composable
private fun Panel(modifier: Modifier = Modifier, hero: Boolean = false, content: @Composable ColumnScope.() -> Unit) {
    Column(
        modifier
            .background(Brand.panel, RoundedCornerShape(16.dp))
            .border(if (hero) 2.dp else 1.dp, if (hero) Brand.gold else Brand.line, RoundedCornerShape(16.dp))
            .padding(16.dp),
        content = content
    )
}

@Composable
private fun Label(text: String) =
    Text(text.uppercase(), color = Brand.muted, fontSize = 11.sp, letterSpacing = 0.8.sp, fontWeight = FontWeight.Medium)

/* ---------------- Energy Flow (main) ---------------- */

@Composable
fun FlowScreen(s: VanState) {
    // continuous frame clock (ms) driving the flowing dots
    val clockMs by produceState(0f) {
        while (true) withInfiniteAnimationFrameMillis { value = it.toFloat() }
    }
    val clockSec = clockMs / 1000f

    val r = s.renogy; val v = s.victron; val b = s.battery
    val solarW = if (r.connected) r.solarW?.toDouble() else null
    val altW = if (r.connected) r.altW?.toDouble() else null
    val mainsW = if (v.connected) v.powerW else null
    val loadW = s.derived.loadW

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(2.dp)
    ) {
        // three source cards across the top
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            SourceCard("Solar", if (r.connected) r.solarV.f(1, "V") else "off", solarW, Modifier.weight(1f))
            SourceCard("Alternator", if (r.connected) r.altV.f(1, "V") else "off", altW, Modifier.weight(1f))
            SourceCard("Mains", if (v.connected) (v.state ?: "on") else if (v.stale) "unplugged" else "off",
                mainsW, Modifier.weight(1f))
        }

        // animated connectors: 3 sources -> battery
        Canvas(Modifier.fillMaxWidth().height(66.dp)) {
            val fracs = listOf(1f / 6f, 3f / 6f, 5f / 6f)
            val endX = size.width / 2f
            fracs.forEachIndexed { i, fx ->
                val sx = size.width * fx
                val path = Path().apply {
                    moveTo(sx, 0f)
                    cubicTo(sx, size.height * 0.55f, endX, size.height * 0.45f, endX, size.height)
                }
                flowLane(path, listOf(solarW, altW, mainsW)[i], Brand.green, clockSec)
            }
        }

        // battery hero
        Panel(hero = true, modifier = Modifier.fillMaxWidth()) {
            Label("Battery")
            if (b.connected) {
                Text("${b.soc ?: "–"}%", color = Brand.txt, fontSize = 40.sp, fontWeight = FontWeight.Bold)
                Text(b.voltage.f(2, " V") + " · " + (b.residualAh?.roundToInt()?.toString() ?: "–") + " Ah",
                    color = Brand.muted, fontSize = 14.sp)
                Spacer(Modifier.height(8.dp))
                val p = b.powerW ?: 0.0
                when {
                    p > 0.5 -> {
                        Text("▲ Charging ${p.roundToInt()} W", color = Brand.green, fontWeight = FontWeight.SemiBold)
                        val t = fmtHours(s.derived.timeToFullH)
                        Text(if ((b.soc ?: 0) >= 100) "Full" else t?.let { "Full in $it" } ?: "Charging",
                            color = Brand.green, fontSize = 13.sp)
                    }
                    p < -0.5 -> {
                        Text("▼ Discharging ${(-p).roundToInt()} W", color = Brand.amber, fontWeight = FontWeight.SemiBold)
                        Text(fmtHours(s.derived.timeToEmptyH)?.let { "Empty in $it" } ?: "—",
                            color = Brand.amber, fontSize = 13.sp)
                    }
                    else -> Text(if ((b.soc ?: 0) >= 100) "Fully charged" else "Idle", color = Brand.muted)
                }
            } else Text("battery offline", color = Brand.muted, fontSize = 14.sp)
        }

        // animated connector: battery -> loads
        Canvas(Modifier.fillMaxWidth().height(56.dp)) {
            val x = size.width / 2f
            val path = Path().apply { moveTo(x, 0f); lineTo(x, size.height) }
            flowLane(path, loadW, Brand.amber, clockSec)
        }

        // loads
        Panel(modifier = Modifier.fillMaxWidth()) {
            Label("Loads")
            Text(loadW.w(), color = if ((loadW ?: 0.0) > 0.5) Brand.amber else Brand.muted,
                fontSize = 26.sp, fontWeight = FontWeight.Bold)
        }
    }
}

@Composable
private fun SourceCard(name: String, sub: String, watts: Double?, modifier: Modifier) {
    val active = (watts ?: 0.0) > 0.5
    Panel(modifier) {
        Label(name)
        Text(watts.w(), color = if (active) Brand.green else Brand.muted,
            fontSize = 20.sp, fontWeight = FontWeight.Bold)
        Text(sub, color = Brand.muted, fontSize = 12.sp)
    }
}

/** Draw a connector path: dim base, arrowhead at the end, and flowing dots when active. */
private fun DrawScope.flowLane(path: Path, watts: Double?, color: Color, clockSec: Float, dots: Int = 5) {
    val pm = PathMeasure().apply { setPath(path, false) }
    val len = pm.length
    if (len <= 1f) return
    val active = (watts ?: 0.0) > 0.5
    drawPath(path, if (active) color.copy(alpha = 0.30f) else Brand.line, style = Stroke(width = 3.dp.toPx()))
    // arrowhead at the terminal end
    val tip = pm.getPosition(len)
    val back = pm.getPosition((len - 14.dp.toPx()).coerceAtLeast(0f))
    drawArrowhead(tip, back, if (active) color else Brand.line)
    if (active) {
        val dur = (160.0 / maxOf(watts!!, 6.0)).coerceIn(0.35, 2.4)   // seconds per cycle
        val speed = (1.0 / dur).toFloat()                             // cycles per second
        for (i in 0 until dots) {
            val frac = (((clockSec * speed) + i.toFloat() / dots) % 1f)
            drawCircle(color, 3.5.dp.toPx(), pm.getPosition(frac * len))
        }
    }
}

private fun DrawScope.drawArrowhead(tip: Offset, from: Offset, color: Color) {
    val dx = tip.x - from.x; val dy = tip.y - from.y
    val d = hypot(dx, dy).coerceAtLeast(0.01f)
    val ux = dx / d; val uy = dy / d       // direction of travel
    val px = -uy; val py = ux              // perpendicular
    val s = 7.dp.toPx()
    val b1 = Offset(tip.x - ux * s + px * s * 0.6f, tip.y - uy * s + py * s * 0.6f)
    val b2 = Offset(tip.x - ux * s - px * s * 0.6f, tip.y - uy * s - py * s * 0.6f)
    drawPath(Path().apply { moveTo(tip.x, tip.y); lineTo(b1.x, b1.y); lineTo(b2.x, b2.y); close() }, color)
}

/* ---------------- Detailed dashboard ---------------- */

@Composable
fun DashboardScreen(s: VanState) {
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        val b = s.battery
        Panel(hero = true) {
            Label("State of charge")
            Text("${b.soc ?: "–"}%", color = Brand.txt, fontSize = 44.sp, fontWeight = FontWeight.Bold)
            Text((b.residualAh?.roundToInt() ?: "–").toString() + " / " + (b.nominalAh?.roundToInt() ?: "–") + " Ah",
                color = Brand.muted, fontSize = 13.sp)
        }
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Panel(Modifier.weight(1f)) { Label("Pack voltage"); Big(b.voltage.f(2, ""), "V") }
            Panel(Modifier.weight(1f)) {
                val c = b.current ?: 0.0
                Label(if (c > 0.05) "Charging" else if (c < -0.05) "Discharging" else "Current")
                Big(b.current.f(2, ""), "A", if (c > 0.05) Brand.green else if (c < -0.05) Brand.amber else Brand.txt)
                Text(b.powerW.f(1, " W"), color = Brand.muted, fontSize = 12.sp)
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Panel(Modifier.weight(1f)) {
                Label("Load"); Big(s.derived.loadA.f(2, ""), "A")
                Text(s.derived.loadW.f(0, " W"), color = Brand.muted, fontSize = 12.sp)
            }
            Panel(Modifier.weight(1f)) {
                Label("Battery temps")
                val roles = listOf("board", "cell", "cell")
                b.tempsC.forEachIndexed { i, t ->
                    Row(Modifier.fillMaxWidth().padding(top = 4.dp), horizontalArrangement = Arrangement.SpaceBetween) {
                        Text("T${i + 1} ${roles.getOrElse(i) { "" }}", color = Brand.muted, fontSize = 12.sp)
                        Text("%.1f °C".format(t), color = if (t == (b.tempsC.maxOrNull() ?: 0.0) && b.tempsC.size > 1) Brand.amber else Brand.txt,
                            fontWeight = FontWeight.SemiBold)
                    }
                }
            }
        }
        // cells
        Panel {
            Label("Cells · Δ ${b.cellDeltaMv ?: "–"} mV")
            b.cellsMv.forEachIndexed { i, mv ->
                Row(Modifier.fillMaxWidth().padding(top = 4.dp), horizontalArrangement = Arrangement.SpaceBetween) {
                    Text("C${i + 1}", color = Brand.muted, fontSize = 13.sp)
                    Text("%.3f V".format(mv / 1000.0), color = Brand.txt, fontWeight = FontWeight.SemiBold)
                }
            }
        }
        // health
        Panel {
            Label("Health")
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(top = 6.dp)) {
                Chip(if (b.chargeFet) "Charge enabled" else "Charge disabled", if (b.chargeFet) Brand.green else Brand.amber)
                Chip(if (b.dischargeFet) "Discharge enabled" else "Discharge disabled", if (b.dischargeFet) Brand.green else Brand.amber)
            }
            Chip(if (b.faults.isEmpty()) "No faults" else b.faults.joinToString(), if (b.faults.isEmpty()) Brand.green else Brand.red)
            Text("Cycles: ${b.cycles ?: "–"}   Mfd ${b.prodDate ?: "–"}", color = Brand.muted, fontSize = 12.sp,
                modifier = Modifier.padding(top = 8.dp))
        }
        // chargers
        val r = s.renogy; val v = s.victron
        Panel {
            Label("Renogy DC-DC charger")
            Big(r.chargeA.f(2, ""), "A", Brand.txt)
            Text(if (r.connected) "${r.chargeW.f(1, " W")} · ${r.state ?: ""} · from ${(r.source ?: "").lowercase()}" else "offline",
                color = Brand.muted, fontSize = 12.sp)
            if (r.connected) Text("Solar ${r.solarW ?: 0} W · Alternator ${r.altW ?: 0} W", color = Brand.muted, fontSize = 12.sp)
        }
        Panel {
            Label("Victron mains hook up")
            Big(v.current.f(2, ""), "A", Brand.txt)
            Text(if (v.connected) "${v.powerW.f(1, " W")} · ${v.state ?: ""} · ${v.voltage.f(2, " V")}"
                else if (v.stale) "not plugged in" else "offline", color = Brand.muted, fontSize = 12.sp)
        }
    }
}

@Composable
private fun Big(value: String, unit: String, color: Color = Brand.txt) {
    Row(verticalAlignment = Alignment.Bottom) {
        Text(value, color = color, fontSize = 28.sp, fontWeight = FontWeight.Bold)
        Text(" $unit", color = Brand.muted, fontSize = 13.sp, modifier = Modifier.padding(bottom = 4.dp))
    }
}

@Composable
private fun Chip(text: String, color: Color) {
    Box(
        Modifier.padding(top = 6.dp)
            .border(1.dp, color, RoundedCornerShape(50))
            .padding(horizontal = 10.dp, vertical = 5.dp)
    ) { Text(text, color = color, fontSize = 12.sp) }
}
