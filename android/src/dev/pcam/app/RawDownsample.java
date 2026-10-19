package dev.pcam.app;

import java.nio.ByteBuffer;

/** Experimental RAW10 green-channel preview. No temporal filtering or frame queue. */
final class RawDownsample {
    private final int[] columns, rows;
    private final byte[] curve = new byte[256];
    private final byte[] packedRow, outputRow, neutral;
    private final int sourceWidth, sourceHeight;
    boolean useNative;
    private static final class NativeLibrary { static { System.loadLibrary("pcam_raw"); } static void load() { } }
    private static native boolean convertNative(ByteBuffer raw, int stride, int sourceWidth, int sourceHeight, int width, int height,
        ByteBuffer y, int ys, int yp, ByteBuffer u, int us, int up, ByteBuffer v, int vs, int vp, byte[] curve);

    RawDownsample(int sourceWidth, int sourceHeight, int width, int height, int black) {
        this.sourceWidth = sourceWidth; this.sourceHeight = sourceHeight;
        columns = new int[width]; rows = new int[height];
        packedRow = new byte[sourceWidth * 5 / 4]; outputRow = new byte[width];
        neutral = new byte[width * 2]; java.util.Arrays.fill(neutral, (byte) 128);
        // BGGR and RGGB both have green at odd x, even y.
        for (int x = 0; x < width; x++) {
            int sx = Math.min(sourceWidth - 2, x * sourceWidth / width) & ~1;
            columns[x] = (sx / 4) * 5 + (sx % 4) + 1;
        }
        for (int y = 0; y < height; y++) rows[y] = Math.min(sourceHeight - 2, y * sourceHeight / height) & ~1;
        for (int i = 0; i < 256; i++)
            curve[i] = (byte) (16 + Math.round(219 * Math.sqrt(Math.max(0, i - black) / (double) (255 - black))));
    }

    void convert(ByteBuffer source, int sourceStride, ByteBuffer yPlane, int yStride, int yPixelStride,
                 ByteBuffer uPlane, int uStride, int uPixelStride, ByteBuffer vPlane, int vStride, int vPixelStride) {
        if (useNative) {
            NativeLibrary.load();
            if (!convertNative(source, sourceStride, sourceWidth, sourceHeight, columns.length, rows.length,
                yPlane, yStride, yPixelStride, uPlane, uStride, uPixelStride, vPlane, vStride, vPixelStride, curve))
                throw new IllegalArgumentException("Unsupported native RAW buffer layout");
            return;
        }
        for (int y = 0; y < rows.length; y++) {
            int input = rows[y] * sourceStride, output = y * yStride;
            source.position(input); source.get(packedRow);
            for (int x = 0; x < columns.length; x++)
                outputRow[x] = curve[packedRow[columns[x]] & 255];
            if (yPixelStride == 1) { yPlane.position(output); yPlane.put(outputRow); }
            else for (int x = 0; x < columns.length; x++) yPlane.put(output + x * yPixelStride, outputRow[x]);
        }
        if (uPlane == null && vPlane == null) return;
        for (int y = 0; y < rows.length / 2; y++) {
            uPlane.position(y * uStride); uPlane.put(neutral, 0, (columns.length / 2 - 1) * uPixelStride + 1);
            vPlane.position(y * vStride); vPlane.put(neutral, 0, (columns.length / 2 - 1) * vPixelStride + 1);
        }
    }
}
