// Deliberately no libraries beyond the Android framework and Kotlin itself: the
// app is one screen around a WebView, and every dependency is build time, APK
// size and disk space for nothing.
plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "uk.co.camperlux.hub"
    compileSdk = 35

    defaultConfig {
        applicationId = "uk.co.camperlux.hub"
        minSdk = 26
        targetSdk = 35
        versionCode = 6
        versionName = "1.4"
    }
    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

// The one library: Android Auto shows only its own templates, and they come
// from Google's Car App Library (car/HubCar.kt).
dependencies {
    implementation("androidx.car.app:app:1.4.0")
}
