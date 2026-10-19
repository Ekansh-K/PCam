package dev.pcam.app;

import android.content.Context;
import android.os.Build;
import android.provider.Settings;

final class DeviceName {
    static String read(Context context) {
        try {
            String name = Settings.Global.getString(context.getContentResolver(), "device_name");
            if (name != null && !name.trim().isEmpty()) return name.trim();
        } catch (SecurityException unavailable) { }
        return Build.MODEL;
    }
}
