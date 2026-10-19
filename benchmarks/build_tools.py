#!/usr/bin/env python3
"""Build the clock probe and optional instrumented scrcpy from downloaded tools."""
import argparse
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = ROOT / "scratch"


def run(*args):
    subprocess.run([str(arg) for arg in args], check=True)


def dex(classes, output, android=None):
    output.mkdir(parents=True, exist_ok=True)
    files = [p for p in classes.rglob("*.class") if not p.relative_to(classes).parts[0] == "android"]
    command = ["java", "-cp", SCRATCH / "tools/r8.jar", "com.android.tools.r8.D8",
               "--min-api", "26", "--output", output]
    if android:
        command.extend(["--lib", android])
    run(*command, *files)


def main(instrumented):
    classes = SCRATCH / "clock-classes"
    classes.mkdir(exist_ok=True)
    run("javac", "--release", "8", "-d", classes, ROOT / "benchmarks/ClockProbe.java")
    dex(classes, SCRATCH / "clock-dex")
    run("jar", "cf", SCRATCH / "clock.jar", "-C", SCRATCH / "clock-dex", "classes.dex")
    if not instrumented:
        return
    upstream = SCRATCH / "tools/scrcpy-5.0.1/server/src/main/java"
    source = SCRATCH / "instrumented-source"
    if source.exists():
        shutil.rmtree(source)
    shutil.copytree(upstream, source)
    package = source / "com/genymobile/scrcpy"
    shutil.copyfile(ROOT / "benchmarks/PcamTrace.java", package / "PcamTrace.java")
    def replace(path, before, after):
        text = path.read_text()
        if text.count(before) != 1:
            raise ValueError(f"Unexpected upstream source at {path}")
        path.write_text(text.replace(before, after))
    replace(package / "video/CameraCapture.java",
            "// Called for each frame captured, do nothing",
            'com.genymobile.scrcpy.PcamTrace.event("sensor", timestamp, frameNumber);')
    replace(package / "video/CameraCapture.java",
            'Ln.i("Using camera \'" + cameraId + "\'");',
            'Ln.i("Using camera \'" + cameraId + "\'");\n'
            '            Ln.i("PCAM_TIMESTAMP_SOURCE=" + ServiceManager.getCameraManager()'
            '.getCameraCharacteristics(cameraId).get(CameraCharacteristics.SENSOR_INFO_TIMESTAMP_SOURCE));')
    replace(package / "device/Streamer.java",
            "long pts = bufferInfo.presentationTimeUs;",
            'long pts = bufferInfo.presentationTimeUs;\n'
            '        if ((bufferInfo.flags & MediaCodec.BUFFER_FLAG_CODEC_CONFIG) == 0) {\n'
            '            com.genymobile.scrcpy.PcamTrace.event("encoded", pts * 1000, -1);\n'
            '        }')
    (package / "BuildConfig.java").write_text(
        'package com.genymobile.scrcpy; public final class BuildConfig {'
        'public static final boolean DEBUG=false; public static final String VERSION_NAME="5.0.1";}')
    # This hidden platform interface is used only to type-check display code.
    # Exclude its compile stub from DEX; Android supplies the real implementation.
    stub = source / "android/view/IDisplayWindowListener.java"
    stub.parent.mkdir(parents=True)
    stub.write_text('''package android.view;
public interface IDisplayWindowListener extends android.os.IInterface {
    void onDisplayAdded(int id);
    void onDisplayRemoved(int id);
    void onDisplayConfigurationChanged(int id, android.content.res.Configuration config);
    abstract class Stub extends android.os.Binder implements IDisplayWindowListener {
        public android.os.IBinder asBinder() { return this; }
    }
}''')
    classes = SCRATCH / "instrumented-classes"
    if classes.exists():
        shutil.rmtree(classes)
    classes.mkdir()
    android = SCRATCH / "tools/android-platform/android-36/android.jar"
    run("javac", "--release", "8", "-cp", android, "-d", classes, *source.rglob("*.java"))
    dex(classes, SCRATCH / "instrumented-dex", android)
    run("jar", "cf", SCRATCH / "instrumented-scrcpy.jar", "-C", SCRATCH / "instrumented-dex", "classes.dex")
    print(SCRATCH / "instrumented-scrcpy.jar")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instrumented", action="store_true")
    main(parser.parse_args().instrumented)
