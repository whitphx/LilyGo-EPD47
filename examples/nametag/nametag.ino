/**
 * @file      nametag.ino
 * @brief     E-paper conference name tag: name + handle + QR code + project icons.
 * @note      Arduino Setting (matches the other examples in this repo)
 *            Tools ->
 *                  Board:"ESP32S3 Dev Module"
 *                  USB CDC On Boot:"Enable"
 *                  USB DFU On Boot:"Disable"
 *                  Flash Size : "16MB(128Mb)"
 *                  Flash Mode"QIO 80MHz
 *                  Partition Scheme:"16M Flash(3M APP/9.9MB FATFS)"
 *                  PSRAM:"OPI PSRAM"
 *                  Upload Mode:"UART0/Hardware CDC"
 *                  USB Mode:"Hardware CDC and JTAG"
 *
 *            Asset headers (qrcode_data.h, streamlit_logo.h, stlite_logo.h)
 *            are produced by `uv run generate_assets.py`. See README.md.
 */

#ifndef BOARD_HAS_PSRAM
#error "Please enable PSRAM, Arduino IDE -> tools -> PSRAM -> OPI !!!"
#endif

#include <Arduino.h>
#include "epd_driver.h"
#include "firasans.h"
#include "utilities.h"

#include "qrcode_data.h"
#include "name_font.h"
#include "streamlit_logo.h"
#include "stlite_logo.h"

static const char NAME_TEXT[]   = "Yuichiro Tachibana";
static const char HANDLE_TEXT[] = "@whitphx";
static const char URL_TEXT[]    = "https://whitphx.info/";

static const int32_t MARGIN_X = 60;
static const int32_t LOGO_GAP = 24;

static uint8_t *framebuffer = NULL;

static void draw_image_at(int32_t x, int32_t y, uint32_t w, uint32_t h, const uint8_t *data)
{
    Rect_t area = { .x = x, .y = y, .width = (int32_t)w, .height = (int32_t)h };
    epd_copy_to_framebuffer(area, (uint8_t *)data, framebuffer);
}

static void draw_nametag()
{
    memset(framebuffer, 0xFF, EPD_WIDTH * EPD_HEIGHT / 2);

    int32_t qr_x = EPD_WIDTH - (int32_t)qrcode_width - MARGIN_X;
    int32_t qr_y = (EPD_HEIGHT - (int32_t)qrcode_height) / 2;
    draw_image_at(qr_x, qr_y, qrcode_width, qrcode_height, qrcode_data);

    int32_t text_x = MARGIN_X;
    // writeln() draws text on the cursor's baseline, so offset the desired
    // top-of-text by the font's ascender to get the first baseline.
    int32_t name_baseline_y = 90 + NameFontBold.ascender;

    int32_t cursor_x = text_x;
    int32_t cursor_y = name_baseline_y;
    writeln((GFXfont *)&NameFontBold, NAME_TEXT, &cursor_x, &cursor_y, framebuffer);

    cursor_x = text_x;
    cursor_y = name_baseline_y + NameFontBold.advance_y + 20;
    writeln((GFXfont *)&FiraSans, HANDLE_TEXT, &cursor_x, &cursor_y, framebuffer);

    cursor_x = text_x;
    cursor_y += FiraSans.advance_y + 20;
    writeln((GFXfont *)&FiraSans, URL_TEXT, &cursor_x, &cursor_y, framebuffer);

    int32_t icon_y = EPD_HEIGHT - (int32_t)streamlit_logo_height - 50;
    draw_image_at(text_x,
                  icon_y,
                  streamlit_logo_width,
                  streamlit_logo_height,
                  streamlit_logo_data);
    draw_image_at(text_x + (int32_t)streamlit_logo_width + LOGO_GAP,
                  icon_y,
                  stlite_logo_width,
                  stlite_logo_height,
                  stlite_logo_data);

    epd_draw_grayscale_image(epd_full_screen(), framebuffer);
}

void setup()
{
    Serial.begin(115200);

    framebuffer = (uint8_t *)ps_calloc(sizeof(uint8_t), EPD_WIDTH * EPD_HEIGHT / 2);
    if (!framebuffer) {
        Serial.println("alloc memory failed !!!");
        while (1);
    }

    epd_init();
    epd_poweron();
    epd_clear();
    draw_nametag();
    epd_poweroff_all();

    // E-paper holds the image without power, so deep-sleep until the BOOT
    // (IO0) button is pressed to redraw.
    esp_sleep_enable_ext1_wakeup(_BV(0), ESP_EXT1_WAKEUP_ANY_LOW);
    esp_deep_sleep_start();
}

void loop()
{
    // Unreachable: setup() enters deep sleep after drawing.
}
