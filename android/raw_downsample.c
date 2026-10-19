#include <jni.h>

JNIEXPORT jboolean JNICALL Java_dev_pcam_app_RawDownsample_convertNative(
    JNIEnv *env, jclass type, jobject raw, jint raw_stride, jint source_width, jint source_height,
    jint width, jint height, jobject y, jint ys, jint yp, jobject u, jint us, jint up,
    jobject v, jint vs, jint vp, jbyteArray curve)
{
    (void)type;
    int chroma = u != 0 && v != 0;
    if (width < 2 || width > 2048 || height < 2 || height > 4096 || (width & 1) || (height & 1) ||
        source_width < 4 || source_width > 16384 || (source_width & 3) || source_height < 2 || source_height > 16384 ||
        raw_stride < source_width * 5 / 4 || raw_stride > 1048576 ||
        yp < 1 || ys < width * yp || (chroma && (up < 1 || vp < 1 || us < width / 2 * up || vs < width / 2 * vp)))
        return JNI_FALSE;
    unsigned char *src = (*env)->GetDirectBufferAddress(env, raw);
    unsigned char *yd = (*env)->GetDirectBufferAddress(env, y);
    unsigned char *ud = chroma ? (*env)->GetDirectBufferAddress(env, u) : 0;
    unsigned char *vd = chroma ? (*env)->GetDirectBufferAddress(env, v) : 0;
    if (!src || !yd || (chroma && (!ud || !vd)) || (*env)->GetArrayLength(env, curve) != 256 ||
        (*env)->GetDirectBufferCapacity(env, raw) < (jlong)(source_height - 1) * raw_stride + source_width * 5 / 4 ||
        (*env)->GetDirectBufferCapacity(env, y) < (jlong)(height - 1) * ys + (width - 1) * yp + 1 ||
        (chroma && ((*env)->GetDirectBufferCapacity(env, u) < (jlong)(height / 2 - 1) * us + (width / 2 - 1) * up + 1 ||
        (*env)->GetDirectBufferCapacity(env, v) < (jlong)(height / 2 - 1) * vs + (width / 2 - 1) * vp + 1)))
        return JNI_FALSE;
    int offsets[2048];
    for (int x = 0; x < width; x++) {
        int sx = x * source_width / width;
        if (sx > source_width - 2) sx = source_width - 2;
        sx &= ~1;
        offsets[x] = sx / 4 * 5 + sx % 4 + 1;
    }
    unsigned char *lut = (*env)->GetPrimitiveArrayCritical(env, curve, 0);
    if (!lut) return JNI_FALSE;
    for (int row = 0; row < height; row++) {
        int sy = row * source_height / height;
        if (sy > source_height - 2) sy = source_height - 2;
        sy &= ~1;
        const unsigned char *input = src + sy * raw_stride;
        unsigned char *output = yd + row * ys;
        for (int x = 0; x < width; x++) output[x * yp] = lut[input[offsets[x]]];
    }
    if (chroma) for (int row = 0; row < height / 2; row++) for (int x = 0; x < width / 2; x++) {
        ud[row * us + x * up] = 128;
        vd[row * vs + x * vp] = 128;
    }
    (*env)->ReleasePrimitiveArrayCritical(env, curve, lut, JNI_ABORT);
    return JNI_TRUE;
}
