package dev.pcam.app;

final class OutputSettings {
    static boolean allows(int width, int height) {
        int shortSide = Math.min(width, height), longSide = Math.max(width, height);
        return (shortSide == 480 && longSide == 960) || (shortSide == 720 && longSide == 1600);
    }
}
