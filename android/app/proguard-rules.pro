# Retrofit
-keepattributes Signature
-keepattributes Exceptions
-keepclassmembernames,allowobfuscation interface * {
    @retrofit2.http.* <methods>;
}
-dontwarn retrofit2.**
-keep class retrofit2.** { *; }

# Our Retrofit interface — R8 sometimes drops the annotations on
# user interfaces even when the generic keepclassmembernames rule
# above matches. Pin it explicitly.
-keep interface com.plantdiseases.app.data.remote.ApiService { *; }

# OkHttp
-dontwarn okhttp3.**
-dontwarn okio.**

# Gson — keep all model classes for serialization
-keepattributes *Annotation*
-keep class com.google.gson.stream.** { *; }
-keep class com.plantdiseases.app.data.model.** { *; }
-keep class com.google.gson.reflect.TypeToken { *; }
-keep class * extends com.google.gson.reflect.TypeToken

# Repository exceptions are returned inside Result<> and matched by
# class in callers' when-blocks. Keep them so R8 minification doesn't
# collapse the sealed hierarchy.
-keep class com.plantdiseases.app.data.repository.** { *; }

# Room
-keep class * extends androidx.room.RoomDatabase
-dontwarn androidx.room.paging.**

# Glide
-keep public class * implements com.bumptech.glide.module.GlideModule
-keep class * extends com.bumptech.glide.module.AppGlideModule { <init>(...); }

# Lottie
-dontwarn com.airbnb.lottie.**
-keep class com.airbnb.lottie.** { *; }
-keep class com.airbnb.lottie.model.** { *; }
-keep class com.airbnb.lottie.animation.** { *; }
