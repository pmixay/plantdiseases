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
import kotlin.coroutines.suspendCoroutine

/**
 * Resolves the device's coarse location into a stable, human-readable
 * "City, Country" region string used by the community scan corpus.
 *
 * Privacy posture
 * ---------------
 * * No raw lat/lon ever leaves [resolveRegion] — only the
 *   reverse-geocoded ``"City, CC"`` string.
 * * We only ask for ``ACCESS_COARSE_LOCATION``. Fine location is not
 *   needed for region-level grouping and is more invasive.
 * * The last-known location is preferred over a fresh fix to avoid
 *   the GPS spinner; we accept ~25 km accuracy because the corpus
 *   groups at city granularity anyway.
 * * The resolved region is cached in SharedPreferences so subsequent
 *   scans don't re-trigger a location read.
 */
object RegionResolver {

    private const val PREFS_NAME = "plantdiseases_region"
    private const val KEY_REGION = "cached_region"
    private const val KEY_REGION_TIMESTAMP = "cached_region_ts"
    private const val CACHE_VALID_MS = 24L * 60L * 60L * 1000L  // refresh once a day

    /** Persisted last-known region, or ``null`` if we've never resolved. */
    fun cachedRegion(context: Context): String? = prefs(context)
        .getString(KEY_REGION, null)
        ?.takeIf { it.isNotBlank() }

    /** Permission probe — true when we hold ``ACCESS_COARSE_LOCATION``. */
    fun hasCoarsePermission(context: Context): Boolean =
        ContextCompat.checkSelfPermission(
            context, Manifest.permission.ACCESS_COARSE_LOCATION,
        ) == PackageManager.PERMISSION_GRANTED

    /**
     * Resolve the device region to a ``"City, CC"`` string. Returns
     * the cached value when one is < 24h old; falls back to ``null``
     * if the permission is missing, no location is available, or
     * geocoding fails.
     *
     * Caller should run this on a background coroutine — Geocoder
     * blocks for a few hundred ms.
     */
    @SuppressLint("MissingPermission")
    suspend fun resolveRegion(context: Context): String? {
        val cached = cachedRegion(context)
        val ts = prefs(context).getLong(KEY_REGION_TIMESTAMP, 0L)
        if (cached != null && (System.currentTimeMillis() - ts) < CACHE_VALID_MS) {
            return cached
        }
        if (!hasCoarsePermission(context)) return cached
        val location = lastKnownCoarseLocation(context) ?: return cached
        val region = reverseGeocode(context, location) ?: return cached
        prefs(context).edit()
            .putString(KEY_REGION, region)
            .putLong(KEY_REGION_TIMESTAMP, System.currentTimeMillis())
            .apply()
        return region
    }

    @SuppressLint("MissingPermission")
    private fun lastKnownCoarseLocation(context: Context): Location? {
        val lm = context.getSystemService(Context.LOCATION_SERVICE) as? LocationManager
            ?: return null
        // Walk every available provider so we get *some* fix even when
        // the user has only NETWORK or only PASSIVE enabled.
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

    private suspend fun reverseGeocode(context: Context, location: Location): String? {
        if (!Geocoder.isPresent()) return null
        val geocoder = Geocoder(context, Locale.getDefault())
        return try {
            if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.TIRAMISU) {
                // Async API on API 33+.
                suspendCoroutine { cont ->
                    geocoder.getFromLocation(location.latitude, location.longitude, 1) { results ->
                        cont.resume(formatRegion(results.firstOrNull()))
                    }
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

    private fun formatRegion(address: Address?): String? {
        address ?: return null
        // City / locality first, then country code. Falls back to the
        // admin area (state / region) if no locality is reported.
        val city = address.locality
            ?: address.subAdminArea
            ?: address.adminArea
        val country = address.countryCode ?: address.countryName
        return when {
            city.isNullOrBlank() && country.isNullOrBlank() -> null
            city.isNullOrBlank() -> country?.trim()
            country.isNullOrBlank() -> city.trim()
            else -> "${city.trim()}, ${country.trim()}"
        }?.take(80)
    }

    private fun prefs(context: Context) = context.applicationContext
        .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
}
