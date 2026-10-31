package com.omniroute.app.core.network

import okhttp3.Authenticator
import okhttp3.Request
import okhttp3.Response
import okhttp3.Route

/**
 * 处理 401：请求失败的 token 交给 AuthRepository 单飞刷新。
 * 重试请求打上标记，避免刷新后再次 401 时无限循环。
 */
class TokenAuthenticator(
    private val refresher: (staleAccessToken: String?) -> String?,
) : Authenticator {

    override fun authenticate(route: Route?, response: Response): Request? {
        if (response.request.header(RETRY_HEADER) != null) return null

        val staleToken = response.request.header("Authorization")
            ?.removePrefix("Bearer ")
            ?.trim()
            ?.takeIf { it.isNotEmpty() }

        val freshToken = refresher(staleToken) ?: return null

        return response.request.newBuilder()
            .header("Authorization", "Bearer $freshToken")
            .header(RETRY_HEADER, "1")
            .build()
    }

    companion object {
        private const val RETRY_HEADER = "X-OmniRoute-Auth-Retry"
    }
}
