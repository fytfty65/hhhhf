package com.omniroute.app.feature.auth

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.omniroute.app.core.network.dto.UserDto
import com.omniroute.app.core.ui.components.AuthTextField
import com.omniroute.app.core.ui.components.MessageBanner
import com.omniroute.app.core.ui.components.PrimaryButton
import kotlinx.coroutines.launch

@Composable
fun LoginScreen(
    initialUsername: String,
    notice: String?,
    onLogin: suspend (username: String, password: String) -> Result<UserDto>,
    onLoggedIn: (UserDto) -> Unit,
    onGoRegister: () -> Unit,
) {
    var username by rememberSaveable { mutableStateOf(initialUsername) }
    var password by rememberSaveable { mutableStateOf("") }
    var loading by remember { mutableStateOf(false) }
    var errorText by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    // 上次登录的用户名来自 DataStore，可能在首次组合之后才到达。
    LaunchedEffect(initialUsername) {
        if (username.isBlank()) username = initialUsername
    }

    val submit: () -> Unit = {
        val name = username.trim()
        if (loading) {
            // 忽略重复点击
        } else if (name.isEmpty() || password.isEmpty()) {
            errorText = "请填写用户名和密码"
        } else {
            errorText = null
            loading = true
            scope.launch {
                val result = onLogin(name, password)
                loading = false
                result
                    .onSuccess { onLoggedIn(it) }
                    .onFailure { errorText = it.message ?: "登录失败" }
            }
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .windowInsetsPadding(WindowInsets.safeDrawing)
            .imePadding()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 24.dp, vertical = 32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Text(
            text = "OmniRoute",
            style = MaterialTheme.typography.headlineLarge,
            fontWeight = FontWeight.SemiBold,
        )
        Spacer(modifier = Modifier.height(8.dp))
        Text(
            text = "登录后继续你的行程",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(modifier = Modifier.height(32.dp))

        AuthTextField(
            value = username,
            onValueChange = { username = it },
            label = "用户名",
            enabled = !loading,
        )
        Spacer(modifier = Modifier.height(12.dp))
        AuthTextField(
            value = password,
            onValueChange = { password = it },
            label = "密码",
            enabled = !loading,
            isPassword = true,
            keyboardType = KeyboardType.Password,
            imeAction = ImeAction.Done,
            onImeAction = submit,
        )

        notice?.let {
            Spacer(modifier = Modifier.height(16.dp))
            MessageBanner(text = it, isError = false)
        }
        errorText?.let {
            Spacer(modifier = Modifier.height(16.dp))
            MessageBanner(text = it, isError = true)
        }

        Spacer(modifier = Modifier.height(24.dp))
        PrimaryButton(text = "登录", loading = loading, onClick = submit)
        Spacer(modifier = Modifier.height(4.dp))
        TextButton(onClick = onGoRegister, enabled = !loading) {
            Text(text = "没有账号？去注册")
        }
    }
}
