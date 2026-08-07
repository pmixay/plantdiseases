package com.plantdiseases.app.util

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Color
import androidx.exifinterface.media.ExifInterface
import com.google.common.truth.Truth.assertThat
import org.junit.After
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.io.ByteArrayOutputStream
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.MessageDigest

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class UploadImageSanitizerTest {

    private val files = mutableListOf<File>()

    @After
    fun cleanup() {
        files.forEach { it.delete() }
    }

    @Test
    fun sanitize_bakesOrientationAndRemovesExifThumbnailXmpIptcAndFilename() {
        val source = tempFile("SENTINEL-original-file", ".jpg")
        val mainJpeg = stripedBitmap(40, 20).useBitmap {
            compress(it, Bitmap.CompressFormat.JPEG)
        }
        val thumbnail = stripedBitmap(4, 2).useBitmap {
            compress(it, Bitmap.CompressFormat.JPEG)
        }
        source.writeBytes(
            addJpegMetadata(
                mainJpeg,
                thumbnail,
                orientation = ExifInterface.ORIENTATION_ROTATE_90,
            ),
        )
        ExifInterface(source.absolutePath).apply {
            setLatLong(55.7558, 37.6173)
            setAttribute(ExifInterface.TAG_MAKE, "SENTINEL-CAMERA-MAKE")
            setAttribute(ExifInterface.TAG_MODEL, "SENTINEL-CAMERA-MODEL")
            setAttribute(ExifInterface.TAG_BODY_SERIAL_NUMBER, "SENTINEL-SERIAL")
            setAttribute(ExifInterface.TAG_DATETIME_ORIGINAL, "2026:08:07 12:34:56")
            setAttribute(ExifInterface.TAG_USER_COMMENT, "SENTINEL-USER-COMMENT")
            saveAttributes()
        }

        val inputExif = ExifInterface(source.absolutePath)
        assertThat(inputExif.hasThumbnail()).isTrue()
        assertThat(inputExif.getAttributeInt(ExifInterface.TAG_ORIENTATION, 0))
            .isEqualTo(ExifInterface.ORIENTATION_ROTATE_90)
        assertThat(inputExif.getAttribute(ExifInterface.TAG_MAKE)).contains("SENTINEL")
        assertThat(source.readBytes().toString(Charsets.ISO_8859_1)).contains("SENTINEL-XMP")

        val sanitized = DefaultUploadImageSanitizer.sanitize(source)

        assertThat(sanitized.filename).isEqualTo("scan.jpg")
        assertThat(sanitized.contentType).isEqualTo("image/jpeg")
        assertThat(sanitized.width).isEqualTo(20)
        assertThat(sanitized.height).isEqualTo(40)
        assertThat(sanitized.bytes.size).isAtMost(DefaultUploadImageSanitizer.MAX_OUTPUT_BYTES)
        assertThat(sanitized.bytes.toString(Charsets.ISO_8859_1))
            .doesNotContain("SENTINEL")

        val output = tempFile("sanitized", ".jpg").apply { writeBytes(sanitized.bytes) }
        val outputExif = ExifInterface(output.absolutePath)
        assertThat(outputExif.hasThumbnail()).isFalse()
        listOf(
            ExifInterface.TAG_GPS_LATITUDE,
            ExifInterface.TAG_GPS_LONGITUDE,
            ExifInterface.TAG_MAKE,
            ExifInterface.TAG_MODEL,
            ExifInterface.TAG_BODY_SERIAL_NUMBER,
            ExifInterface.TAG_DATETIME_ORIGINAL,
            ExifInterface.TAG_USER_COMMENT,
            ExifInterface.TAG_XMP,
        ).forEach { tag -> assertThat(outputExif.getAttribute(tag)).isNull() }

        val decoded = BitmapFactory.decodeByteArray(sanitized.bytes, 0, sanitized.bytes.size)
        assertThat(decoded.width).isEqualTo(20)
        assertThat(decoded.height).isEqualTo(40)
        decoded.recycle()
    }

    @Test
    fun sanitize_acceptsPngAndWebpAndAlwaysProducesFreshJpeg() {
        listOf(
            ".png" to Bitmap.CompressFormat.PNG,
            ".webp" to Bitmap.CompressFormat.WEBP_LOSSY,
        ).forEach { (suffix, format) ->
            val source = tempFile("source", suffix)
            source.writeBytes(stripedBitmap(64, 32).useBitmap { compress(it, format) })

            val sanitized = DefaultUploadImageSanitizer.sanitize(source)

            assertThat(sanitized.bytes[0].toInt() and 0xff).isEqualTo(0xff)
            assertThat(sanitized.bytes[1].toInt() and 0xff).isEqualTo(0xd8)
            assertThat(sanitized.width).isEqualTo(64)
            assertThat(sanitized.height).isEqualTo(32)
        }
    }

    @Test
    fun sanitize_enforcesDimensionLimitAndHashesOnlyOutputBytes() {
        val source = tempFile("large", ".png")
        source.writeBytes(stripedBitmap(1_800, 900).useBitmap { compress(it, Bitmap.CompressFormat.PNG) })

        val sanitized = DefaultUploadImageSanitizer.sanitize(source, maxDimension = 512)
        val expectedHash = MessageDigest.getInstance("SHA-256")
            .digest(sanitized.bytes)
            .joinToString("") { "%02x".format(it.toInt() and 0xff) }

        assertThat(sanitized.width).isEqualTo(512)
        assertThat(sanitized.height).isEqualTo(256)
        assertThat(sanitized.sha256).isEqualTo(expectedHash)
    }

    private fun stripedBitmap(width: Int, height: Int): Bitmap =
        Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888).apply {
            for (x in 0 until width) {
                val color = if (x < width / 2) Color.RED else Color.BLUE
                for (y in 0 until height) setPixel(x, y, color)
            }
        }

    private fun compress(bitmap: Bitmap, format: Bitmap.CompressFormat): ByteArray =
        ByteArrayOutputStream().use { output ->
            check(bitmap.compress(format, 90, output))
            output.toByteArray()
        }

    private fun addJpegMetadata(
        jpeg: ByteArray,
        thumbnail: ByteArray,
        orientation: Int,
    ): ByteArray {
        check(jpeg[0] == 0xff.toByte() && jpeg[1] == 0xd8.toByte())
        val exif = jpegSegment(0xe1, exifPayload(thumbnail, orientation))
        val xmp = jpegSegment(
            0xe1,
            ("http://ns.adobe.com/xap/1.0/\u0000" +
                "<x:xmpmeta>SENTINEL-XMP</x:xmpmeta>").encodeToByteArray(),
        )
        val iptc = jpegSegment(0xed, "Photoshop 3.0\u0000SENTINEL-IPTC".encodeToByteArray())
        return jpeg.copyOfRange(0, 2) + exif + xmp + iptc + jpeg.copyOfRange(2, jpeg.size)
    }

    private fun exifPayload(thumbnail: ByteArray, orientation: Int): ByteArray {
        val tiff = ByteBuffer.allocate(56 + thumbnail.size).order(ByteOrder.LITTLE_ENDIAN)
        tiff.put('I'.code.toByte()).put('I'.code.toByte())
        tiff.putShort(42)
        tiff.putInt(8)
        tiff.position(8)
        tiff.putShort(1)
        tiff.putShort(0x0112)
        tiff.putShort(3)
        tiff.putInt(1)
        tiff.putShort(orientation.toShort()).putShort(0)
        tiff.putInt(26)
        tiff.position(26)
        tiff.putShort(2)
        tiff.putShort(0x0201).putShort(4).putInt(1).putInt(56)
        tiff.putShort(0x0202).putShort(4).putInt(1).putInt(thumbnail.size)
        tiff.putInt(0)
        tiff.put(thumbnail)
        return "Exif\u0000\u0000".encodeToByteArray() + tiff.array()
    }

    private fun jpegSegment(marker: Int, payload: ByteArray): ByteArray {
        val size = payload.size + 2
        require(size <= 0xffff)
        return byteArrayOf(
            0xff.toByte(),
            marker.toByte(),
            (size ushr 8).toByte(),
            size.toByte(),
        ) + payload
    }

    private fun tempFile(prefix: String, suffix: String): File =
        File.createTempFile(prefix, suffix).also(files::add)

    private inline fun <T> Bitmap.useBitmap(block: (Bitmap) -> T): T = try {
        block(this)
    } finally {
        recycle()
    }
}
