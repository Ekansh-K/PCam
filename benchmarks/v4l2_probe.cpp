#include <cerrno>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <fcntl.h>
#include <linux/videodev2.h>
#include <poll.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>
#include <vector>
#include "frame_hash.h"

static volatile sig_atomic_t stopped = 0;
static void stop(int) { stopped = 1; }
static int64_t now_ns() {
    timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return now.tv_sec * INT64_C(1000000000) + now.tv_nsec;
}
static void checked(int result, const char *operation) {
    if (result < 0) { perror(operation); std::exit(1); }
}

int main(int argc, char **argv) {
    if (argc != 2) return 2;
    signal(SIGTERM, stop);
    signal(SIGINT, stop);
    int device = open(argv[1], O_RDWR | O_NONBLOCK | O_CLOEXEC);
    checked(device, "open V4L2 consumer");
    v4l2_capability capabilities{};
    checked(ioctl(device, VIDIOC_QUERYCAP, &capabilities), "consumer QUERYCAP");
    v4l2_format format{};
    format.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    checked(ioctl(device, VIDIOC_G_FMT, &format), "consumer G_FMT");
    if (!format.fmt.pix.width || format.fmt.pix.pixelformat != V4L2_PIX_FMT_YUYV) return 3;
    v4l2_requestbuffers request{};
    request.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    request.memory = V4L2_MEMORY_MMAP;
    request.count = 2;
    checked(ioctl(device, VIDIOC_REQBUFS, &request), "consumer REQBUFS");
    std::vector<void *> memory(request.count);
    std::vector<size_t> sizes(request.count);
    for (unsigned index = 0; index < request.count; ++index) {
        v4l2_buffer buffer{};
        buffer.type = request.type;
        buffer.memory = request.memory;
        buffer.index = index;
        checked(ioctl(device, VIDIOC_QUERYBUF, &buffer), "consumer QUERYBUF");
        sizes[index] = buffer.length;
        memory[index] = mmap(nullptr, buffer.length, PROT_READ | PROT_WRITE, MAP_SHARED, device, buffer.m.offset);
        if (memory[index] == MAP_FAILED) { perror("consumer mmap"); return 1; }
        checked(ioctl(device, VIDIOC_QBUF, &buffer), "consumer QBUF");
    }
    v4l2_buf_type type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    checked(ioctl(device, VIDIOC_STREAMON, &type), "consumer STREAMON");
    puts("sequence,kernel_ns,dequeued_ns,bytes,width,height,fingerprint");
    fflush(stdout);
    while (!stopped) {
        pollfd descriptor{device, POLLIN, 0};
        int ready = poll(&descriptor, 1, 200);
        if (ready < 0 && errno == EINTR) continue;
        checked(ready, "consumer poll");
        if (ready == 0) continue;
        v4l2_buffer buffer{};
        buffer.type = type;
        buffer.memory = V4L2_MEMORY_MMAP;
        int result = ioctl(device, VIDIOC_DQBUF, &buffer);
        int64_t dequeued = now_ns();
        if (result < 0 && (errno == EAGAIN || errno == EINTR)) continue;
        checked(result, "consumer DQBUF");
        if (buffer.index >= memory.size() || buffer.bytesused > sizes[buffer.index]) return 4;
        uint64_t fingerprint = frame_hash(static_cast<const uint8_t *>(memory[buffer.index]), buffer.bytesused);
        int64_t kernel = buffer.timestamp.tv_sec * INT64_C(1000000000) + buffer.timestamp.tv_usec * INT64_C(1000);
        printf("%u,%lld,%lld,%u,%u,%u,%llu\n", buffer.sequence, (long long)kernel, (long long)dequeued,
               buffer.bytesused, format.fmt.pix.width, format.fmt.pix.height, (unsigned long long)fingerprint);
        fflush(stdout);
        checked(ioctl(device, VIDIOC_QBUF, &buffer), "consumer requeue");
    }
    checked(ioctl(device, VIDIOC_STREAMOFF, &type), "consumer STREAMOFF");
    for (unsigned index = 0; index < memory.size(); ++index) munmap(memory[index], sizes[index]);
    close(device);
    return 0;
}
