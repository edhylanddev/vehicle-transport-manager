import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Version comes from -PversionCode / -PversionName (CI) or falls back to these.
val vCode = (project.findProperty("versionCode") as String?)?.toInt() ?: 1
val vName = (project.findProperty("versionName") as String?) ?: "1.0.0"
// The site the app opens. Override with -PbaseUrl=https://staging.example.com for testing.
val baseUrl = (project.findProperty("baseUrl") as String?) ?: "https://vehicletransport.autos"

// Release signing from keystore.properties (local) or env vars (CI). Never commit the keystore.
val keyProps = Properties().apply {
    val f = rootProject.file("keystore.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}
fun signingValue(key: String, env: String): String? = keyProps.getProperty(key) ?: System.getenv(env)

android {
    namespace = "autos.vehicletransport.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "autos.vehicletransport.app"
        minSdk = 24
        targetSdk = 35
        versionCode = vCode
        versionName = vName
        buildConfigField("String", "BASE_URL", "\"$baseUrl\"")
    }

    signingConfigs {
        create("release") {
            val store = signingValue("storeFile", "VTM_KEYSTORE_FILE")
            if (store != null) {
                storeFile = file(store)
                storePassword = signingValue("storePassword", "VTM_KEYSTORE_PASSWORD")
                keyAlias = signingValue("keyAlias", "VTM_KEY_ALIAS")
                keyPassword = signingValue("storePassword", "VTM_KEYSTORE_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            val rel = signingConfigs.getByName("release")
            if (rel.storeFile != null) signingConfig = rel
        }
        debug {
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
        }
    }

    buildFeatures { buildConfig = true }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
}
