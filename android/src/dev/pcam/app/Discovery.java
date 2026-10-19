package dev.pcam.app;

import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.function.BooleanSupplier;
import org.json.JSONObject;

/** Answers local discovery probes without publishing pairing credentials. */
final class Discovery implements AutoCloseable {
    private final DatagramSocket socket;
    private volatile boolean closed;

    Discovery(String deviceId, String name, int controlPort, BooleanSupplier busy) throws Exception {
        socket = new DatagramSocket(5005);
        Thread worker = new Thread(() -> {
            while (!closed) {
                try {
                    DatagramPacket request = new DatagramPacket(new byte[256], 256);
                    socket.receive(request);
                    String message = new String(request.getData(), 0, request.getLength(), StandardCharsets.UTF_8);
                    if (!message.matches("PCAM_DISCOVER/1 [a-f0-9]{24}")) continue;
                    byte[] response = new JSONObject().put("protocol", "PCAM/1").put("nonce", message.substring(16))
                            .put("id", deviceId).put("name", name).put("control_port", controlPort)
                            .put("busy", busy.getAsBoolean()).toString().getBytes(StandardCharsets.UTF_8);
                    socket.send(new DatagramPacket(response, response.length, request.getAddress(), request.getPort()));
                } catch (Exception error) {
                    if (!closed) android.util.Log.w("PCam", "Discovery request failed", error);
                }
            }
        }, "pcam-discovery");
        worker.setDaemon(true); worker.start();
    }
    @Override public void close() { closed = true; socket.close(); }
}
