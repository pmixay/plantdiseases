package com.plantdiseases.app

internal enum class OpinionWorkAction {
    CANCEL,
    SCHEDULE,
    NONE,
}

internal fun opinionWorkAction(
    accountUiEnabled: Boolean,
    notificationsEnabled: Boolean,
): OpinionWorkAction = when {
    !accountUiEnabled -> OpinionWorkAction.CANCEL
    notificationsEnabled -> OpinionWorkAction.SCHEDULE
    else -> OpinionWorkAction.NONE
}
