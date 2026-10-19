# PCam

Use an Android phone as a wireless webcam on Ubuntu. PCam has a minimal desktop app, automatic discovery on shared Wi-Fi, and camera-scanned QR pairing over Wi-Fi Direct.

## Download and install

Download both apps from [GitHub Releases](https://github.com/Ekansh-K/PCam/releases/latest):

- **Android:** `PCam-0.1.0-android-arm64.apk`, for Android 10 or later on ARM64.
- **Ubuntu:** `PCam-0.1.0-ubuntu22.04-amd64.deb`, for Ubuntu 22.04 on Intel or AMD 64-bit computers.

On Android, open the APK and follow the installation prompts. Android may ask you to allow installations from the receiving app. This is a testing build signed with a development certificate. You can share the APK using Files or Quick Share.

On Ubuntu, install the downloaded package:

```sh
sudo apt update
sudo apt install ./PCam-0.1.0-ubuntu22.04-amd64.deb
```

Open **PCam** from the applications menu, or run `pcam`. The package includes the decoder and installs its Python and networking dependencies. It does not need a compiler, the Android SDK, USB debugging, or this source checkout to run.

The webcam needs the `v4l2loopback` kernel module. Updated Ubuntu 22.04 HWE generic kernels include it. Check your current kernel with `modinfo v4l2loopback`. If the module is missing, install the Ubuntu HWE kernel and reboot:

```sh
sudo apt install linux-image-generic-hwe-22.04
```

PCam loads the driver when you connect. It requests system authorization when needed. The package does not install Ubuntu 22.04's older `v4l2loopback-dkms`, which can fail to build on kernel 6.8. Custom kernels and Secure Boot configurations may need separate driver setup.

The release has been tested with a OnePlus Nord CE4, model CPH2613, on Android 16 and an Ubuntu 22.04 laptop. Other phones and Wi-Fi adapters are unverified. The fixed camera mode needs 1280×720 capture at 120 FPS. Android version and CPU compatibility alone do not guarantee camera support. This release does not support iPhone, Windows, macOS, or Ubuntu 24.04.

## Connect over shared Wi-Fi

1. Open **PCam** on the phone and computer. Connect both to the same network.
2. Select **Shared Wi-Fi** on the computer. The app scans for phones running PCam.
3. Select the phone and click **Connect camera**.
4. Tap **Connect** on the phone to approve the computer.
5. Choose **PCam** as the webcam in your video application.

Use the scan button to refresh discovery. Networks that isolate clients or block local broadcasts prevent discovery. No IP address, pairing-token entry, or USB cable is needed.

## Connect over Wi-Fi Direct

1. Keep the laptop connected to its usual Wi-Fi.
2. Select **Wi-Fi Direct** in the desktop app.
3. Click **Show pairing QR** and approve system authorization.
4. On the phone, tap **Scan pairing QR** and scan the laptop's code.
5. Allow camera and nearby-device permissions when requested.
6. Choose **PCam** as the webcam in your video application.

The phone joins automatically. The pairing panel disappears when video starts. QR pairing needs no USB cable, and the phone does not need to join the laptop's ordinary Wi-Fi network. Neither device enables its normal hotspot setting or shares internet. Wi-Fi Direct still has a group owner, which is the laptop in this implementation.

The laptop's adapter must support a concurrent Wi-Fi station and P2P group on the router's channel. PCam detects the connected adapter, creates a temporary `pcam-p2p0` interface, and preserves the router's default route. Changing the router or channel stops the direct session. An unused QR expires after five minutes.

Click **Disconnect** on either device or close the desktop app to stop. This removes the temporary P2P group. To enable the receiver again, reopen PCam, select Wi-Fi Direct, show a fresh QR, and scan it from the phone.

## Choose the webcam output

The desktop controls offer two presets and portrait or landscape orientation:

| Preset | Portrait | Landscape |
| --- | --- | --- |
| Default | 480×960 | 960×480 |
| Large | 720×1600 | 1600×720 |

The selection applies to both connection modes and can change during a session. Reopen the camera in your video application after changing size. The receiver preserves the old size while another application holds the webcam open. Hold the phone horizontally for landscape.

The camera captures 1280×720 at a fixed 120 FPS and encodes H.264 at 12 Mbps. The receiver crops, rotates, and scales that feed to the selected webcam output. Larger output dimensions do not add captured detail. There are no custom sizes. Variable high-speed capture is disabled because it produced green frames on the tested phone.

The phone shows the connected computer, connection type, and a Disconnect button. Resolution and orientation controls stay on the computer.

## Privacy and local data

Pairing authenticates the control connection. The Wi-Fi Direct group uses WPA2. Video has no application-level encryption, so use trusted shared networks. Discovery broadcasts do not contain pairing tokens. PCam has no account or cloud video service.

The installed Ubuntu app stores session data and logs in `~/.cache/pcam`. It deletes QR and pairing files when a direct session ends. Desktop preferences are in `~/.config/PCam/Desktop.conf`. Source-checkout runs use `scratch` for development data. Signing keys, camera recordings, logs, downloaded tools, and generated installers are excluded from Git.

## Run from source

Install the desktop and native build dependencies on Ubuntu 22.04:

```sh
sudo apt install python3-pyqt5 python3-numpy python3-qrcode g++ libavcodec-dev libavutil-dev libswscale-dev libyuv-dev policykit-1 network-manager iw wpasupplicant dnsmasq-base v4l-utils adb
mkdir -p scratch
./pcam-app
```

The source launcher uses system dependencies and compiles the decoder locally. `desktop-requirements.txt` pins the QR renderer used during development. The Ubuntu package uses the compatible version supplied by Ubuntu.

To connect with USB-assisted Wi-Fi Direct instead of the QR:

```sh
./start-pcam
```

Connect the phone by USB, unlock it, and approve USB debugging first. Add `--serial DEVICE_ID` when several phones are attached, or `--interface WIFI_ADAPTER` to select the laptop adapter explicitly. Press Ctrl+C to stop. The installed package exposes this launcher as `pcam-direct` and needs the optional `adb` package for this route.

For a terminal-only shared connection:

```sh
sudo modprobe v4l2loopback devices=1 video_nr=10 card_label=PCam exclusive_caps=1
python3 -m pcam.live --phone PHONE_IP
```

Approve the connection on the phone. Add `--output-size 480 960` or `--output-size 720 1600` to override its saved output.

## Build the downloads

Build the Ubuntu installer on Ubuntu 22.04 amd64 after installing the source dependencies:

```sh
python3 packaging/build_ubuntu.py
```

The output is `dist/PCam-0.1.0-ubuntu22.04-amd64.deb`. The script stages only application files and compiles the native binaries. Runtime installations do not use development tools.

The Android builder uses Java 17, Clang 14 with LLD 14, Android SDK platform 36 and build tools 35.0.0, and D8. Install `openjdk-17-jdk clang-14 lld-14` through apt. It supports `ANDROID_HOME` or `ANDROID_SDK_ROOT`. Install the platform and build tools with Android's SDK Manager. The existing development workspace can also use its tools under `scratch/tools`.

```sh
python3 android/build.py
adb install -r -g scratch/android-build/pcam.apk
```

The local APK keeps debugging enabled so timestamp benchmarks can retrieve sensor traces. The shareable `dist/PCam.apk` disables debugging. Both use the same local development signing key, excluded from Git. Preserve that key privately to sign compatible updates. A fresh checkout generates a new key, so its APK cannot update an installation signed by a different key without uninstalling first.

The builder downloads ZXing Core 3.5.4 and verifies its SHA-256. Its Apache 2.0 license is included in the APK. The launcher icon is an Android adaptive vector with a themed monochrome layer. The optional RAW experiment includes an ARM64 native library, so the APK targets ARM64 phones.

## Verify changes and measure latency

Install Java 17, Python OpenCV, and the native development dependencies to run the tests. The QR round-trip test also needs the ZXing jar downloaded by the Android build.

```sh
mkdir -p scratch
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s benchmarks -p 'test_*.py' -v
```

The tests cover RTP interoperability with Java, loss and reordering recovery, timestamp matching, output rotation and color conversion, discovery and pairing, desktop state transitions, and helper shutdown.

For a timestamp benchmark, use the debuggable local APK, stop the live receiver, and keep USB debugging connected. Video and clock messages still use Wi-Fi. Replace the device values and choose a new result directory:

```sh
python3 -m pcam.run --phone PHONE_IP --serial DEVICE_ID \
    --width 1280 --height 720 --fps 120 --min-fps 120 --bitrate 12000000 \
    --vendor-low-latency --output-size 480 960 --seconds 30 \
    --output scratch/results/measurement
```

Results in `summary.json` measure sensor capture to an independent V4L2 consumer. They exclude final application rendering and display refresh. Check sensor timestamp verification, clock uncertainty, dropped frames, and frame delivery alongside latency.

A development run at 480×960 measured 63.6 ms median on shared Wi-Fi and 63.8 ms on concurrent Wi-Fi Direct. The latter delivered 119.1 FPS after warmup. These are measurements on one phone and laptop, not guarantees. Earlier roughly 41 ms results used a variable-rate mode that included invalid green frames and are not a valid quality baseline. The project has not achieved a median below 30 ms with valid color video.

The `benchmarks` directory also contains scrcpy comparisons, camera buffer probes, and experimental RAW paths. Those experiments are not the default camera pipeline. Benchmark recordings and device-specific results stay local under `scratch`.

## Third-party notices

The desktop includes JetBrains Mono Nerd Font under the SIL Open Font License in `assets/NerdFont-OFL.txt`. Android includes ZXing under Apache 2.0, with its notice in `assets/ZXing-LICENSE.txt`. The Ubuntu decoder dynamically links the system FFmpeg and libyuv libraries. Those dependencies are installed by Ubuntu rather than bundled in the release.
