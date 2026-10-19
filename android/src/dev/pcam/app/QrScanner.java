package dev.pcam.app;

import android.app.Activity;
import android.content.Intent;
import android.graphics.*;
import android.hardware.camera2.*;
import android.media.*;
import android.os.*;
import android.view.*;
import android.widget.*;
import java.nio.ByteBuffer;
import java.util.Arrays;
import com.google.zxing.*;
import com.google.zxing.common.HybridBinarizer;
import com.google.zxing.qrcode.QRCodeReader;

/** Camera-only QR reader. Camera resources close before the webcam starts. */
public final class QrScanner extends Activity implements TextureView.SurfaceTextureListener {
    private TextureView preview;
    private TextView title;
    private HandlerThread thread;
    private Handler worker;
    private CameraDevice camera;
    private CameraCaptureSession session;
    private ImageReader reader;
    private Surface surface;
    private volatile boolean finished;
    private long lastDecode;

    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        LinearLayout layout = new LinearLayout(this); layout.setOrientation(LinearLayout.VERTICAL);
        layout.setBackgroundColor(Color.rgb(8,8,9));
        int pad = (int) (20 * getResources().getDisplayMetrics().density); layout.setPadding(pad,pad,pad,pad);
        title = new TextView(this); title.setText("Scan pairing QR"); title.setTextColor(Color.WHITE); title.setTextSize(20); layout.addView(title);
        preview = new TextureView(this); layout.addView(preview, new LinearLayout.LayoutParams(-1,0,1));
        Button cancel = new Button(this); cancel.setText("Cancel"); cancel.setAllCaps(false); cancel.setOnClickListener(view -> finish()); layout.addView(cancel);
        setContentView(layout);
        thread = new HandlerThread("pcam-qr-camera"); thread.start(); worker = new Handler(thread.getLooper());
        preview.setSurfaceTextureListener(this);
    }
    @Override public void onSurfaceTextureAvailable(SurfaceTexture texture, int width, int height) {
        try {
            texture.setDefaultBufferSize(640,480); surface = new Surface(texture);
            reader = ImageReader.newInstance(640,480,ImageFormat.YUV_420_888,2);
            reader.setOnImageAvailableListener(this::decode, worker);
            ((CameraManager) getSystemService(CAMERA_SERVICE)).openCamera("0", new CameraDevice.StateCallback() {
                @Override public void onOpened(CameraDevice device) {
                    if (finished) { device.close(); return; } camera = device;
                    try {
                        device.createCaptureSession(Arrays.asList(surface, reader.getSurface()), new CameraCaptureSession.StateCallback() {
                            @Override public void onConfigured(CameraCaptureSession ready) {
                                if (finished) { ready.close(); return; } session = ready;
                                try {
                                    CaptureRequest.Builder request = camera.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW);
                                    request.addTarget(surface); request.addTarget(reader.getSurface());
                                    request.set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_PICTURE);
                                    ready.setRepeatingRequest(request.build(), null, worker);
                                } catch (Exception error) { fail(); }
                            }
                            @Override public void onConfigureFailed(CameraCaptureSession failed) { fail(); }
                        }, worker);
                    } catch (Exception error) { fail(); }
                }
                @Override public void onDisconnected(CameraDevice device) { device.close(); fail(); }
                @Override public void onError(CameraDevice device, int error) { device.close(); fail(); }
            }, worker);
        } catch (Exception error) { fail(); }
    }
    private void decode(ImageReader source) {
        try (Image image = source.acquireLatestImage()) {
            if (image == null || finished || SystemClock.elapsedRealtime() - lastDecode < 200) return;
            lastDecode = SystemClock.elapsedRealtime();
            int width = image.getWidth(), height = image.getHeight();
            Image.Plane plane = image.getPlanes()[0]; ByteBuffer bytes = plane.getBuffer();
            byte[] gray = new byte[width * height];
            for (int row = 0; row < height; row++) { bytes.position(row * plane.getRowStride()); bytes.get(gray, row * width, width); }
            PlanarYUVLuminanceSource luminance = new PlanarYUVLuminanceSource(gray,width,height,0,0,width,height,false);
            String result = new QRCodeReader().decode(new BinaryBitmap(new HybridBinarizer(luminance))).getText();
            if (!result.contains("PCAM-DIRECT/1")) return;
            finished = true;
            runOnUiThread(() -> { setResult(RESULT_OK,new Intent().putExtra("pairing",result)); finish(); });
        } catch (ReaderException ignored) { }
        catch (Exception error) { if (!finished) android.util.Log.w("PCam", "QR frame unavailable", error); }
    }
    private void fail() { runOnUiThread(() -> title.setText("Camera unavailable. Close other camera apps and try again.")); }
    @Override protected void onPause() {
        finished = true;
        if (session != null) session.close();
        if (camera != null) camera.close();
        if (reader != null) reader.close();
        if (surface != null) surface.release();
        if (thread != null) thread.quitSafely();
        super.onPause();
    }
    @Override public boolean onSurfaceTextureDestroyed(SurfaceTexture texture) { return true; }
    @Override public void onSurfaceTextureSizeChanged(SurfaceTexture texture, int width, int height) { }
    @Override public void onSurfaceTextureUpdated(SurfaceTexture texture) { }
}
