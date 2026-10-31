package com.omniroute.app.core.network.dto

import com.omniroute.app.core.network.AppJson
import kotlinx.serialization.Serializable
import retrofit2.Response

@Serializable
data class ApiErrorDto(
    val error: String? = null,
    val code: String? = null,
)

/**
 * 把后端真实的错误体转成可展示文案，保留 error 与 code。
 * 解析失败或没有错误体时退化为 HTTP 状态码，绝不伪造成功。
 */
fun Response<*>.backendErrorMessage(): String {
    val raw = runCatching { errorBody()?.string() }.getOrNull()
    val dto = raw
        ?.takeIf { it.isNotBlank() }
        ?.let { runCatching { AppJson.decodeFromString<ApiErrorDto>(it) }.getOrNull() }
    val errorText = dto?.error?.takeIf { it.isNotBlank() }
    val errorCode = dto?.code?.takeIf { it.isNotBlank() }
    return when {
        errorText != null && errorCode != null -> "$errorText（$errorCode）"
        errorText != null -> errorText
        errorCode != null -> "请求失败（$errorCode）"
        else -> "请求失败（HTTP ${code()}）"
    }
}

/** 携带后端错误文案的异常，UI 直接展示其 message。 */
class ApiException(message: String) : Exception(message)
