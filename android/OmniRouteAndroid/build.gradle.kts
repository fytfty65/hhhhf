// 顶层构建脚本：只声明插件版本，具体应用在 app 模块。
// AGP 9 内置 Kotlin 支持，不再需要 org.jetbrains.kotlin.android。
plugins {
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.kotlin.compose) apply false
    alias(libs.plugins.kotlin.serialization) apply false
}
