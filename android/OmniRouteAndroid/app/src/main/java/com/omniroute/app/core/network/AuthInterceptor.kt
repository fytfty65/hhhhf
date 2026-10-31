package com.omniroute.app.core.network

import com.omniroute.app.core.auth.TokenStore
import okhttp3.Interceptor
import okhttp3.Response
import java.util.UUID

/** 给每个请求注入 Bearer 与 X-Request-Id；没有 access token 时只带请求 ID。 */
class AuthInterceptor(private val tokenStore: TokenStore) : Interceptor {

    override fun intercept(chain: Interceptor.Chain): Response {
        val builder = chain.request().newBuilder()
            .header("X-Request-Id", UUID.randomUUID().toString())

        tokenStore.currentAccessToken()
            ?.takeIf { it.isNotBlank() }
            ?.let { builder.header("Authorization", "Bearer $it") }

        return chain.proceed(builder.build())
    }
}
