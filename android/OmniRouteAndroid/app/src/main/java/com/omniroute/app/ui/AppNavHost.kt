package com.omniroute.app.ui

import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import com.omniroute.app.AppContainer
import com.omniroute.app.core.ui.components.LoadingScreen
import com.omniroute.app.feature.auth.LoginScreen
import com.omniroute.app.feature.auth.RegisterScreen
import com.omniroute.app.feature.home.HomeScreen
import kotlinx.coroutines.launch

private object Routes {
    const val LOGIN = "login"
    const val REGISTER = "register"
    const val HOME = "home"
}

@Composable
fun AppNavHost(container: AppContainer) {
    val navController = rememberNavController()
    val scope = rememberCoroutineScope()

    // null 表示启动检查尚未结束：先显示加载，避免登录页与首页闪现切换。
    var startRoute by remember { mutableStateOf<String?>(null) }
    var notice by remember { mutableStateOf<String?>(null) }

    val lastUsername by container.settingsStore.lastUsername
        .collectAsStateWithLifecycle(initialValue = "")
    val currentUser by container.authRepository.user.collectAsStateWithLifecycle()

    LaunchedEffect(Unit) {
        val restored = container.authRepository.refreshSilent()
        startRoute = if (restored) Routes.HOME else Routes.LOGIN
    }

    // 刷新失败或服务端撤销会话时，统一回到登录页。
    LaunchedEffect(navController) {
        container.authRepository.sessionExpired.collect {
            navController.navigate(Routes.LOGIN) {
                popUpTo(navController.graph.startDestinationId) { inclusive = true }
                launchSingleTop = true
            }
        }
    }

    val resolvedStart = startRoute
    if (resolvedStart == null) {
        LoadingScreen(text = "正在检查登录状态…")
        return
    }

    NavHost(navController = navController, startDestination = resolvedStart) {
        composable(Routes.LOGIN) {
            LoginScreen(
                initialUsername = lastUsername,
                notice = notice,
                onLogin = container.authRepository::login,
                onLoggedIn = { user ->
                    notice = null
                    scope.launch { container.settingsStore.setLastUsername(user.username) }
                    navController.navigate(Routes.HOME) {
                        popUpTo(navController.graph.startDestinationId) { inclusive = true }
                        launchSingleTop = true
                    }
                },
                onGoRegister = {
                    notice = null
                    navController.navigate(Routes.REGISTER)
                },
            )
        }

        composable(Routes.REGISTER) {
            RegisterScreen(
                onRegister = container.authRepository::register,
                onBackToLogin = { navController.popBackStack() },
                onRegistered = { message ->
                    notice = "$message，请使用新账号登录"
                    navController.popBackStack()
                },
            )
        }

        composable(Routes.HOME) {
            HomeScreen(
                initialUser = currentUser,
                onLoadProfile = container.authRepository::loadProfile,
                onLogout = container.authRepository::logout,
                onLoggedOut = {
                    navController.navigate(Routes.LOGIN) {
                        popUpTo(navController.graph.startDestinationId) { inclusive = true }
                        launchSingleTop = true
                    }
                },
            )
        }
    }
}
