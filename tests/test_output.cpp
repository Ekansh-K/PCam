#include <cassert>
#include <sys/mman.h>
#include "../benchmarks/decoder.cpp"

int main() {
    Decoder decoder{};
    decoder.frame = av_frame_alloc();
    decoder.frame->format = AV_PIX_FMT_YUVJ420P;
    decoder.frame->width = 4; decoder.frame->height = 2;
    assert(av_frame_get_buffer(decoder.frame, 32) == 0);
    const uint8_t y[] = {0, 255, 0, 255, 64, 128, 192, 255};
    for (int row = 0; row < 2; ++row)
        std::memcpy(decoder.frame->data[0] + row * decoder.frame->linesize[0], y + row * 4, 4);
    std::memset(decoder.frame->data[1], 128, 2);
    std::memset(decoder.frame->data[2], 128, 2);
    decoder.rotation = 90;
    decoder.output_width = 2; decoder.output_height = 4; decoder.output_stride = 4;
    decoder.pixels.resize(16);
    decoder.output_fd = memfd_create("pcam-synthetic-test", MFD_CLOEXEC);
    assert(decoder.output_fd >= 0);
    DecodedFrame result{};
    assert(output_frame(&decoder, result) == 0);
    assert(result.width == 2 && result.height == 4);
    uint8_t output[16];
    assert(lseek(decoder.output_fd, 0, SEEK_SET) == 0);
    assert(read(decoder.output_fd, output, sizeof(output)) == sizeof(output));
    const int expected[] = {71, 16, 126, 235, 181, 16, 235, 235};
    for (int index = 0; index < 8; ++index) {
        assert(std::abs(int(output[index * 2]) - expected[index]) <= 1);
        assert(output[index * 2 + 1] == 128);
    }
    assert(result.fingerprint == frame_hash(output, sizeof(output)));
    assert(result.write_start_ns <= result.write_done_ns);
    assert(lseek(decoder.output_fd, 0, SEEK_SET) == 0);
    const uint8_t gray[] = {16, 235, 16, 235, 71, 126, 181, 235};
    assert(decoder_feed_gray(&decoder, gray, sizeof(gray), 123456, 4, 2, &result) == 1);
    assert(result.pts_us == 123456 && result.width == 2 && result.height == 4);
    assert(lseek(decoder.output_fd, 0, SEEK_SET) == 0);
    assert(read(decoder.output_fd, output, sizeof(output)) == sizeof(output));
    for (int index = 0; index < 8; index++) {
        assert(output[index * 2] == expected[index]);
        assert(output[index * 2 + 1] == 128);
    }
    // Cropping must discard the side bars without stretching the portrait.
    decoder.rotation = 0;
    assert(decoder_set_size(&decoder, 3, 8) == -EINVAL);
    assert(decoder_set_size(&decoder, 4, 8) == 0);
    decoder.output_width = 4; decoder.output_height = 8; decoder.output_stride = 8;
    decoder.pixels.resize(64);
    // Changing source size with the same output also exercises converter caching.
    for (int scale = 1; scale <= 2; ++scale) {
        const int width = 12 * scale, height = 16 * scale;
        std::vector<uint8_t> bars(width * height, 32);
        for (int row = 0; row < height; ++row)
            std::memset(bars.data() + row * width + 2 * scale, 192, 8 * scale);
        assert(lseek(decoder.output_fd, 0, SEEK_SET) == 0);
        assert(decoder_feed_gray(&decoder, bars.data(), bars.size(), 123457, width, height, &result) == 1);
        assert(result.width == 4 && result.height == 8);
        for (size_t index = 0; index < decoder.pixels.size(); ++index)
            assert(decoder.pixels[index] == (index & 1 ? 128 : 192));
    }
    decoder.rotation = 90;
    for (const auto &size : {std::pair<int, int>{480, 960}, {720, 1600}, {960, 480}, {1600, 720}}) {
        decoder.rotation = size.first > size.second ? 0 : 90;
        assert(decoder_set_size(&decoder, size.first, size.second) == 0);
        decoder.output_width = size.first; decoder.output_height = size.second;
        decoder.output_stride = size.first * 2;
        decoder.pixels.resize(size.first * size.second * 2);
        std::vector<uint8_t> image(1280 * 720, 192);
        assert(decoder_feed_gray(&decoder, image.data(), image.size(), 123458, 1280, 720, &result) == 1);
        assert(result.width == size.first && result.height == size.second);
        for (size_t index = 0; index < decoder.pixels.size(); ++index)
            assert(decoder.pixels[index] == (index & 1 ? 128 : 192));
    }
    close(decoder.output_fd); av_frame_free(&decoder.frame); av_frame_free(&decoder.rotated);
    sws_freeContext(decoder.converter);
    puts("Portrait rotation, colour range, center crop and scaling verified.");
}
