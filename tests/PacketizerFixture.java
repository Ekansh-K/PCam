import dev.pcam.app.H264Rtp;
import java.io.*;
import java.nio.file.*;
import java.util.*;

public final class PacketizerFixture {
    public static void main(String[] args) throws Exception {
        byte[] large = new byte[5000]; Arrays.fill(large, (byte) 0x55); large[0] = 0x65;
        List<byte[]> nals = Arrays.asList(new byte[]{0x67, 0x42, 0, 0x1f}, new byte[]{0x68, 1, 2}, large);
        H264Rtp packetizer = new H264Rtp(0x12345678, 65534);
        long pts = 50_000_000_000_000L;
        try (DataOutputStream out = new DataOutputStream(new FileOutputStream(args[0]))) {
            for (byte[] packet : packetizer.packets(nals, 42, pts, pts + 3_000_000)) {
                out.writeInt(packet.length); out.write(packet);
            }
        }
        byte[] annexB = {0, 0, 0, 1, 0x67, 1, 2, 0, 0, 1, 0x68, 3};
        if (H264Rtp.nals(annexB).size() != 2) throw new AssertionError("Annex B parsing");
        byte[] avcc = {0, 0, 0, 3, 0x65, 1, 2};
        if (H264Rtp.nals(avcc).get(0).length != 3) throw new AssertionError("AVCC parsing");
        byte[] ambiguous = new byte[280]; Arrays.fill(ambiguous, (byte) 0x55);
        ambiguous[0] = 0; ambiguous[1] = 0; ambiguous[2] = 1; ambiguous[3] = 20; ambiguous[4] = 0x65;
        if (H264Rtp.nals(ambiguous).get(0).length != 276) throw new AssertionError("Ambiguous AVCC length");
    }
}
