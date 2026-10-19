#include <cerrno>
#include <cstdint>
#include <cstring>
#include <cstdio>
#include <ctime>
#include <fcntl.h>
#include <linux/videodev2.h>
#include <sys/ioctl.h>
#include <unistd.h>
#include <string>
#include <vector>
#include <libyuv/rotate.h>
#include "frame_hash.h"
extern "C" {
#include <libavcodec/avcodec.h>
#include <libavutil/error.h>
#include <libavutil/pixdesc.h>
#include <libswscale/swscale.h>
}

struct Decoder {
    AVCodecContext *context;
    AVFrame *frame;
    int output_fd = -1;
    int output_fps = 60;
    int output_width = 0, output_height = 0, output_stride = 0;
    int target_width = 0, target_height = 0;
    std::string output_device;
    int64_t next_resize_ns = 0;
    SwsContext *converter = nullptr;
    int converter_format = -1;
    int converter_width = 0, converter_height = 0;
    int rotation = 0;
    AVFrame *rotated = nullptr;
    std::vector<uint8_t> pixels;
};
struct DecodedFrame {
    int64_t pts_us, ready_ns;
    int width, height;
    int64_t converted_ns, write_start_ns, write_done_ns;
    uint64_t fingerprint;
};

static int64_t now_ns() {
    timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return now.tv_sec * INT64_C(1000000000) + now.tv_nsec;
}

static int device_error(const char *operation) {
    int error = errno;
    std::fprintf(stderr, "%s: %s\n", operation, std::strerror(error));
    return -error;
}

extern "C" int decoder_set_output(void *handle, const char *device, int fps) {
    auto *decoder = static_cast<Decoder *>(handle);
    decoder->output_fd = open(device, O_WRONLY | O_CLOEXEC);
    if (decoder->output_fd < 0) return -errno;
    v4l2_capability caps{};
    if (ioctl(decoder->output_fd, VIDIOC_QUERYCAP, &caps) < 0) return device_error("V4L2 QUERYCAP");
    // Old exclusive-caps loopback versions can temporarily advertise neither
    // direction after a write-only producer exits. Verify the driver itself.
    if (std::strcmp(reinterpret_cast<const char *>(caps.driver), "v4l2 loopback") != 0) return -ENOTSUP;
    decoder->output_fps = fps;
    decoder->output_device = device;
    return 0;
}

extern "C" int decoder_set_rotation(void *handle, int rotation) {
    if (rotation != 0 && rotation != 90 && rotation != 270) return -EINVAL;
    static_cast<Decoder *>(handle)->rotation = rotation;
    return 0;
}

extern "C" int decoder_set_size(void *handle, int width, int height) {
    if (width < 2 || height < 2 || width > 4096 || height > 4096 || (width & 1) || (height & 1)) return -EINVAL;
    auto *decoder = static_cast<Decoder *>(handle);
    decoder->target_width = width;
    decoder->target_height = height;
    return 0;
}

static int output_frame(Decoder *decoder, DecodedFrame &result) {
    AVFrame *frame = decoder->frame;
    if (decoder->rotation) {
        if (frame->format != AV_PIX_FMT_YUV420P && frame->format != AV_PIX_FMT_YUVJ420P) {
            std::fprintf(stderr, "Unsupported rotation pixel format: %s\n", av_get_pix_fmt_name(static_cast<AVPixelFormat>(frame->format)));
            return -ENOTSUP;
        }
        if (!decoder->rotated || decoder->rotated->width != frame->height || decoder->rotated->height != frame->width || decoder->rotated->format != frame->format) {
            av_frame_free(&decoder->rotated);
            decoder->rotated = av_frame_alloc();
            if (!decoder->rotated) return -ENOMEM;
            decoder->rotated->format = frame->format;
            decoder->rotated->width = frame->height;
            decoder->rotated->height = frame->width;
            if (av_frame_get_buffer(decoder->rotated, 32) < 0) return -ENOMEM;
        }
        AVFrame *rotated = decoder->rotated;
        if (libyuv::I420Rotate(frame->data[0], frame->linesize[0], frame->data[1], frame->linesize[1],
                frame->data[2], frame->linesize[2], rotated->data[0], rotated->linesize[0],
                rotated->data[1], rotated->linesize[1], rotated->data[2], rotated->linesize[2],
                frame->width, frame->height, static_cast<libyuv::RotationMode>(decoder->rotation)) != 0) return -EINVAL;
        frame = rotated;
        result.width = frame->width;
        result.height = frame->height;
    }
    int width = decoder->target_width ? decoder->target_width : frame->width;
    int height = decoder->target_height ? decoder->target_height : frame->height;
    // This loopback driver releases its format only after every opener closes.
    // Keep feeding the old size until the viewer closes, then negotiate again.
    if (decoder->output_width && (decoder->output_width != width || decoder->output_height != height)
            && !decoder->output_device.empty()) {
        if (now_ns() >= decoder->next_resize_ns) {
            close(decoder->output_fd);
            decoder->output_fd = open(decoder->output_device.c_str(), O_WRONLY | O_CLOEXEC);
            if (decoder->output_fd < 0) return -errno;
            decoder->next_resize_ns = now_ns() + INT64_C(1000000000);
        } else {
            width = decoder->output_width; height = decoder->output_height;
        }
    }
    if (decoder->output_width != width || decoder->output_height != height) {
        v4l2_format format{};
        format.type = V4L2_BUF_TYPE_VIDEO_OUTPUT;
        format.fmt.pix.width = width;
        format.fmt.pix.height = height;
        format.fmt.pix.pixelformat = V4L2_PIX_FMT_YUYV;
        format.fmt.pix.field = V4L2_FIELD_NONE;
        format.fmt.pix.bytesperline = width * 2;
        format.fmt.pix.sizeimage = width * height * 2;
        if (ioctl(decoder->output_fd, VIDIOC_S_FMT, &format) < 0) return device_error("V4L2 S_FMT");
        if (format.fmt.pix.pixelformat != V4L2_PIX_FMT_YUYV) return -EINVAL;
        // An attached viewer pins the old format; retry after it closes.
        width = format.fmt.pix.width; height = format.fmt.pix.height;
        if (!decoder->target_width && (width != frame->width || height != frame->height)) return -EINVAL;
        decoder->output_width = width;
        decoder->output_height = height;
        decoder->output_stride = format.fmt.pix.bytesperline;
        decoder->pixels.resize(format.fmt.pix.sizeimage);
        sws_freeContext(decoder->converter); decoder->converter = nullptr;
        v4l2_streamparm parameters{};
        parameters.type = V4L2_BUF_TYPE_VIDEO_OUTPUT;
        parameters.parm.output.timeperframe.numerator = 1;
        parameters.parm.output.timeperframe.denominator = decoder->output_fps;
        if (ioctl(decoder->output_fd, VIDIOC_S_PARM, &parameters) < 0) return device_error("V4L2 S_PARM");
        v4l2_buf_type type = V4L2_BUF_TYPE_VIDEO_OUTPUT;
        // Register the writer explicitly so this driver's close path restores
        // output capabilities when the producer exits.
        if (ioctl(decoder->output_fd, VIDIOC_STREAMON, &type) < 0) return device_error("V4L2 STREAMON");
    }
    result.width = width;
    result.height = height;
    int crop_width = frame->width, crop_height = frame->height;
    const uint8_t *source_planes[] = {frame->data[0], frame->data[1], frame->data[2], frame->data[3]};
    if (decoder->target_width) {
        if (frame->format != AV_PIX_FMT_YUV420P && frame->format != AV_PIX_FMT_YUVJ420P) return -ENOTSUP;
        // Preserve proportions with a centered crop aligned to the chroma grid.
        if (int64_t(crop_width) * height > int64_t(crop_height) * width)
            crop_width = (int64_t(crop_height) * width / height) & ~1;
        else
            crop_height = (int64_t(crop_width) * height / width) & ~1;
        if (crop_width < 2 || crop_height < 2) return -EINVAL;
        const int x = ((frame->width - crop_width) / 2) & ~1;
        const int y = ((frame->height - crop_height) / 2) & ~1;
        source_planes[0] += y * frame->linesize[0] + x;
        for (int plane = 1; plane < 3; ++plane)
            source_planes[plane] += (y / 2) * frame->linesize[plane] + x / 2;
    }
    // FFmpeg internally normalizes YUVJ420P to YUV420P plus a range flag.
    // Cache by the original AVFrame format to avoid rebuilding that context
    // every frame, while retaining FFmpeg's full-to-limited range conversion.
    if (!decoder->converter || decoder->converter_format != frame->format
            || decoder->converter_width != crop_width || decoder->converter_height != crop_height) {
        sws_freeContext(decoder->converter);
        decoder->converter = sws_getContext(crop_width, crop_height, static_cast<AVPixelFormat>(frame->format),
                width, height, AV_PIX_FMT_YUYV422,
                crop_width == width && crop_height == height ? SWS_FAST_BILINEAR : SWS_BILINEAR,
                nullptr, nullptr, nullptr);
        decoder->converter_format = frame->format;
        decoder->converter_width = crop_width;
        decoder->converter_height = crop_height;
    }
    if (!decoder->converter) return -ENOMEM;
    uint8_t *planes[] = {decoder->pixels.data(), nullptr, nullptr, nullptr};
    int strides[] = {decoder->output_stride, 0, 0, 0};
    if (sws_scale(decoder->converter, source_planes, frame->linesize, 0, crop_height, planes, strides) != height)
        return -EINVAL;
    result.converted_ns = now_ns();
    result.fingerprint = frame_hash(decoder->pixels.data(), decoder->pixels.size());
    result.write_start_ns = now_ns();
    ssize_t written;
    do { written = write(decoder->output_fd, decoder->pixels.data(), decoder->pixels.size()); }
    while (written < 0 && errno == EINTR);
    result.write_done_ns = now_ns();
    if (written < 0) return -errno;
    if (size_t(written) != decoder->pixels.size()) return -EIO;
    return 0;
}

extern "C" void *decoder_open(const char *name) {
    const AVCodec *codec = avcodec_find_decoder_by_name(name);
    if (!codec) return nullptr;
    auto *decoder = new Decoder{};
    decoder->context = avcodec_alloc_context3(codec);
    decoder->frame = av_frame_alloc();
    if (!decoder->context || !decoder->frame) {
        avcodec_free_context(&decoder->context);
        av_frame_free(&decoder->frame);
        delete decoder;
        return nullptr;
    }
    decoder->context->thread_count = 1;
    decoder->context->flags |= AV_CODEC_FLAG_LOW_DELAY;
    decoder->context->pkt_timebase = AVRational{1, 1000000};
    if (avcodec_open2(decoder->context, codec, nullptr) < 0) {
        avcodec_free_context(&decoder->context);
        av_frame_free(&decoder->frame);
        delete decoder;
        return nullptr;
    }
    return decoder;
}

extern "C" int decoder_feed(void *handle, const uint8_t *data, int size,
                            int64_t pts, DecodedFrame *out, int capacity) {
    auto *decoder = static_cast<Decoder *>(handle);
    AVPacket *packet = av_packet_alloc();
    if (!packet) return AVERROR(ENOMEM);
    int result = av_new_packet(packet, size);
    if (result < 0) { av_packet_free(&packet); return result; }
    std::memcpy(packet->data, data, size);
    packet->pts = packet->dts = pts;
    result = avcodec_send_packet(decoder->context, packet);
    av_packet_free(&packet);
    if (result < 0) return result;
    int count = 0;
    while (count < capacity) {
        result = avcodec_receive_frame(decoder->context, decoder->frame);
        if (result == AVERROR(EAGAIN) || result == AVERROR_EOF) break;
        if (result < 0) return result;
        auto &result = out[count++];
        result = {decoder->frame->pts, now_ns(), decoder->frame->width, decoder->frame->height, 0, 0, 0, 0};
        if (decoder->output_fd >= 0) {
            int error = output_frame(decoder, result);
            if (error < 0) return error;
        }
        av_frame_unref(decoder->frame);
    }
    return count;
}

extern "C" int decoder_feed_gray(void *handle, const uint8_t *data, int size, int64_t pts, int width, int height, DecodedFrame *out) {
    if (width < 2 || width > 2048 || height < 2 || height > 4096 || (width & 1) || (height & 1) || size != width * height) return -EINVAL;
    auto *decoder = static_cast<Decoder *>(handle);
    AVFrame *frame = decoder->frame;
    if (frame->width != width || frame->height != height || frame->format != AV_PIX_FMT_YUV420P || !frame->buf[0]) {
        av_frame_unref(frame); frame->width = width; frame->height = height; frame->format = AV_PIX_FMT_YUV420P;
        frame->color_range = AVCOL_RANGE_MPEG;
        int status = av_frame_get_buffer(frame, 32); if (status < 0) return status;
    }
    int status = av_frame_make_writable(frame); if (status < 0) return status;
    for (int row = 0; row < height; row++) std::memcpy(frame->data[0] + row * frame->linesize[0], data + row * width, width);
    for (int plane = 1; plane < 3; plane++) for (int row = 0; row < height / 2; row++)
        std::memset(frame->data[plane] + row * frame->linesize[plane], 128, width / 2);
    *out = {}; out->pts_us = pts; out->ready_ns = now_ns(); out->width = width; out->height = height;
    status = output_frame(decoder, *out);
    return status < 0 ? status : 1;
}

extern "C" const uint8_t *decoder_output_pixels(void *handle, int *size) {
    auto *decoder = static_cast<Decoder *>(handle);
    *size = static_cast<int>(decoder->pixels.size());
    return decoder->pixels.data();
}

extern "C" void decoder_close(void *handle) {
    auto *decoder = static_cast<Decoder *>(handle);
    avcodec_free_context(&decoder->context);
    av_frame_free(&decoder->frame);
    av_frame_free(&decoder->rotated);
    if (decoder->output_fd >= 0) {
        v4l2_buf_type type = V4L2_BUF_TYPE_VIDEO_OUTPUT;
        ioctl(decoder->output_fd, VIDIOC_STREAMOFF, &type);
        close(decoder->output_fd);
    }
    sws_freeContext(decoder->converter);
    delete decoder;
}
