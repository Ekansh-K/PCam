import dev.pcam.app.H264Rtp;
import java.io.*;
import java.nio.ByteBuffer;

public final class GrayPacketFixture {
    public static void main(String[] args) throws Exception {
        byte[] data = new byte[8 + 320 * 180];
        ByteBuffer.wrap(data).putInt(0x47524159).putShort((short) 320).putShort((short) 180);
        for (int i = 8; i < data.length; i++) data[i] = (byte) i;
        try (DataOutputStream out = new DataOutputStream(new FileOutputStream(args[0]))) {
            for (byte[] packet : new H264Rtp(42, 65534).grayPackets(data, 0, 50_000_000_000_000L, 50_000_000_001_000L)) {
                out.writeInt(packet.length); out.write(packet);
            }
        }
    }
}
