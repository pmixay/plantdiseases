package com.plantdiseases.app.data.model

import com.google.common.truth.Truth.assertThat
import kotlinx.serialization.json.Json
import org.junit.Test

/** Wire-format checks for the forum models (kotlinx is the wire format). */
class ForumModelsTest {

    private val json = Json {
        ignoreUnknownKeys = true
        coerceInputValues = true
    }

    @Test
    fun postWithThreadedAnswersDecodes() {
        val post = json.decodeFromString<ForumPost>(
            """
            {"id":"fp1","kind":"question","space":"aroids","title":"Spots","body":"Help",
             "author":{"nickname":"Asker","author_token":"ca1","is_me":true},
             "solved":true,"accepted_answer_id":"fa1","locked":false,"state":"visible",
             "created_at":1,"last_activity_at":2,"training_consent":null,
             "recovery_updates":[{"id":"fp2","title":"Better","created_at":3}],
             "answers":[
               {"id":"fa0","is_ai":true,"author":null,"diagnosis":"leaf_spot","state":"visible"},
               {"id":"fa1","parent_id":null,"depth":0,"author":{"nickname":"Helper","is_post_author":false},
                "body":"Cut it","steps":["Sterilise","Cut"],"diagnosis":"leaf_spot",
                "like_count":2,"not_helpful_count":1,"my_reaction":"like","accepted":true,"verified":true},
               {"id":"fa2","parent_id":"fa1","depth":1,"state":"removed"}
             ]}
            """.trimIndent(),
        )
        assertThat(post.author.isMe).isTrue()
        assertThat(post.recoveryUpdates.single().id).isEqualTo("fp2")
        assertThat(post.answers.map { it.id }).containsExactly("fa0", "fa1", "fa2").inOrder()
        assertThat(post.answers[0].isAi).isTrue()
        assertThat(post.answers[1].steps).containsExactly("Sterilise", "Cut").inOrder()
        assertThat(post.answers[1].myReaction).isEqualTo("like")
        assertThat(post.answers[2].isRemoved).isTrue()
        assertThat(post.answers[2].parentId).isEqualTo("fa1")
    }

    @Test
    fun requestBodiesAlwaysCarryEveryField() {
        val encoded = json.encodeToString(ForumReactionRequest(reaction = null))
        assertThat(encoded).isEqualTo("""{"reaction":null}""")
        val like = json.encodeToString(ForumLikeRequest(liked = true))
        assertThat(like).isEqualTo("""{"liked":true}""")
    }

    @Test
    fun staffRolesDeriveCapabilities() {
        val admin = StaffMe(available = true, roles = listOf(StaffRole("admin")))
        val expert = StaffMe(available = true, roles = listOf(StaffRole("expert")))
        val scopedMod = StaffMe(available = true, roles = listOf(StaffRole("moderator", "aroids")))
        assertThat(admin.isModerator && admin.isExpert && admin.isAdmin).isTrue()
        assertThat(expert.isModerator).isFalse()
        assertThat(expert.isExpert).isTrue()
        assertThat(scopedMod.isModerator).isTrue()
        assertThat(StaffMe().isStaff).isFalse()
    }

    @Test
    fun helperProfileDecodesWithAndWithoutDiscount() {
        val visitor = json.decodeFromString<HelperProfile>(
            """{"nickname":"Fern","level":3,"leaves":420,"next_level_leaves":1000,
               "stats":{"accepted_answers":12,"plants_saved":2,"labels_taught":5},
               "earned":4,"total":30,"achievements":[{"key":"first_aid","group":"helping","value":12,
               "tiers":[{"tier":1,"threshold":1,"awarded_at":5}],"earned_tiers":1}]}""",
        )
        assertThat(visitor.discount).isNull()
        assertThat(visitor.achievements.single().tiers.single().awardedAt).isEqualTo(5L)
        val own = json.decodeFromString<HelperProfile>(
            """{"nickname":"Fern","discount":{"achievements":13,"percent":26,"per_achievement":2,
               "cap":40,"unlocks_at":null,"frozen":false}}""",
        )
        assertThat(own.discount?.percent).isEqualTo(26)
    }
}
