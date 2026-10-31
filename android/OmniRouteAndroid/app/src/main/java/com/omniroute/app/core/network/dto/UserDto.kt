package com.omniroute.app.core.network.dto

import kotlinx.serialization.Serializable

@Serializable
data class UserDto(
    val id: String = "",
    val username: String = "",
    val nickname: String? = null,
    val avatarSeed: String? = null,
    val avatarUrl: String? = null,
    val signature: String? = null,
    val createdAt: String? = null,
) {
    /** 昵称为空时回退到用户名，与后端 buildAuthUser 的行为保持一致。 */
    val displayName: String
        get() = nickname?.takeIf { it.isNotBlank() } ?: username
}
