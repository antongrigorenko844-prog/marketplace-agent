"""Слайды карточек в оранжевом стиле (1200x1600, Inter, белые плашки) — как 0am325025l_2/_3.

Запуск: python slide_render.py <папка-превью> [boards]. Блок __main__ — пример (01X301127C, 0am325025f, 0cw325025h, 28.10).
Правило владельца: на карточке одиночной детали — только сама деталь и «1 шт.», комплект не показываем."""
import os, sys
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops
import numpy as np

W, H = 1200, 1600
FD = "/usr/share/fonts/opentype/inter/"
def font(w, s): return ImageFont.truetype(FD + {"b": "Inter-Bold.otf", "r": "Inter-Regular.otf", "s": "Inter-SemiBold.otf"}[w], s)
BLACK, GREY, WHITE = (10, 10, 10), (90, 90, 90), (255, 255, 255)
REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "photos")


def bg(w=W, h=H, c=(600, 800)):
    y, x = np.mgrid[0:h, 0:w]
    d = np.sqrt(((x - c[0]) / w) ** 2 + ((y - c[1]) / h) ** 2) / 0.72
    d = np.clip(d, 0, 1)
    inner, outer = np.array([255, 146, 62]), np.array([255, 102, 0])
    a = inner[None, None, :] * (1 - d[..., None]) + outer[None, None, :] * d[..., None]
    return Image.fromarray(a.astype("uint8"), "RGB")


def plate(dr, box, fill=WHITE, outline=None, r=34, width=4):
    dr.rounded_rectangle(box, r, fill=fill, outline=outline, width=width)


def text_c(dr, cx, y, s, f, col):
    w = dr.textlength(s, font=f); dr.text((cx - w / 2, y), s, font=f, fill=col)


def fit(dr, s, weight, size, maxw):
    while dr.textlength(s, font=font(weight, size)) > maxw and size > 20:
        size -= 2
    return font(weight, size)


def header(im, dr, title, sub):
    plate(dr, (22, 22, W - 22, 250), r=40)
    f = fit(dr, title, "b", 78, W - 140); text_c(dr, W / 2, 68, title, f, BLACK)
    f2 = fit(dr, sub, "r", 50, W - 140); text_c(dr, W / 2, 160, sub, f2, BLACK)


def cutout(src, box, white_is_bg=True):
    """Вырезать деталь с оранжевого фона фото _1 (хромакей по оранжевому)."""
    im = Image.open(src).convert("RGB").crop(box)
    a = np.asarray(im).astype(int)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    orange = (r > 200) & (r - b > 120) & (g > 70) & (g < 190)
    white = (r > 225) & (g > 225) & (b > 225) if white_is_bg else np.zeros_like(r, bool)
    m = Image.fromarray(((~(orange | white)) * 255).astype("uint8"))
    m = m.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.GaussianBlur(1.2))
    im.putalpha(m)
    bb = m.point(lambda v: 255 if v > 128 else 0).getbbox()
    return im.crop(bb)


def photo_frame(im, box, part, scale=0.82, shadow=True):
    x0, y0, x1, y1 = box
    fw, fh = x1 - x0, y1 - y0
    inner = bg(fw, fh, (fw // 2, fh // 2))
    k = min(fw * scale / part.width, fh * scale / part.height)
    p = part.resize((int(part.width * k), int(part.height * k)), Image.LANCZOS)
    px, py = (fw - p.width) // 2, (fh - p.height) // 2
    if shadow:
        sh = Image.new("RGBA", p.size, (120, 40, 0, 0)); sh.putalpha(p.getchannel("A").point(lambda v: v * 0.35))
        sh = sh.filter(ImageFilter.GaussianBlur(14)); inner.paste(sh, (px + 14, py + 22), sh)
    inner.paste(p, (px, py), p)
    m = Image.new("L", (fw, fh), 0); ImageDraw.Draw(m).rounded_rectangle((0, 0, fw - 1, fh - 1), 34, fill=255)
    im.paste(inner, (x0, y0), m)
    ImageDraw.Draw(im).rounded_rectangle(box, 34, outline=WHITE, width=6)


def two_plates(dr, y, a, b):
    for (x0, x1), (t1, t2) in zip(((60, 585), (615, 1140)), (a, b)):
        plate(dr, (x0, y, x1, y + 170))
        if t2:
            dr.text((x0 + 36, y + 42), t1, font=fit(dr, t1, "b", 46, x1 - x0 - 70), fill=BLACK)
            dr.text((x0 + 36, y + 100), t2, font=fit(dr, t2, "r", 36, x1 - x0 - 70), fill=GREY)
        else:
            dr.text((x0 + 36, y + 60), t1, font=fit(dr, t1, "b", 46, x1 - x0 - 70), fill=BLACK)


def outline_plate(dr, y, t1, t2, h=210):
    plate(dr, (60, y, 1140, y + h), fill=None, outline=WHITE, width=4)
    dr.text((96, y + 58), t1, font=fit(dr, t1, "b", 46, 1000), fill=WHITE)
    dr.text((96, y + 118), t2, font=fit(dr, t2, "r", 38, 1000), fill=WHITE)


def slide_main(out, title, sub, part, p1, p2, o1, o2, scale=0.8):
    im = bg(); dr = ImageDraw.Draw(im)
    header(im, dr, title, sub)
    photo_frame(im, (55, 295, 1145, 1065), part, scale)
    dr = ImageDraw.Draw(im)
    two_plates(dr, 1100, p1, p2)
    outline_plate(dr, 1310, o1, o2)
    im.save(out)


def slide_fits(out, part, name, scale=0.8, sub="Коробки DSG7 DQ200 (0AM, 0CW)"):
    im = bg(); dr = ImageDraw.Draw(im)
    header(im, dr, "ПОДХОДИТ", sub)
    photo_frame(im, (55, 315, 510, 1025), part, scale)
    dr = ImageDraw.Draw(im)
    for i, b in enumerate(("Volkswagen", "Skoda", "Audi", "Seat")):
        y = 320 + i * 175
        plate(dr, (560, y, 1140, y + 145)); dr.text((598, y + 46), b, font=font("b", 46), fill=BLACK)
    plate(dr, (60, 1090, 1140, 1240))
    dr.text((96, 1142), name, font=fit(dr, name, "b", 44, 1010), fill=BLACK)
    plate(dr, (60, 1290, 1140, 1530), fill=None, outline=WHITE, width=4)
    for i, s in enumerate(("Не уверены, что подойдёт?", "Подберём по VIN — задайте вопрос", "в карточке товара")):
        text_c(dr, W / 2, 1335 + i * 56, s, font("s", 42), WHITE)
    im.save(out)


def board_cutout(art):
    """Плата с фото _1 (одинаковая раскладка у 0AM927769D/F): без вставки с номером и стрелки."""
    src = f"{REPO}/{art}/{art}_1.png"
    im = Image.open(src).convert("RGB")
    a = np.asarray(im).astype(int)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    orange = (r > 195) & (r - b > 80) & (g > 60) & (g < 225) & (r - g > 25)   # фон _1 светлее к центру
    white = (r > 225) & (g > 225) & (b > 225)
    keep = ~(orange | white)
    keep[:610, :420] = False          # вставка-лупа с номером
    keep[:380, :] = False             # шапка и стрелка
    keep[1205:, :] = False            # подписи внизу
    m = Image.fromarray((keep * 255).astype("uint8"))
    m = m.filter(ImageFilter.MinFilter(7)).filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.GaussianBlur(1.2))
    m = m.point(lambda v: 0 if v < 90 else v)
    im.putalpha(m)
    return im.crop(m.point(lambda v: 255 if v > 128 else 0).getbbox())


def slide_board_install(out, art, code):
    """Слайд «перед установкой»: номер на родной плате + клонирование + адаптация (сверено с источниками 28.10)."""
    im = bg(); dr = ImageDraw.Draw(im)
    header(im, dr, "ПЕРЕД УСТАНОВКОЙ", f"Электронная плата DSG7 DQ200 {code}")
    # лупа с номером — кусок реального фото _1
    lab = Image.open(f"{REPO}/{art}/{art}_1.png").convert("RGB").crop((104, 304, 398, 586))
    fx0, fy0, fx1, fy1 = 60, 290, 585, 810
    k = max((fx1 - fx0) / lab.width, (fy1 - fy0) / lab.height)
    lab = lab.resize((int(lab.width * k), int(lab.height * k)), Image.LANCZOS)
    lab = lab.crop(((lab.width - (fx1 - fx0)) // 2, (lab.height - (fy1 - fy0)) // 2,
                    (lab.width - (fx1 - fx0)) // 2 + fx1 - fx0, (lab.height - (fy1 - fy0)) // 2 + fy1 - fy0))
    m = Image.new("L", lab.size, 0); ImageDraw.Draw(m).rounded_rectangle((0, 0, lab.width - 1, lab.height - 1), 34, fill=255)
    im.paste(lab, (fx0, fy0), m)
    dr = ImageDraw.Draw(im)
    dr.rounded_rectangle((fx0, fy0, fx1, fy1), 34, outline=WHITE, width=6)
    plate(dr, (615, 290, 1140, 810))
    for y, t, w, sz, col in ((345, "Сверьте номер", "b", 50, BLACK), (425, "на вашей родной", "r", 38, GREY),
                             (475, "плате должен быть:", "r", 38, GREY), (580, code, "b", 64, BLACK),
                             (680, " ".join((code[:3], code[3:6], code[6:9], code[9:])), "r", 40, GREY)):
        text_c(dr, 877, y, t, fit(dr, t, w, sz, 460), col)
    steps = (("1", "Клонирование данных", "со старой платы на новую"),
             ("2", "Установка платы", "на гидравлическую часть мехатроника"),
             ("3", "Адаптация коробки", "обязательна после установки"))
    for i, (n, t1, t2) in enumerate(steps):
        y = 850 + i * 180
        plate(dr, (60, y, 1140, y + 155))
        dr.ellipse((90, y + 30, 185, y + 125), fill=(255, 102, 0))
        text_c(dr, 137.5, y + 45, n, font("b", 52), WHITE)
        dr.text((215, y + 28), t1, font=fit(dr, t1, "b", 46, 890), fill=BLACK)
        dr.text((215, y + 88), t2, font=fit(dr, t2, "r", 36, 890), fill=GREY)
    plate(dr, (60, 1400, 1140, 1545), fill=None, outline=WHITE, width=4)
    text_c(dr, W / 2, 1425, "Не уверены, какая плата нужна?", font("s", 42), WHITE)
    text_c(dr, W / 2, 1483, "Пришлите VIN в вопросах — подберём", font("s", 42), WHITE)
    im.save(out)


def boards(out):
    for art in ("0AM927769D", "0AM927769F"):
        code = art
        slide_board_install(f"{out}/{art}_2.png", art, code)
        slide_fits(f"{out}/{art}_3.png", board_cutout(art), f"Если на родной плате номер {code}", 0.9,
                   sub=f"DSG7 DQ200 · плата {code}")


if __name__ == "__main__":
    out = sys.argv[1]; os.makedirs(out, exist_ok=True)
    if len(sys.argv) > 2 and sys.argv[2] == "boards":   # платы 0AM927769D/F, 28.10
        boards(out); print("ok"); sys.exit(0)
    bolt = cutout(f"{REPO}/01X301127C/01X301127C_1.png", (150, 600, 950, 1250))
    bolt = bolt.rotate(0, expand=True)
    slide_main(f"{out}/01X301127C_2.png", "БОЛТ МЕХАТРОНИКА", "Короткий · DSG7 DQ200 (0AM, 0CW)", bolt,
               ("M8 × 35", "резьба × длина, мм"), ("Под звёздочку", "головка болта"),
               "Короткий болт крепления мехатроника", "DSG7 DQ200 · в упаковке 1 шт.", 0.78)
    slide_fits(f"{out}/01X301127C_3.png", bolt.rotate(-35, expand=True, resample=Image.BICUBIC),
               "Короткий болт крепления мехатроника", 0.86)
    kit = cutout(f"{REPO}/0am325025f/0am325025f_1.png", (0, 440, 1170, 1180))
    slide_main(f"{out}/0am325025f_2.png", "СОСТАВ КОМПЛЕКТА", "7 болтов мехатроника DSG7 DQ200", kit,
               ("4 шт. длинные", "WHT001922 · M8 × 90"), ("3 шт. короткие", "01X301127C · M8 × 35"),
               "Комплект болтов крепления мехатроника", "DSG7 DQ200 (0AM, 0CW) · 7 шт.", 0.86)
    slide_fits(f"{out}/0am325025f_3.png", kit, "Комплект болтов мехатроника, 7 шт.", 0.92)
    gas = cutout(f"{REPO}/0cw325025h/0cw325025h_1.png", (100, 300, 1030, 1030))
    slide_main(f"{out}/0cw325025h_2.png", "ПРОКЛАДКА ЭЛ. ПЛАТЫ", "Мехатроник DSG7 DQ200 (0AM, 0CW)", gas,
               ("1 шт.", "одна прокладка"), ("Под плату", "мехатроника"),
               "Прокладка электронной платы", "мехатроника DSG7 DQ200 · 1 шт.", 0.84)
    slide_fits(f"{out}/0cw325025h_3.png", gas, "Прокладка электронной платы мехатроника", 0.9)
    print("ok")
