package uk.co.camperlux.dash

import android.Manifest
import android.app.Application
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.compose.viewModel
import uk.co.camperlux.dash.ble.BleManager
import uk.co.camperlux.dash.ui.Brand
import uk.co.camperlux.dash.ui.DashboardScreen
import uk.co.camperlux.dash.ui.FlowScreen

class DashViewModel(app: Application) : AndroidViewModel(app) {
    private val ble = BleManager(app)
    val state = ble.state
    private var started = false
    fun startBle() { if (!started) { started = true; ble.start(viewModelScope) } }
}

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { App() }
    }
}

@Composable
private fun App() {
    val vm: DashViewModel = viewModel()
    val state by vm.state.collectAsStateWithLifecycle()
    var granted by remember { mutableStateOf(false) }
    var tab by remember { mutableStateOf(0) }

    val perms = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S)
        arrayOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT)
    else arrayOf(Manifest.permission.ACCESS_FINE_LOCATION)

    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { res ->
        if (res.values.all { it }) { granted = true; vm.startBle() }
    }
    LaunchedEffect(Unit) { launcher.launch(perms) }

    Column(Modifier.fillMaxSize().background(Brand.bg)) {
        // header
        Row(
            Modifier.fillMaxWidth().statusBarsPadding().padding(16.dp, 12.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Image(painterResource(R.mipmap.ic_launcher_round), null,
                Modifier.size(30.dp).clip(CircleShape))
            Spacer(Modifier.width(9.dp))
            Row(Modifier.weight(1f)) {
                Text("Camper", color = Brand.txt, fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
                Text("lux", color = Brand.gold, fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
            }
            Dot(state.battery.connected); Spacer(Modifier.width(6.dp))
            Dot(state.renogy.connected); Spacer(Modifier.width(6.dp))
            Dot(state.victron.connected)
        }

        // tabs
        Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            TabPill("Energy Flow", tab == 0) { tab = 0 }
            TabPill("Dashboard", tab == 1) { tab = 1 }
        }

        if (!granted) {
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Text("Grant Bluetooth permission to connect", color = Brand.muted)
            }
        } else {
            when (tab) {
                0 -> FlowScreen(state)
                else -> DashboardScreen(state)
            }
        }
    }
}

@Composable
private fun Dot(ok: Boolean) {
    Box(Modifier.size(9.dp).clip(CircleShape).background(if (ok) Brand.green else Brand.red))
}

@Composable
private fun TabPill(text: String, active: Boolean, onClick: () -> Unit) {
    Box(
        Modifier.clip(RoundedCornerShape(50))
            .background(if (active) Brand.panel2 else Color.Transparent)
            .clickable(onClick = onClick)
            .padding(horizontal = 14.dp, vertical = 7.dp)
    ) { Text(text, color = if (active) Brand.gold else Brand.muted, fontSize = 13.sp) }
}
