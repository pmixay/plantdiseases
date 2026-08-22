package com.plantdiseases.app.ui.garden.detail

import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.data.local.PlantJournalEntity
import com.plantdiseases.app.data.repository.PlantRepository
import org.junit.Test

class DetailJournalControllerTest {

    @Test
    fun chronologicalJournalPhotoUris_ordersByTimestampNotUri_andDropsInvalidDuplicates() {
        val entries = listOf(
            photo(id = 4, uri = "content://provider/a", timestamp = 400),
            photo(id = 3, uri = "content://provider/z", timestamp = 100),
            photo(id = 2, uri = "content://provider/a", timestamp = 200),
            photo(id = 1, uri = "", timestamp = 50),
            PlantJournalEntity(
                id = 5,
                plantId = 1,
                type = PlantRepository.JournalType.NOTE.value,
                photoUri = "content://provider/note",
                timestamp = 25,
            ),
        )

        assertThat(chronologicalJournalPhotoUris(entries)).containsExactly(
            "content://provider/z",
            "content://provider/a",
        ).inOrder()
    }

    private fun photo(id: Long, uri: String, timestamp: Long) = PlantJournalEntity(
        id = id,
        plantId = 1,
        type = PlantRepository.JournalType.PHOTO.value,
        photoUri = uri,
        timestamp = timestamp,
    )
}
