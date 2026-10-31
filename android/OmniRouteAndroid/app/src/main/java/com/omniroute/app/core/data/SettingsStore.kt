package com.omniroute.app.core.data

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

private val Context.settingsDataStore: DataStore<Preferences> by preferencesDataStore(
    name = "omniroute_settings",
)

/** 非敏感偏好设置（DataStore）。令牌不放在这里。 */
class SettingsStore(context: Context) {

    private val dataStore = context.applicationContext.settingsDataStore

    /** 上次登录成功的用户名，用于登录页预填。 */
    val lastUsername: Flow<String> = dataStore.data.map { it[KEY_LAST_USERNAME].orEmpty() }

    /** 是否跟随系统动态取色（Android 12+ 生效）。 */
    val dynamicColor: Flow<Boolean> = dataStore.data.map { it[KEY_DYNAMIC_COLOR] ?: true }

    suspend fun setLastUsername(username: String) {
        dataStore.edit { it[KEY_LAST_USERNAME] = username }
    }

    suspend fun setDynamicColor(enabled: Boolean) {
        dataStore.edit { it[KEY_DYNAMIC_COLOR] = enabled }
    }

    private companion object {
        val KEY_LAST_USERNAME = stringPreferencesKey("last_username")
        val KEY_DYNAMIC_COLOR = booleanPreferencesKey("dynamic_color")
    }
}
