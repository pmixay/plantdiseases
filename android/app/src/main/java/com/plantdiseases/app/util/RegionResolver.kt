package com.plantdiseases.app.util

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.location.Address
import android.location.Geocoder
import android.location.Location
import android.location.LocationManager
import androidx.core.content.ContextCompat
import java.io.IOException
import java.util.Locale
import kotlin.coroutines.resume
import kotlin.math.roundToInt
import kotlinx.coroutines.suspendCancellableCoroutine

/**
 * Resolves the device's COARSE location into a country + city label plus a
 * deliberately blunted lat/lon for the community corpus.
 *
 * Privacy posture
 * ---------------
 * * Only [Manifest.permission.ACCESS_COARSE_LOCATION] is requested; the coords
 *   it yields are already imprecise (Wi-Fi / cell, ~1 km). We additionally
 *   round them to 2 decimals (~1.1 km grid) before they ever leave the device,
 *   so the corpus never holds a precise position — enough for a 30 km "nearby"
 *   radius + the community heat-map, not enough to pinpoint a home.
 * * Community is opt-in (see [CommunityPrefs]); nothing here runs until the
 *   user enables it and grants the permission.
 * * Cached in SharedPreferences so each scan / feed open doesn't re-read GPS;
 *   refreshed hourly so "nearby" stays current as the user moves.
 */
data class ResolvedRegion(
    val country: String,
    val locality: String?,
    val lat: Double?,
    val lon: Double?,
)

object RegionResolver {

    private const val PREFS_NAME = "plantdiseases_region"
    private const val KEY_COUNTRY = "cached_country"
    private const val KEY_LOCALITY = "cached_locality"
    private const val KEY_LAT = "cached_lat"
    private const val KEY_LON = "cached_lon"
    private const val KEY_REGION_TIMESTAMP = "cached_region_ts"

    // Refresh hourly so the "nearby" scope tracks the user as they move,
    // without re-reading location on every scan / feed open.
    private const val CACHE_VALID_MS = 60L * 60L * 1000L

    /** Persisted last-known location, or ``null`` if we've never resolved. */
    fun cachedRegion(context: Context): ResolvedRegion? {
        val p = prefs(context)
        val country = p.getString(KEY_COUNTRY, null) ?: return null
        val locality = p.getString(KEY_LOCALITY, null)?.takeIf { it.isNotBlank() }
        val lat = if (p.contains(KEY_LAT)) p.getFloat(KEY_LAT, 0f).toDouble() else null
        val lon = if (p.contains(KEY_LON)) p.getFloat(KEY_LON, 0f).toDouble() else null
        if (country.isBlank() && lat == null) return null
        return ResolvedRegion(country, locality, lat, lon)
    }

    /** Permission probe — true when we hold ``ACCESS_COARSE_LOCATION``. */
    fun hasCoarsePermission(context: Context): Boolean =
        ContextCompat.checkSelfPermission(
            context,
            Manifest.permission.ACCESS_COARSE_LOCATION,
        ) == PackageManager.PERMISSION_GRANTED

    /**
     * Resolve the device location to a country + city + rounded coords.
     * Returns the cached value when it's < 1h old; falls back to the cache
     * (or ``null``) when the permission is missing or no fix is available.
     *
     * Run on a background coroutine — Geocoder blocks for a few hundred ms.
     */
    @SuppressLint("MissingPermission")
    suspend fun resolveRegion(context: Context): ResolvedRegion? {
        val cached = cachedRegion(context)
        val ts = prefs(context).getLong(KEY_REGION_TIMESTAMP, 0L)
        if (cached != null && (System.currentTimeMillis() - ts) < CACHE_VALID_MS) {
            return cached
        }
        if (!hasCoarsePermission(context)) return cached
        val location = lastKnownCoarseLocation(context) ?: return cached
        val lat = round2(location.latitude)
        val lon = round2(location.longitude)
        val geo = reverseGeocode(context, location) // (country, locality) or null
        val resolved = ResolvedRegion(
            country = geo?.first ?: cached?.country ?: "",
            locality = geo?.second ?: cached?.locality,
            lat = lat,
            lon = lon,
        )
        prefs(context).edit()
            .putString(KEY_COUNTRY, resolved.country)
            .putString(KEY_LOCALITY, resolved.locality ?: "")
            .putFloat(KEY_LAT, lat.toFloat())
            .putFloat(KEY_LON, lon.toFloat())
            .putLong(KEY_REGION_TIMESTAMP, System.currentTimeMillis())
            .apply()
        return resolved
    }

    /** Round to 2 decimals (~1.1 km) so a precise position never leaves the device. */
    private fun round2(v: Double): Double = (v * 100.0).roundToInt() / 100.0

    @SuppressLint("MissingPermission")
    private fun lastKnownCoarseLocation(context: Context): Location? {
        val lm = context.getSystemService(Context.LOCATION_SERVICE) as? LocationManager
            ?: return null
        // Walk every available provider so we get *some* fix even when the
        // user has only NETWORK or only PASSIVE enabled.
        val providers = lm.getProviders(true)
        var best: Location? = null
        for (name in providers) {
            val location = try {
                lm.getLastKnownLocation(name)
            } catch (_: SecurityException) {
                null
            } ?: continue
            if (best == null || location.time > best.time) best = location
        }
        return best
    }

    private suspend fun reverseGeocode(
        context: Context,
        location: Location,
    ): Pair<String, String?>? {
        if (!Geocoder.isPresent()) return null
        val geocoder = Geocoder(context, Locale.getDefault())
        return try {
            if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.TIRAMISU) {
                // Async API on API 33+. Supply BOTH callbacks: a geocoder error
                // would otherwise never resume the continuation — hanging the
                // coroutine (and leaking it). suspendCancellableCoroutine also
                // lets lifecycleScope cancel a pending lookup.
                suspendCancellableCoroutine { cont ->
                    geocoder.getFromLocation(
                        location.latitude,
                        location.longitude,
                        1,
                        object : Geocoder.GeocodeListener {
                            override fun onGeocode(addresses: MutableList<Address>) {
                                if (cont.isActive) cont.resume(formatRegion(addresses.firstOrNull()))
                            }

                            override fun onError(errorMessage: String?) {
                                if (cont.isActive) cont.resume(null)
                            }
                        },
                    )
                }
            } else {
                @Suppress("DEPRECATION")
                val results = geocoder.getFromLocation(location.latitude, location.longitude, 1)
                formatRegion(results?.firstOrNull())
            }
        } catch (_: IOException) {
            null
        }
    }

    /** (country, locality) from a reverse-geocoded address, or null. */
    private fun formatRegion(address: Address?): Pair<String, String?>? {
        address ?: return null
        // Country bucket = ISO-3166 alpha-2 code ONLY (locale-independent). We
        // deliberately do NOT fall back to the localized countryName: "Russia"
        // (en) vs "Россия" (ru) would split one country across locale-specific
        // buckets server-side and break the popular/nearby aggregation. When
        // the geocoder yields no code we return null so the caller keeps the
        // last good cached country instead of a mis-bucketed name.
        val country = address.countryCode?.trim()?.uppercase(Locale.ROOT)
            ?.takeIf { it.length == 2 && it.all(Char::isLetter) }
            ?: return null
        // locality prefers the city, falling back to the admin area.
        val locality = (
            address.locality
                ?: address.subAdminArea
                ?: address.adminArea
            )
            ?.trim()
            ?.take(80)
            ?.takeIf { it.isNotBlank() }
        return country.take(80) to locality
    }

    private fun prefs(context: Context) = context.applicationContext
        .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
}
