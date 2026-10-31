package com.omniroute.app.core.network.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class LoginRequest(
    val username: String,
    val password: String,
)

@Serializable
data class RegisterRequest(
    val username: String,
    val password: String,
    val nickname: String? = null,
)

@Serializable
data class RefreshRequest(
    @SerialName("refresh_token") val refreshToken: String,
)

@Serializable
data class LogoutRequest(
    @SerialName("refresh_token") val refreshToken: String,
)

/** 移动端登录/刷新的统一响应，字段名与 Gateway 的 mobile_auth.go 一致。 */
@Serializable
data class AuthResponse(
    @SerialName("access_token") val accessToken: String,
    @SerialName("refresh_token") val refreshToken: String,
    @SerialName("expires_in") val expiresIn: Long = 0,
    val user: UserDto,
)

@Serializable
data class RegisterResponse(
    val message: String? = null,
    val user: UserDto? = null,
)

@Serializable
data class LogoutResponse(
    val message: String? = null,
)

/** GET /api/user/profile 返回 {user, trips}，客户端暂只读取 user。 */
@Serializable
data class ProfileResponse(
    val user: UserDto? = null,
)
