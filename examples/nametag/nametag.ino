/**
 * @file      nametag.ino
 * @brief     E-paper conference name tag with multi-page cycling on BUTTON_1 press.
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
 *            Asset headers (qrcode_*.h, *_logo.h, name_font.h, projects_data.h)
 *            are produced by `uv run generate_assets.py`. See README.md.
 */

#ifndef BOARD_HAS_PSRAM
#error "Please enable PSRAM, Arduino IDE -> tools -> PSRAM -> OPI !!!"
#endif

#include <Arduino.h>
#include "epd_driver.h"
#include "firasans.h"
#include "utilities.h"

#include "name_font.h"
#include "streamlit_logo.h"
#include "stlite_logo.h"
#include "qrcode_site.h"
#include "qrcode_github.h"
#include "qrcode_linkedin.h"
#include "qrcode_stlite.h"
#include "qrcode_webrtc.h"
#include "projects_data.h"

static const char NAME_TEXT[]   = "Yuichiro Tachibana";
static const char HANDLE_TEXT[] = "@whitphx";
static const char URL_TEXT[]    = "https://whitphx.info/";

static const int32_t MARGIN_X = 60;
static const int32_t MARGIN_Y_TOP = 60;
static const int32_t LOGO_GAP = 32;
static const int32_t ICON_ROW_BOTTOM_MARGIN = 30;

static const uint8_t PAGE_COUNT = 4;

// Topics for the "Ask me about" page. Areas of familiarity that aren't
// projects in their own right — edit freely; the layout fits 4 entries
// best (2x2 grid).
static const char *const TOPICS[] = {
    "Streamlit",
    "Python",
    "Pyodide",
    "WebRTC",
};
static const size_t TOPICS_COUNT = sizeof(TOPICS) / sizeof(TOPICS[0]);

// Per-project repo QR bitmaps, indexed in the same order as projects[] in
// projects_data.h (Stlite, Streamlit-WebRTC).
static const uint8_t  *const PROJECT_QR_DATA[]    = { qrcode_stlite_data, qrcode_webrtc_data };
static const uint32_t        PROJECT_QR_WIDTHS[]  = { qrcode_stlite_width, qrcode_webrtc_width };
static const uint32_t        PROJECT_QR_HEIGHTS[] = { qrcode_stlite_height, qrcode_webrtc_height };
static const uint32_t        PROJECT_QR_SLOT_W    = 170;  // matches QR_LINKS target_px
// BUTTON_1 (GPIO 21) is the board's dedicated user button (see utilities.h).
// BOOT/IO0 stays free for the bootloader-entry sequence (hold BOOT, tap RST).
static const gpio_num_t USER_BUTTON_GPIO = (gpio_num_t)BUTTON_1;

enum Mode : uint8_t {
    MODE_MANUAL = 0,
    MODE_AUTO   = 1,
};

// RTC_NOINIT_ATTR persists across crashes/resets without being touched by
// startup code; the magic sentinel distinguishes a real cold boot (where RTC
// RAM holds power-on garbage) from a soft reset.
RTC_NOINIT_ATTR uint32_t page_state_magic;
RTC_NOINIT_ATTR uint8_t  page_index;
RTC_NOINIT_ATTR uint8_t  current_mode;

static const uint32_t PAGE_STATE_MAGIC  = 0xC0DEFEEDu;
static const uint32_t AUTO_INTERVAL_MS  = 10000;  // auto-play page dwell time
static const uint32_t LONG_PRESS_MS     = 1000;
static const uint32_t DEBOUNCE_MS       = 30;

static uint8_t *framebuffer = NULL;


static void draw_image_at(int32_t x, int32_t y, uint32_t w, uint32_t h, const uint8_t *data)
{
    Rect_t area = { .x = x, .y = y, .width = (int32_t)w, .height = (int32_t)h };
    epd_copy_to_framebuffer(area, (uint8_t *)data, framebuffer);
}


// writeln() draws on the cursor baseline. Offset by ascender so y_top is the
// visual top of the line, and return the y_top that follows.
static int32_t draw_line(const GFXfont *font, const char *str, int32_t x, int32_t y_top)
{
    int32_t cx = x;
    int32_t cy = y_top + font->ascender;
    writeln((GFXfont *)font, str, &cx, &cy, framebuffer);
    return y_top + font->advance_y;
}


static int32_t draw_line_centered(const GFXfont *font, const char *str, int32_t center_x, int32_t y_top)
{
    int32_t x = 0, y = 0, x1 = 0, y1 = 0, w = 0, h = 0;
    get_text_bounds((const GFXfont *)font, str, &x, &y, &x1, &y1, &w, &h, NULL);
    return draw_line(font, str, center_x - w / 2, y_top);
}


// Bottom-right indicator row: a small filled ▶ triangle (only shown in AUTO
// mode), followed by the page dots (filled = active, hollow = inactive).
// FiraSans doesn't ship ●/○/▶ glyphs, so render as primitives.
static void draw_indicator_dots(uint8_t active, uint8_t mode)
{
    const int32_t r = 6;
    const int32_t spacing = 22;
    const int32_t total_w = (PAGE_COUNT - 1) * spacing;
    const int32_t y = EPD_HEIGHT - 24;
    const int32_t x_start = EPD_WIDTH - MARGIN_X - total_w;

    if (mode == MODE_AUTO) {
        const int32_t tri_h = 8;
        const int32_t tri_w = 12;
        const int32_t tx = x_start - 28;
        epd_fill_triangle(tx, y - tri_h, tx, y + tri_h, tx + tri_w, y, 0, framebuffer);
    }

    for (uint8_t i = 0; i < PAGE_COUNT; i++) {
        int32_t cx = x_start + i * spacing;
        if (i == active) {
            epd_fill_circle(cx, y, r, 0, framebuffer);
        } else {
            epd_draw_circle(cx, y, r, 0, framebuffer);
        }
    }
}


static void draw_page_default()
{
    int32_t qr_x = EPD_WIDTH - (int32_t)qrcode_site_width - MARGIN_X;
    int32_t qr_y = MARGIN_Y_TOP;
    draw_image_at(qr_x, qr_y, qrcode_site_width, qrcode_site_height, qrcode_site_data);

    // Name's y_top == QR's y_top so their top edges line up.
    int32_t y_top = MARGIN_Y_TOP;
    y_top = draw_line(&NameFontBold, NAME_TEXT, MARGIN_X, y_top);
    y_top += 20;
    y_top = draw_line(&FiraSans, HANDLE_TEXT, MARGIN_X, y_top);
    y_top += 20;
    y_top = draw_line(&FiraSans, URL_TEXT, MARGIN_X, y_top);

    // Icon row: bottom-aligned band, the two logos have different heights so
    // center-align them around the row's midline.
    int32_t icon_max_h = (int32_t)streamlit_logo_height > (int32_t)stlite_logo_height
                             ? (int32_t)streamlit_logo_height
                             : (int32_t)stlite_logo_height;
    int32_t row_center_y = EPD_HEIGHT - ICON_ROW_BOTTOM_MARGIN - icon_max_h / 2;
    int32_t streamlit_y = row_center_y - (int32_t)streamlit_logo_height / 2;
    int32_t stlite_y    = row_center_y - (int32_t)stlite_logo_height / 2;
    int32_t streamlit_x = MARGIN_X;
    int32_t stlite_x    = streamlit_x + (int32_t)streamlit_logo_width + LOGO_GAP;
    draw_image_at(streamlit_x, streamlit_y, streamlit_logo_width, streamlit_logo_height, streamlit_logo_data);
    draw_image_at(stlite_x,    stlite_y,    stlite_logo_width,    stlite_logo_height,    stlite_logo_data);
}


static void draw_page_projects()
{
    int32_t y_top = MARGIN_Y_TOP;
    y_top = draw_line(&NameFontBold, "My projects", MARGIN_X, y_top);
    y_top += 20;

    // Each project row: bold name + indented "desc · N stars" on the left,
    // repo QR on the right. Rows are tall enough to fit the QR; vertically
    // center the text within the row. ("·" = U+00B7, UTF-8 0xC2 0xB7.)
    const int32_t row_gap         = 10;
    const int32_t text_block_h    = NameFontBold.advance_y + FiraSans.advance_y;
    char desc_line[160];

    for (size_t i = 0; i < projects_count; i++) {
        int32_t row_h = (int32_t)PROJECT_QR_SLOT_W;
        int32_t text_top = y_top + (row_h - text_block_h) / 2;

        int32_t text_y = draw_line(&NameFontBold, projects[i].name, MARGIN_X + 20, text_top);
        if (projects[i].stars[0]) {
            snprintf(desc_line, sizeof(desc_line), "%s \xC2\xB7 %s stars",
                     projects[i].desc, projects[i].stars);
        } else {
            snprintf(desc_line, sizeof(desc_line), "%s", projects[i].desc);
        }
        draw_line(&FiraSans, desc_line, MARGIN_X + 60, text_y);

        int32_t slot_x = EPD_WIDTH - MARGIN_X - (int32_t)PROJECT_QR_SLOT_W;
        int32_t qr_w   = (int32_t)PROJECT_QR_WIDTHS[i];
        int32_t qr_h   = (int32_t)PROJECT_QR_HEIGHTS[i];
        int32_t qr_x   = slot_x + ((int32_t)PROJECT_QR_SLOT_W - qr_w) / 2;
        int32_t qr_y   = y_top + (row_h - qr_h) / 2;
        draw_image_at(qr_x, qr_y, qr_w, qr_h, PROJECT_QR_DATA[i]);

        y_top += row_h + row_gap;
    }
}


// 2×2 grid of expertise topics. Each topic centered within its quadrant in
// the bold name font, so the page reads as a tag cloud rather than a list.
static void draw_page_topics()
{
    int32_t y_top = MARGIN_Y_TOP;
    y_top = draw_line_centered(&NameFontBold, "Ask me about", EPD_WIDTH / 2, y_top);
    y_top += 50;

    const int32_t left_col_x  = EPD_WIDTH / 4;
    const int32_t right_col_x = 3 * EPD_WIDTH / 4;
    const int32_t row_pitch   = NameFontBold.advance_y + 70;

    for (size_t i = 0; i < TOPICS_COUNT; i += 2) {
        int32_t row_y = y_top + (int32_t)(i / 2) * row_pitch;
        draw_line_centered(&NameFontBold, TOPICS[i], left_col_x, row_y);
        if (i + 1 < TOPICS_COUNT) {
            draw_line_centered(&NameFontBold, TOPICS[i + 1], right_col_x, row_y);
        }
    }
}


static void draw_page_connect()
{
    int32_t y_top = MARGIN_Y_TOP;
    y_top = draw_line_centered(&NameFontBold, "Connect", EPD_WIDTH / 2, y_top);
    y_top += 40;

    const int32_t qr_w = (int32_t)qrcode_site_width;
    const int32_t qr_h = (int32_t)qrcode_site_height;
    const int32_t qr_gap = 30;
    const int32_t total_w = qr_w * 3 + qr_gap * 2;
    int32_t qr_x = (EPD_WIDTH - total_w) / 2;
    int32_t qr_y = y_top;
    int32_t label_y_top = qr_y + qr_h + 20;

    draw_image_at(qr_x, qr_y, qrcode_site_width, qrcode_site_height, qrcode_site_data);
    draw_line_centered(&FiraSans, "Site", qr_x + qr_w / 2, label_y_top);

    qr_x += qr_w + qr_gap;
    draw_image_at(qr_x, qr_y, qrcode_github_width, qrcode_github_height, qrcode_github_data);
    draw_line_centered(&FiraSans, "GitHub", qr_x + qr_w / 2, label_y_top);

    qr_x += qr_w + qr_gap;
    // LinkedIn QR may be a hair narrower than the other two; center it within the slot.
    int32_t lk_offset_x = (qr_w - (int32_t)qrcode_linkedin_width) / 2;
    draw_image_at(qr_x + lk_offset_x, qr_y,
                  qrcode_linkedin_width, qrcode_linkedin_height, qrcode_linkedin_data);
    draw_line_centered(&FiraSans, "LinkedIn", qr_x + qr_w / 2, label_y_top);
}


static void render_current_page()
{
    memset(framebuffer, 0xFF, EPD_WIDTH * EPD_HEIGHT / 2);

    switch (page_index) {
    case 0: draw_page_default();  break;
    case 1: draw_page_projects(); break;
    case 2: draw_page_topics();   break;
    case 3: draw_page_connect();  break;
    default:
        page_index = 0;
        draw_page_default();
        break;
    }

    draw_indicator_dots(page_index, current_mode);

    epd_poweron();
    epd_clear();
    epd_draw_grayscale_image(epd_full_screen(), framebuffer);
    epd_poweroff_all();
}


static void advance_page()
{
    page_index = (page_index + 1) % PAGE_COUNT;
}


void setup()
{
    Serial.begin(115200);

    bool cold_boot = (page_state_magic != PAGE_STATE_MAGIC);
    if (cold_boot) {
        page_state_magic = PAGE_STATE_MAGIC;
        page_index = 0;
        current_mode = MODE_MANUAL;
    }
    Serial.printf("[nametag] cold=%d page=%u mode=%s\n",
                  (int)cold_boot, page_index,
                  current_mode == MODE_AUTO ? "AUTO" : "MANUAL");

    pinMode(USER_BUTTON_GPIO, INPUT_PULLUP);

    framebuffer = (uint8_t *)ps_calloc(sizeof(uint8_t), EPD_WIDTH * EPD_HEIGHT / 2);
    if (!framebuffer) {
        Serial.println("alloc memory failed !!!");
        while (1);
    }

    epd_init();
    render_current_page();
}


void loop()
{
    // Always-awake polling loop: BUTTON_1 (short = next page, long = toggle
    // mode) plus a wall-clock timer for AUTO mode. No deep sleep — that path
    // was unreliable and would have been incompatible with auto-play anyway.
    static uint32_t last_render_ms  = 0;
    static uint32_t last_check_ms   = 0;
    static uint32_t button_down_ms  = 0;
    static int      last_button     = HIGH;

    uint32_t now = millis();

    if (now - last_check_ms >= DEBOUNCE_MS) {
        last_check_ms = now;
        int btn = digitalRead(USER_BUTTON_GPIO);
        if (last_button == HIGH && btn == LOW) {
            button_down_ms = now;
        } else if (last_button == LOW && btn == HIGH) {
            uint32_t held = now - button_down_ms;
            if (held >= LONG_PRESS_MS) {
                current_mode = (current_mode == MODE_MANUAL) ? MODE_AUTO : MODE_MANUAL;
                Serial.printf("[nametag] mode -> %s\n",
                              current_mode == MODE_AUTO ? "AUTO" : "MANUAL");
                render_current_page();
            } else if (held >= DEBOUNCE_MS) {
                advance_page();
                Serial.printf("[nametag] btn page -> %u\n", page_index);
                render_current_page();
            }
            last_render_ms = now;
        }
        last_button = btn;
    }

    if (current_mode == MODE_AUTO && now - last_render_ms >= AUTO_INTERVAL_MS) {
        advance_page();
        Serial.printf("[nametag] auto page -> %u\n", page_index);
        render_current_page();
        last_render_ms = now;
    }

    delay(10);
}
