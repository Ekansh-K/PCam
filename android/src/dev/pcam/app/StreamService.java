package dev.pcam.app;

import android.app.*;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.IBinder;

public final class StreamService extends Service {
    @Override public void onCreate() {
        super.onCreate();
        NotificationManager manager = getSystemService(NotificationManager.class);
        manager.createNotificationChannel(new NotificationChannel("stream", "Camera streaming", NotificationManager.IMPORTANCE_LOW));
        PendingIntent controls = PendingIntent.getActivity(this, 0, new Intent(this, MainActivity.class),
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        Notification notification = new Notification.Builder(this, "stream")
                .setContentTitle("PCam camera is active").setContentText("Open PCam to disconnect.")
                .setSmallIcon(android.R.drawable.presence_video_online).setOngoing(true).setContentIntent(controls).build();
        startForeground(1, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA);
    }
    @Override public IBinder onBind(Intent intent) { return null; }
    @Override public int onStartCommand(Intent intent, int flags, int startId) { return START_NOT_STICKY; }
}
