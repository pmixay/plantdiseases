package com.plantdiseases.app.auth

import com.plantdiseases.app.BuildConfig
import com.vk.id.VKID
import com.vk.id.logout.VKIDLogoutCallback
import com.vk.id.logout.VKIDLogoutFail
import com.vk.id.logout.VKIDLogoutParams

/** Clears provider-SDK session state after local account credentials are gone. */
class OfficialProviderSessionCleaner : ProviderSessionCleaner {
    override suspend fun clear() {
        if (!BuildConfig.VK_OAUTH_CONFIGURED || BuildConfig.SYNTHETIC_AUTH_ENABLED) return
        runCatching {
            VKID.instance.logout(
                callback = object : VKIDLogoutCallback {
                    override fun onSuccess() = Unit

                    override fun onFail(fail: VKIDLogoutFail) = Unit
                },
                params = VKIDLogoutParams {},
            )
        }
        // Yandex LoginSDK does not retain the returned token. The app never
        // stores provider tokens, so there is no Yandex credential to clear.
    }
}
