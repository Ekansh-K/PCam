package dev.pcam.app;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.ArrayList;
import java.util.List;

/** RFC 6184 single-NAL/FU-A packets, with a timestamp extension ignored by standard depayloaders. */
public final class H264Rtp {
    public static final int MTU = 1200;
    public static final int HEADER = 48;
    private int sequence;
    private final int ssrc;

    public H264Rtp(int ssrc, int sequence) { this.ssrc = ssrc; this.sequence = sequence; }

    public static List<byte[]> nals(byte[] data) {
        List<byte[]> result = new ArrayList<>();
        // Length-prefixed sizes such as 0x00000114 also contain a three-byte
        // Annex B prefix. Accept AVCC only when every length consumes the input.
        boolean fourByteStart = data.length >= 4 && data[0] == 0 && data[1] == 0 && data[2] == 0 && data[3] == 1;
        if (!fourByteStart) {
            int offset = 0;
            while (offset + 4 <= data.length) {
                int length = ByteBuffer.wrap(data, offset, 4).getInt();
                if (length <= 0 || length > data.length - offset - 4) break;
                result.add(java.util.Arrays.copyOfRange(data, offset + 4, offset + 4 + length));
                offset += 4 + length;
            }
            if (offset == data.length && !result.isEmpty()) return result;
            result.clear();
        }
        int first = start(data, 0);
        if (first >= 0 && first <= 3) {
            int position = first;
            while (position >= 0) {
                int prefix = data[position + 2] == 1 ? 3 : 4;
                int next = start(data, position + prefix);
                int end = next < 0 ? data.length : next;
                if (end > position + prefix)
                    result.add(java.util.Arrays.copyOfRange(data, position + prefix, end));
                position = next;
            }
        } else {
            if (data.length == 0 || (data[0] & 31) == 0 || (data[0] & 31) >= 24)
                throw new IllegalArgumentException("Invalid H.264 NAL");
            result.add(data);
        }
        return result;
    }

    private static int start(byte[] data, int from) {
        for (int i = from; i + 2 < data.length; i++) {
            if (data[i] == 0 && data[i + 1] == 0 && (data[i + 2] == 1 ||
                    (i + 3 < data.length && data[i + 2] == 0 && data[i + 3] == 1))) return i;
        }
        return -1;
    }

    public List<byte[]> packets(List<byte[]> nals, int frameId, long ptsNs, long encodedNs) {
        List<byte[]> payloads = new ArrayList<>();
        for (byte[] nal : nals) {
            if (nal.length <= MTU - HEADER) payloads.add(nal);
            else {
                int position = 1;
                while (position < nal.length) {
                    int size = Math.min(MTU - HEADER - 2, nal.length - position);
                    byte[] fragment = new byte[size + 2];
                    fragment[0] = (byte) ((nal[0] & 0xe0) | 28);
                    fragment[1] = (byte) ((nal[0] & 31) | (position == 1 ? 0x80 : 0)
                            | (position + size == nal.length ? 0x40 : 0));
                    System.arraycopy(nal, position, fragment, 2, size);
                    payloads.add(fragment);
                    position += size;
                }
            }
        }
        return packetize(payloads, frameId, ptsNs, encodedNs, 96);
    }

    public List<byte[]> grayPackets(byte[] image, int frameId, long ptsNs, long readyNs) {
        List<byte[]> payloads = new ArrayList<>();
        for (int offset = 0; offset < image.length; offset += MTU - HEADER)
            payloads.add(java.util.Arrays.copyOfRange(image, offset, Math.min(image.length, offset + MTU - HEADER)));
        return packetize(payloads, frameId, ptsNs, readyNs, 97);
    }

    private List<byte[]> packetize(List<byte[]> payloads, int frameId, long ptsNs, long encodedNs, int type) {
        if (payloads.size() > 65535) throw new IllegalArgumentException("Access unit too large");
        List<byte[]> result = new ArrayList<>();
        for (int index = 0; index < payloads.size(); index++) {
            byte[] payload = payloads.get(index);
            ByteBuffer packet = ByteBuffer.allocate(HEADER + payload.length).order(ByteOrder.BIG_ENDIAN);
            packet.put((byte) 0x90); // RTP v2 and extension present
            packet.put((byte) (type | (index == payloads.size() - 1 ? 0x80 : 0)));
            packet.putShort((short) sequence++);
            packet.putInt((int) (ptsNs / 1000 * 90 / 1000));
            packet.putInt(ssrc);
            packet.putShort((short) 0x5043).putShort((short) 8);
            packet.putInt(frameId).putLong(ptsNs).putLong(encodedNs).putLong(0);
            packet.putShort((short) index).putShort((short) payloads.size());
            packet.put(payload);
            result.add(packet.array());
        }
        return result;
    }
}
