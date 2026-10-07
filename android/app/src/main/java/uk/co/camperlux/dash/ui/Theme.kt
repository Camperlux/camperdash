package uk.co.camperlux.dash.ui

import androidx.compose.ui.graphics.Color

object Brand {
    val bg = Color(0xFF0F0E12)
    val panel = Color(0xFF17151B)
    val panel2 = Color(0xFF201D26)
    val line = Color(0xFF322D3A)
    val txt = Color(0xFFEFE9E0)
    val muted = Color(0xFF9A8F9E)
    val green = Color(0xFF3FB950)
    val amber = Color(0xFFD29922)
    val red = Color(0xFFF85149)
    val blue = Color(0xFF58A6FF)
    val gold = Color(0xFFD3A94A)
}

fun fmtHours(h: Double?): String? {
    if (h == null) return null
    if (h > 240) return "10+ days"
    val m = Math.round(h * 60).toInt()
    return if (m < 60) "$m min" else "${m / 60} h ${(m % 60).toString().padStart(2, '0')} m"
}
