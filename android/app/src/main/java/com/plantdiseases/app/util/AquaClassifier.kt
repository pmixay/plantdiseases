package com.plantdiseases.app.util

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.Log
import org.json.JSONObject
import org.tensorflow.lite.Interpreter
import java.io.RandomAccessFile
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.MappedByteBuffer
import java.nio.channels.FileChannel
import kotlin.math.exp

/**
 * On-device TFLite classifier for the **Aqua** model family.
 *
 * Loads `assets/models/aqua_classifier.tflite` + `assets/models/aqua_classes.json`
 * (produced by §5 of the training notebook — MobileNetV3-Small student distilled
 * from the gamma DINOv2 teacher). Like the server weights, the model is
 * provisioned out of band and never committed: [isAvailable] returns false when
 * the asset is missing and callers fall back to the server.
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
) {

    /** Run inference on a full-frame bitmap. Returns per-class probabilities
     *  (softmax) keyed by class name, in declared order. */
    fun classify(bitmap: Bitmap): Map<String, Float> {
        val input = preprocess(bitmap)
        val output = Array(1) { FloatArray(classNames.size) }
        interpreter.run(input, output)
        val probs = softmax(output[0])
        return classNames.indices.associate { classNames[it] to probs[it] }
    }

    private fun preprocess(bitmap: Bitmap): ByteBuffer {
        val scaled = Bitmap.createScaledBitmap(bitmap, inputSize, inputSize, true)
        val buf = ByteBuffer.allocateDirect(4 * inputSize * inputSize * 3)
            .order(ByteOrder.nativeOrder())
        val pixels = IntArray(inputSize * inputSize)
        scaled.getPixels(pixels, 0, inputSize, 0, 0, inputSize, inputSize)
        for (p in pixels) {
            val r = ((p shr 16) and 0xFF) / 255f
            val g = ((p shr 8) and 0xFF) / 255f
            val b = (p and 0xFF) / 255f
            buf.putFloat((r - mean[0]) / std[0])
            buf.putFloat((g - mean[1]) / std[1])
            buf.putFloat((b - mean[2]) / std[2])
        }
        if (scaled != bitmap) scaled.recycle()
        buf.rewind()
        return buf
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
            if (!AquaModelManager.isInstalled(context)) return null
            return try {
                val model = loadModelFile(context)
                val meta = JSONObject(AquaModelManager.classesFile(context).readText())
                val classes = meta.getJSONArray("class_names")
                    .let { arr -> (0 until arr.length()).map { arr.getString(it) } }
                val size = meta.optJSONObject("training")?.optInt("img_size", 224) ?: 224
                val inf = meta.optJSONObject("inference")
                val mean = inf.floatTriple("normalize_mean", floatArrayOf(0.485f, 0.456f, 0.406f))
                val std = inf.floatTriple("normalize_std", floatArrayOf(0.229f, 0.224f, 0.225f))
                val opts = Interpreter.Options().apply { setNumThreads(2) }
                AquaClassifier(Interpreter(model, opts), classes, size, mean, std)
            } catch (e: Exception) {
                Log.w(TAG, "Aqua model unavailable: ${e.message}")
                null
            }
        }

        private fun loadModelFile(context: Context): MappedByteBuffer {
            val file = AquaModelManager.modelFile(context)
            RandomAccessFile(file, "r").use { raf ->
                return raf.channel.map(FileChannel.MapMode.READ_ONLY, 0, raf.length())
            }
        }

        private fun JSONObject?.floatTriple(key: String, fallback: FloatArray): FloatArray {
            val arr = this?.optJSONArray(key) ?: return fallback
            if (arr.length() < 3) return fallback
            return floatArrayOf(
                arr.getDouble(0).toFloat(),
                arr.getDouble(1).toFloat(),
                arr.getDouble(2).toFloat(),
            )
        }

        fun softmax(logits: FloatArray): FloatArray {
            val max = logits.maxOrNull() ?: 0f
            val exps = logits.map { exp((it - max).toDouble()).toFloat() }
            val sum = exps.sum().takeIf { it > 0f } ?: 1f
            return FloatArray(logits.size) { exps[it] / sum }
        }

        /** Decode a captured image file to a bitmap for on-device inference,
         *  down-sampled so a large photo doesn't OOM before we scale to the
         *  model input size. */
        fun decodeForInference(imagePath: String): Bitmap? {
            val opts = BitmapFactory.Options().apply { inSampleSize = 2 }
            return BitmapFactory.decodeFile(imagePath, opts)
        }
    }
}
