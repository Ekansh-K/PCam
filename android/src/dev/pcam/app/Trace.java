package dev.pcam.app;

import android.content.Context;
import android.os.SystemClock;
import java.io.BufferedWriter;
import java.io.File;
import java.io.FileWriter;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;

final class Trace implements AutoCloseable {
    private final ArrayBlockingQueue<String> queue = new ArrayBlockingQueue<>(8192);
    private final AtomicLong dropped = new AtomicLong();
    private volatile boolean running = true;
    private final Thread writer;

    Trace(Context context, boolean enabled) {
        if (!enabled) { writer = null; return; }
        writer = new Thread(() -> {
            try (BufferedWriter file = new BufferedWriter(new FileWriter(new File(context.getFilesDir(), "trace.csv")))) {
                file.write("event,pts_ns,time_ns,frame_id,value\n");
                while (running || !queue.isEmpty()) {
                    String line = queue.poll(10, TimeUnit.MILLISECONDS);
                    if (line != null) file.write(line);
                    if (queue.isEmpty()) file.flush();
                }
                file.write("trace_dropped,0,0,0," + dropped.get() + "\n");
            } catch (Exception error) { android.util.Log.e("PCam", "Trace writer", error); }
        }, "pcam-trace");
        writer.start();
    }
    void event(String event, long pts, long frame, long value) {
        if (writer == null) return;
        long now = SystemClock.elapsedRealtimeNanos();
        if (!queue.offer(event + "," + pts + "," + now + "," + frame + "," + value + "\n")) dropped.incrementAndGet();
    }
    @Override public void close() {
        if (writer == null) return;
        running = false;
        try { writer.join(2000); } catch (InterruptedException error) { Thread.currentThread().interrupt(); }
    }
}
