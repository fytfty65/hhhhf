package com.omniroute.app.core.network

import com.jakewharton.retrofit2.converter.kotlinx.serialization.asConverterFactory
import com.omniroute.app.BuildConfig
import com.omniroute.app.core.auth.TokenStore
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import retrofit2.Retrofit
import java.util.concurrent.TimeUnit

/**
 * 网络入口。分两个 OkHttp 实例：
 * - plainClient 用于登录/注册/刷新，避免刷新自身触发 401 刷新造成递归；
 * - authorizedClient 额外挂 Authenticator，用于需要 Bearer 的业务接口。
 */
class ApiClient(
    baseUrl: String,
    tokenStore: TokenStore,
) {

    private val normalizedBaseUrl = baseUrl.trimEnd('/') + "/"

    /** 由 AuthRepository 注入，刷新逻辑统一放在仓库层（Mutex 单飞）。 */
    @Volatile
    var refreshBlocking: ((staleAccessToken: String?) -> String?)? = null

    private val plainClient: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(20, TimeUnit.SECONDS)
        .writeTimeout(20, TimeUnit.SECONDS)
        .addInterceptor(AuthInterceptor(tokenStore))
        .addInterceptor(SafeLoggingInterceptor(BuildConfig.DEBUG))
        .build()

    private fun retrofit(client: OkHttpClient): Retrofit = Retrofit.Builder()
        .baseUrl(normalizedBaseUrl)
        .client(client)
        .addConverterFactory(AppJson.asConverterFactory("application/json".toMediaType()))
        .build()

    val authApi: AuthApi = retrofit(plainClient).create(AuthApi::class.java)

    private val authorizedClient: OkHttpClient = plainClient.newBuilder()
        .authenticator(TokenAuthenticator { stale -> refreshBlocking?.invoke(stale) })
        .build()

    /** 需要 Authorization 的接口实例，401 时自动刷新并重试一次。 */
    val api: AuthApi = retrofit(authorizedClient).create(AuthApi::class.java)
}
