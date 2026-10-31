package com.omniroute.app

import android.content.Context
import com.omniroute.app.core.auth.AuthRepository
import com.omniroute.app.core.auth.TokenStore
import com.omniroute.app.core.data.SettingsStore
import com.omniroute.app.core.network.ApiClient

/** 手写依赖容器：这个规模的应用不需要引入 DI 框架。 */
class AppContainer(context: Context) {

    private val appContext = context.applicationContext

    val settingsStore = SettingsStore(appContext)

    private val tokenStore = TokenStore(appContext)

    private val apiClient = ApiClient(BuildConfig.BASE_URL, tokenStore)

    val authRepository = AuthRepository(apiClient, tokenStore)
}
