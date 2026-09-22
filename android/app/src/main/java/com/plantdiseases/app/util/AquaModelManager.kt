package com.plantdiseases.app.util

import android.content.Context
import android.util.Log
import com.google.gson.JsonObject
import java.io.File
import java.io.InputStream
import java.security.MessageDigest

/**
 * Stores the Aqua on-device model downloaded from the server (instead of
 * bundling it in the APK). Files live in `filesDir/aqua/`:
 *  - `aqua_classifier.tflite` — the model bytes
 *  - `aqua_classes.json`      — the sidecar (class order, input size, norm)
 *
 * The installed model's sha256 is tracked in prefs so the app
 * re-downloads only when the server publishes new bytes. [AquaClassifier]
 * reads the model from here; if nothing is installed it reports unavailable
 * and the app uses the server model.
 */
object AquaModelManager {
    private const val TAG = "AquaModelManager"
    private const val DIR = "aqua"
    private const val MODEL_FILE = "aqua_classifier.tflite"
    private const val CLASSES_FILE = "aqua_classes.json"
    private const val PREFS = "plantdiseases_aqua"
    private const val KEY_SHA = "model_sha256"

    private fun modelDir(context: Context): File =
        File(context.filesDir, DIR).apply { if (!exists()) mkdirs() }

    fun modelFile(context: Context): File = File(modelDir(context), MODEL_FILE)
    fun classesFile(context: Context): File = File(modelDir(context), CLASSES_FILE)

    /** A usable model is installed (both files present). */
    fun isInstalled(context: Context): Boolean =
        modelFile(context).isFile && classesFile(context).isFile

    private fun installedSha(context: Context): String? =
        prefs(context).getString(KEY_SHA, null)

    /** True when the installed bytes already match the server's sha. */
    fun isUpToDate(context: Context, serverSha: String?): Boolean =
        isInstalled(context) && serverSha != null && serverSha == installedSha(context)

    /**
     * Atomically install a freshly-downloaded model: stream to a temp file
     * while hashing, verify the sha matches the manifest (guards against a
     * truncated/corrupt download), then move it into place and write the
     * sidecar + prefs. Returns true on success.
     */
    fun install(
        context: Context,
        body: InputStream,
        expectedSha: String,
        version: String?,
        classes: JsonObject,
    ): Boolean {
        val dir = modelDir(context)
        val tmp = File(dir, "$MODEL_FILE.tmp")
        return try {
            val digest = MessageDigest.getInstance("SHA-256")
            tmp.outputStream().use { out ->
                val buf = ByteArray(64 * 1024)
                body.use { input ->
                    while (true) {
                        val n = input.read(buf)
                        if (n < 0) break
                        digest.update(buf, 0, n)
                        out.write(buf, 0, n)
                    }
                }
            }
            val actualSha = digest.digest().joinToString("") { "%02x".format(it) }
            if (!actualSha.equals(expectedSha, ignoreCase = true)) {
                Log.w(TAG, "Aqua model sha mismatch (got $actualSha, want $expectedSha) — discarding")
                tmp.delete()
                return false
            }
            classesFile(context).writeText(classes.toString())
            if (!tmp.renameTo(modelFile(context))) {
                // renameTo can fail across some filesystems; fall back to copy.
                modelFile(context).writeBytes(tmp.readBytes())
                tmp.delete()
            }
            prefs(context).edit()
                .putString(KEY_SHA, actualSha)
                .apply()
            Log.i(TAG, "Aqua model installed (version=$version, sha=$actualSha)")
            true
        } catch (_: Exception) {
            Log.w(TAG, "Aqua model install failed")
            tmp.delete()
            false
        }
    }

    private fun prefs(context: Context) = context.applicationContext
        .getSharedPreferences(PREFS, Context.MODE_PRIVATE)
}
