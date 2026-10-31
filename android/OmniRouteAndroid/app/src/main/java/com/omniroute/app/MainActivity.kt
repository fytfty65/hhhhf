package com.omniroute.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.getValue
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.omniroute.app.core.ui.theme.OmniRouteTheme
import com.omniroute.app.ui.AppNavHost

class MainActivity : ComponentActivity() {

    private val container by lazy { AppContainer(applicationContext) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            val dynamicColor by container.settingsStore.dynamicColor
                .collectAsStateWithLifecycle(initialValue = true)
            OmniRouteTheme(dynamicColor = dynamicColor) {
                AppNavHost(container)
            }
        }
    }
}
