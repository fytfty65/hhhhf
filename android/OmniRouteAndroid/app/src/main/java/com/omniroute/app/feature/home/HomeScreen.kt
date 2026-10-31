package com.omniroute.app.feature.home

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.omniroute.app.core.network.dto.UserDto
import com.omniroute.app.core.ui.components.MessageBanner
import com.omniroute.app.core.ui.components.PrimaryButton
import kotlinx.coroutines.launch

/** 登录后的占位首页：展示当前用户资料，并提供退出登录。 */
@Composable
fun HomeScreen(
    initialUser: UserDto?,
    onLoadProfile: suspend () -> Result<UserDto>,
    onLogout: suspend () -> Unit,
    onLoggedOut: () -> Unit,
) {
    var user by remember { mutableStateOf(initialUser) }
    var loadingProfile by remember { mutableStateOf(true) }
    var loggingOut by remember { mutableStateOf(false) }
    var errorText by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    val loadProfile: suspend () -> Unit = {
        loadingProfile = true
        onLoadProfile()
            .onSuccess {
                user = it
                errorText = null
            }
            .onFailure { errorText = it.message ?: "加载个人资料失败" }
        loadingProfile = false
    }

    LaunchedEffect(Unit) { loadProfile() }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .windowInsetsPadding(WindowInsets.safeDrawing)
            .padding(horizontal = 24.dp, vertical = 32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Column(
            modifier = Modifier
                .weight(1f)
                .verticalScroll(rememberScrollState()),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Spacer(modifier = Modifier.height(48.dp))
            Text(
                text = "已登录",
                style = MaterialTheme.typography.labelLarge,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(modifier = Modifier.height(12.dp))
            Text(
                text = user?.displayName ?: "正在加载…",
                style = MaterialTheme.typography.headlineMedium,
                fontWeight = FontWeight.SemiBold,
            )
            Spacer(modifier = Modifier.height(4.dp))
            Text(
                text = "@" + user?.username.orEmpty(),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            val signature = user?.signature
            if (!signature.isNullOrBlank()) {
                Spacer(modifier = Modifier.height(16.dp))
                Text(
                    text = signature,
                    style = MaterialTheme.typography.bodyLarge,
                )
            }

            if (loadingProfile) {
                Spacer(modifier = Modifier.height(24.dp))
                Text(
                    text = "正在同步个人资料…",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            errorText?.let {
                Spacer(modifier = Modifier.height(20.dp))
                MessageBanner(text = it, isError = true)
                Spacer(modifier = Modifier.height(12.dp))
                OutlinedButton(onClick = { scope.launch { loadProfile() } }) {
                    Text(text = "重新加载")
                }
            }
        }

        PrimaryButton(
            text = "退出登录",
            loading = loggingOut,
            onClick = {
                if (!loggingOut) {
                    loggingOut = true
                    scope.launch {
                        onLogout()
                        loggingOut = false
                        onLoggedOut()
                    }
                }
            },
        )
    }
}
