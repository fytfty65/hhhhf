package com.omniroute.app.core.network

import android.util.Log
import okhttp3.Interceptor
import okhttp3.Response
import java.io.IOException

/**
 * 轻量日志拦截器：只记录方法、路径、状态码与耗时。
 * 刻意不输出任何请求/响应体和头部，确保 refresh token、密码不会进入 logcat。
 * 仅 debug 构建启用。
 */
class SafeLoggingInterceptor(private val enabled: Boolean) : Interceptor {

    override fun intercept(chain: Interceptor.Chain): Response {
        if (!enabled) return chain.proceed(chain.request())

        val request = chain.request()
        val startNs = System.nanoTime()
        val response = try {
            chain.proceed(request)
        } catch (e: IOException) {
            Log.w(TAG, "${request.method} ${request.url.encodedPath} 网络异常: ${e.javaClass.simpleName}")
            throw e
        }
        val costMs = (System.nanoTime() - startNs) / 1_000_000
        Log.d(TAG, "${request.method} ${request.url.encodedPath} -> ${response.code} (${costMs}ms)")
        return response
    }

    companion object {
        private const val TAG = "OmniRouteHttp"
    }
}
