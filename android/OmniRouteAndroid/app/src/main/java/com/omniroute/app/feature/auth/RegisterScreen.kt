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
import com.omniroute.app.core.ui.components.AuthTextField
import com.omniroute.app.core.ui.components.MessageBanner
import com.omniroute.app.core.ui.components.PrimaryButton
import kotlinx.coroutines.launch

@Composable
fun RegisterScreen(
    onRegister: suspend (username: String, password: String, nickname: String?) -> Result<String>,
    onBackToLogin: () -> Unit,
    onRegistered: (message: String) -> Unit,
) {
    var username by rememberSaveable { mutableStateOf("") }
    var nickname by rememberSaveable { mutableStateOf("") }
    var password by rememberSaveable { mutableStateOf("") }
    var confirmPassword by rememberSaveable { mutableStateOf("") }
    var loading by remember { mutableStateOf(false) }
    var errorText by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    val submit: () -> Unit = {
        val name = username.trim()
        when {
            loading -> Unit
            name.isEmpty() -> errorText = "请填写用户名"
            password.length < 6 -> errorText = "密码至少 6 位"
            password != confirmPassword -> errorText = "两次输入的密码不一致"
            else -> {
                errorText = null
                loading = true
                scope.launch {
                    val result = onRegister(name, password, nickname)
                    loading = false
                    result
                        .onSuccess { onRegistered(it) }
                        .onFailure { errorText = it.message ?: "注册失败" }
                }
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
            text = "注册账号",
            style = MaterialTheme.typography.headlineMedium,
            fontWeight = FontWeight.SemiBold,
        )
        Spacer(modifier = Modifier.height(8.dp))
        Text(
            text = "注册成功后需要返回登录",
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
            value = nickname,
            onValueChange = { nickname = it },
            label = "昵称（可选）",
            enabled = !loading,
        )
        Spacer(modifier = Modifier.height(12.dp))
        AuthTextField(
            value = password,
            onValueChange = { password = it },
            label = "密码（至少 6 位）",
            enabled = !loading,
            isPassword = true,
            keyboardType = KeyboardType.Password,
        )
        Spacer(modifier = Modifier.height(12.dp))
        AuthTextField(
            value = confirmPassword,
            onValueChange = { confirmPassword = it },
            label = "确认密码",
            enabled = !loading,
            isPassword = true,
            keyboardType = KeyboardType.Password,
            imeAction = ImeAction.Done,
            onImeAction = submit,
        )

        errorText?.let {
            Spacer(modifier = Modifier.height(16.dp))
            MessageBanner(text = it, isError = true)
        }

        Spacer(modifier = Modifier.height(24.dp))
        PrimaryButton(text = "注册", loading = loading, onClick = submit)
        Spacer(modifier = Modifier.height(4.dp))
        TextButton(onClick = onBackToLogin, enabled = !loading) {
            Text(text = "已有账号？返回登录")
        }
    }
}
