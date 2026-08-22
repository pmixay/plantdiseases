package com.plantdiseases.app.ui.result

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.HorizontalScrollView
import android.widget.LinearLayout
import androidx.appcompat.app.AppCompatActivity
import androidx.core.widget.NestedScrollView
import com.google.common.truth.Truth.assertThat
import com.google.common.truth.Truth.assertWithMessage
import com.plantdiseases.app.R
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Structural contract for the rebuilt "second opinion" sheet.
 *
 * CommunityOpinionSheet is `@AndroidEntryPoint` and needs a repository, so
 * this inflates the layout directly rather than showing the fragment. What it
 * guards is exactly what the redesign was for and what a later hand-edit
 * would quietly undo: the like and ⋯ overlaying the photo instead of floating
 * between blocks, and the vote living in a dock that cannot scroll away.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class CommunityOpinionSheetLayoutTest {

    private fun inflate(): View {
        val activity = Robolectric.buildActivity(AppCompatActivity::class.java).setup().get()
        return LayoutInflater.from(activity).inflate(R.layout.sheet_community_opinion, null)
    }

    private fun View.isDescendantOf(ancestor: View): Boolean {
        var p = parent
        while (p is View) {
            if (p === ancestor) return true
            p = p.parent
        }
        return false
    }

    /**
     * The like pill and the ⋯ must share the photo's frame. As a free-standing
     * row the like was indented to match a card's inner padding and so lined
     * up with nothing visible — overlaying the photo is what gives the pair a
     * real edge, and it only works while all three are in the same parent.
     */
    @Test
    fun likeAndOverflowOverlayThePhoto() {
        val root = inflate()
        val actions = root.findViewById<View>(R.id.ll_opinion_actions)
        val photoCard = root.findViewById<View>(R.id.card_opinion_photo)

        assertThat(actions).isNotNull()
        assertThat(photoCard).isNotNull()
        assertWithMessage("actions bar and photo must share a parent to overlay")
            .that(actions.parent).isSameInstanceAs(photoCard.parent)
        assertThat(root.findViewById<View>(R.id.ll_opinion_like).isDescendantOf(actions)).isTrue()
        assertThat(root.findViewById<View>(R.id.btn_opinion_more).isDescendantOf(actions)).isTrue()
    }

    /**
     * The dock is the whole point of the layout: the vote has to stay
     * reachable while the consensus and the replies are read. If it ever ends
     * up inside the scroll view it becomes just another block near the bottom.
     */
    @Test
    fun theDockSitsOutsideTheScrollAreaAndTakesTheLeftoverHeight() {
        val root = inflate() as LinearLayout
        val scroll = root.findViewById<NestedScrollView>(R.id.scroll_opinion)
        val dock = root.findViewById<View>(R.id.ll_opinion_dock)

        assertThat(dock.isDescendantOf(scroll)).isFalse()
        assertThat(scroll.parent).isSameInstanceAs(root)
        assertThat(dock.parent).isSameInstanceAs(root)

        val scrollParams = scroll.layoutParams as LinearLayout.LayoutParams
        assertWithMessage("the scroll pane takes the leftover height, so the dock keeps its own")
            .that(scrollParams.height).isEqualTo(0)
        assertThat(scrollParams.weight).isGreaterThan(0f)
    }

    /** All three faces live in the dock, and applyDockState() shows exactly
     *  one. The layout ships the Agree/Disagree face, since a fresh viewer has
     *  no pick yet and the composer only opens on demand. */
    @Test
    fun theDockHoldsThreeMutuallyExclusiveFaces() {
        val root = inflate()
        val dock = root.findViewById<View>(R.id.ll_opinion_dock)
        val vote = root.findViewById<View>(R.id.ll_opinion_vote)
        val mine = root.findViewById<View>(R.id.ll_opinion_current_vote)
        val composer = root.findViewById<View>(R.id.ll_opinion_composer)

        for (face in listOf(vote, mine, composer)) {
            assertThat(face.isDescendantOf(dock)).isTrue()
        }
        assertWithMessage("a fresh viewer sees Agree/Disagree")
            .that(vote.visibility).isEqualTo(View.VISIBLE)
        assertThat(mine.visibility).isEqualTo(View.GONE)
        assertThat(composer.visibility).isEqualTo(View.GONE)
    }

    /**
     * The composer carries a note field, so the dock has to stay short enough
     * that the replies behind it are still readable — hence one horizontally
     * scrolling row of chips rather than a wrapping block, and a close
     * affordance instead of the Cancel button the sheet no longer has.
     */
    @Test
    fun theComposerKeepsItsChipsOnOneScrollingRow() {
        val root = inflate()
        val chips = root.findViewById<View>(R.id.chip_group_opinion)
        val composer = root.findViewById<View>(R.id.ll_opinion_composer)

        assertWithMessage("chips must scroll horizontally, not wrap the dock taller")
            .that(chips.parent).isInstanceOf(HorizontalScrollView::class.java)
        for (id in listOf(R.id.til_opinion_note, R.id.btn_opinion_send, R.id.btn_opinion_composer_close)) {
            assertThat(root.findViewById<View>(id).isDescendantOf(composer)).isTrue()
        }
    }

    /** Confidence reads as a number and a meter, both hidden until renderMeta
     *  has a value; the species card hides itself the same way. */
    @Test
    fun confidenceAndSpeciesStartHiddenUntilTheyHaveContent() {
        val root = inflate()
        assertThat(root.findViewById<View>(R.id.progress_opinion_confidence).visibility)
            .isEqualTo(View.GONE)
        assertThat(root.findViewById<View>(R.id.tv_opinion_meta).visibility)
            .isEqualTo(View.GONE)
        assertThat(root.findViewById<View>(R.id.card_opinion_species).visibility)
            .isEqualTo(View.GONE)
    }

    /** The species card is the shared one, never a bespoke copy — see
     *  CLAUDE.md, "One species surface". */
    @Test
    fun theSpeciesCardIsTheSharedComponent() {
        val root = inflate()
        assertThat(root.findViewById<View>(R.id.card_opinion_species))
            .isInstanceOf(com.plantdiseases.app.ui.common.SpeciesInfoCardView::class.java)
    }

    /** Everything above the dock has to be reachable by scrolling; a block
     *  left outside the scroll pane would be clipped on a short screen. */
    @Test
    fun everyContentBlockLivesInsideTheScrollPane() {
        val root = inflate()
        val scroll = root.findViewById<View>(R.id.scroll_opinion)
        val blocks = listOf(
            R.id.card_opinion_photo,
            R.id.tv_opinion_title,
            R.id.card_opinion_current,
            R.id.card_opinion_species,
            R.id.card_opinion_locked,
            R.id.ll_opinion_graph,
            R.id.ll_opinion_list,
            R.id.tv_opinion_empty,
        )
        for (id in blocks) {
            val view = root.findViewById<View>(id)
            assertWithMessage("block $id missing").that(view).isNotNull()
            assertThat(view.isDescendantOf(scroll)).isTrue()
        }
    }

    /** The chip picker is single-select: the vote graph counts one class per
     *  author, so a multi-select picker would post something it can't tally. */
    @Test
    fun theChipPickerIsSingleSelect() {
        val root = inflate()
        val chips = root.findViewById<com.google.android.material.chip.ChipGroup>(R.id.chip_group_opinion)
        assertThat(chips.isSingleSelection).isTrue()
        assertThat((chips.layoutParams as ViewGroup.LayoutParams).width)
            .isEqualTo(ViewGroup.LayoutParams.WRAP_CONTENT)
    }
}
