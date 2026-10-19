import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.lang.reflect.Method;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;

/** Loopback-only clock exchange, reached through adb forward. No camera access. */
public final class ClockProbe {
    public static void main(String[] args) throws Exception {
        Method boot = Class.forName("android.os.SystemClock").getMethod("elapsedRealtimeNanos");
        try (ServerSocket server = new ServerSocket(Integer.parseInt(args[0]), 1,
                InetAddress.getByName("127.0.0.1"))) {
            System.out.println("CLOCK_READY");
            while (true) {
                try (Socket socket = server.accept()) {
                    socket.setTcpNoDelay(true);
                    DataInputStream input = new DataInputStream(socket.getInputStream());
                    DataOutputStream output = new DataOutputStream(socket.getOutputStream());
                    while (true) {
                        long token = input.readLong();
                        long received = (Long) boot.invoke(null);
                        long monotonic = System.nanoTime();
                        long sent = (Long) boot.invoke(null);
                        output.writeLong(token);
                        output.writeLong(received);
                        output.writeLong(sent);
                        output.writeLong(monotonic);
                        output.flush();
                    }
                } catch (java.io.EOFException | java.net.SocketException ended) {
                    // A new benchmark opens a new connection.
                }
            }
        }
    }
}
