"""Generate clearly labeled SYNTHETIC Chinese veterinary PDFs for offline testing.

Produces 7 synthetic fixtures covering:
1. Native journal single-case PDF
2. Pure scanned dual-column PDF with laboratory table & OCR traps
3. Mixed native + scanned PDF
4. 1-article multi-case PDF (2 cases, one missing treatment/follow-up)
5. Omnibus bound-volume PDF (unrelated pig article + target feline article)
6. Synthetic scanned veterinary book (2 chapters, 4 cases, `病例1一等级1` dash variant, page offset=2)
7. Corrupted unreadable PDF
"""

from __future__ import annotations

import argparse
import io
import json
import textwrap
from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFont

SYNTHETIC_BANNER = "【SYNTHETIC TEST FIXTURE / 合成测试数据 — 非真实临床病例】"

_DEFAULT_FONT_CANDIDATES = (
    Path(r"C:\Windows\Fonts\msyh.ttc"),
    Path(r"C:\Windows\Fonts\simsun.ttc"),
    Path(r"C:\Windows\Fonts\simhei.ttf"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
)


def find_cjk_font(explicit_font: str | Path | None = None) -> Path:
    """Locate a CJK TrueType/TTC font on Windows or Linux."""
    if explicit_font is not None:
        p = Path(explicit_font)
        if p.exists():
            return p
        raise FileNotFoundError(f"Explicit font not found: {p}")
    for candidate in _DEFAULT_FONT_CANDIDATES:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "No CJK font found. Checked: " + ", ".join(str(p) for p in _DEFAULT_FONT_CANDIDATES)
    )


def _wrap_cjk_lines(text: str, width: int = 34) -> list[str]:
    """Wrap paragraphs of CJK/ASCII text to fixed character width."""
    lines: list[str] = []
    for para in text.splitlines():
        if not para.strip():
            lines.append("")
            continue
        lines.extend(textwrap.wrap(para, width=width, break_long_words=True, break_on_hyphens=False))
    return lines


def _add_native_page(
    doc: fitz.Document,
    paragraphs: list[str],
    font_path: Path,
    *,
    fontsize: float = 11.5,
    line_height: float = 18.0,
    chars_per_line: int = 38,
) -> None:
    """Create an A4 native-text PDF page using PyMuPDF + embedded CJK font."""
    page = doc.new_page(width=595, height=842)
    page.insert_font(fontname="cjk", fontfile=str(font_path))
    y = 48.0
    for para in paragraphs:
        for line in _wrap_cjk_lines(para, width=chars_per_line):
            if y > 790:
                page = doc.new_page(width=595, height=842)
                page.insert_font(fontname="cjk", fontfile=str(font_path))
                y = 48.0
            if line:
                page.insert_text((48.0, y), line, fontname="cjk", fontsize=fontsize)
            y += line_height
        y += 6.0


def _render_image_to_pdf_page(doc: fitz.Document, image: Image.Image) -> None:
    """Embed a Pillow RGB image as a JPEG-compressed full-page scanned PDF page with zero text layer."""
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="JPEG", quality=85)
    page = doc.new_page(width=595, height=842)
    page.insert_image(fitz.Rect(0, 0, 595, 842), stream=buf.getvalue())


def _save_pdf(doc: fitz.Document, path: Path) -> None:
    """Subset embedded fonts and save PDF compactly."""
    doc.subset_fonts()
    doc.save(path, garbage=4, deflate=True)
    doc.close()


def _draw_wrapped_block(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    line_height: int,
    fill: str = "black",
) -> int:
    curr_y = y
    for line in lines:
        if line:
            draw.text((x, curr_y), line, fill=fill, font=font)
        curr_y += line_height
    return curr_y


def _generate_native_single(out_dir: Path, font_path: Path) -> Path:
    path = out_dir / "合成_猫血支原体感染病例报告.pdf"
    doc = fitz.open()
    _add_native_page(
        doc,
        [
            SYNTHETIC_BANNER,
            "中国兽医杂志  2024年第60卷第3期",
            "收稿日期：2023-11-12",
            "合成_猫血支原体感染病例报告",
            "1 病例简介与体格检查",
            "患猫，雄性，3岁，体重4.2 kg。主诉精神沉郁、食欲废绝3天。一般体格检查：体温39.8℃，脉搏140次/min，呼吸频率32次/min，可视黏膜苍白，心肺听诊未见明显杂音。",
            "2 实验室与影像学检查",
            "血常规检查：RBC 2.8×10¹²/L（参考区间5.0~10.0×10¹²/L），HCT 16.5%（参考区间30%~45%）。PCR检测检出猫血支原体阳性。腹部B超检查见脾脏轻度肿大。",
            "3 治疗与转归",
            "给予口服多西环素10 mg/kg，每日2次，连用21天；同时静脉滴注复方氯化钠注射液纠偏。治疗2周后复查食欲恢复，精神好转，贫血指标回升。",
            "4 诊断结论",
            "猫血支原体感染。",
        ],
        font_path,
    )
    _save_pdf(doc, path)
    return path


def _generate_scanned_dual_col(out_dir: Path, font_path: Path) -> Path:
    path = out_dir / "合成_纯扫描双栏犬肝酶异常病例.pdf"
    width, height = 1654, 2339
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)

    heading_font = ImageFont.truetype(str(font_path), 30)
    body_font = ImageFont.truetype(str(font_path), 26)
    small_font = ImageFont.truetype(str(font_path), 24)

    # Full-width top header & title (spanning across both columns)
    draw.text((80, 60), SYNTHETIC_BANNER, fill="black", font=small_font)
    draw.text(
        (80, 110),
        "中国兽医杂志  2024年第60卷第4期",
        fill="black",
        font=heading_font,
    )
    draw.text(
        (80, 150),
        "合成_纯扫描双栏犬肝酶异常病例  收稿日期：2023-12-01",
        fill="black",
        font=body_font,
    )
    draw.line([(80, 210), (1570, 210)], fill="black", width=2)

    # Left column (x=80..760)
    y_l = 270
    draw.text((80, y_l), "1 病史与体格检查", fill="black", font=heading_font)
    y_l += 50
    left_lines = _wrap_cjk_lines(
        "患犬，雄性，6岁，体重12 kg。主诉食欲减退3天，外院怀疑胰腺炎。体格检查：体温38.6℃，脉搏110次/min，腹部触诊轻度敏感。",
        width=22,
    )
    y_l = _draw_wrapped_block(draw, 80, y_l, left_lines, body_font, 42)
    y_l += 30
    draw.text((80, y_l), "2 临床意义与推理", fill="black", font=heading_font)
    y_l += 50
    left_lines2 = _wrap_cjk_lines(
        "肝酶异常（ALT与ALP轻度升高），结合外伤史考虑为创伤后肝细胞一过性损伤。",
        width=22,
    )
    _draw_wrapped_block(draw, 80, y_l, left_lines2, body_font, 42)

    # Right column (x=880..1570)
    y_r = 270
    draw.text((880, y_r), "3 治疗方案与随访", fill="black", font=heading_font)
    y_r += 50
    right_lines = _wrap_cjk_lines(
        "给予静脉注射头孢曲松钠每日1次，口服熊去氧胆酸每日2次。1个月后复查临床症状消失。诊断：犬创伤后肝细胞损伤。",
        width=22,
    )
    _draw_wrapped_block(draw, 880, y_r, right_lines, body_font, 42)

    # Full-width laboratory table at bottom with explicit OCR traps
    y_tbl = 1020
    draw.line([(80, y_tbl), (1570, y_tbl)], fill="black", width=2)
    y_tbl += 20
    draw.text((80, y_tbl), "血常规检查与血清生化检查表（含合成OCR陷阱）", fill="black", font=heading_font)
    y_tbl += 60

    table_rows = [
        ("项目", "测定值", "单位", "参考区间", "异常标记"),
        ("WBC", "12.5", "×10^12/L", "6.0~17.0×10^9/L", "正常"),
        ("HGB", "111.5", "g/L", "120~180", "偏低"),
        ("PCV", "134%", "%", "37~55", "升高"),
        ("GCT", "85", "U/L", "0~10", "↑"),
    ]
    col_x = [100, 360, 600, 900, 1340]
    for row in table_rows:
        for cx, cell_txt in zip(col_x, row, strict=True):
            draw.text((cx, y_tbl), cell_txt, fill="black", font=heading_font)
        y_tbl += 62
        draw.line([(80, y_tbl - 12), (1570, y_tbl - 12)], fill="#cccccc", width=1)

    doc = fitz.open()
    _render_image_to_pdf_page(doc, img)
    _save_pdf(doc, path)
    return path


def _generate_mixed_native_scanned(out_dir: Path, font_path: Path) -> Path:
    path = out_dir / "合成_混合原生与扫描病例报告.pdf"
    doc = fitz.open()

    # Page 1: Native text
    _add_native_page(
        doc,
        [
            SYNTHETIC_BANNER,
            "畜牧与兽医  2023年第55卷第8期",
            "收稿日期：2022-10-08",
            "合成_混合原生与扫描病例报告",
            "1 病例信息与检查",
            "患猫，雌性，3岁，体重3.5 kg。因频繁呕吐、食欲废绝2天来院就诊。一般体格检查：体温39.2℃，脱水约5%，腹部触诊可及条索状硬物。",
        ],
        font_path,
    )

    # Page 2: Scanned image only
    img = Image.new("RGB", (1654, 2339), "white")
    draw = ImageDraw.Draw(img)
    heading_font = ImageFont.truetype(str(font_path), 34)
    body_font = ImageFont.truetype(str(font_path), 28)

    draw.text((90, 90), SYNTHETIC_BANNER, fill="black", font=body_font)
    draw.text((90, 160), "2 影像学、手术探查与转归（扫描页）", fill="black", font=heading_font)
    lines = _wrap_cjk_lines(
        "腹部超声检查见肠管内强回声线状异物及肠管褶皱。行开腹探查术，切开空肠取出棉线状异物。术后静脉滴注氨苄西林每日2次，共5天。出院后7天复查伤口愈合良好，完全康复。最终诊断：猫肠道线性异物。",
        width=36,
    )
    _draw_wrapped_block(draw, 90, 240, lines, body_font, 48)
    _render_image_to_pdf_page(doc, img)

    _save_pdf(doc, path)
    return path


def _generate_multi_case(out_dir: Path, font_path: Path) -> Path:
    path = out_dir / "合成_一文多病例报告.pdf"
    doc = fitz.open()
    _add_native_page(
        doc,
        [
            SYNTHETIC_BANNER,
            "黑龙江畜牧兽医  2024年第4期",
            "收稿日期：2024-01-15",
            "合成_一文多病例报告",
            "病例1：患犬，拉布拉多，2岁，雄性。因误食葡萄后出现急性少尿、呕吐就诊。体格检查：体温38.4℃，心率100次/min。血清生化检查：BUN 32 mmol/L，CREA 620 μmol/L。给予静脉滴注生理盐水并实施血液透析治疗。2周后复查肾功能指标恢复正常。诊断：犬急性肾损伤。",
            "病例2：患猫，英国短毛猫，5岁，雄性。突发双后肢瘫痪、肢端冰凉伴疼痛嚎叫就诊。体格检查：体温37.2℃，股动脉搏动消失。超声心动图检查见左心房显著扩张及自发显影血栓回声。诊断：猫动脉血栓栓塞。",
        ],
        font_path,
    )
    _save_pdf(doc, path)
    return path


def _generate_omnibus(out_dir: Path, font_path: Path) -> Path:
    path = out_dir / "合成_猫角膜穿孔合订本病例.pdf"
    doc = fitz.open()

    # Page 1: Unrelated pig article
    _add_native_page(
        doc,
        [
            SYNTHETIC_BANNER,
            "中国兽医杂志  2023年第59卷第2期",
            "规模化猪场猪流行性腹泻的防控体会",
            "2023年3月某规模化猪场发生仔猪急性水样腹泻，发病率达45%。剖检见小肠肠壁变薄，肠内容物呈黄色水样。经猪流行性腹泻病毒RT-PCR检测阳性。通过返饲与生物安全管理控制疫情。诊断：猪流行性腹泻。",
        ],
        font_path,
    )

    # Page 2: Target feline article
    _add_native_page(
        doc,
        [
            SYNTHETIC_BANNER,
            "中国兽医杂志  2023年第59卷第2期",
            "收稿日期：2023-02-14",
            "猫角膜穿孔结膜瓣移植术病例报告",
            "1 病史与眼科检查",
            "患猫，加菲猫，1岁，雌性。因右眼被抓伤流泪2天就诊。一般体格检查：体温38.8℃。眼科检查见右眼角膜中央直径约3 mm穿孔伴部分虹膜脱出，前房浅。",
            "2 手术治疗与转归",
            "行角膜清创及带蒂结膜瓣遮盖移植术，术后给予左氧氟沙星滴眼液每日3次。3周后复查结膜瓣存活，角膜愈合良好。诊断：猫角膜穿孔。",
        ],
        font_path,
    )
    _save_pdf(doc, path)
    return path


def _generate_scanned_book(out_dir: Path, font_path: Path) -> Path:
    path = out_dir / "合成_扫描兽医病例分析书籍.pdf"
    doc = fitz.open()
    width, height = 1654, 2339

    title_font = ImageFont.truetype(str(font_path), 40)
    heading_font = ImageFont.truetype(str(font_path), 34)
    body_font = ImageFont.truetype(str(font_path), 28)

    # PDF page 1 (book front cover, pdf_page_number=1, book_page=-1)
    img1 = Image.new("RGB", (width, height), "white")
    d1 = ImageDraw.Draw(img1)
    d1.text((120, 260), SYNTHETIC_BANNER, fill="black", font=body_font)
    d1.text((120, 380), "兽医临床病例分析（合成扫描测试版）", fill="black", font=title_font)
    d1.text((120, 480), "第2版  合成测试出版社", fill="black", font=heading_font)
    _render_image_to_pdf_page(doc, img1)

    # PDF page 2 (TOC, pdf_page_number=2, book_page=0)
    img2 = Image.new("RGB", (width, height), "white")
    d2 = ImageDraw.Draw(img2)
    d2.text((120, 120), SYNTHETIC_BANNER, fill="black", font=body_font)
    d2.text((120, 200), "目录", fill="black", font=title_font)
    d2.text((120, 300), "第一章 肝酶升高病例 ............................ 页1", fill="black", font=heading_font)
    d2.text((120, 380), "第二章 肾功能异常病例 .......................... 页3", fill="black", font=heading_font)
    _render_image_to_pdf_page(doc, img2)

    # PDF page 3 (book_page=1 with page_offset=2): Chapter 1 + Case 1 (dash variant 一)
    img3 = Image.new("RGB", (width, height), "white")
    d3 = ImageDraw.Draw(img3)
    d3.text((100, 60), "1", fill="black", font=body_font)
    d3.text((100, 130), "第一章 肝酶升高病例", fill="black", font=title_font)
    d3.text((100, 220), "病例1一等级1", fill="black", font=heading_font)
    c1_lines = _wrap_cjk_lines(
        "患犬，3岁，雄性。主诉精神沉郁2天，体格检查体温38.5℃。生化检查 ALT 420 U/L（参考区间10~100 U/L）。病例总结和结果：诊断为犬急性肝细胞损伤。经保肝治疗1周后复查好转。",
        width=36,
    )
    _draw_wrapped_block(d3, 100, 290, c1_lines, body_font, 48)
    _render_image_to_pdf_page(doc, img3)

    # PDF page 4 (book_page=2 with page_offset=2): Chapter 1 + Case 2 (no treatment/follow-up)
    img4 = Image.new("RGB", (width, height), "white")
    d4 = ImageDraw.Draw(img4)
    d4.text((100, 60), "2", fill="black", font=body_font)
    d4.text((100, 150), "病例2-等级2", fill="black", font=heading_font)
    c2_lines = _wrap_cjk_lines(
        "患猫，5岁，雌性。主诉厌食3天。体格检查可视黏膜黄染。尿液分析见胆红素阳性。病例总结及结论：诊断为猫肝脂肪沉积。",
        width=36,
    )
    _draw_wrapped_block(d4, 100, 220, c2_lines, body_font, 48)
    _render_image_to_pdf_page(doc, img4)

    # PDF page 5 (book_page=3 with page_offset=2): Chapter 2 + Case 1
    img5 = Image.new("RGB", (width, height), "white")
    d5 = ImageDraw.Draw(img5)
    d5.text((100, 60), "3", fill="black", font=body_font)
    d5.text((100, 130), "第二章 肾功能异常病例", fill="black", font=title_font)
    d5.text((100, 220), "病例1—等级1", fill="black", font=heading_font)
    c3_lines = _wrap_cjk_lines(
        "患犬，7岁，雌性。主诉多饮多尿1个月。体格检查脱水约5%。生化检查 CREA 310 μmol/L，尿比重1.012。病例总结和结果：诊断为犬慢性肾脏病。",
        width=36,
    )
    _draw_wrapped_block(d5, 100, 290, c3_lines, body_font, 48)
    _render_image_to_pdf_page(doc, img5)

    # PDF page 6 (book_page=4 with page_offset=2): Chapter 2 + Case 2
    img6 = Image.new("RGB", (width, height), "white")
    d6 = ImageDraw.Draw(img6)
    d6.text((100, 60), "4", fill="black", font=body_font)
    d6.text((100, 150), "病例2-等级3", fill="black", font=heading_font)
    c4_lines = _wrap_cjk_lines(
        "患猫，10岁，雄性。主诉消瘦、呕吐。血常规检查 HCT 22%。病例总结及结果：诊断为猫慢性肾衰竭。给予皮下补液每日1次，2周后食欲改善。",
        width=36,
    )
    _draw_wrapped_block(d6, 100, 220, c4_lines, body_font, 48)
    _render_image_to_pdf_page(doc, img6)

    _save_pdf(doc, path)
    return path


def _generate_corrupted_pdf(out_dir: Path) -> Path:
    path = out_dir / "合成_损坏PDF文件.pdf"
    path.write_bytes(b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Corrupted_Synthetic_Test")
    return path


def generate_all_fixtures(
    output_dir: str | Path,
    font_path: str | Path | None = None,
) -> dict[str, Path]:
    """Generate all 7 synthetic PDF fixtures in `output_dir` and return a name->Path mapping."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    resolved_font = find_cjk_font(font_path)

    fixtures = {
        "native_single": _generate_native_single(out_dir, resolved_font),
        "scanned_dual_col": _generate_scanned_dual_col(out_dir, resolved_font),
        "mixed_native_scanned": _generate_mixed_native_scanned(out_dir, resolved_font),
        "multi_case": _generate_multi_case(out_dir, resolved_font),
        "omnibus": _generate_omnibus(out_dir, resolved_font),
        "scanned_book": _generate_scanned_book(out_dir, resolved_font),
        "corrupted": _generate_corrupted_pdf(out_dir),
    }

    manifest_path = out_dir / "synthetic_fixtures_manifest.json"
    manifest_payload = {
        "warning": "UNANNOTATED_SYNTHETIC_NOT_CLINICAL_ACCURACY",
        "font_used": str(resolved_font),
        "fixtures": {k: v.name for k, v in fixtures.items()},
    }
    manifest_path.write_text(
        json.dumps(manifest_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return fixtures


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate clearly labeled SYNTHETIC Chinese veterinary PDFs for testing."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("tmp/synthetic_fixtures"),
        help="Destination directory for synthetic PDFs (default: tmp/synthetic_fixtures).",
    )
    parser.add_argument(
        "--font",
        type=Path,
        default=None,
        help="Optional path to CJK font (.ttc/.ttf).",
    )
    args = parser.parse_args()
    fixtures = generate_all_fixtures(args.output_dir, font_path=args.font)
    for name, path in fixtures.items():
        print(f"{name}: {path}")


if __name__ == "__main__":
    main()
