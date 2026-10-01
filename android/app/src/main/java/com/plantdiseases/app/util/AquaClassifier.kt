package com.plantdiseases.app.util

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.util.Log
import androidx.exifinterface.media.ExifInterface
import org.json.JSONObject
import org.tensorflow.lite.DataType
import org.tensorflow.lite.Interpreter
import java.io.RandomAccessFile
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.MappedByteBuffer
import java.nio.channels.FileChannel
import kotlin.math.exp
import kotlin.math.roundToInt

/**
 * On-device TFLite classifier for the **Aqua** model family.
 *
 * Loads the immutable model + sidecar snapshot downloaded by
 * [AquaModelManager] (produced by §5 of the training notebook —
 * MobileNetV3-Small student distilled from the gamma DINOv2 teacher). Like the
 * server weights, the model is provisioned out of band and never committed:
 * [isAvailable] returns false when the download is missing and callers fall
 * back to the server.
 *
 * The model is single-stage (whole-frame classify, no on-device detector). It
 * expects a normalised float NHWC tensor; the class order + input size +
 * normalisation are read from the sidecar JSON so the app never hard-codes what
 * the notebook declared.
 */
class AquaClassifier private constructor(
    private val interpreter: Interpreter,
    val classNames: List<String>,
    private val inputSize: Int,
    private val mean: FloatArray,
    private val std: FloatArray,
    private val evalResizeRatio: Float,
) {

    /** Run inference on a full-frame bitmap. Returns per-class probabilities
     *  (softmax) keyed by class name, in declared order. */
    @Synchronized
    fun classify(bitmap: Bitmap): Map<String, Float> {
        val input = preprocess(bitmap)
        val output = Array(1) { FloatArray(classNames.size) }
        interpreter.run(input, output)
        val probs = softmax(output[0])
        return classNames.indices.associate { classNames[it] to probs[it] }
    }

    private fun preprocess(bitmap: Bitmap): ByteBuffer {
        require(bitmap.width > 0 && bitmap.height > 0) { "Cannot classify an empty bitmap" }
        val prepared = resizeAndCenterCrop(bitmap)
        try {
            val buf = ByteBuffer.allocateDirect(4 * inputSize * inputSize * 3)
                .order(ByteOrder.nativeOrder())
            val pixels = IntArray(inputSize * inputSize)
            prepared.getPixels(pixels, 0, inputSize, 0, 0, inputSize, inputSize)
            for (p in pixels) {
                val r = ((p shr 16) and 0xFF) / 255f
                val g = ((p shr 8) and 0xFF) / 255f
                val b = (p and 0xFF) / 255f
                buf.putFloat((r - mean[0]) / std[0])
                buf.putFloat((g - mean[1]) / std[1])
                buf.putFloat((b - mean[2]) / std[2])
            }
            buf.rewind()
            return buf
        } finally {
            if (prepared !== bitmap) prepared.recycle()
        }
    }

    /** Mirror training/eval: resize the short side, then centre-crop square.
     *  Stretching arbitrary phone frames directly to 224×224 changed leaf
     *  geometry and did not match the loader used for the reported Aqua F1. */
    private fun resizeAndCenterCrop(bitmap: Bitmap): Bitmap {
        val targetShort = maxOf(inputSize, (inputSize * evalResizeRatio).toInt())
        val scale = targetShort.toFloat() / minOf(bitmap.width, bitmap.height).toFloat()
        val width = maxOf(inputSize, (bitmap.width * scale).roundToInt())
        val height = maxOf(inputSize, (bitmap.height * scale).roundToInt())
        val resized = Bitmap.createScaledBitmap(bitmap, width, height, true)
        val left = (width - inputSize) / 2
        val top = (height - inputSize) / 2
        val cropped = Bitmap.createBitmap(resized, left, top, inputSize, inputSize)
        if (cropped !== resized && resized !== bitmap) resized.recycle()
        return cropped
    }

    fun close() = interpreter.close()

    companion object {
        private const val TAG = "AquaClassifier"

        /** Whether the on-device Aqua model has been downloaded (see
         *  [AquaModelManager]). The model is fetched from the server, not
         *  bundled in the APK. */
        fun isAvailable(context: Context): Boolean = AquaModelManager.isInstalled(context)

        /** Build a classifier from the downloaded model, or null if it isn't
         *  installed / is unreadable. */
        fun create(context: Context): AquaClassifier? {
            val bundle = AquaModelManager.verifiedBundle(context) ?: return null
            return try {
                val model = loadModelFile(bundle.modelFile)
                val meta = JSONObject(bundle.classesFile.readText())
                require(meta.optString("model_family") == ModelFamily.AQUA.id) {
                    "Aqua sidecar declares another model family"
                }
                val classes = meta.getJSONArray("class_names")
                    .let { arr -> (0 until arr.length()).map { arr.getString(it) } }
                require(classes.isNotEmpty() && classes.all { it.isNotBlank() && it == it.trim() }) {
                    "class_names must contain non-blank, trimmed keys"
                }
                require(classes.distinct().size == classes.size) { "class_names contains duplicates" }
                val training = meta.optJSONObject("training")
                    ?: error("Aqua sidecar has no training object")
                val size = training.strictPositiveInt("img_size")
                val resizeRatio = training.strictFiniteFloat("eval_resize_ratio").also {
                    require(it in 1f..4f) { "eval_resize_ratio must be in [1, 4]" }
                }
                val inf = meta.optJSONObject("inference")
                    ?: error("Aqua sidecar has no inference object")
                require(inf.optString("layout", "NHWC") == "NHWC") {
                    "Aqua model layout must be NHWC"
                }
                require(inf.optString("tta", "none") == "none") {
                    "Aqua runtime only supports inference.tta=none"
                }
                require(inf.optString("output_semantics") == "logits") {
                    "Aqua runtime requires inference.output_semantics=logits"
                }
                val mean = inf.floatTriple("normalize_mean")
                val std = inf.floatTriple("normalize_std")
                require(mean.all { it.isFinite() }) { "normalize_mean must be finite" }
                require(std.all { it.isFinite() && it > 0f }) {
                    "normalize_std must contain finite positive values"
                }
                val opts = Interpreter.Options().apply { setNumThreads(2) }
                val interpreter = Interpreter(model, opts)
                try {
                    validateTensorContract(interpreter, classes.size, size)
                    AquaClassifier(interpreter, classes, size, mean, std, resizeRatio)
                } catch (e: OutOfMemoryError) {
                    interpreter.close()
                    throw e
                } catch (e: Exception) {
                    interpreter.close()
                    throw e
                }
            } catch (_: Exception) {
                Log.w(TAG, "Aqua model unavailable")
                null
            }
        }

        private fun loadModelFile(file: java.io.File): MappedByteBuffer {
            RandomAccessFile(file, "r").use { raf ->
                return raf.channel.map(FileChannel.MapMode.READ_ONLY, 0, raf.length())
            }
        }

        private fun JSONObject.floatTriple(key: String): FloatArray {
            require(has(key)) { "$key is required" }
            val arr = optJSONArray(key) ?: error("$key must be an array")
            require(arr.length() == 3) { "$key must contain exactly three values" }
            return FloatArray(3) { index ->
                val raw = arr.get(index)
                require(raw is Number) { "$key[$index] must be numeric" }
                raw.toFloat()
            }
        }

        private fun JSONObject.strictPositiveInt(key: String): Int {
            require(has(key)) { "$key is required" }
            val raw = get(key)
            require(raw is Number && raw.toDouble().isFinite()) { "$key must be an integer" }
            val value = raw.toInt()
            require(raw.toDouble() == value.toDouble() && value in 1..4096) {
                "$key must be an integer in [1, 4096]"
            }
            return value
        }

        private fun JSONObject.strictFiniteFloat(key: String): Float {
            require(has(key)) { "$key is required" }
            val raw = get(key)
            require(raw is Number) { "$key must be numeric" }
            return raw.toFloat().also { require(it.isFinite()) { "$key must be finite" } }
        }

        private fun validateTensorContract(interpreter: Interpreter, classes: Int, size: Int) {
            require(interpreter.inputTensorCount == 1 && interpreter.outputTensorCount == 1) {
                "Aqua graph must have exactly one input and one output"
            }
            val input = interpreter.getInputTensor(0)
            val output = interpreter.getOutputTensor(0)
            require(input.dataType() == DataType.FLOAT32) {
                // A FLOAT16 graph cannot be fed by the app's float32 CPU path.
                // Reject it before a scan so the caller can use gamma instead
                // of crashing Interpreter.run.
                "Aqua input must be FLOAT32, got ${input.dataType()}"
            }
            require(output.dataType() == DataType.FLOAT32) {
                "Aqua output must be FLOAT32 logits, got ${output.dataType()}"
            }
            require(input.shape().contentEquals(intArrayOf(1, size, size, 3))) {
                "Aqua input shape ${input.shape().contentToString()} != [1,$size,$size,3]"
            }
            require(output.shape().contentEquals(intArrayOf(1, classes))) {
                "Aqua output shape ${output.shape().contentToString()} != [1,$classes]"
            }
        }

        fun softmax(logits: FloatArray): FloatArray {
            require(logits.isNotEmpty()) { "Cannot softmax an empty logit vector" }
            require(logits.all { it.isFinite() }) { "Aqua model returned non-finite logits" }
            val max = logits.maxOrNull()!!
            val exps = logits.map { exp((it - max).toDouble()).toFloat() }
            val sum = exps.sum()
            check(sum.isFinite() && sum > 0f) { "Aqua softmax denominator is invalid" }
            return FloatArray(logits.size) { exps[it] / sum }
        }

        /** Decode a captured image file to a bitmap for on-device inference,
         *  down-sampled so a large photo doesn't OOM before we scale to the
         *  model input size.
         *
         *  Bounds-aware: caps the longest edge near [targetMaxEdge] (≈1024 px,
         *  far above any model input of 224–448) rather than the old blanket
         *  ``inSampleSize = 2`` — a 4000 px phone shot used to decode to a
         *  ~2000 px (16 MB ARGB) bitmap, which OOMed the offline scan on
         *  low-RAM devices. On an OutOfMemoryError it doubles the sampling and
         *  retries so the worst case is a slightly softer crop, not a crash. */
        fun decodeForInference(imagePath: String, targetMaxEdge: Int = 1024): Bitmap? {
            require(targetMaxEdge > 0) { "targetMaxEdge must be positive" }
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeFile(imagePath, bounds)
            val w = bounds.outWidth
            val h = bounds.outHeight
            if (w <= 0 || h <= 0) return null
            var sample = 1
            while (w / (sample * 2) >= targetMaxEdge || h / (sample * 2) >= targetMaxEdge) {
                sample *= 2
            }
            val orientation = try {
                ExifInterface(imagePath).getAttributeInt(
                    ExifInterface.TAG_ORIENTATION,
                    ExifInterface.ORIENTATION_NORMAL,
                )
            } catch (e: Exception) {
                Log.w(TAG, "Could not read Aqua inference EXIF orientation", e)
                ExifInterface.ORIENTATION_NORMAL
            }
            while (sample <= 32) {
                var decoded: Bitmap? = null
                try {
                    val opts = BitmapFactory.Options().apply { inSampleSize = sample }
                    decoded = BitmapFactory.decodeFile(imagePath, opts) ?: return null
                    val upright = applyExifOrientation(decoded, orientation)
                    if (upright !== decoded) decoded.recycle()
                    return upright
                } catch (_: OutOfMemoryError) {
                    decoded?.recycle()
                    Log.w(TAG, "Inference decode OOM; retrying smaller")
                    sample *= 2
                }
            }
            return null
        }

        private fun applyExifOrientation(bitmap: Bitmap, orientation: Int): Bitmap {
            val matrix = Matrix()
            when (orientation) {
                ExifInterface.ORIENTATION_FLIP_HORIZONTAL -> matrix.setScale(-1f, 1f)
                ExifInterface.ORIENTATION_ROTATE_180 -> matrix.postRotate(180f)
                ExifInterface.ORIENTATION_FLIP_VERTICAL -> {
                    matrix.postRotate(180f)
                    matrix.postScale(-1f, 1f)
                }
                ExifInterface.ORIENTATION_TRANSPOSE -> {
                    matrix.postRotate(90f)
                    matrix.postScale(-1f, 1f)
                }
                ExifInterface.ORIENTATION_ROTATE_90 -> matrix.postRotate(90f)
                ExifInterface.ORIENTATION_TRANSVERSE -> {
                    matrix.postRotate(-90f)
                    matrix.postScale(-1f, 1f)
                }
                ExifInterface.ORIENTATION_ROTATE_270 -> matrix.postRotate(270f)
                else -> return bitmap
            }
            return Bitmap.createBitmap(bitmap, 0, 0, bitmap.width, bitmap.height, matrix, true)
        }
    }
}
