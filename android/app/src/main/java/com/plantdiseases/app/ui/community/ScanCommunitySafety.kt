package com.plantdiseases.app.ui.community

import android.content.Intent
import android.net.Uri
import android.widget.Toast
import androidx.annotation.StringRes
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.R
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.ui.account.QuickSignIn
import com.plantdiseases.app.util.CommunityPrefs
import kotlinx.coroutines.launch

/**
 * The scan-sharing safety tools: community rules with the blocked-authors
 * list and the moderation contact, and leaving scan sharing. They live on the
 * community map, the one screen left that shows other people's scans.
 * [onChanged] runs after an unblock or after leaving, so the screen reloads.
 */
class ScanCommunitySafety(
    private val activity: AppCompatActivity,
    private val scanRepository: ScanRepository,
    private val quickSignIn: QuickSignIn,
    private val onChanged: () -> Unit,
) {

    fun showRules() {
        MaterialAlertDialogBuilder(activity)
            .setTitle(R.string.community_rules_title)
            .setMessage(R.string.community_rules_body)
            .setNegativeButton(R.string.close, null)
            .setNeutralButton(R.string.community_manage_blocks) { _, _ -> showBlockedAuthors() }
            .setPositiveButton(R.string.community_contact_moderation) { _, _ ->
                runCatching {
                    activity.startActivity(
                        Intent(Intent.ACTION_VIEW, Uri.parse(activity.getString(R.string.community_moderation_url))),
                    )
                }.onFailure { toast(R.string.community_safety_error) }
            }
            .show()
    }

    /** Stops sharing new scans. Scans already shared stay in the community. */
    fun confirmLeave() {
        MaterialAlertDialogBuilder(activity)
            .setTitle(R.string.community_disable_title)
            .setMessage(R.string.community_disable_body)
            .setNegativeButton(R.string.cancel, null)
            .setPositiveButton(R.string.community_disable_confirm) { _, _ ->
                CommunityPrefs.setUploadsEnabled(activity, false)
                onChanged()
            }
            .show()
    }

    private fun showBlockedAuthors() {
        if (!quickSignIn.require()) return
        activity.lifecycleScope.launch {
            scanRepository.communityBlocks()
                .onSuccess { blocks ->
                    if (blocks.isEmpty()) {
                        toast(R.string.community_blocks_empty)
                        return@onSuccess
                    }
                    val labels = blocks.map {
                        it.nickname?.takeIf(String::isNotBlank)
                            ?: activity.getString(R.string.community_unknown_author)
                    }.toTypedArray()
                    MaterialAlertDialogBuilder(activity)
                        .setTitle(R.string.community_manage_blocks)
                        .setItems(labels) { _, index -> confirmUnblock(blocks[index].blockId, labels[index]) }
                        .setNegativeButton(R.string.close, null)
                        .show()
                }
                .onFailure { toast(R.string.community_safety_error) }
        }
    }

    private fun confirmUnblock(blockId: String, label: String) {
        MaterialAlertDialogBuilder(activity)
            .setTitle(activity.getString(R.string.community_unblock_title, label))
            .setNegativeButton(R.string.cancel, null)
            .setPositiveButton(R.string.community_unblock_confirm) { _, _ ->
                activity.lifecycleScope.launch {
                    scanRepository.unblockCommunityAuthor(blockId)
                        .onSuccess {
                            toast(R.string.community_author_unblocked)
                            onChanged()
                        }
                        .onFailure { toast(R.string.community_safety_error) }
                }
            }
            .show()
    }

    private fun toast(@StringRes text: Int) {
        Toast.makeText(activity, text, Toast.LENGTH_SHORT).show()
    }
}
