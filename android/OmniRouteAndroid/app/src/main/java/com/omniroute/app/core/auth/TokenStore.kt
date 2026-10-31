package com.omniroute.app.core.auth

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * 令牌存储：
 * - access token 只保留在内存（StateFlow），进程退出即消失；
 * - refresh token 经 Keystore AES/GCM 加密后写入 SharedPreferences。
 */
class TokenStore(context: Context) {

    private val prefs = context.applicationContext
        .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    private val cipher = TokenCipher()

    private val _accessToken = MutableStateFlow<String?>(null)
    val accessTokenFlow: StateFlow<String?> = _accessToken.asStateFlow()

    fun currentAccessToken(): String? = _accessToken.value

    fun save(accessToken: String, refreshToken: String) {
        _accessToken.value = accessToken
        val encrypted = runCatching { cipher.encrypt(refreshToken) }.getOrNull()
        prefs.edit().apply {
            if (encrypted != null) putString(KEY_REFRESH_TOKEN, encrypted) else remove(KEY_REFRESH_TOKEN)
        }.apply()
    }

    fun currentRefreshToken(): String? {
        val payload = prefs.getString(KEY_REFRESH_TOKEN, null) ?: return null
        val plain = cipher.decrypt(payload)
        if (plain == null) prefs.edit().remove(KEY_REFRESH_TOKEN).apply()
        return plain
    }

    fun clear() {
        _accessToken.value = null
        prefs.edit().remove(KEY_REFRESH_TOKEN).apply()
    }

    private companion object {
        const val PREFS_NAME = "omniroute_tokens"
        const val KEY_REFRESH_TOKEN = "refresh_token_cipher"
    }
}
