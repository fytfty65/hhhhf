package com.omniroute.app.core.network

import kotlinx.serialization.json.Json

/**
 * 全局 JSON 配置。后端会持续演进字段，客户端只声明自己需要的部分，
 * 因此必须忽略未知字段，避免多一个字段就解析失败。
 */
val AppJson: Json = Json {
    ignoreUnknownKeys = true
    explicitNulls = false
    coerceInputValues = true
}
