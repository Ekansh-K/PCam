#!/usr/bin/env python3
"""Build a native Android APK with the local SDK and no Gradle dependencies."""
from pathlib import Path
import subprocess
import shutil
import zipfile
import hashlib
import urllib.request
import xml.etree.ElementTree as ET
import os

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "scratch/android-build"
SDK = ROOT / "scratch/tools"
TOOLS = SDK / "build-tools35/android-15"
PLATFORM = SDK / "android-platform/android-36/android.jar"
android_sdk = os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT')
if android_sdk:
    TOOLS = Path(android_sdk) / 'build-tools/35.0.0'
    PLATFORM = Path(android_sdk) / 'platforms/android-36/android.jar'

def run(*args):
    subprocess.run([str(arg) for arg in args], check=True)

BUILD.mkdir(parents=True, exist_ok=True)
SDK.mkdir(parents=True, exist_ok=True)
zxing = SDK / 'zxing-core.jar'
zxing_hash = '71de5d89341b5fcf5dd89da7f44e84d825d0e084cdf3ec77c9abe26b0f0ceb13'
if not zxing.exists():
    zxing.write_bytes(urllib.request.urlopen('https://repo.maven.apache.org/maven2/com/google/zxing/core/3.5.4/core-3.5.4.jar').read())
if hashlib.sha256(zxing.read_bytes()).hexdigest() != zxing_hash:
    raise RuntimeError('ZXing dependency checksum mismatch')
# The optional RAW loop has no libc dependencies. Use Clang's Android target and the JDK's portable JNI ABI.
jdk = Path(shutil.which("javac")).resolve().parents[1]
shim = BUILD / "freestanding"
shim.mkdir(exist_ok=True)
# jni.h includes stdio.h but uses no stdio types. Avoid pulling host libc headers into the ARM build.
(shim / "stdio.h").write_text("/* No stdio APIs are used by this freestanding JNI library. */\n")
native = BUILD / "libpcam_raw.so"
linker = SDK / 'lld/usr/lib/llvm-14/bin/ld.lld'
if not linker.exists(): linker = shutil.which('ld.lld-14') or shutil.which('ld.lld') or 'ld.lld'
run("clang-14", "--target=aarch64-linux-android29", "-O3", "-fPIC", "-ffreestanding", "-fno-builtin", "-nostdlib", "-shared",
    "-fuse-ld=" + str(linker), "-Wl,--no-undefined", "-Wl,-z,max-page-size=16384",
    "-I", shim, "-I", jdk / "include", "-I", jdk / "include/linux", ROOT / "android/raw_downsample.c", "-o", native)
classes = BUILD / "classes"
if classes.exists(): shutil.rmtree(classes)
classes.mkdir()
run("javac", "--release", "8", "-cp", str(PLATFORM) + ':' + str(zxing), "-d", classes,
    *(ROOT / "android/src").rglob("*.java"))
dex = BUILD / "dex"
if dex.exists(): shutil.rmtree(dex)
dex.mkdir()
compiler = [TOOLS / 'd8'] if android_sdk else ['java', '-cp', SDK / 'r8.jar', 'com.android.tools.r8.D8']
run(*compiler, "--min-api", "29", "--lib", PLATFORM,
    "--output", dex, zxing, *classes.rglob("*.class"))
key = BUILD / "debug.keystore"
if not key.exists():
    run("keytool", "-genkeypair", "-keystore", key, "-storepass", "android", "-keypass", "android",
        "-alias", "androiddebugkey", "-dname", "CN=PCam development", "-keyalg", "RSA", "-validity", "10000")

def package(manifest, apk):
    unsigned = BUILD / "unsigned.apk"
    run(TOOLS / "aapt", "package", "-f", "-M", manifest, "-S", ROOT / "android/res",
        "-I", PLATFORM, "-F", unsigned)
    with zipfile.ZipFile(unsigned, "a", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(dex / "classes.dex", "classes.dex")
        archive.write(native, "lib/arm64-v8a/libpcam_raw.so")
        archive.write(ROOT / 'assets/ZXing-LICENSE.txt', 'assets/zxing-license.txt')
    aligned = BUILD / "aligned.apk"
    run(TOOLS / "zipalign", "-f", "4", unsigned, aligned)
    apk.parent.mkdir(parents=True, exist_ok=True)
    run(TOOLS / "apksigner", "sign", "--ks", key, "--ks-pass", "pass:android", "--key-pass", "pass:android",
        "--out", apk, aligned)
    run(TOOLS / "apksigner", "verify", apk)
    print(apk)


manifest = ROOT / "android/AndroidManifest.xml"
package(manifest, BUILD / "pcam.apk")
# Keep sensor-trace access in the local build; disable app debugging in the shared APK.
android_namespace = "http://schemas.android.com/apk/res/android"
ET.register_namespace("android", android_namespace)
shared_manifest = ET.parse(manifest)
shared_manifest.find("application").set("{" + android_namespace + "}debuggable", "false")
share_dir = BUILD / "share"
share_dir.mkdir(exist_ok=True)
shared_manifest.write(share_dir / "AndroidManifest.xml", encoding="utf-8", xml_declaration=True)
package(share_dir / "AndroidManifest.xml", ROOT / "dist/PCam.apk")
version = shared_manifest.getroot().get('{' + android_namespace + '}versionName')
shutil.copy2(ROOT / 'dist/PCam.apk', ROOT / 'dist' / f'PCam-{version}-android-arm64.apk')
