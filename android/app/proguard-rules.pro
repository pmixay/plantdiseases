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

# kotlinx.serialization models (wire, Room field JSON and the response cache).
# The library ships its own R8 rules for generated serializers; the models are
# also kept whole, and annotations and generic signatures stay, so full-mode
# R8 (AGP 8+) cannot strip what reflection-free serializers still reference.
-keepattributes *Annotation*
-keepattributes Signature, InnerClasses, EnumConstantName, AnnotationDefault
-keepattributes RuntimeVisibleAnnotations, RuntimeVisibleParameterAnnotations
-keep class com.plantdiseases.app.data.model.** { *; }

# Enums looked up by name (valueOf on stored strings): full-mode R8 would
# otherwise rename the constants and break the name round-trip.
-keepclassmembers enum * {
    <fields>;
    public static **[] values();
    public static ** valueOf(java.lang.String);
}

# Repository exceptions are returned inside Result<> and matched by
# class in callers' when-blocks. Keep them so R8 minification doesn't
# collapse the sealed hierarchy.
-keep class com.plantdiseases.app.data.repository.** { *; }

# Room. Room loads the generated `<Database>_Impl` by name and calls its
# no-arg constructor, so the same AGP 9 strict-full-mode note as above
# applies — keep the constructor explicitly, not just the class.
-keep class * extends androidx.room.RoomDatabase { <init>(); }
-dontwarn androidx.room.paging.**

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
