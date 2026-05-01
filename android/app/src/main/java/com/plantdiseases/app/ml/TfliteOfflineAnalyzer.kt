package com.plantdiseases.app.ml

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.AnalysisResponse
import com.plantdiseases.app.data.model.TopKEntry
import org.tensorflow.lite.DataType
import org.tensorflow.lite.Interpreter
import java.io.FileInputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.MappedByteBuffer
import java.nio.channels.FileChannel
import kotlin.math.abs
import kotlin.math.exp
import kotlin.math.roundToInt

class TfliteOfflineAnalyzer(
    private val context: Context,
) {
    fun analyze(imagePath: String): AnalysisResponse? {
        val modelAsset = findAsset(MODEL_ASSETS) ?: return null
        val interpreter = try {
            Interpreter(loadModel(modelAsset), Interpreter.Options().setNumThreads(2))
        } catch (_: Exception) {
            return null
        }

        return try {
            runModel(interpreter, imagePath)
        } catch (_: Exception) {
            null
        } finally {
            interpreter.close()
        }
    }

    private fun runModel(interpreter: Interpreter, imagePath: String): AnalysisResponse? {
        val inputTensor = interpreter.getInputTensor(0)
        val inputShape = inputTensor.shape()
        if (inputShape.size != 4) return null

        val usesNchw = inputShape[1] == 3 && inputShape[3] != 3
        val height = if (usesNchw) inputShape[2] else inputShape[1]
        val width = if (usesNchw) inputShape[3] else inputShape[2]
        val channels = if (usesNchw) inputShape[1] else inputShape[3]
        if (height <= 0 || width <= 0 || channels != 3) return null

        val bitmap = decodeCenterCrop(imagePath, width, height) ?: return null
        val input = try {
            val quantization = inputTensor.quantizationParams()
            buildInput(
                bitmap = bitmap,
                dataType = inputTensor.dataType(),
                usesNchw = usesNchw,
                scale = quantization.scale,
                zeroPoint = quantization.zeroPoint,
            )
        } finally {
            bitmap.recycle()
        }

        val outputTensor = interpreter.getOutputTensor(0)
        val classCount = outputTensor.shape().lastOrNull()?.takeIf { it > 1 } ?: return null
        val outputQuantization = outputTensor.quantizationParams()
        val rawScores = runOutput(
            interpreter = interpreter,
            input = input,
            dataType = outputTensor.dataType(),
            classCount = classCount,
            scale = outputQuantization.scale,
            zeroPoint = outputQuantization.zeroPoint,
        ) ?: return null
        val probabilities = normalizeScores(rawScores)
        val labels = loadLabels(classCount)
        val topIndex = probabilities.indices.maxByOrNull { probabilities[it] } ?: return null
        val topLabel = labels[topIndex]
        val isHealthy = isHealthyLabel(topLabel)
        val confidence = probabilities[topIndex].coerceIn(0.01f, 0.99f)

        return AnalysisResponse(
            diseaseName = if (isHealthy) "Healthy Plant" else displayName(topLabel),
            diseaseNameRu = if (isHealthy) {
                context.getString(R.string.status_healthy)
            } else {
                displayName(topLabel)
            },
            confidence = confidence,
            description = if (isHealthy) {
                context.getString(R.string.offline_healthy_description)
            } else {
                context.getString(R.string.offline_disease_description)
            },
            descriptionRu = if (isHealthy) {
                context.getString(R.string.offline_healthy_description_ru)
            } else {
                context.getString(R.string.offline_disease_description_ru)
            },
            treatment = if (isHealthy) {
                emptyList()
            } else {
                listOf(
                    context.getString(R.string.offline_disease_treatment_1),
                    context.getString(R.string.offline_disease_treatment_2),
                    context.getString(R.string.offline_disease_treatment_3),
                )
            },
            treatmentRu = if (isHealthy) {
                emptyList()
            } else {
                listOf(
                    context.getString(R.string.offline_disease_treatment_1_ru),
                    context.getString(R.string.offline_disease_treatment_2_ru),
                    context.getString(R.string.offline_disease_treatment_3_ru),
                )
            },
            prevention = listOf(
                context.getString(R.string.offline_prevention_1),
                context.getString(R.string.offline_prevention_2),
            ),
            preventionRu = listOf(
                context.getString(R.string.offline_prevention_1_ru),
                context.getString(R.string.offline_prevention_2_ru),
            ),
            isHealthy = isHealthy,
            allProbs = labels.indices.associate { labels[it] to probabilities[it] },
            topK = probabilities.indices
                .sortedByDescending { probabilities[it] }
                .take(3)
                .map { index ->
                    TopKEntry(
                        className = labels[index],
                        confidence = probabilities[index],
                        nameEn = if (isHealthyLabel(labels[index])) "Healthy Plant" else displayName(labels[index]),
                        nameRu = if (isHealthyLabel(labels[index])) {
                            context.getString(R.string.status_healthy)
                        } else {
                            displayName(labels[index])
                        },
                        isHealthy = isHealthyLabel(labels[index]),
                    )
                },
            warnings = listOf("offline_tflite"),
            pipelineMode = "offline_tflite",
        )
    }

    private fun buildInput(
        bitmap: Bitmap,
        dataType: DataType,
        usesNchw: Boolean,
        scale: Float,
        zeroPoint: Int,
    ): ByteBuffer {
        val bytesPerChannel = if (dataType == DataType.FLOAT32) 4 else 1
        val buffer = ByteBuffer
            .allocateDirect(bitmap.width * bitmap.height * 3 * bytesPerChannel)
            .order(ByteOrder.nativeOrder())

        if (usesNchw) {
            for (channel in 0 until 3) {
                for (y in 0 until bitmap.height) {
                    for (x in 0 until bitmap.width) {
                        putChannel(buffer, dataType, bitmap.getPixel(x, y), channel, scale, zeroPoint)
                    }
                }
            }
        } else {
            for (y in 0 until bitmap.height) {
                for (x in 0 until bitmap.width) {
                    val pixel = bitmap.getPixel(x, y)
                    putChannel(buffer, dataType, pixel, 0, scale, zeroPoint)
                    putChannel(buffer, dataType, pixel, 1, scale, zeroPoint)
                    putChannel(buffer, dataType, pixel, 2, scale, zeroPoint)
                }
            }
        }
        buffer.rewind()
        return buffer
    }

    private fun putChannel(
        buffer: ByteBuffer,
        dataType: DataType,
        pixel: Int,
        channel: Int,
        scale: Float,
        zeroPoint: Int,
    ) {
        val value = when (channel) {
            0 -> (pixel shr 16) and 0xFF
            1 -> (pixel shr 8) and 0xFF
            else -> pixel and 0xFF
        }
        val normalized = value / 255f
        when (dataType) {
            DataType.FLOAT32 -> buffer.putFloat(normalized)
            DataType.UINT8 -> {
                val quantized = quantize(normalized, scale, zeroPoint).coerceIn(0, 255)
                buffer.put(quantized.toByte())
            }
            DataType.INT8 -> {
                val quantized = quantize(normalized, scale, zeroPoint).coerceIn(-128, 127)
                buffer.put(quantized.toByte())
            }
            else -> buffer.put(value.toByte())
        }
    }

    private fun quantize(value: Float, scale: Float, zeroPoint: Int): Int =
        if (scale > 0f) (value / scale + zeroPoint).roundToInt() else (value * 255f).roundToInt()

    private fun runOutput(
        interpreter: Interpreter,
        input: ByteBuffer,
        dataType: DataType,
        classCount: Int,
        scale: Float,
        zeroPoint: Int,
    ): FloatArray? {
        val output = when (dataType) {
            DataType.FLOAT32 -> ByteBuffer.allocateDirect(classCount * 4)
            DataType.UINT8, DataType.INT8 -> ByteBuffer.allocateDirect(classCount)
            else -> return null
        }.order(ByteOrder.nativeOrder())

        interpreter.run(input, output)
        output.rewind()
        return when (dataType) {
            DataType.FLOAT32 -> FloatArray(classCount) { output.float }
            DataType.UINT8 -> FloatArray(classCount) {
                dequantize(output.get().toInt() and 0xFF, scale, zeroPoint)
            }
            DataType.INT8 -> FloatArray(classCount) {
                dequantize(output.get().toInt(), scale, zeroPoint)
            }
            else -> null
        }
    }

    private fun dequantize(value: Int, scale: Float, zeroPoint: Int): Float =
        if (scale > 0f) (value - zeroPoint) * scale else value / 255f

    private fun normalizeScores(scores: FloatArray): FloatArray {
        val sum = scores.sum()
        if (scores.all { it in 0f..1f } && abs(sum - 1f) < 0.15f) {
            return scores
        }

        val max = scores.maxOrNull() ?: return scores
        val expScores = scores.map { exp((it - max).toDouble()) }
        val expSum = expScores.sum().takeIf { it > 0.0 } ?: return scores
        return FloatArray(scores.size) { index -> (expScores[index] / expSum).toFloat() }
    }

    private fun decodeCenterCrop(path: String, targetWidth: Int, targetHeight: Int): Bitmap? {
        val original = BitmapFactory.decodeFile(path) ?: return null
        val cropSize = minOf(original.width, original.height)
        val left = (original.width - cropSize) / 2
        val top = (original.height - cropSize) / 2
        val cropped = Bitmap.createBitmap(original, left, top, cropSize, cropSize)
        val resized = Bitmap.createScaledBitmap(cropped, targetWidth, targetHeight, true)

        if (cropped !== resized) cropped.recycle()
        if (original !== cropped) original.recycle()
        return resized
    }

    private fun loadModel(assetName: String): MappedByteBuffer {
        context.assets.openFd(assetName).use { descriptor ->
            FileInputStream(descriptor.fileDescriptor).channel.use { channel ->
                return channel.map(
                    FileChannel.MapMode.READ_ONLY,
                    descriptor.startOffset,
                    descriptor.declaredLength,
                )
            }
        }
    }

    private fun loadLabels(classCount: Int): List<String> {
        val labels = findAsset(LABEL_ASSETS)?.let { assetName ->
            context.assets.open(assetName).bufferedReader().useLines { lines ->
                lines.map { it.trim() }
                    .filter { it.isNotEmpty() && !it.startsWith("#") }
                    .toList()
            }
        }.orEmpty().ifEmpty { DEFAULT_LABELS }

        return if (labels.size >= classCount) {
            labels.take(classCount)
        } else {
            labels + (labels.size until classCount).map { "class_$it" }
        }
    }

    private fun findAsset(candidates: List<String>): String? {
        val rootAssets = context.assets.list("")?.toSet().orEmpty()
        return candidates.firstOrNull { it in rootAssets }
    }

    private fun isHealthyLabel(label: String): Boolean =
        normalizeLabel(label).contains("healthy")

    private fun displayName(label: String): String =
        normalizeLabel(label)
            .split("_")
            .filter { it.isNotBlank() }
            .joinToString(" ") { it.replaceFirstChar { char -> char.uppercase() } }
            .ifBlank { "Possible plant disease" }

    private fun normalizeLabel(label: String): String =
        label.substringAfterLast("/")
            .substringAfterLast("\\")
            .substringBefore(":")
            .trim()
            .lowercase()
            .replace("-", "_")
            .replace(" ", "_")

    companion object {
        private val MODEL_ASSETS = listOf(
            "plantvision_classifier.tflite",
            "plant_disease_classifier.tflite",
            "classifier.tflite",
            "model.tflite",
        )
        private val LABEL_ASSETS = listOf(
            "plantvision_labels.txt",
            "labels.txt",
            "classes.txt",
        )
        private val DEFAULT_LABELS = listOf(
            "healthy",
            "anthracnose",
            "bacterial_leaf_spot",
            "gray_mold",
            "iron_chlorosis",
            "late_blight",
            "mosaic_virus",
            "powdery_mildew",
            "sunburn",
            "not_a_plant",
        )
    }
}
