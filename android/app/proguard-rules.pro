# Retrofit
-keepattributes Signature
-keepattributes Exceptions
-keepclassmembernames,allowobfuscation interface * {
    @retrofit2.http.* <methods>;
}
-dontwarn retrofit2.**
-keep class retrofit2.** { *; }

# OkHttp
-dontwarn okhttp3.**
-dontwarn okio.**

# Gson — keep all model classes for serialization
-keepattributes *Annotation*
-keep class com.google.gson.stream.** { *; }
-keep class com.plantdiseases.app.data.model.** { *; }
-keep class com.google.gson.reflect.TypeToken { *; }
-keep class * extends com.google.gson.reflect.TypeToken

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
