package dev.pcam.app;

import java.nio.ByteBuffer;

public final class RawFixture {
    public static void main(String[] args) {
        // 8x4 RAW10, 16-byte padded rows. Red/blue and packed low bits must not leak into green.
        ByteBuffer raw = ByteBuffer.allocateDirect(64);
        for (int row = 0; row < 4; row++) for (int group = 0; group < 2; group++) {
            int base = row * 16 + group * 5;
            raw.put(base, (byte) 200); raw.put(base + 1, (byte) (row == 0 ? 16 : 255));
            raw.put(base + 2, (byte) 40); raw.put(base + 3, (byte) (row == 0 ? 16 : 255));
            raw.put(base + 4, (byte) 255);
        }
        ByteBuffer y = ByteBuffer.allocateDirect(12), u = ByteBuffer.allocateDirect(4), v = ByteBuffer.allocateDirect(4);
        for (int i = 0; i < 12; i++) y.put(i, (byte) 99);
        for (int i = 0; i < 4; i++) { u.put(i, (byte) 99); v.put(i, (byte) 99); }
        RawDownsample converter = new RawDownsample(8, 4, 4, 2, 16);
        converter.useNative = args.length > 0;
        converter.convert(raw, 16, y, 6, 1, u, 4, 2, v, 4, 2);
        for (int x = 0; x < 4; x++) {
            if ((y.get(x) & 255) != 16 || (y.get(6 + x) & 255) != 235) throw new AssertionError("RAW packing, curve or stride");
        }
        if (y.get(4) != 99 || u.get(0) != (byte) 128 || u.get(2) != (byte) 128 || u.get(3) != 99 || v.get(3) != 99)
            throw new AssertionError("Plane stride or padding");
        ByteBuffer luma = ByteBuffer.allocateDirect(8);
        converter.convert(raw, 16, luma, 4, 1, null, 0, 0, null, 0, 0);
        for (int x = 0; x < 4; x++) if ((luma.get(x) & 255) != 16 || (luma.get(x + 4) & 255) != 235)
            throw new AssertionError("Luma-only path");
        System.out.println("RAW10 green sampling, black subtraction, range, and plane strides verified.");
    }
}
