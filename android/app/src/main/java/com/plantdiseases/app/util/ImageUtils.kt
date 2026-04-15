package com.plantdiseases.app.util

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.net.Uri
import androidx.exifinterface.media.ExifInterface
import java.io.File
import java.io.FileOutputStream
import java.text.SimpleDateFormat
import java.util.*

object ImageUtils {

    /** Create a temp file for camera capture */
    fun createImageFile(context: Context): File {
        val timeStamp = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date())
        val storageDir = File(context.filesDir, "plant_images").apply { mkdirs() }
        return File(storageDir, "PLANT_${timeStamp}.jpg")
    }

    /** Compress and resize image for upload */
    fun prepareImageForUpload(context: Context, imagePath: String, maxSize: Int = 1024): File {
        val original = BitmapFactory.decodeFile(imagePath)
        val rotated = rotateIfNeeded(imagePath, original)

        val ratio = maxSize.toFloat() / maxOf(rotated.width, rotated.height)
        val scaled = if (ratio < 1f) {
            Bitmap.createScaledBitmap(
                rotated,
                (rotated.width * ratio).toInt(),
                (rotated.height * ratio).toInt(),
                true
            )
        } else rotated

        val outputFile = File(context.cacheDir, "upload_${System.currentTimeMillis()}.jpg")
        FileOutputStream(outputFile).use { out ->
            scaled.compress(Bitmap.CompressFormat.JPEG, 85, out)
        }

        if (scaled !== rotated) scaled.recycle()
        if (rotated !== original) rotated.recycle()
        original.recycle()

        return outputFile
    }

    /** Copy URI to internal storage */
    fun copyUriToFile(context: Context, uri: Uri): File? {
        return try {
            val file = createImageFile(context)
            context.contentResolver.openInputStream(uri)?.use { input ->
                FileOutputStream(file).use { output ->
                    input.copyTo(output)
                }
            }
            file
        } catch (e: Exception) {
            e.printStackTrace()
            null
        }
    }

    private fun rotateIfNeeded(imagePath: String, bitmap: Bitmap): Bitmap {
        val exif = ExifInterface(imagePath)
        val orientation = exif.getAttributeInt(
            ExifInterface.TAG_ORIENTATION,
            ExifInterface.ORIENTATION_NORMAL
        )
        val matrix = Matrix()
        when (orientation) {
            ExifInterface.ORIENTATION_ROTATE_90 -> matrix.postRotate(90f)
            ExifInterface.ORIENTATION_ROTATE_180 -> matrix.postRotate(180f)
            ExifInterface.ORIENTATION_ROTATE_270 -> matrix.postRotate(270f)
            else -> return bitmap
        }
        return Bitmap.createBitmap(bitmap, 0, 0, bitmap.width, bitmap.height, matrix, true)
    }

    fun formatTimestamp(timestamp: Long): String {
        val sdf = SimpleDateFormat("dd.MM.yyyy HH:mm", Locale.getDefault())
        return sdf.format(Date(timestamp))
    }
}
