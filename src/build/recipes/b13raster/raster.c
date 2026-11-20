/* Native CPU accumulation for the ordinary RGBA texture on Adreno 840. */
#include <math.h>
#include <stddef.h>
#include <stdint.h>

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT __attribute__((visibility("default")))
#endif

EXPORT void b13_accumulate(int count, const float *x, const float *y,
                           const float *support, const float *tone, float scale,
                           int width, int low, int high, float *total, float *dye)
{
    for (int i = 0; i < count; ++i) {
        float radius = support[i], reach = radius * scale;
        int left = (int)floorf(x[i] - reach), right = (int)ceilf(x[i] + reach);
        int bottom = (int)floorf(y[i] - reach), top = (int)ceilf(y[i] + reach);
        if (left < 0) left = 0;
        if (right > width) right = width;
        if (bottom < low) bottom = low;
        if (top > high) top = high;
        for (int row = bottom; row < top; ++row) {
            float dy = ((float)row + .5f - y[i]) / scale;
            float dy2 = dy * dy;
            size_t offset = (size_t)(row - low) * width;
            for (int col = left; col < right; ++col) {
                float dx = ((float)col + .5f - x[i]) / scale;
                float squared = dx * dx + dy2;
                if (squared < radius * radius) {
                    float coverage = radius - sqrtf(squared);
                    if (coverage > .86f) coverage = .86f;
                    total[offset + col] += coverage;
                    dye[offset + col] += coverage * tone[i];
                }
            }
        }
    }
}

static uint8_t channel(float value)
{
    if (value <= 0.f) return 0;
    if (value >= 255.f) return 255;
    return (uint8_t)nearbyintf(value);
}

EXPORT void b13_composite(int width, int height, const float *total, const float *dye,
                          const float *y, const float *extinction, const float *body,
                          const float *core, const uint8_t *inside_lip, int above_outlet,
                          const double *surface, float red, float green, float blue,
                          uint8_t *rgba)
{
    uint8_t base[3] = {channel(red), channel(green), channel(blue)};
    for (int row = 0; row < height; ++row) {
        for (int col = 0; col < width; ++col) {
            size_t index = (size_t)row * width + col;
            uint8_t *pixel = rgba + 4 * index;
            float ink = total[index];
            if (ink <= 0.f) {
                pixel[0] = base[0];
                pixel[1] = base[1];
                pixel[2] = base[2];
                pixel[3] = 0;
                continue;
            }
            float tone = dye[index] / ink;
            pixel[0] = channel(red + 18.f * tone);
            pixel[1] = channel(green + 20.f * tone);
            pixel[2] = channel(blue + 17.f * tone);
            float alpha = 0.f;
            if (y[row] >= surface[col] && (row < above_outlet || inside_lip[col])) {
                alpha = 1.f - expf(-extinction[row] * ink);
                alpha += (1.f - alpha) * alpha * body[row] * core[col];
            }
            pixel[3] = channel(255.f * alpha);
        }
    }
}
