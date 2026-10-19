package com.genymobile.scrcpy;

import android.os.SystemClock;
import java.io.BufferedWriter;
import java.io.FileWriter;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;

/** Diagnostic events only. Never writes image or encoded video data. */
public final class PcamTrace {
    private static final ArrayBlockingQueue<String> QUEUE = new ArrayBlockingQueue<>(4096);
    private static final AtomicLong DROPPED = new AtomicLong();
    static {
        Thread writer = new Thread(() -> {
            try (BufferedWriter file = new BufferedWriter(new FileWriter("/data/local/tmp/pcam-trace.csv"))) {
                file.write("event,pts_ns,event_boot_ns,frame_id,dropped_events\n");
                while (true) {
                    String line = QUEUE.poll(10, TimeUnit.MILLISECONDS);
                    if (line != null) file.write(line);
                    if (QUEUE.isEmpty()) file.flush();
                }
            } catch (Exception failure) {
                failure.printStackTrace();
            }
        }, "pcam-trace");
        writer.setDaemon(true);
        writer.start();
    }
    public static void event(String event, long ptsNs, long frameId) {
        long now = SystemClock.elapsedRealtimeNanos();
        if (!QUEUE.offer(event + "," + ptsNs + "," + now + "," + frameId + "," + DROPPED.get() + "\n")) {
            DROPPED.incrementAndGet();
        }
    }
}
