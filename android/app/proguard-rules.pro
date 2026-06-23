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

# Ktor (new HTTP client) pulls in slf4j-api transitively. SLF4J 1.x's
# LoggerFactory references an impl binder (org.slf4j.impl.StaticLoggerBinder)
# that isn't on the classpath — no SLF4J backend is bundled and Ktor's own
# logging is unused. R8 full mode treats the missing class as a hard error, so
# silence it. (The @Serializable model classes and their generated serializers
# are already kept by the com.plantdiseases.app.data.model.** rule below.)
-dontwarn org.slf4j.**

# Gson — keep all model classes for serialization.
#
# AGP 8 runs R8 in "full mode", which is more aggressive than the legacy
# ProGuard-compat mode: it strips generic signatures, enum metadata and
# annotation attributes unless they are explicitly kept. Gson reads all
# three reflectively (Signature for List<T>/Map<K,V> field generics,
# EnumConstantName + the enum's values() for @SerializedName-less enums,
# and the @SerializedName annotations themselves), so pin them here.
-keepattributes *Annotation*
-keepattributes Signature, InnerClasses, EnumConstantName, AnnotationDefault
-keepattributes RuntimeVisibleAnnotations, RuntimeVisibleParameterAnnotations
-keep class com.google.gson.stream.** { *; }
-keep class com.plantdiseases.app.data.model.** { *; }
-keep class com.google.gson.reflect.TypeToken { *; }
-keep class * extends com.google.gson.reflect.TypeToken

# Belt-and-suspenders for any @SerializedName field on a model that R8 might
# otherwise rename — without the keep, Gson would silently deserialise null
# into the renamed field (a field-drop bug that only surfaces in the minified
# release build, never in debug).
-keepclassmembers,allowobfuscation class * {
    @com.google.gson.annotations.SerializedName <fields>;
}

# Gson serialises enums by their constant names; full-mode R8 would rename
# the constants and break the value↔name round-trip (e.g. GuideCategory).
-keepclassmembers enum * {
    <fields>;
    public static **[] values();
    public static ** valueOf(java.lang.String);
}

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

# TensorFlow Lite / LiteRT — the on-device Aqua classifier (AquaClassifier)
# reaches the interpreter through JNI, and the runtime uses reflection
# internally for optional delegates (GPU/NNAPI). R8 has no view into the native
# side, so keep the public API surface and silence warnings about the optional
# delegate classes this build doesn't bundle. Without this the release build
# can strip a class the native layer expects and the offline scan crashes only
# in the minified APK. LiteRT retains the org.tensorflow.lite Interpreter API
# but also ships classes under com.google.ai.edge.litert, so keep both.
-keep class org.tensorflow.lite.** { *; }
-dontwarn org.tensorflow.lite.**
-keep class com.google.ai.edge.litert.** { *; }
-dontwarn com.google.ai.edge.litert.**

# Firebase Messaging ships its own consumer ProGuard rules, but it is
# initialised manually here (no google-services plugin), so silence any
# stray warnings from the optional auto-init classes we remove in the manifest.
-dontwarn com.google.firebase.**
