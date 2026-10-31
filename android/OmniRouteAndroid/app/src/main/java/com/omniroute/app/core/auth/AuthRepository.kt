package com.omniroute.app.core.auth

import com.omniroute.app.core.network.ApiClient
import com.omniroute.app.core.network.dto.ApiException
import com.omniroute.app.core.network.dto.AuthResponse
import com.omniroute.app.core.network.dto.LoginRequest
import com.omniroute.app.core.network.dto.LogoutRequest
import com.omniroute.app.core.network.dto.RefreshRequest
import com.omniroute.app.core.network.dto.RegisterRequest
import com.omniroute.app.core.network.dto.UserDto
import com.omniroute.app.core.network.dto.backendErrorMessage
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

/**
 * 鉴权仓库：登录/注册/刷新/登出，以及 401 触发的单飞刷新。
 * 刷新失败会清空本地会话并通过 [sessionExpired] 通知 UI 回到登录页。
 */
class AuthRepository(
    private val apiClient: ApiClient,
    private val tokenStore: TokenStore,
) {

    private val refreshMutex = Mutex()

    private val _user = MutableStateFlow<UserDto?>(null)
    val user: StateFlow<UserDto?> = _user.asStateFlow()

    private val _sessionExpired = MutableSharedFlow<Unit>(extraBufferCapacity = 1)
    val sessionExpired: SharedFlow<Unit> = _sessionExpired.asSharedFlow()

    init {
        // OkHttp Authenticator 运行在后台线程，这里同步等待刷新结果。
        apiClient.refreshBlocking = { staleToken -> refreshBlocking(staleToken) }
    }

    suspend fun login(username: String, password: String): Result<UserDto> =
        withContext(Dispatchers.IO) {
            runCatching {
                val response = apiClient.authApi.login(LoginRequest(username.trim(), password))
                val body = response.body()
                if (!response.isSuccessful || body == null) {
                    throw ApiException(response.backendErrorMessage())
                }
                tokenStore.save(body.accessToken, body.refreshToken)
                _user.value = body.user
                body.user
            }
        }

    suspend fun register(username: String, password: String, nickname: String?): Result<String> =
        withContext(Dispatchers.IO) {
            runCatching {
                val request = RegisterRequest(
                    username = username.trim(),
                    password = password,
                    nickname = nickname?.trim()?.takeIf { it.isNotEmpty() },
                )
                val response = apiClient.authApi.register(request)
                if (!response.isSuccessful) throw ApiException(response.backendErrorMessage())
                response.body()?.message?.takeIf { it.isNotBlank() } ?: "注册成功"
            }
        }

    /** 启动时静默刷新：有 refresh token 才尝试，失败即回登录页。 */
    suspend fun refreshSilent(): Boolean = withContext(Dispatchers.IO) {
        if (tokenStore.currentRefreshToken().isNullOrBlank()) {
            false
        } else {
            refreshBlocking(tokenStore.currentAccessToken()) != null
        }
    }

    suspend fun loadProfile(): Result<UserDto> = withContext(Dispatchers.IO) {
        runCatching {
            val response = apiClient.api.profile()
            val body = response.body()
            if (!response.isSuccessful || body == null) {
                throw ApiException(response.backendErrorMessage())
            }
            val user = body.user ?: throw ApiException("个人资料响应缺少 user 字段")
            _user.value = user
            user
        }
    }

    suspend fun logout() = withContext(Dispatchers.IO) {
        val refreshToken = tokenStore.currentRefreshToken().orEmpty()
        // 服务端撤销失败不影响本地退出，避免用户被困在已失效的会话里。
        runCatching { apiClient.authApi.logout(LogoutRequest(refreshToken)) }
        tokenStore.clear()
        _user.value = null
    }

    /**
     * 供 OkHttp Authenticator 调用的同步刷新。
     * 用 Mutex 保证并发 401 只真正刷新一次；若其他请求已经换到新 token 则直接复用。
     */
    private fun refreshBlocking(staleAccessToken: String?): String? = runBlocking {
        refreshMutex.withLock {
            val current = tokenStore.currentAccessToken()
            if (current != null && current != staleAccessToken) {
                current
            } else {
                performRefresh()
            }
        }
    }

    private suspend fun performRefresh(): String? {
        val refreshToken = tokenStore.currentRefreshToken()
        if (refreshToken.isNullOrBlank()) {
            clearSessionAndNotify()
            return null
        }

        val response = runCatching {
            apiClient.authApi.refresh(RefreshRequest(refreshToken))
        }.getOrNull()

        return applyAuthResponse(response)
    }

    private fun applyAuthResponse(response: retrofit2.Response<AuthResponse>?): String? {
        val body = response?.body()
        if (response == null || !response.isSuccessful || body == null) {
            clearSessionAndNotify()
            return null
        }
        tokenStore.save(body.accessToken, body.refreshToken)
        _user.value = body.user
        return body.accessToken
    }

    private fun clearSessionAndNotify() {
        tokenStore.clear()
        _user.value = null
        _sessionExpired.tryEmit(Unit)
    }
}
