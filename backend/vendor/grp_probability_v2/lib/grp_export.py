"""PNG/NPZ presentation helpers; does not change the V2 numeric algorithm."""
from concurrent.futures import ThreadPoolExecutor
import html

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def colorize(probability, state):
    palette = np.asarray([[190, 55, 62], [235, 180, 63], [48, 151, 113]], np.float32)
    q = np.clip(np.nan_to_num(probability, nan=0), 0, 1)
    a = np.minimum((q * 2).astype("i4"), 1)
    t = (q * 2 - a)[..., None]
    rgb = (palette[a] * (1 - t) + palette[a + 1] * t).astype("u1")
    rgb[state == 0] = [21, 30, 40]
    rgb[state == 1] = [89, 103, 120]
    rgb[state == 2] = [170, 85, 180]
    return rgb


def angle_name(angle):
    if float(angle).is_integer():
        return f"{int(angle):03d}"
    return ("%020.12f" % angle).rstrip("0").rstrip(".").replace(".", "p")


def display_font(size):
    for name in ("DejaVuSans.ttf", "C:/Windows/Fonts/arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def export_images(key, probability, state, risk, plan, out, hd_scale, workers):
    directory = out / "images" / key
    directory.mkdir(parents=True, exist_ok=True)
    rgb = colorize(probability, state)
    yy, xx = np.indices(rgb.shape[1:3])
    hatch = ((xx + yy) % 8 < 2)[None] & ((risk & 3) != 0)
    rgb[hatch] = ((rgb[hatch].astype(np.uint16) + np.array([210, 210, 210], np.uint16)) // 2).astype(np.uint8)

    def export_one(item):
        i, angle = item
        image = Image.fromarray(rgb[i])
        stem = "yaw_" + angle_name(angle)
        native = directory / (stem + ".png")
        image.save(native)
        result = {"angle_deg": angle, "native": native.relative_to(out).as_posix()}
        if hd_scale:
            w, h = image.width * hd_scale, image.height * hd_scale
            canvas = Image.new("RGB", (max(4050, w + 280), max(1500, h + 404)), (246, 248, 249))
            canvas.paste(image.resize((w, h), Image.Resampling.NEAREST), (140, 160))
            draw = ImageDraw.Draw(canvas)
            font = display_font(42)
            draw.text((140, 55), f"{key} | yaw {angle:g} deg | V2 conditional estimate", fill=(25, 35, 45), font=font)
            draw.text((140, canvas.height - 200), "Red 0% - Green 100% | Purple unknown | Light hatch: NERA/ERA dynamics unvalidated", fill=(25, 35, 45), font=font)
            draw.text((140, canvas.height - 130), f"True ray pitch {plan['pixel_pitch_m']:g} m; HD is nearest-neighbour display. Not game-calibrated.", fill=(25, 35, 45), font=font)
            hd = directory / (stem + "_hd.png")
            canvas.save(hd)
            result["hd"] = hd.relative_to(out).as_posix()
        return result

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(export_one, enumerate(plan["angles_deg"])))


def write_gallery(report, out):
    plan = report["plan"]
    esc = html.escape
    sections = []
    for group in report["groups"]:
        cards = []
        for item in group["images"]:
            native = esc(item["native"], quote=True)
            target = esc(item.get("hd", item["native"]), quote=True)
            cards.append(f'<a href="{target}"><figure><img src="{native}" loading="lazy"><figcaption>{item["angle_deg"]:g}°</figcaption></figure></a>')
        array = esc(group["array_file"], quote=True)
        sections.append(f'<section><h2>{esc(group["key"])}</h2><p><a href="{array}">NPZ 原始数组</a></p><div class="grid">{"".join(cards)}</div></section>')
    document = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>GRP 多角度击穿概率</title>
<style>body{{font:16px system-ui,sans-serif;background:#f4f6f8;color:#182431;max-width:1600px;margin:32px auto;padding:0 24px}}a{{color:inherit}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:16px}}figure{{margin:0;background:white;padding:12px;border:1px solid #dce1e8}}img{{width:100%;image-rendering:pixelated}}figcaption{{padding-top:8px}}section{{margin:32px 0}}p{{line-height:1.7}}</style>
<h1>GRP 多角度击穿概率 · V2</h1><p>{plan['width']} × {plan['height']} 真实采样／视角；采样间距 {plan['pixel_pitch_m']:g} 米；{len(plan['angles_deg'])} 个视角。<a href="run.json">参数、文件清单和耗时</a></p>
<p>红色 0% → 黄色 50% → 绿色 100%；紫色为未知，灰色为射线上无内部目标，深色为背景。浅色斜纹表示 NERA／ERA 的完整动态反应未验证。颜色是当前简化算法的条件估计，未标定为游戏真实概率；中间装甲消耗量不是 RHAe。</p>
<p>水平 yaw 0° 从 +X 朝载具射击；正角度绕向 +Z；+Y 向上。图片左侧在 yaw 0° 对应 +Z。各车各角度采用相同米／像素与色标。</p>{''.join(sections)}</html>'''
    (out / "index.html").write_text(document, encoding="utf-8")
