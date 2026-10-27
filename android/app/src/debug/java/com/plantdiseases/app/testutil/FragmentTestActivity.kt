package com.plantdiseases.app.testutil

import androidx.appcompat.app.AppCompatActivity
import dagger.hilt.android.AndroidEntryPoint

/** Debug-only host for instrumented tests of injected fragments. */
@AndroidEntryPoint
class FragmentTestActivity : AppCompatActivity()
