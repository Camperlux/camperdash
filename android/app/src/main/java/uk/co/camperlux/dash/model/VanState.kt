package uk.co.camperlux.dash.model

/** Complete van power state, mirroring the Python server's /api/data feed. */
data class BatteryState(
    val connected: Boolean = false,
    val soc: Int? = null,
    val voltage: Double? = null,
    val current: Double? = null,        // +ve into battery
    val powerW: Double? = null,
    val residualAh: Double? = null,
    val nominalAh: Double? = null,
    val cycles: Int? = null,
    val chargeFet: Boolean = false,
    val dischargeFet: Boolean = false,
    val cellsMv: List<Int> = emptyList(),
    val cellDeltaMv: Int? = null,
    val tempsC: List<Double> = emptyList(),
    val faults: List<String> = emptyList(),
    val prodDate: String? = null,
)

data class RenogyState(
    val connected: Boolean = false,
    val batteryV: Double? = null,
    val chargeA: Double? = null,
    val chargeW: Double? = null,
    val solarV: Double? = null,
    val solarA: Double? = null,
    val solarW: Int? = null,
    val altV: Double? = null,
    val altA: Double? = null,
    val altW: Int? = null,
    val state: String? = null,
    val source: String? = null,
    val ctrlTempC: Int? = null,
    val battTempC: Int? = null,
    val todayChgWh: Int? = null,
    val todayChgAh: Int? = null,
    val faults: List<String> = emptyList(),
)

data class VictronState(
    val connected: Boolean = false,
    val stale: Boolean = false,
    val model: String? = null,
    val state: String? = null,
    val voltage: Double? = null,
    val current: Double? = null,
    val powerW: Double? = null,
    val error: String? = null,
)

data class Derived(
    val loadA: Double? = null,
    val loadW: Double? = null,
    val timeToEmptyH: Double? = null,
    val timeToFullH: Double? = null,
    val avgCurrent: Double? = null,
)

data class VanState(
    val battery: BatteryState = BatteryState(),
    val renogy: RenogyState = RenogyState(),
    val victron: VictronState = VictronState(),
    val derived: Derived = Derived(),
)
