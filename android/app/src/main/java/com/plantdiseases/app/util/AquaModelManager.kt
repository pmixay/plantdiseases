package com.plantdiseases.app.util

import android.content.Context
import android.util.Log
import com.google.gson.JsonElement
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import com.google.gson.JsonPrimitive
import org.tensorflow.lite.schema.TensorType
import org.tensorflow.lite.support.metadata.MetadataExtractor
import java.io.File
import java.io.IOException
import java.io.InputStream
import java.nio.ByteBuffer
import java.nio.file.AtomicMoveNotSupportedException
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.security.MessageDigest
import java.util.UUID

/**
 * Stores the Aqua on-device model downloaded from the server (instead of
 * bundling it in the APK). Each immutable install lives in a versioned slot
 * under `filesDir/aqua/bundles/`; SharedPreferences atomically selects the
 * active slot:
 *  - `aqua_classifier.tflite` — the model bytes
 *  - `aqua_classes.json`      — the sidecar (class order, input size, norm)
 *
 * A combined model+sidecar digest is tracked in prefs so the app re-downloads
 * when either weights or their serving contract changes. [AquaClassifier]
 * reads the model from here; if nothing is installed it reports unavailable
 * and the app uses the server model.
 */
object AquaModelManager {
    private const val TAG = "AquaModelManager"
    private const val DIR = "aqua"
    private const val MODEL_FILE = "aqua_classifier.tflite"
    private const val CLASSES_FILE = "aqua_classes.json"
    private const val BUNDLES_DIR = "bundles"
    private const val PREFS = "plantdiseases_aqua"
    private const val KEY_MODEL_SHA = "model_sha256"
    private const val KEY_CLASSES_SHA = "classes_sha256"
    private const val KEY_BUNDLE_SHA = "bundle_sha256"
    private const val KEY_SLOT = "model_slot"
    private const val MAX_MODEL_BYTES = 64L * 1024L * 1024L
    private val SHA_RE = Regex("^[0-9a-f]{64}$")
    private val SLOT_RE = Regex("^[0-9a-f]{64}-[0-9a-f-]{36}$")

    data class InstalledBundle internal constructor(
        val modelFile: File,
        val classesFile: File,
        val modelSha256: String,
        val classesSha256: String,
        val bundleSha256: String,
    )

    private fun modelDir(context: Context): File = File(context.filesDir, DIR)

    private fun bundlesDir(context: Context): File = File(modelDir(context), BUNDLES_DIR)

    private fun ensureBundlesDir(context: Context): File =
        bundlesDir(context).apply {
            if (!exists() && !mkdirs()) {
                throw IOException("Cannot create Aqua bundle directory: $this")
            }
        }

    private fun allSha256(vararg digests: String): Boolean = digests.all { SHA_RE.matches(it) }

    /**
     * Stream [body] into [dest] while hashing it. Throws when the stream is
     * empty or exceeds [MAX_MODEL_BYTES]; returns the lowercase SHA-256 hex.
     */
    private fun streamModelToFile(body: InputStream, dest: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        var total = 0L
        dest.outputStream().use { out ->
            val buf = ByteArray(64 * 1024)
            body.use { input ->
                while (true) {
                    val n = input.read(buf)
                    if (n < 0) break
                    if (n == 0) continue
                    total += n
                    if (total > MAX_MODEL_BYTES) {
                        throw IOException("Aqua model exceeds $MAX_MODEL_BYTES bytes")
                    }
                    digest.update(buf, 0, n)
                    out.write(buf, 0, n)
                }
            }
        }
        if (total == 0L) throw IOException("Aqua model download is empty")
        return digest.digest().joinToString("") { "%02x".format(it) }
    }

    private fun activeBundle(context: Context): InstalledBundle? {
        // Read one SharedPreferences snapshot. Resolving model and sidecar in
        // separate calls could otherwise straddle an update and pair files
        // from two immutable slots despite the atomic pointer commit.
        val stored = prefs(context).all
        val modelSha = (stored[KEY_MODEL_SHA] as? String)?.lowercase() ?: return null
        val classesSha = (stored[KEY_CLASSES_SHA] as? String)?.lowercase() ?: return null
        val bundleSha = (stored[KEY_BUNDLE_SHA] as? String)?.lowercase() ?: return null
        val slot = stored[KEY_SLOT] as? String ?: return null
        val slotWellFormed = SLOT_RE.matches(slot) && slot.startsWith("$bundleSha-")
        if (!allSha256(modelSha, classesSha, bundleSha) || !slotWellFormed) {
            return null
        }
        val dir = File(bundlesDir(context), slot)
        return InstalledBundle(
            modelFile = File(dir, MODEL_FILE),
            classesFile = File(dir, CLASSES_FILE),
            modelSha256 = modelSha,
            classesSha256 = classesSha,
            bundleSha256 = bundleSha,
        )
    }

    fun modelFile(context: Context): File =
        activeBundle(context)?.modelFile
            ?: File(File(modelDir(context), "_inactive"), MODEL_FILE)

    fun classesFile(context: Context): File =
        activeBundle(context)?.classesFile
            ?: File(File(modelDir(context), "_inactive"), CLASSES_FILE)

    /** A usable model is installed (both files present). */
    fun isInstalled(context: Context): Boolean = inspectActive(context, verifyHash = false) != null

    /** Return one verified, internally consistent model+sidecar snapshot. */
    fun verifiedBundle(context: Context): InstalledBundle? =
        inspectActive(context, verifyHash = true)

    private fun inspectActive(context: Context, verifyHash: Boolean): InstalledBundle? {
        val bundle = try {
            activeBundle(context)
        } catch (e: Exception) {
            Log.w(TAG, "Could not resolve installed Aqua bundle: ${e.message}")
            null
        } ?: return null
        return try {
            val model = bundle.modelFile
            val sidecar = bundle.classesFile
            if (!model.isFile || model.length() !in 1..MAX_MODEL_BYTES || !sidecar.isFile) {
                return null
            }
            val parsed = JsonParser.parseString(sidecar.readText()).asJsonObject
            if (!validClasses(parsed, parsed.get("_model_version")?.asString)) return null
            if (
                parsed.get("_model_sha256")?.asString
                    ?.equals(bundle.modelSha256, ignoreCase = true) != true ||
                parsed.get("_classes_sha256")?.asString
                    ?.equals(bundle.classesSha256, ignoreCase = true) != true ||
                parsed.get("_bundle_sha256")?.asString
                    ?.equals(bundle.bundleSha256, ignoreCase = true) != true
            ) {
                return null
            }
            val semanticClasses = parsed.deepCopy().apply {
                remove("_model_sha256")
                remove("_classes_sha256")
                remove("_bundle_sha256")
                remove("_model_version")
            }
            if (!classesSha256(semanticClasses).equals(bundle.classesSha256, ignoreCase = true)) {
                return null
            }
            if (
                bundleSha256(bundle.modelSha256, bundle.classesSha256) !=
                bundle.bundleSha256
            ) {
                return null
            }
            if (
                verifyHash &&
                !sha256(model).equals(bundle.modelSha256, ignoreCase = true)
            ) {
                return null
            }
            bundle
        } catch (e: Exception) {
            Log.w(TAG, "Installed Aqua bundle is unreadable: ${e.message}")
            null
        }
    }

    /** True when installed weights and serving sidecar match the server bundle. */
    fun isUpToDate(context: Context, serverBundleSha: String?): Boolean =
        serverBundleSha
            ?.lowercase()
            ?.takeIf { SHA_RE.matches(it) }
            ?.let { expected -> verifiedBundle(context)?.bundleSha256 == expected }
            ?: false

    /**
     * Atomically clear the active pointer after an authoritative successful
     * ``available:false`` manifest. Immutable bundle bytes remain on disk as a
     * rollback; transient HTTP/network failures never call this method.
     */
    @Synchronized
    fun deactivate(context: Context): Boolean {
        val preferences = prefs(context)
        val active = runCatching { activeBundle(context) }.getOrNull()
        val hasPointer = listOf(
            KEY_MODEL_SHA,
            KEY_CLASSES_SHA,
            KEY_BUNDLE_SHA,
            KEY_SLOT,
        ).any(preferences::contains)
        if (!hasPointer) return true
        val committed = preferences.edit()
            .remove(KEY_MODEL_SHA)
            .remove(KEY_CLASSES_SHA)
            .remove(KEY_BUNDLE_SHA)
            .remove(KEY_SLOT)
            .commit()
        if (committed) {
            Log.i(
                TAG,
                "Aqua bundle deactivated (retained bundle=${active?.bundleSha256 ?: "unknown"})",
            )
        } else {
            Log.w(TAG, "Could not commit Aqua bundle deactivation")
        }
        return committed
    }

    /**
     * Atomically install a freshly-downloaded model: stream to a temp file
     * while hashing, verify the sha matches the manifest (guards against a
     * truncated/corrupt download), publish one complete immutable model +
     * sidecar directory, then atomically switch the active pointer. Returns
     * true on success.
     */
    @Synchronized
    fun install(
        context: Context,
        body: InputStream,
        expectedModelSha: String,
        expectedClassesSha: String,
        expectedBundleSha: String,
        version: String?,
        classes: JsonObject,
    ): Boolean {
        val modelSha = expectedModelSha.lowercase()
        val classesSha = expectedClassesSha.lowercase()
        val bundleSha = expectedBundleSha.lowercase()
        val hasReservedBindings = listOf(
            "_model_sha256",
            "_classes_sha256",
            "_bundle_sha256",
            "_model_version",
        ).any(classes::has)
        val contractMatches = runCatching {
            classesSha256(classes).equals(classesSha, ignoreCase = true) &&
                bundleSha256(modelSha, classesSha).equals(bundleSha, ignoreCase = true)
        }.getOrDefault(false)
        val contractValid = !hasReservedBindings &&
            validClasses(classes, version) &&
            contractMatches
        if (!allSha256(modelSha, classesSha, bundleSha) || !contractValid) {
            Log.w(TAG, "Rejecting Aqua install with malformed or inconsistent bundle contract")
            try {
                body.close()
            } catch (_: Exception) {
                // Nothing else can be recovered from an invalid manifest.
            }
            return false
        }
        val slot = "$bundleSha-${UUID.randomUUID()}"
        var tmpDir: File? = null
        var finalDir: File? = null
        var activated = false
        return try {
            val root = ensureBundlesDir(context)
            val staging = File(root, ".$slot.tmp")
            val published = File(root, slot)
            tmpDir = staging
            finalDir = published
            if (!staging.mkdir()) throw IOException("Cannot create Aqua staging directory")
            val tmpModel = File(staging, MODEL_FILE)
            val actualSha = streamModelToFile(body, tmpModel)
            if (!actualSha.equals(modelSha, ignoreCase = true)) {
                Log.w(
                    TAG,
                    "Aqua model sha mismatch (got $actualSha, want $modelSha) — discarding",
                )
                return false
            }

            // A digest-valid file can still be the wrong export (for example
            // INT8/FLOAT16 I/O or a head width from another class list).
            // Inspect the staged FlatBuffer before publishing the immutable
            // slot or switching the active pointer. No inference is run.
            validateStagedTensorContract(tmpModel, classes)

            // Bind the sidecar to these exact bytes. A crash can leave a
            // complete inactive slot, never a live model with another run's
            // class order/preprocessing metadata.
            val boundClasses = classes.deepCopy().apply {
                addProperty("_model_sha256", actualSha)
                addProperty("_classes_sha256", classesSha)
                addProperty("_bundle_sha256", bundleSha)
                if (version != null) addProperty("_model_version", version)
            }
            File(staging, CLASSES_FILE).writeText(boundClasses.toString())
            publishDirectory(staging, published)

            val committed = prefs(context).edit()
                .putString(KEY_MODEL_SHA, actualSha)
                .putString(KEY_CLASSES_SHA, classesSha)
                .putString(KEY_BUNDLE_SHA, bundleSha)
                .putString(KEY_SLOT, slot)
                .commit()
            if (!committed) {
                published.deleteRecursively()
                throw IOException("Could not commit active Aqua bundle pointer")
            }
            activated = true
            runCatching { pruneInactiveBundles(root, slot) }
                .onFailure { Log.w(TAG, "Could not prune old Aqua bundles: ${it.message}") }
            Log.i(TAG, "Aqua model installed (version=$version, bundle=$bundleSha)")
            true
        } catch (e: Exception) {
            Log.w(TAG, "Aqua model install failed: ${e.message}")
            if (!activated) finalDir?.deleteRecursively()
            false
        } finally {
            tmpDir?.deleteRecursively()
            try {
                body.close()
            } catch (_: Exception) {
                // The model stream has already been consumed or rejected.
            }
        }
    }

    private fun validClasses(classes: JsonObject, manifestVersion: String?): Boolean = try {
        val names = classes.getAsJsonArray("class_names") ?: return false
        if (names.size() == 0) return false
        val unique = LinkedHashSet<String>()
        for (entry in names) {
            if (!entry.isJsonPrimitive || !entry.asJsonPrimitive.isString) return false
            val key = entry.asString
            if (key.isBlank() || key != key.trim() || !unique.add(key)) return false
        }
        val family = classes.get("model_family")?.asString
        val sidecarVersion = classes.get("model_version")?.asString
        val versionsAgree = !sidecarVersion.isNullOrBlank() &&
            !manifestVersion.isNullOrBlank() &&
            sidecarVersion == manifestVersion
        if (family != ModelFamily.AQUA.id || !versionsAgree) return false

        val training = classes.getAsJsonObject("training") ?: return false
        val sizeValue = training.get("img_size")
        if (sizeValue == null || !sizeValue.isJsonPrimitive || !sizeValue.asJsonPrimitive.isNumber) {
            return false
        }
        val size = sizeValue.asDouble
        if (!size.isFinite() || size % 1.0 != 0.0 || size !in 1.0..4096.0) return false
        val ratioValue = training.get("eval_resize_ratio") ?: return false
        if (!ratioValue.isJsonPrimitive || !ratioValue.asJsonPrimitive.isNumber) return false
        val ratio = ratioValue.asDouble
        if (!ratio.isFinite() || ratio !in 1.0..4.0) return false

        val inference = classes.getAsJsonObject("inference") ?: return false
        if (inference.get("layout")?.asString != "NHWC") return false
        if (inference.get("tta")?.asString != "none") return false
        if (inference.get("output_semantics")?.asString != "logits") return false

        fun numericTriple(key: String, positive: Boolean): Boolean {
            val values = inference.getAsJsonArray(key) ?: return false
            if (values.size() != 3) return false
            return values.all { value ->
                if (!value.isJsonPrimitive || !value.asJsonPrimitive.isNumber) return@all false
                val number = value.asDouble
                number.isFinite() && (!positive || number > 0.0)
            }
        }
        numericTriple("normalize_mean", positive = false) &&
            numericTriple("normalize_std", positive = true)
    } catch (_: Exception) {
        false
    }

    private fun validateStagedTensorContract(model: File, classes: JsonObject) {
        val names = classes.getAsJsonArray("class_names")
        val inputSize = classes.getAsJsonObject("training").get("img_size").asInt
        // MetadataExtractor is a pure-Java FlatBuffer reader. A heap buffer
        // avoids a mapped-file handle surviving long enough to block the
        // staging-directory rename on Windows/Robolectric.
        val graph = MetadataExtractor(ByteBuffer.wrap(model.readBytes()))
        require(graph.inputTensorCount == 1 && graph.outputTensorCount == 1) {
            "Aqua graph must have exactly one input and one output"
        }
        require(graph.getInputTensorType(0) == TensorType.FLOAT32) {
            "Aqua input tensor must be FLOAT32"
        }
        require(graph.getOutputTensorType(0) == TensorType.FLOAT32) {
            "Aqua output tensor must be FLOAT32 logits"
        }
        val expectedInput = intArrayOf(1, inputSize, inputSize, 3)
        val expectedOutput = intArrayOf(1, names.size())
        require(graph.getInputTensorShape(0).contentEquals(expectedInput)) {
            "Aqua input tensor does not match ${expectedInput.contentToString()}"
        }
        require(graph.getOutputTensorShape(0).contentEquals(expectedOutput)) {
            "Aqua output tensor does not match ${expectedOutput.contentToString()}"
        }
    }

    private fun publishDirectory(staged: File, destination: File) {
        try {
            Files.move(staged.toPath(), destination.toPath(), StandardCopyOption.ATOMIC_MOVE)
        } catch (_: AtomicMoveNotSupportedException) {
            Files.move(staged.toPath(), destination.toPath())
        }
    }

    /** Keep the active bundle plus one newest rollback; sweep abandoned stages. */
    private fun pruneInactiveBundles(root: File, activeSlot: String) {
        val children = root.listFiles() ?: return
        children
            .filter { it.isDirectory && it.name.startsWith(".") && it.name.endsWith(".tmp") }
            .forEach { it.deleteRecursively() }
        val inactive = children
            .filter { it.isDirectory && SLOT_RE.matches(it.name) && it.name != activeSlot }
            .sortedByDescending { it.lastModified() }
        inactive.drop(1).forEach { stale ->
            if (!stale.deleteRecursively()) {
                Log.w(TAG, "Could not delete stale Aqua bundle ${stale.name}")
            }
        }
    }

    /** Canonical semantic JSON shared with the server's sidecar digest. */
    private fun canonicalJson(element: JsonElement): String = when {
        element.isJsonNull -> "null"
        element.isJsonArray -> element.asJsonArray.joinToString(",", "[", "]") {
            canonicalJson(it)
        }
        element.isJsonObject -> element.asJsonObject.entrySet()
            .sortedBy { it.key }
            .joinToString(",", "{", "}") { entry ->
                "${JsonPrimitive(entry.key)}:${canonicalJson(entry.value)}"
            }
        else -> element.toString()
    }

    private fun classesSha256(classes: JsonObject): String {
        val digest = MessageDigest.getInstance("SHA-256")
        return digest.digest(canonicalJson(classes).toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it) }
    }

    private fun bundleSha256(modelSha: String, classesSha: String): String {
        require(SHA_RE.matches(modelSha) && SHA_RE.matches(classesSha))
        val digest = MessageDigest.getInstance("SHA-256")
        digest.update("plantvision-aqua-bundle-v1\u0000".toByteArray(Charsets.UTF_8))
        digest.update(hexBytes(modelSha))
        digest.update(hexBytes(classesSha))
        return digest.digest().joinToString("") { "%02x".format(it) }
    }

    private fun hexBytes(value: String): ByteArray = ByteArray(value.length / 2) { index ->
        value.substring(index * 2, index * 2 + 2).toInt(16).toByte()
    }

    private fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buf = ByteArray(64 * 1024)
            while (true) {
                val n = input.read(buf)
                if (n < 0) break
                if (n > 0) digest.update(buf, 0, n)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }

    private fun prefs(context: Context) = context.applicationContext
        .getSharedPreferences(PREFS, Context.MODE_PRIVATE)
}
