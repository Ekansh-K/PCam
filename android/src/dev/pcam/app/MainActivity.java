package dev.pcam.app;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.pm.PackageManager;
import android.hardware.camera2.*;
import android.hardware.camera2.params.*;
import android.media.*;
import android.net.wifi.WifiManager;
import android.os.*;
import android.os.Process;
import android.util.Range;
import android.util.Size;
import android.view.Surface;
import android.view.WindowManager;
import android.view.Gravity;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.widget.*;
import java.io.*;
import java.net.*;
import java.nio.*;
import java.security.SecureRandom;
import java.util.*;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import org.json.JSONObject;

public final class MainActivity extends Activity {
    private HandlerThread cameraThread, codecThread;
    private Handler cameraHandler, codecHandler;
    private CameraDevice camera;
    private CameraCaptureSession session;
    private MediaCodec encoder;
    private Surface encoderSurface;
    private Surface cameraSurface;
    private ImageReader bufferProbe;
    private final ArrayDeque<Integer> rawInputs = new ArrayDeque<>();
    private boolean rawStream;
    private boolean rawUncompressed;
    private WifiManager.WifiLock wifiLock;
    private ServerSocket controlServer;
    private volatile Socket controlClient;
    private DatagramSocket videoSocket;
    private Thread senderThread, controlThread;
    private final ArrayBlockingQueue<Unit> pending = new ArrayBlockingQueue<>(4);
    private volatile boolean running, closing, needsIdr, stopping;
    private volatile boolean metricsEnabled;
    private final ArrayList<byte[]> configNals = new ArrayList<>();
    private Trace trace;
    private TextView status;
    private Button disconnectButton, scanButton;
    private TextView computerDetails;
    private String computerName = "", connectionMode = "";
    private String host, cameraId;
    private volatile String token;
    private int width, height, fps, bitrate, videoPort, controlPort, frameId;
    private boolean highSpeed, fastProcessing;
    private volatile int[] outputSize;
    private long exposureNs;
    private CameraCharacteristics characteristics;
    private String codecName;
    private final JSONObject details = new JSONObject();
    private DirectConnection direct;
    private Discovery discovery;

    private static final class Unit {
        final List<byte[]> nals;
        final int id;
        final long pts, encoded;
        Unit(List<byte[]> nals, int id, long pts, long encoded) {
            this.nals = nals; this.id = id; this.pts = pts; this.encoded = encoded;
        }
    }

    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        setShowWhenLocked(true); setTurnScreenOn(true);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        host = getIntent().getStringExtra("host");
        if (host == null) host = "";
        cameraId = getIntent().getStringExtra("camera");
        if (cameraId == null) cameraId = "0";
        token = getIntent().getStringExtra("token");
        if (token == null) token = UUID.randomUUID().toString().replace("-", "");
        width = getIntent().getIntExtra("width", 1280);
        height = getIntent().getIntExtra("height", 720);
        fps = getIntent().getIntExtra("fps", 120);
        bitrate = getIntent().getIntExtra("bitrate", 12_000_000);
        videoPort = getIntent().getIntExtra("video_port", 5004);
        controlPort = getIntent().getIntExtra("control_port", 27185);
        highSpeed = getIntent().getBooleanExtra("high_speed", fps >= 120);
        fastProcessing = getIntent().getBooleanExtra("fast_processing", true);
        exposureNs = getIntent().getLongExtra("exposure_ns", 0);
        metricsEnabled = getIntent().getBooleanExtra("trace_enabled", false);
        rawStream = getIntent().getBooleanExtra("trial_raw_stream", false);
        rawUncompressed = getIntent().getBooleanExtra("trial_raw_uncompressed", false);
        if (rawUncompressed) rawStream = true;
        android.content.SharedPreferences savedSettings = getSharedPreferences("settings", MODE_PRIVATE);
        boolean large = savedSettings.getInt("output_preset", 0) == 1;
        int savedWidth = savedSettings.getInt("output_width", large ? 720 : 480);
        int savedHeight = savedSettings.getInt("output_height", large ? 1600 : 960);
        outputSize = OutputSettings.allows(savedWidth, savedHeight) ? new int[]{savedWidth, savedHeight} : new int[]{480, 960};
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setBackgroundColor(Color.rgb(8, 8, 9));
        getWindow().setStatusBarColor(Color.rgb(8, 8, 9));
        getWindow().setNavigationBarColor(Color.rgb(8, 8, 9));
        getWindow().getDecorView().setSystemUiVisibility(0);
        int padding = (int) (28 * getResources().getDisplayMetrics().density);
        layout.setPadding(padding, padding, padding, padding);
        TextView title = new TextView(this); title.setText("PCam"); title.setTextSize(26);
        title.setTextColor(Color.rgb(240, 240, 242)); title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        layout.addView(title);
        LinearLayout center = new LinearLayout(this); center.setGravity(Gravity.CENTER); center.setOrientation(LinearLayout.VERTICAL);
        layout.addView(center, new LinearLayout.LayoutParams(-1, 0, 1));
        status = new TextView(this); status.setText("Ready"); status.setTextSize(24);
        status.setGravity(Gravity.CENTER); status.setTextColor(Color.rgb(240, 240, 242)); center.addView(status);
        computerDetails = new TextView(this); computerDetails.setGravity(Gravity.CENTER); computerDetails.setTextSize(15);
        computerDetails.setTextColor(Color.rgb(140,140,150)); computerDetails.setPadding(0,padding/2,0,0); center.addView(computerDetails);
        scanButton = new Button(this); scanButton.setText("Scan pairing QR"); scanButton.setAllCaps(false);
        scanButton.setOnClickListener(view -> {
            if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
                requestPermissions(new String[]{Manifest.permission.CAMERA},1); return;
            }
            startActivityForResult(new android.content.Intent(this,QrScanner.class),100);
        });
        layout.addView(scanButton);
        disconnectButton = new Button(this); disconnectButton.setText("Disconnect"); disconnectButton.setAllCaps(false);
        disconnectButton.setTextSize(16); disconnectButton.setTextColor(Color.rgb(240, 240, 242));
        GradientDrawable background = new GradientDrawable(); background.setColor(Color.rgb(24, 24, 28));
        background.setCornerRadius(14 * getResources().getDisplayMetrics().density);
        background.setStroke((int) getResources().getDisplayMetrics().density, Color.rgb(44, 44, 50));
        disconnectButton.setBackground(background);
        disconnectButton.setOnClickListener(view -> disconnect());
        layout.addView(disconnectButton, new LinearLayout.LayoutParams(-1, (int) (56 * getResources().getDisplayMetrics().density)));
        direct = new DirectConnection(this, message -> {
            android.util.Log.i("PCam", message);
            if (!running) status.setText(message.startsWith("Connecting") ? "Connecting" : message.startsWith("Allow") ? "Allow nearby devices" : "Ready");
            updateDisconnect();
        }, peer -> {
            if (!running) host = peer;
        });
        updateDisconnect();
        if (getIntent().getBooleanExtra("direct_connect", false)) layout.post(this::joinDirect);
        setContentView(layout);
        cameraThread = new HandlerThread("pcam-camera", Process.THREAD_PRIORITY_DISPLAY); cameraThread.start();
        codecThread = new HandlerThread("pcam-encoder", Process.THREAD_PRIORITY_DISPLAY); codecThread.start();
        cameraHandler = new Handler(cameraThread.getLooper()); codecHandler = new Handler(codecThread.getLooper());
        if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED)
            requestPermissions(new String[]{Manifest.permission.CAMERA}, 1);
        try {
            characteristics = ((CameraManager) getSystemService(CAMERA_SERVICE)).getCameraCharacteristics(cameraId);
            details.put("public_camera_ids", Arrays.toString(((CameraManager) getSystemService(CAMERA_SERVICE)).getCameraIdList()));
            details.put("name", DeviceName.read(this)).put("model", Build.MODEL).put("sdk", Build.VERSION.SDK_INT).put("camera", cameraId)
                   .put("timestamp_source", characteristics.get(CameraCharacteristics.SENSOR_INFO_TIMESTAMP_SOURCE))
                   .put("requested_fps", fps).put("width", width).put("height", height).put("high_speed", highSpeed);
            details.put("pipeline_max_depth", characteristics.get(CameraCharacteristics.REQUEST_PIPELINE_MAX_DEPTH));
            details.put("video_payload", rawUncompressed ? "GRAY8" : "H264");
            if (Build.VERSION.SDK_INT >= 34)
                details.put("readout_timestamp_support", characteristics.get(CameraCharacteristics.SENSOR_READOUT_TIMESTAMP));
            JSONObject controls = new JSONObject();
            List<CaptureRequest.Key<?>> sessionKeys = characteristics.getAvailableSessionKeys();
            for (CaptureRequest.Key<?> key : characteristics.getAvailableCaptureRequestKeys()) {
                if (key.getName().matches("(?i).*(sensor.?mode|earlypcr|numPCR|RawCbSource|IdealRAW|dynamicFPS|disableFPS|streamBasedFPS).*"))
                    controls.put(key.getName(), sessionKeys != null && sessionKeys.contains(key) ? "session_and_request" : "request");
            }
            details.put("trial_available_controls", controls);
            controlServer = new ServerSocket(controlPort);
            controlThread = new Thread(this::controlLoop, "pcam-control"); controlThread.start();
            String deviceId = getSharedPreferences("settings", MODE_PRIVATE).getString("device_id", null);
            if (deviceId == null) {
                deviceId = UUID.randomUUID().toString();
                getSharedPreferences("settings", MODE_PRIVATE).edit().putString("device_id", deviceId).apply();
            }
            discovery = new Discovery(deviceId, DeviceName.read(this), controlPort, () -> running || controlClient != null);
        } catch (Exception error) { fail(error); }
    }

    private void updateDisconnect() {
        boolean active = running || controlClient != null || (direct != null && direct.connected());
        disconnectButton.setEnabled(active); disconnectButton.setAlpha(active ? 1f : .4f);
        scanButton.setVisibility(active ? android.view.View.GONE : android.view.View.VISIBLE);
        computerDetails.setText(active && !computerName.isEmpty() ? computerName + "\n" + connectionMode : "");
    }
    @Override protected void onActivityResult(int request, int result, android.content.Intent data) {
        super.onActivityResult(request,result,data);
        if (request != 100 || result != RESULT_OK || data == null) return;
        try {
            JSONObject qr = new JSONObject(data.getStringExtra("pairing"));
            long now = System.currentTimeMillis()/1000;
            if (!"PCAM-DIRECT/1".equals(qr.getString("protocol")) || !"172.29.67.1".equals(qr.getString("host"))
                    || qr.getLong("expires") < now || qr.getLong("expires") > now + 600
                    || !qr.getString("token").matches("[a-f0-9]{32}") || !qr.getString("ssid").startsWith("DIRECT-")
                    || qr.getString("passphrase").length() < 8 || qr.getString("passphrase").length() > 63)
                throw new IOException("Invalid or expired pairing QR");
            token = qr.getString("token"); host = qr.getString("host");
            computerName = qr.optString("computer","Laptop"); connectionMode = "Wi-Fi Direct";
            getIntent().putExtra("direct_ssid", qr.getString("ssid")).putExtra("direct_passphrase",qr.getString("passphrase"))
                    .putExtra("direct_device",qr.getString("device")).putExtra("direct_frequency",qr.getInt("frequency"));
            joinDirect();
        } catch (Exception error) { status.setText("QR expired. Show a new code on the PC."); }
    }
    private void joinDirect() {
        String ssid = getIntent().getStringExtra("direct_ssid");
        if (ssid != null) direct.join(ssid, getIntent().getStringExtra("direct_passphrase"),
                getIntent().getStringExtra("direct_device"), getIntent().getIntExtra("direct_frequency", 5180));
    }
    @Override public void onRequestPermissionsResult(int request, String[] permissions, int[] grants) {
        super.onRequestPermissionsResult(request, permissions, grants);
        if (grants.length == 0 || grants[0] != PackageManager.PERMISSION_GRANTED) return;
        if (request == 2) joinDirect();
        if (request == 1 && controlClient != null) start();
    }
    private void disconnect() {
        stopCamera();
        try { if (controlClient != null) controlClient.close(); } catch (IOException ignored) { }
        direct.disconnect(); status.setText("Ready"); updateDisconnect();
    }
    private void state(String text) {
        android.util.Log.i("PCam", text);
        runOnUiThread(() -> {
            status.setText(text.startsWith("Error:") ? "Camera unavailable" : running ?
                    (text.startsWith("Streaming") ? "Connected" : "Connecting") : "Ready");
            updateDisconnect();
        });
    }
    private void fail(Throwable error) { android.util.Log.e("PCam", "Streaming failed", error); state("Error: " + error); }

    private String connectionDetails() throws IOException {
        try {
            // Keep UI-owned settings out of the camera threads' details object.
            JSONObject current = new JSONObject(details.toString());
            int[] selected = outputSize;
            current.put("output_width", selected[0]).put("output_height", selected[1]);
            if (direct != null) current.put("wifi_direct", direct.details());
            return current.toString();
        } catch (org.json.JSONException error) { throw new IOException(error); }
    }

    private void controlLoop() {
        while (!closing) {
            boolean authenticated = false;
            try (Socket socket = controlServer.accept()) {
                socket.setSoTimeout(10000); socket.setTcpNoDelay(true);
                DataInputStream input = new DataInputStream(socket.getInputStream());
                DataOutputStream output = new DataOutputStream(socket.getOutputStream());
                String offered = input.readUTF();
                if ("PCAM/PAIR".equals(offered)) {
                    final boolean[] approved = {false};
                    final AlertDialog[] dialog = new AlertDialog[1];
                    CountDownLatch answered = new CountDownLatch(1);
                    runOnUiThread(() -> {
                        if (closing || isFinishing()) { answered.countDown(); return; }
                        dialog[0] = new AlertDialog.Builder(this).setTitle("Connect this laptop?")
                                .setMessage("Allow " + socket.getInetAddress().getHostAddress() + " to use your PCam camera over shared Wi-Fi?")
                                .setPositiveButton("Connect", (view, button) -> { approved[0] = true; answered.countDown(); })
                                .setNegativeButton("Cancel", (view, button) -> answered.countDown())
                                .setOnCancelListener(view -> answered.countDown()).show();
                    });
                    try { answered.await(30, TimeUnit.SECONDS); }
                    catch (InterruptedException ended) { Thread.currentThread().interrupt(); }
                    runOnUiThread(() -> { if (dialog[0] != null) dialog[0].dismiss(); });
                    if (approved[0] && !closing) {
                        output.writeUTF("PCAM/PAIR/1"); output.writeUTF(token); output.flush();
                    }
                    continue;
                }
                if (!token.equals(offered)) continue;
                authenticated = true;
                socket.setSoTimeout(0); controlClient = socket;
                runOnUiThread(this::updateDisconnect);
                output.writeUTF("PCAM/1"); output.writeUTF(connectionDetails()); output.flush();
                while (!closing) {
                    int command = input.readUnsignedByte();
                    if (command == 1) {
                        long echo = input.readLong(), received = SystemClock.elapsedRealtimeNanos();
                        long mono = System.nanoTime(), sent = SystemClock.elapsedRealtimeNanos();
                        output.writeLong(echo); output.writeLong(received); output.writeLong(sent); output.writeLong(mono); output.flush();
                    } else if (command == 2) requestIdr();
                    else if (command == 3) { stopCamera(); output.writeByte(0); output.flush(); }
                    else if (command == 4) { output.writeUTF(connectionDetails()); output.flush(); }
                    else if (command == 5) runOnUiThread(() -> {
                        host = socket.getInetAddress().getHostAddress();
                        start();
                    });
                    else if (command == 6) { metricsEnabled = input.readBoolean(); output.writeByte(0); output.flush(); }
                    else if (command == 7) { runOnUiThread(() -> direct.disconnect()); output.writeByte(0); output.flush(); }
                    else if (command == 8) {
                        int selectedWidth = input.readInt(), selectedHeight = input.readInt();
                        if (!OutputSettings.allows(selectedWidth, selectedHeight)) throw new IOException("Unsupported webcam resolution");
                        outputSize = new int[]{selectedWidth, selectedHeight};
                        getSharedPreferences("settings", MODE_PRIVATE).edit().putInt("output_width", selectedWidth).putInt("output_height", selectedHeight).apply();
                        output.writeUTF(connectionDetails()); output.flush();
                    }
                    else if (command == 9) {
                        try {
                            JSONObject computer = new JSONObject(input.readUTF());
                            computerName = computer.optString("name","Laptop"); connectionMode = computer.optString("connection","");
                            runOnUiThread(this::updateDisconnect); output.writeByte(0); output.flush();
                        } catch (org.json.JSONException error) { throw new IOException(error); }
                    }
                    else throw new IOException("Unknown control command");
                }
            } catch (IOException ended) { if (!closing) android.util.Log.i("PCam", "Control disconnected"); }
            finally { controlClient = null; if (authenticated && running) stopCamera(); runOnUiThread(this::updateDisconnect); }
        }
    }

    private synchronized void start() {
        if (running || closing || stopping) return;
        if (host.isEmpty()) { state("Waiting for the PC."); return; }
        if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.CAMERA}, 1); return;
        }
        running = true; needsIdr = false; frameId = 0; pending.clear(); configNals.clear();
        startForegroundService(new android.content.Intent(this, StreamService.class));
        trace = new Trace(this, metricsEnabled);
        try {
            wifiLock = ((WifiManager) getApplicationContext().getSystemService(WIFI_SERVICE))
                    .createWifiLock(WifiManager.WIFI_MODE_FULL_LOW_LATENCY, "PCam:stream");
            wifiLock.acquire();
            videoSocket = new DatagramSocket(); videoSocket.setSendBufferSize(64 * 1024);
            senderThread = new Thread(this::sendLoop, "pcam-udp"); senderThread.start();
            codecHandler.post(() -> {
                try { prepareEncoder(); cameraHandler.post(this::openCamera); }
                catch (Exception error) { fail(error); stopCamera(); }
            });
            state("Starting " + fps + " FPS camera to " + host + "…");
        } catch (Exception error) { fail(error); stopCamera(); }
    }

    private void prepareEncoder() throws Exception {
        if (rawUncompressed) {
            details.put("video_payload", "GRAY8");
            prepareRawInput(); return;
        }
        int probeFormat = getIntent().getIntExtra("buffer_probe", 0);
        if (probeFormat != 0) {
            if (highSpeed) throw new IOException("CPU buffer probing requires a regular camera session");
            bufferProbe = ImageReader.newInstance(width, height, probeFormat, 2);
            bufferProbe.setOnImageAvailableListener(reader -> {
                try (Image image = reader.acquireLatestImage()) {
                    if (image != null) {
                        if (!details.has("actual_buffer_width")) {
                            try {
                                details.put("actual_buffer_width", image.getWidth()).put("actual_buffer_height", image.getHeight())
                                       .put("actual_buffer_format", image.getFormat());
                            } catch (org.json.JSONException error) { fail(error); stopCamera(); return; }
                        }
                        // Mapping and reading also waits for the camera's acquire fence.
                        image.getPlanes()[0].getBuffer().get(0);
                        if (trace != null) trace.event("camera_buffer", image.getTimestamp(), 0, 0);
                    }
                }
            }, codecHandler);
            cameraSurface = bufferProbe.getSurface();
            details.put("buffer_probe_format", probeFormat);
            state("Measuring camera buffer timing. No images are saved.");
            return;
        }
        MediaFormat format = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, width, height);
        format.setInteger(MediaFormat.KEY_COLOR_FORMAT, rawStream ? MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Flexible : MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface);
        format.setInteger(MediaFormat.KEY_BIT_RATE, bitrate);
        int encoderFps = getIntent().getIntExtra("trial_encoder_fps", fps);
        format.setInteger(MediaFormat.KEY_FRAME_RATE, encoderFps);
        format.setInteger(MediaFormat.KEY_OPERATING_RATE, encoderFps);
        format.setInteger(MediaFormat.KEY_PRIORITY, 0);
        format.setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 1);
        format.setInteger(MediaFormat.KEY_BITRATE_MODE, MediaCodecInfo.EncoderCapabilities.BITRATE_MODE_CBR);
        format.setInteger(MediaFormat.KEY_MAX_B_FRAMES, 0);
        format.setInteger(MediaFormat.KEY_LATENCY, 0);
        format.setInteger(MediaFormat.KEY_PROFILE, MediaCodecInfo.CodecProfileLevel.AVCProfileBaseline);
        for (MediaCodecInfo info : new MediaCodecList(MediaCodecList.ALL_CODECS).getCodecInfos()) {
            if (!info.isEncoder() || !info.isHardwareAccelerated() || info.isAlias()) continue;
            if (!Arrays.asList(info.getSupportedTypes()).contains(MediaFormat.MIMETYPE_VIDEO_AVC)) continue;
            if (!info.getCapabilitiesForType(MediaFormat.MIMETYPE_VIDEO_AVC).getVideoCapabilities()
                    .areSizeAndRateSupported(width, height, fps)) continue;
            codecName = info.getName(); break;
        }
        if (codecName == null) throw new IOException("No hardware H.264 encoder supports this mode");
        encoder = MediaCodec.createByCodecName(codecName);
        if (Build.VERSION.SDK_INT >= 31) details.put("vendor_parameters", encoder.getSupportedVendorParameters().toString());
        String lowLatency = "vendor.qti-ext-enc-low-latency.enable";
        boolean advertisedLowLatency = Build.VERSION.SDK_INT >= 31 && encoder.getSupportedVendorParameters().contains(lowLatency);
        if (getIntent().getBooleanExtra("vendor_low_latency", advertisedLowLatency)) {
            if (Build.VERSION.SDK_INT < 31 || !encoder.getSupportedVendorParameters().contains(lowLatency))
                throw new IOException("This encoder does not advertise the requested vendor low-latency mode");
            if (encoder.getParameterDescriptor(lowLatency).getType() != MediaFormat.TYPE_INTEGER)
                throw new IOException("Unexpected vendor parameter type");
            format.setInteger(lowLatency, 1);
        }
        details.put("encoder", codecName).put("requested_encoder_format", format.toString());
        encoder.setCallback(new MediaCodec.Callback() {
            @Override public void onInputBufferAvailable(MediaCodec codec, int index) { if (rawStream) rawInputs.add(index); }
            @Override public void onOutputFormatChanged(MediaCodec codec, MediaFormat actual) {
                try { details.put("encoder_format", actual.toString()); } catch (Exception error) { fail(error); }
                synchronized (configNals) {
                    configNals.clear();
                    for (String key : new String[]{"csd-0", "csd-1"}) {
                        ByteBuffer value = actual.getByteBuffer(key);
                        if (value != null) { byte[] data = new byte[value.remaining()]; value.duplicate().get(data); configNals.addAll(H264Rtp.nals(data)); }
                    }
                }
                state("Streaming to " + host + ". " + codecName + (rawStream ? ", experimental monochrome RAW." : ", direct camera surface."));
            }
            @Override public void onError(MediaCodec codec, MediaCodec.CodecException error) { fail(error); stopCamera(); }
            @Override public void onOutputBufferAvailable(MediaCodec codec, int index, MediaCodec.BufferInfo info) {
                long available = SystemClock.elapsedRealtimeNanos();
                byte[] data = null;
                try {
                    ByteBuffer buffer = codec.getOutputBuffer(index);
                    if (buffer != null && info.size > 0) {
                        buffer.position(info.offset); buffer.limit(info.offset + info.size);
                        data = new byte[info.size]; buffer.get(data);
                    }
                } finally { codec.releaseOutputBuffer(index, false); }
                if (!running || data == null) return;
                List<byte[]> nals = H264Rtp.nals(data);
                if ((info.flags & MediaCodec.BUFFER_FLAG_CODEC_CONFIG) != 0) {
                    synchronized (configNals) { configNals.clear(); configNals.addAll(nals); } return;
                }
                boolean key = (info.flags & MediaCodec.BUFFER_FLAG_KEY_FRAME) != 0;
                long pts = info.presentationTimeUs * 1000;
                int id = frameId++;
                trace.event("encoded", pts, id, available);
                if (needsIdr && !key) { trace.event("drop_recovery", pts, id, 0); return; }
                if (key) {
                    needsIdr = false;
                    synchronized (configNals) { List<byte[]> all = new ArrayList<>(configNals); all.addAll(nals); nals = all; }
                }
                Unit unit = new Unit(nals, id, pts, available);
                if (!pending.offer(unit)) {
                    needsIdr = true; dropPending("drop_backpressure");
                    trace.event("drop_backpressure", pts, id, 0); requestIdr();
                }
            }
        }, codecHandler);
        encoder.configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE);
        if (!rawStream) encoderSurface = encoder.createInputSurface();
        encoder.start();
        if (rawStream) prepareRawInput(); else cameraSurface = encoderSurface;
    }

    private void prepareRawInput() throws Exception {
            if (highSpeed) throw new IOException("RAW requires a regular camera session");
            int cfa = characteristics.get(CameraCharacteristics.SENSOR_INFO_COLOR_FILTER_ARRANGEMENT);
            if (cfa != CameraMetadata.SENSOR_INFO_COLOR_FILTER_ARRANGEMENT_BGGR && cfa != CameraMetadata.SENSOR_INFO_COLOR_FILTER_ARRANGEMENT_RGGB)
                throw new IOException("Experimental RAW preview requires BGGR or RGGB");
            int rawWidth = getIntent().getIntExtra("raw_width", 4096), rawHeight = getIntent().getIntExtra("raw_height", 3072);
            int contentHeight = getIntent().getIntExtra("raw_content_height", rawHeight);
            if (contentHeight <= 0 || contentHeight > rawHeight || (contentHeight & 1) != 0) throw new IOException("Invalid RAW content height");
            BlackLevelPattern black = characteristics.get(CameraCharacteristics.SENSOR_BLACK_LEVEL_PATTERN);
            RawDownsample downsample = new RawDownsample(rawWidth, contentHeight, width, height, black.getOffsetForIndex(1, 0) / 4);
            downsample.useNative = getIntent().getBooleanExtra("trial_raw_native", false);
            details.put("trial_raw_native", downsample.useNative);
            details.put("raw_content_height", contentHeight);
            details.put("trial_raw_stream", "RAW10 green-channel monochrome preview");
            ByteBuffer luma = rawUncompressed ? ByteBuffer.allocateDirect(width * height) : null;
            bufferProbe = ImageReader.newInstance(rawWidth, rawHeight, android.graphics.ImageFormat.RAW10, 2);
            bufferProbe.setOnImageAvailableListener(reader -> {
                try (Image image = reader.acquireLatestImage()) {
                    if (image == null || !running) return;
                    ByteBuffer raw = image.getPlanes()[0].getBuffer(); raw.get(0);
                    if (!details.has("raw_layout")) {
                        details.put("raw_layout", new JSONObject().put("width", image.getWidth()).put("height", image.getHeight())
                            .put("row_stride", image.getPlanes()[0].getRowStride()).put("bytes", raw.limit()).put("crop", image.getCropRect().toString()));
                        org.json.JSONArray bands = new org.json.JSONArray();
                        for (int band = 0; band < 16; band++) {
                            int row = (band * image.getHeight() / 16) & ~1, total = 0;
                            for (int x = 1; x < rawWidth; x += 64) total += raw.get(row * image.getPlanes()[0].getRowStride() + x / 4 * 5 + x % 4) & 255;
                            bands.put(total / (rawWidth / 64));
                        }
                        details.put("raw_row_band_green_means", bands);
                    }
                    long pts = image.getTimestamp(), started = SystemClock.elapsedRealtimeNanos();
                    if (trace != null) trace.event("camera_buffer", pts, 0, 0);
                    if (rawUncompressed) {
                        downsample.convert(raw, image.getPlanes()[0].getRowStride(), luma, width, 1, null, 0, 0, null, 0, 0);
                        byte[] data = new byte[8 + width * height];
                        ByteBuffer.wrap(data).putInt(0x47524159).putShort((short) width).putShort((short) height);
                        luma.position(0); luma.get(data, 8, width * height);
                        long ready = SystemClock.elapsedRealtimeNanos(); int id = frameId++;
                        if (trace != null) { trace.event("raw_processed", pts, id, ready - started); trace.event("payload", pts, id, ready); }
                        if (!pending.offer(new Unit(Collections.singletonList(data), id, pts, ready))) {
                            dropPending("drop_backpressure"); trace.event("drop_backpressure", pts, id, 0);
                        }
                        return;
                    }
                    Integer index = rawInputs.poll();
                    if (index == null) { if (trace != null) trace.event("drop_raw_input", pts, 0, 0); return; }
                    Image input = encoder.getInputImage(index);
                    if (input == null) throw new IOException("Encoder has no writable YUV image");
                    if (trace != null) trace.event("raw_input_mapped", pts, 0, SystemClock.elapsedRealtimeNanos() - started);
                    Image.Plane[] planes = input.getPlanes();
                    downsample.convert(raw, image.getPlanes()[0].getRowStride(),
                        planes[0].getBuffer(), planes[0].getRowStride(), planes[0].getPixelStride(),
                        planes[1].getBuffer(), planes[1].getRowStride(), planes[1].getPixelStride(),
                        planes[2].getBuffer(), planes[2].getRowStride(), planes[2].getPixelStride());
                    if (trace != null) trace.event("raw_converted", pts, 0, SystemClock.elapsedRealtimeNanos() - started);
                    // Retain the camera exposure timestamp, including microsecond truncation used by MediaCodec.
                    encoder.queueInputBuffer(index, 0, width * height * 3 / 2, pts / 1000, 0);
                    if (trace != null) trace.event("raw_processed", pts, 0, SystemClock.elapsedRealtimeNanos() - started);
                } catch (Exception error) { fail(error); stopCamera(); }
            }, codecHandler);
            cameraSurface = bufferProbe.getSurface();
    }

    private void openCamera() {
        try {
            CameraManager manager = (CameraManager) getSystemService(CAMERA_SERVICE);
            manager.openCamera(cameraId, new CameraDevice.StateCallback() {
                @Override public void onOpened(CameraDevice device) {
                    if (!running) { device.close(); return; }
                    camera = device;
                    try { configureSession(); } catch (Exception error) { fail(error); stopCamera(); }
                }
                @Override public void onDisconnected(CameraDevice device) { device.close(); stopCamera(); }
                @Override public void onError(CameraDevice device, int error) { device.close(); fail(new IOException("Camera error " + error)); stopCamera(); }
            }, cameraHandler);
        } catch (Exception error) { fail(error); stopCamera(); }
    }

    private void configureSession() throws Exception {
        int minFps = getIntent().getIntExtra("min_fps", fps);
        // This phone's variable-rate encoder-only HFR stream emits blank green frames between real frames.
        if (highSpeed && minFps != fps) throw new IOException("Use a fixed frame rate for high-speed recording");
        Range<Integer> range = new Range<>(minFps, fps);
        details.put("fps_range", range.toString());
        StreamConfigurationMap map = characteristics.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP);
        if (highSpeed && !Arrays.asList(map.getHighSpeedVideoFpsRangesFor(new Size(width, height))).contains(range))
            throw new IOException("Unsupported high-speed size/FPS");
        if (!highSpeed && !getIntent().getBooleanExtra("trial_unadvertised_fps", false) && !Arrays.asList(characteristics.get(CameraCharacteristics.CONTROL_AE_AVAILABLE_TARGET_FPS_RANGES)).contains(range))
            throw new IOException("Unsupported normal FPS range");
        CaptureRequest.Builder request = camera.createCaptureRequest(CameraDevice.TEMPLATE_RECORD);
        JSONObject vendorDefaults = new JSONObject();
        for (CaptureRequest.Key<?> key : characteristics.getAvailableCaptureRequestKeys()) {
            String name = key.getName();
            if (name.matches(".*sessionParameters\\.(enableMFNR|enableMCTFwithReferenceFrame|EnableVIULL|EnableSHDR|EnableMFHDR|HDRVideoMode|EnableVSR|EnableAutoHDR|numPCRsBeforeStreamOn|dynamicFPSConfig)")) {
                Object value = request.get(key);
                vendorDefaults.put(name, value == null ? JSONObject.NULL : value.getClass().isArray() ? new org.json.JSONArray(value) : value);
            }
        }
        details.put("vendor_request_defaults", vendorDefaults);
        int trialPcr = getIntent().getIntExtra("trial_pcr", -1);
        if (trialPcr >= 0) {
            if (trialPcr > 2) throw new IOException("Trial PCR must be 0, 1 or 2");
            boolean found = false;
            for (CaptureRequest.Key<?> key : characteristics.getAvailableCaptureRequestKeys()) {
                if (key.getName().equals("org.codeaurora.qcamera3.sessionParameters.numPCRsBeforeStreamOn")) {
                    @SuppressWarnings("unchecked") CaptureRequest.Key<int[]> typed = (CaptureRequest.Key<int[]>) key;
                    request.set(typed, new int[]{trialPcr});
                    details.put("trial_pcr_requested", trialPcr);
                    found = true;
                }
            }
            if (!found) throw new IOException("HAL does not expose PCR session control");
        }
        request.addTarget(cameraSurface); request.set(CaptureRequest.CONTROL_AE_TARGET_FPS_RANGE, range);
        setSupported(request, CaptureRequest.CONTROL_VIDEO_STABILIZATION_MODE, CameraMetadata.CONTROL_VIDEO_STABILIZATION_MODE_OFF,
                characteristics.get(CameraCharacteristics.CONTROL_AVAILABLE_VIDEO_STABILIZATION_MODES));
        setSupported(request, CaptureRequest.LENS_OPTICAL_STABILIZATION_MODE, CameraMetadata.LENS_OPTICAL_STABILIZATION_MODE_OFF,
                characteristics.get(CameraCharacteristics.LENS_INFO_AVAILABLE_OPTICAL_STABILIZATION));
        if (!highSpeed && fastProcessing) {
            setSupported(request, CaptureRequest.NOISE_REDUCTION_MODE, CameraMetadata.NOISE_REDUCTION_MODE_OFF,
                    characteristics.get(CameraCharacteristics.NOISE_REDUCTION_AVAILABLE_NOISE_REDUCTION_MODES));
            setSupported(request, CaptureRequest.EDGE_MODE, CameraMetadata.EDGE_MODE_OFF, characteristics.get(CameraCharacteristics.EDGE_AVAILABLE_EDGE_MODES));
            request.set(CaptureRequest.CONTROL_ENABLE_ZSL, false);
        }
        if (!highSpeed && exposureNs > 0) {
            int[] capabilities = characteristics.get(CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES);
            boolean manual = false; for (int cap : capabilities) if (cap == CameraMetadata.REQUEST_AVAILABLE_CAPABILITIES_MANUAL_SENSOR) manual = true;
            if (!manual) throw new IOException("Manual exposure unsupported");
            request.set(CaptureRequest.CONTROL_AE_MODE, CameraMetadata.CONTROL_AE_MODE_OFF);
            request.set(CaptureRequest.SENSOR_EXPOSURE_TIME, characteristics.get(CameraCharacteristics.SENSOR_INFO_EXPOSURE_TIME_RANGE).clamp(exposureNs));
            request.set(CaptureRequest.SENSOR_FRAME_DURATION, 1_000_000_000L / fps);
            request.set(CaptureRequest.SENSOR_SENSITIVITY, characteristics.get(CameraCharacteristics.SENSOR_INFO_SENSITIVITY_RANGE).clamp(getIntent().getIntExtra("sensitivity", 400)));
        }
        OutputConfiguration output = new OutputConfiguration(cameraSurface);
        if (Build.VERSION.SDK_INT >= 33) output.setTimestampBase(OutputConfiguration.TIMESTAMP_BASE_SENSOR);
        SessionConfiguration configuration = new SessionConfiguration(highSpeed ? SessionConfiguration.SESSION_HIGH_SPEED : SessionConfiguration.SESSION_REGULAR,
                Collections.singletonList(output), command -> cameraHandler.post(command), new CameraCaptureSession.StateCallback() {
            @Override public void onConfigured(CameraCaptureSession configured) {
                if (!running) { configured.close(); return; }
                session = configured;
                CameraCaptureSession.CaptureCallback callback = new CameraCaptureSession.CaptureCallback() {
                    @Override public void onCaptureStarted(CameraCaptureSession session, CaptureRequest request, long timestamp, long number) {
                        if (trace != null) trace.event("sensor", timestamp, number, 0);
                    }
                    @Override public void onReadoutStarted(CameraCaptureSession session, CaptureRequest request, long timestamp, long number) {
                        if (trace != null) trace.event("readout", timestamp, number, 0);
                    }
                    @Override public void onCaptureProgressed(CameraCaptureSession session, CaptureRequest request, CaptureResult result) {
                        Long timestamp = result.get(CaptureResult.SENSOR_TIMESTAMP);
                        if (trace != null && timestamp != null) trace.event("partial", timestamp, result.getFrameNumber(), 0);
                    }
                    @Override public void onCaptureCompleted(CameraCaptureSession session, CaptureRequest request, TotalCaptureResult result) {
                        Long timestamp = result.get(CaptureResult.SENSOR_TIMESTAMP), exposure = result.get(CaptureResult.SENSOR_EXPOSURE_TIME);
                        if (trace != null && timestamp != null && exposure != null) trace.event("exposure", timestamp, result.getFrameNumber(), exposure);
                        Long duration = result.get(CaptureResult.SENSOR_FRAME_DURATION);
                        if (trace != null && timestamp != null && duration != null) trace.event("frame_duration", timestamp, result.getFrameNumber(), duration);
                        Long skew = result.get(CaptureResult.SENSOR_ROLLING_SHUTTER_SKEW);
                        if (trace != null && timestamp != null && skew != null) trace.event("rolling_skew", timestamp, result.getFrameNumber(), skew);
                        Byte depth = result.get(CaptureResult.REQUEST_PIPELINE_DEPTH);
                        if (trace != null && timestamp != null && depth != null) trace.event("pipeline_depth", timestamp, result.getFrameNumber(), depth);
                    }
                    @Override public void onCaptureFailed(CameraCaptureSession session, CaptureRequest request, CaptureFailure failure) {
                        if (trace != null) trace.event("capture_failed", 0, failure.getFrameNumber(), failure.getReason());
                    }
                };
                try {
                    if (highSpeed) {
                        CameraConstrainedHighSpeedCaptureSession high = (CameraConstrainedHighSpeedCaptureSession) configured;
                        List<CaptureRequest> requests = high.createHighSpeedRequestList(request.build());
                        details.put("request_list_size", requests.size());
                        high.setRepeatingBurst(requests, callback, cameraHandler);
                    } else configured.setRepeatingRequest(request.build(), callback, cameraHandler);
                    if (rawUncompressed) state("Streaming monochrome camera to " + host + ". " + width + " × " + height + " at " + fps + " FPS.");
                } catch (Exception error) { fail(error); stopCamera(); }
            }
            @Override public void onConfigureFailed(CameraCaptureSession session) { fail(new IOException("Camera session configuration failed")); stopCamera(); }
        });
        configuration.setSessionParameters(request.build()); camera.createCaptureSession(configuration);
    }

    private static void setSupported(CaptureRequest.Builder request, CaptureRequest.Key<Integer> key, int value, int[] supported) {
        if (supported != null) for (int candidate : supported) if (candidate == value) { request.set(key, value); return; }
    }

    private void requestIdr() {
        codecHandler.post(() -> {
            if (running && encoder != null) try {
                Bundle parameters = new Bundle(); parameters.putInt(MediaCodec.PARAMETER_KEY_REQUEST_SYNC_FRAME, 0); encoder.setParameters(parameters);
            } catch (IllegalStateException ended) { android.util.Log.i("PCam", "Encoder stopping"); }
        });
    }

    private void sendLoop() {
        Process.setThreadPriority(Process.THREAD_PRIORITY_DISPLAY);
        H264Rtp packetizer = new H264Rtp(new SecureRandom().nextInt(), new SecureRandom().nextInt(65536));
        try {
            InetAddress destination = InetAddress.getByName(host);
            while (running) {
                Unit unit = pending.poll(100, java.util.concurrent.TimeUnit.MILLISECONDS);
                if (unit == null) continue;
                if (SystemClock.elapsedRealtimeNanos() - unit.encoded > 8_000_000L) {
                    needsIdr = true; dropPending("drop_sender_deadline");
                    trace.event("drop_sender_deadline", unit.pts, unit.id, 0); requestIdr(); continue;
                }
                List<byte[]> packets = rawUncompressed ? packetizer.grayPackets(unit.nals.get(0), unit.id, unit.pts, unit.encoded)
                    : packetizer.packets(unit.nals, unit.id, unit.pts, unit.encoded);
                for (byte[] bytes : packets) {
                    ByteBuffer.wrap(bytes).putLong(36, SystemClock.elapsedRealtimeNanos());
                    videoSocket.send(new DatagramPacket(bytes, bytes.length, destination, videoPort));
                }
                trace.event("sent", unit.pts, unit.id, packets.size());
            }
        } catch (Exception error) { if (running) { fail(error); stopCamera(); } }
    }

    private void dropPending(String reason) {
        Unit dropped;
        while ((dropped = pending.poll()) != null) trace.event(reason, dropped.pts, dropped.id, 0);
    }

    private synchronized void stopCamera() {
        if (!running) return;
        running = false; stopping = true;
        if (videoSocket != null) videoSocket.close();
        if (senderThread != null && Thread.currentThread() != senderThread) {
            try { senderThread.join(1000); } catch (InterruptedException error) { Thread.currentThread().interrupt(); }
        }
        cameraHandler.post(() -> {
            if (session != null) { session.close(); session = null; }
            if (camera != null) { camera.close(); camera = null; }
            codecHandler.post(() -> {
                if (encoder != null) { try { encoder.stop(); } catch (IllegalStateException ended) { } encoder.release(); encoder = null; }
                if (encoderSurface != null) { encoderSurface.release(); encoderSurface = null; }
                if (bufferProbe != null) { bufferProbe.close(); bufferProbe = null; }
                rawInputs.clear();
                cameraSurface = null;
                if (trace != null) { trace.close(); trace = null; }
                if (wifiLock != null && wifiLock.isHeld()) wifiLock.release();
                stopService(new android.content.Intent(MainActivity.this, StreamService.class));
                pending.clear(); stopping = false; state("Camera stopped.");
            });
        });
    }

    @Override protected void onDestroy() {
        if (discovery != null) discovery.close();
        if (direct != null) direct.close();
        closing = true; stopCamera();
        try { if (controlClient != null) controlClient.close(); if (controlServer != null) controlServer.close(); } catch (IOException ended) { }
        cameraHandler.post(() -> codecHandler.post(() -> {
            cameraThread.quitSafely(); codecThread.quitSafely();
        }));
        super.onDestroy();
    }
}
