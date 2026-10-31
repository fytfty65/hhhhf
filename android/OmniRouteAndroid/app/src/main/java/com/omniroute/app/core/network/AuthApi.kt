package com.omniroute.app.core.network

import com.omniroute.app.core.network.dto.AuthResponse
import com.omniroute.app.core.network.dto.LoginRequest
import com.omniroute.app.core.network.dto.LogoutRequest
import com.omniroute.app.core.network.dto.LogoutResponse
import com.omniroute.app.core.network.dto.ProfileResponse
import com.omniroute.app.core.network.dto.RefreshRequest
import com.omniroute.app.core.network.dto.RegisterRequest
import com.omniroute.app.core.network.dto.RegisterResponse
import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST

/** Gateway 的移动端鉴权与个人资料接口，路径见 gateway/internal/api/route.go。 */
interface AuthApi {

    @POST("api/auth/mobile/login")
    suspend fun login(@Body body: LoginRequest): Response<AuthResponse>

    @POST("api/auth/mobile/refresh")
    suspend fun refresh(@Body body: RefreshRequest): Response<AuthResponse>

    @POST("api/auth/mobile/logout")
    suspend fun logout(@Body body: LogoutRequest): Response<LogoutResponse>

    @POST("api/auth/register")
    suspend fun register(@Body body: RegisterRequest): Response<RegisterResponse>

    @GET("api/user/profile")
    suspend fun profile(): Response<ProfileResponse>
}
