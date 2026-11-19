package com.plantdiseases.app.ui.notifications

import android.content.Context
import android.content.Intent
import android.graphics.Canvas
import android.os.Bundle
import android.view.View
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.ItemTouchHelper
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.R
import com.plantdiseases.app.data.repository.ForumNotificationSync
import com.plantdiseases.app.data.repository.NotificationRepository
import com.plantdiseases.app.databinding.ActivityNotificationsBinding
import com.plantdiseases.app.util.LocaleHelper
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * Every community reply, care reminder and garden alert the app has ever
 * surfaced, regardless of whether the system notification itself was
 * allowed to post — see [com.plantdiseases.app.util.NotificationCenter] —
 * plus Community answers and moderators' decisions ([ForumNotificationSync]).
 * Unread rows sit under "New", the rest under "Earlier".
 * Tap marks a row read and routes via [NotificationRouter]; swipe dismisses
 * one row; the overflow menu marks everything read or clears the list.
 */
@AndroidEntryPoint
class NotificationsActivity : AppCompatActivity() {

    private lateinit var binding: ActivityNotificationsBinding
    private lateinit var adapter: NotificationsAdapter

    @Inject
    lateinit var notificationRepository: NotificationRepository

    @Inject
    lateinit var forumSync: ForumNotificationSync

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        binding = ActivityNotificationsBinding.inflate(layoutInflater)
        setContentView(binding.root)

        ViewCompat.setOnApplyWindowInsetsListener(binding.appBar) { view, insets ->
            view.updatePadding(top = insets.getInsets(WindowInsetsCompat.Type.systemBars()).top)
            insets
        }
        binding.toolbar.setNavigationOnClickListener { finish() }
        binding.toolbar.inflateMenu(R.menu.notifications_menu)
        binding.toolbar.setOnMenuItemClickListener { item ->
            when (item.itemId) {
                R.id.action_mark_all_read -> {
                    lifecycleScope.launch {
                        notificationRepository.markAllRead()
                        load()
                    }
                    true
                }
                R.id.action_clear_all -> {
                    confirmClearAll()
                    true
                }
                else -> false
            }
        }

        adapter = NotificationsAdapter(
            onClick = { item ->
                lifecycleScope.launch { notificationRepository.markRead(item.id) }
                val target = NotificationRouter.intentFor(this, item.deeplink)
                startActivity(target ?: Intent(this, com.plantdiseases.app.MainActivity::class.java))
            },
        )
        binding.rvNotifications.layoutManager = LinearLayoutManager(this)
        binding.rvNotifications.adapter = adapter
        attachSwipeToDismiss()

        load()
    }

    override fun onResume() {
        super.onResume()
        load()
        // Community events land in the list as they arrive from the server.
        lifecycleScope.launch {
            forumSync.sync()
            load()
        }
    }

    private fun load() {
        lifecycleScope.launch {
            val items = notificationRepository.getAll()
            adapter.submit(items)
            binding.rvNotifications.visibility = if (items.isEmpty()) View.GONE else View.VISIBLE
            binding.stateLayout.visibility = if (items.isEmpty()) View.VISIBLE else View.GONE
        }
    }

    private fun confirmClearAll() {
        MaterialAlertDialogBuilder(this)
            .setTitle(R.string.notifications_clear_confirm_title)
            .setMessage(R.string.notifications_clear_confirm_body)
            .setNegativeButton(R.string.cancel, null)
            .setPositiveButton(R.string.notifications_clear_all) { _, _ ->
                lifecycleScope.launch {
                    notificationRepository.clearAll()
                    load()
                }
            }
            .show()
    }

    private fun attachSwipeToDismiss() {
        val callback = object : ItemTouchHelper.SimpleCallback(
            0,
            ItemTouchHelper.LEFT or ItemTouchHelper.RIGHT,
        ) {
            override fun onMove(
                recyclerView: RecyclerView,
                viewHolder: RecyclerView.ViewHolder,
                target: RecyclerView.ViewHolder,
            ): Boolean = false

            override fun getSwipeDirs(recyclerView: RecyclerView, viewHolder: RecyclerView.ViewHolder): Int =
                if (adapter.itemAt(viewHolder.bindingAdapterPosition) == null) 0 else super.getSwipeDirs(recyclerView, viewHolder)

            override fun onSwiped(viewHolder: RecyclerView.ViewHolder, direction: Int) {
                val position = viewHolder.bindingAdapterPosition
                val item = adapter.itemAt(position) ?: return
                adapter.removeAt(position)
                lifecycleScope.launch { notificationRepository.delete(item.id) }
                if (adapter.notificationCount() == 0) {
                    binding.rvNotifications.visibility = View.GONE
                    binding.stateLayout.visibility = View.VISIBLE
                }
            }

            override fun onChildDraw(
                c: Canvas,
                recyclerView: RecyclerView,
                viewHolder: RecyclerView.ViewHolder,
                dX: Float,
                dY: Float,
                actionState: Int,
                isCurrentlyActive: Boolean,
            ) {
                viewHolder.itemView.alpha = 1f - (Math.abs(dX) / viewHolder.itemView.width.coerceAtLeast(1))
                super.onChildDraw(c, recyclerView, viewHolder, dX, dY, actionState, isCurrentlyActive)
            }
        }
        ItemTouchHelper(callback).attachToRecyclerView(binding.rvNotifications)
    }
}
