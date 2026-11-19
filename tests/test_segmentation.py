"""Tests for journal and scanned-book segmentation and project index building."""

from __future__ import annotations

from pathlib import Path

from vet_case_extractor.config import AppConfig, ExtractionRules, load_config
from vet_case_extractor.models import (
    BBox,
    DocumentRecord,
    LabTableRow,
    PageArtifact,
    SourceType,
    TaskStatus,
)
from vet_case_extractor.providers.base import LLMProvider, LLMResponse
from vet_case_extractor.segmentation.book import segment_book_document
from vet_case_extractor.segmentation.indexer import build_project_index
from vet_case_extractor.segmentation.journal import segment_journal_document


def _make_doc(
    filename: str = "猫弥散性虹膜黑色素瘤的病例报告.pdf",
    doc_id: str = "doc_001",
    source_type: SourceType = SourceType.JOURNAL,
    page_offset: int = 0,
    target_query: str | None = None,
) -> DocumentRecord:
    return DocumentRecord(
        document_id=doc_id,
        filename=filename,
        file_path=f"/tmp/{filename}",
        file_sha256="a" * 64,
        source_type=source_type,
        page_offset=page_offset,
        target_article_query=target_query,
        status=TaskStatus.COMPLETED,
    )


def _make_page(
    doc_id: str,
    page_index: int,
    text: str,
    book_page_number: int | None = None,
    image_path: str | None = None,
    lab_rows: list[LabTableRow] | None = None,
) -> PageArtifact:
    return PageArtifact(
        document_id=doc_id,
        page_index=page_index,
        pdf_page_number=page_index + 1,
        book_page_number=book_page_number,
        raw_text=text,
        cleaned_text=text,
        layout_text=text,
        image_path=image_path,
        lab_rows=lab_rows or [],
    )


def test_journal_single_case_metadata_and_year() -> None:
    doc = _make_doc(filename="猫弥散性虹膜黑色素瘤的病例报告.pdf")
    p0 = _make_page(
        doc.document_id,
        0,
        "中国兽医杂志 2023年(第59卷)第2期\n"
        "Chinese Journal of Veterinary Medicine\n"
        "猫弥散性虹膜黑色素瘤的病例报告\n"
        "吕迪1，金艺鹏1\n"
        "（1.中国农业大学动物医学院，北京海淀100193）\n"
        "中图分类号：S829.3 文献标志码：B 文章编号：0529-6005(2023)02-0120-03\n"
        "收稿日期：20201116\n"
        "1 病例介绍\n"
        "异国短毛猫，11岁，雄性，体重2.77kg。",
    )
    p1 = _make_page(
        doc.document_id,
        1,
        "2 诊断与治疗\n行眼球摘除术，术后病理诊断为弥散性虹膜黑色素瘤。",
    )

    segs = segment_journal_document(doc, [p0, p1])
    assert len(segs) == 1
    seg = segs[0]
    assert seg.source_journal == "中国兽医杂志"
    assert seg.publication_year == "2023"
    assert seg.received_year == "2020"
    assert seg.article_title == "猫弥散性虹膜黑色素瘤的病例报告"
    assert seg.page_range == "1-2"
    assert seg.boundary_uncertain is False


def test_journal_omnibus_slices_target_article_and_excludes_unrelated() -> None:
    doc = _make_doc(
        filename="合订本_2022_05.pdf",
        target_query="猫脂肪肝病例报告",
    )
    p0 = _make_page(
        doc.document_id,
        0,
        "《中国兽医杂志》2022年第58卷第5期\n"
        "猪链球菌病的诊治\n"
        "王五（某养猪场，北京100000）\n"
        "中图分类号：S858.28 收稿日期：2021-01-15\n"
        "1 发病情况\n某猪场育肥猪出现发热和关节肿大。\n"
        "参考文献\n[1] 猪病学.",
    )
    p1 = _make_page(
        doc.document_id,
        1,
        "《中国兽医杂志》2022年第58卷第5期\n"
        "猫脂肪肝病例报告\n"
        "李四（某动物医院，北京100083）\n"
        "中图分类号：S858.293 收稿日期：2022-02-10\n"
        "1 病例介绍\n英短猫，4岁，绝食5天，黄疸明显。",
    )
    p2 = _make_page(
        doc.document_id,
        2,
        "《中国兽医杂志》2022年第58卷第5期\n"
        "2 治疗与转归\n放置鼻饲管营养支持后康复。\n"
        "参考文献\n[1] 猫病学.\n"
        "犬细小病毒心肌炎诊治\n"
        "赵六（某宠物医院，上海200000）\n"
        "中图分类号：S858.292 收稿日期：2022-03-01\n"
        "1 病史\n幼犬突发呕吐。",
    )

    segs = segment_journal_document(doc, [p0, p1, p2])
    assert len(segs) == 1
    seg = segs[0]
    assert seg.boundary_uncertain is False
    assert seg.article_title == "猫脂肪肝病例报告"
    assert seg.received_year == "2022"
    assert "育肥猪" not in seg.raw_text
    assert "幼犬突发呕吐" not in seg.raw_text
    assert "英短猫，4岁" in seg.raw_text
    assert seg.page_indices == [1, 2]


def test_journal_omnibus_ambiguous_target_sets_boundary_uncertain() -> None:
    doc = _make_doc(
        filename="合订本_未知.pdf",
        target_query="犬胰腺炎诊治",
    )
    p0 = _make_page(
        doc.document_id,
        0,
        "中国兽医杂志 2021年第57卷第1期\n"
        "猪水肿病的防治\n王某（某兽医站，100000）\n中图分类号：S858\n1 发病情况\n猪群发病。",
    )
    p1 = _make_page(
        doc.document_id,
        1,
        "中国兽医杂志 2021年第57卷第1期\n"
        "牛前胃弛缓病例\n张某（某牧场，100000）\n中图分类号：S858\n1 发病情况\n奶牛食欲减退。",
    )

    segs = segment_journal_document(doc, [p0, p1])
    assert len(segs) == 1
    assert segs[0].boundary_uncertain is True
    assert segs[0].boundary_notes


def test_journal_crosstalk_detection_against_chinese_filename() -> None:
    doc = _make_doc(filename="犬输尿管囊肿诊治病例.pdf")
    p0 = _make_page(
        doc.document_id,
        0,
        "中国兽医杂志 2020年第56卷第4期\n"
        "犬真性红细胞增多症病例报告\n"
        "刘某（某动物医院，北京100000）\n"
        "中图分类号：S858.292 收稿日期：2019-08-12\n"
        "1 病例介绍\n泰迪犬，红细胞压积72%。",
    )
    segs = segment_journal_document(doc, [p0])
    assert len(segs) == 1
    assert segs[0].boundary_uncertain is True


def test_journal_multi_case_splitting_and_non_splitting_guards() -> None:
    doc = _make_doc(filename="猫糖尿病2例诊治报告.pdf")
    p0 = _make_page(
        doc.document_id,
        0,
        "畜牧与兽医 2021, 53(4): 110-112\n"
        "猫糖尿病2例诊治报告\n"
        "陈某（某动物医院，南京210000）\n"
        "收稿日期：2020-09-01\n"
        "本院共接诊猫糖尿病病例1例及复发例1例（误写），现将2例报告如下：\n"
        "病例1：英国短毛猫，6岁，雄性，多饮多尿，血糖24.5mmol/L。\n"
        "病例2：美短猫，8岁，雌性，酮症酸中毒，血糖28.1mmol/L。",
    )
    p1 = _make_page(
        doc.document_id,
        1,
        "2 讨论\n病例1与病例2均接受甘精胰岛素治疗。",
    )

    segs = segment_journal_document(doc, [p0, p1])
    assert len(segs) == 2
    assert segs[0].case_order == 1
    assert segs[0].case_number == 1
    assert segs[1].case_order == 2
    assert segs[1].case_number == 2
    assert segs[0].source_journal == "畜牧与兽医"
    assert segs[0].publication_year == "2021"
    assert segs[0].received_year == "2020"
    assert "英国短毛猫" in segs[0].raw_text
    assert "美短猫" not in segs[0].raw_text
    assert "美短猫" in segs[1].raw_text
    assert "英国短毛猫" not in segs[1].raw_text


def test_book_segmentation_dash_variants_crossref_rejection_and_continuity() -> None:
    rules = ExtractionRules()
    doc = _make_doc(
        filename="兽医临床病例分析.pdf",
        doc_id="book_001",
        source_type=SourceType.BOOK,
        page_offset=2,
    )
    toc = _make_page(
        doc.document_id,
        0,
        "目录\n第一章 肝酶升高 ...... 1\n第二章 肾脏疾病 ...... 20",
        book_page_number=None,
    )
    p1 = _make_page(
        doc.document_id,
        1,
        "[[通栏]] 第一章 肝酶升高\n"
        "[[左栏]] 病例1一等级1\n"
        "6岁绝育雌性犬。见第二章病例9（猫）和参考病例2 等级1。\n"
        "[[右栏]] 病例3—等级13\n"
        "8岁雄性猫，ALT显著升高。",
        book_page_number=1,
        image_path="pages_img/book_001/p002.png",
    )
    p2 = _make_page(
        doc.document_id,
        2,
        "[[通栏]] 第二章 肾脏疾病\n"
        "[[左栏]] 病例1 - 等级二\n"
        "10岁猫，肌酐升高。\n"
        "[[右栏]] 病例1 － 等级2\n"
        "重复编号病例用于测试重号检查。",
        book_page_number=2,
        image_path="pages_img/book_001/p003.png",
    )

    segs, issues = segment_book_document(doc, [toc, p1, p2], rules)
    assert len(segs) == 4
    assert segs[0].title == "第一章 肝酶升高 · 病例1 等级1"
    assert segs[0].case_level == 1
    assert segs[0].boundary_uncertain is False
    assert segs[0].book_page_range == "1"

    # OCR level 13 -> suspicious flag
    assert segs[1].title == "第一章 肝酶升高 · 病例3 等级13"
    assert segs[1].case_level == 13
    assert segs[1].boundary_uncertain is True
    assert any("等级13" in n for n in segs[1].boundary_notes)

    # Duplicate case_number in Chapter 2
    assert segs[2].title == "第二章 肾脏疾病 · 病例1 等级2"
    assert segs[2].boundary_uncertain is True
    assert segs[3].boundary_uncertain is True

    # Chapter 1 missing case 2; Chapter 2 duplicate case 1
    assert any("缺号" in iss and "2" in iss for iss in issues)
    assert any("重号" in iss and "1" in iss for iss in issues)


def test_book_rejects_inline_cross_references_only() -> None:
    doc = _make_doc(filename="book.pdf", source_type=SourceType.BOOK)
    p0 = _make_page(
        doc.document_id,
        0,
        "第一章 肝酶升高\n本病例表现见第二章病例9，或参见病例3 等级2的讨论。",
    )
    segs, issues = segment_book_document(doc, [p0], ExtractionRules())
    assert segs == []
    assert issues


class _FakeBoundaryProvider(LLMProvider):
    provider_name = "fake"

    def __init__(self) -> None:
        super().__init__(model_name="fake-model")
        self.calls = 0

    def extract_structured(self, system_prompt, user_prompt, response_model):
        self.calls += 1
        parsed = response_model.model_validate(
            {
                "confirmed_title": "第一章 肝酶升高 · 病例1 等级3",
                "confirmed_case_level": 3,
                "is_valid_case_boundary": True,
                "boundary_note": "页图确认原数字为等级3",
            }
        )
        return LLMResponse(parsed=parsed, model_name=self.model_name, token_usage={"total_tokens": 50})


def test_indexer_auto_detects_writes_packet_and_consults_llm(tmp_path: Path) -> None:
    cfg: AppConfig = load_config()
    doc_journal = _make_doc(
        filename="猫结石.pdf",
        doc_id="doc_j",
        source_type=SourceType.AUTO,
    )
    doc_book = _make_doc(
        filename="译著.pdf",
        doc_id="doc_b",
        source_type=SourceType.AUTO,
        page_offset=1,
    )

    j_page = _make_page(
        "doc_j",
        0,
        "中国兽医杂志 2024,60(11):143-146\n猫胱氨酸结石的病例报告\n收稿日期：2023-05-01\n1 病例\n暹罗猫。",
        image_path=str(tmp_path / "j_p1.png"),
        lab_rows=[
            LabTableRow(
                item_raw="WBC",
                value_raw="13.19",
                unit_raw="×10⁹/L",
                ref_range_raw="2.30~10.29",
                flag_raw="↑",
                page_index=0,
                bbox=BBox(x0=10, y0=10, x1=100, y1=30),
                table_title="血常规",
                anomalies=[],
            )
        ],
    )
    b_page = _make_page(
        "doc_b",
        0,
        "[[通栏]] 第一章 肝酶升高\n[[左栏]] 病例1一等级13\n[[右栏]] 8岁犬ALT升高。",
        book_page_number=1,
        image_path=str(tmp_path / "b_p1.png"),
        lab_rows=[
            LabTableRow(
                item_raw="GCT",
                value_raw="250",
                unit_raw="U/L",
                ref_range_raw="0~12",
                flag_raw="↑",
                page_index=0,
                table_title="生化检查",
                anomalies=["lookalike_typo:GCT->GGT"],
            )
        ],
    )

    fake_provider = _FakeBoundaryProvider()
    cases_dir = tmp_path / "cases"
    index = build_project_index(
        documents=[doc_journal, doc_book],
        pages_by_doc={"doc_j": [j_page], "doc_b": [b_page]},
        cases_dir=cases_dir,
        cfg=cfg,
        source_type_override=SourceType.AUTO,
        provider=fake_provider,
    )

    assert index.total_documents == 2
    assert index.total_cases == 2
    assert index.cases[0].source_type == SourceType.JOURNAL
    assert index.cases[1].source_type == SourceType.BOOK
    assert fake_provider.calls == 1
    assert index.cases[1].case_level == 3
    assert any("LLM边界辅助" in n for n in index.cases[1].boundary_notes)

    packet_path = Path(index.cases[1].packet_path)
    assert packet_path.exists()
    packet_text = packet_path.read_text(encoding="utf-8")
    assert "--- PDF第1页 ---" in packet_text
    assert "[[左栏]]" in packet_text
    assert "[[右栏]]" in packet_text
    assert "GCT" in packet_text
    assert "lookalike_typo" in packet_text
    assert str(tmp_path / "b_p1.png") in packet_text


def test_journal_ignores_wrapped_prose_matching_title_substring() -> None:
    doc = _make_doc(filename="猫弥散性虹膜黑色素瘤的病例报告.pdf")
    p0 = _make_page(
        doc.document_id,
        0,
        "中国兽医杂志 2023年(第59卷)第2期\n"
        "猫弥散性虹膜黑色素瘤的病例报告吕迪1，金艺鹏1\n"
        "（1.中国农业大学动物医学院，北京海淀100193）\n"
        "中图分类号：S829.3\n"
        "猫弥散性虹膜黑色素瘤(Felinediffuseirismelanoma，FDIM)是一种肿瘤。\n"
        "收稿日期：2020-11-16\n"
        "1 病例介绍\n"
        "异国短毛猫，11岁。",
    )
    p1 = _make_page(
        doc.document_id,
        1,
        "2 治疗与结果\n右眼球摘除术。猫弥散性虹膜黑色素瘤的预后通常不良。",
    )
    segs = segment_journal_document(doc, [p0, p1])
    assert len(segs) == 1
    seg = segs[0]
    assert seg.boundary_uncertain is False
    assert seg.article_title == "猫弥散性虹膜黑色素瘤的病例报告"
    assert seg.page_indices == [0, 1]
    assert "右眼球摘除术" in seg.raw_text


def test_journal_ignores_ascii_semicolon_fig_xref_and_affiliation_anchors() -> None:
    # Case A: ASCII semicolon + `见图2` right before `文章编号` in right column
    doc1 = _make_doc(filename="猫皮肤无力病例报告.pdf", doc_id="doc_skin")
    p0_skin = _make_page(
        doc1.document_id,
        0,
        "[[通栏]] 中国兽医杂志 2021年（第57卷）第8期\n"
        "[[通栏]] 猫皮肤无力病例报告唐玉洁1,张迪2\n"
        "[[通栏]] （1.北京中农大动物医院有限公司，北京海淀100193；2.中国农业大学动物医学院）\n"
        "[[通栏]] 中图分类号：S829.3\n"
        "[[左栏]] 2.1猫体检查皮肤触感比正常猫更加柔软，\n"
        "见图2,后背皮肤略松弛;之前创口愈合后形成很\n"
        "[[右栏]] 收稿日期：2019-10-18\n"
        "文章编号：0529-6005（2021）08-0102-03\n"
        "2.2实验室检查\n血常规各项结果均在正常参考范围内。",
    )
    segs1 = segment_journal_document(doc1, [p0_skin])
    assert len(segs1) == 1
    assert segs1[0].article_title == "猫皮肤无力病例报告"
    assert segs1[0].received_year == "2019"
    assert "2.2实验室检查" in segs1[0].raw_text

    # Case B: 2-line wrapped title + affiliation line `3.北京新仁仁动物医院，北京昌平102200）` before `中图分类号`
    doc2 = _make_doc(filename="猫肠道线性异物致肠穿孔导致腹膜炎病例报告.pdf", doc_id="doc_fb")
    p0_fb = _make_page(
        doc2.document_id,
        0,
        "中国兽医杂志 2022年（第58卷）第5期\n"
        "猫肠道线性异物致肠穿孔导致\n"
        "腹膜炎病例报告\n"
        "张雁1,郭俊林2\n"
        "（1.北京市朝阳区农业综合服务中心，北京朝阳100018；\n"
        "3.北京新仁仁动物医院，北京昌平102200）\n"
        "中图分类号：S858.293\n"
        "1 病例\n美短猫，雄性，7岁，体重2.9kg。",
    )
    segs2 = segment_journal_document(doc2, [p0_fb])
    assert len(segs2) == 1
    assert segs2[0].article_title == "猫肠道线性异物致肠穿孔导致腹膜炎病例报告"
    assert segs2[0].boundary_uncertain is False
    assert "美短猫" in segs2[0].raw_text


def test_journal_preserves_continuation_after_early_references_and_filters_plate_blocks() -> None:
    doc = _make_doc(filename="猫乳腺纤维腺瘤样增生的病理诊断和治疗.pdf", doc_id="doc_mam")
    p0 = _make_page(
        doc.document_id,
        0,
        "[[通栏]] 中国兽医杂志2015年（第51卷）第10期\n"
        "[[通栏]] 猫乳腺纤维腺瘤样增生的病理诊断和治疗\n"
        "[[通栏]] 朱国1，师福山2\n"
        "[[通栏]] （1.浙江大学动物科学学院附属动物医院，浙江杭州310012）\n"
        "[[通栏]] 中图分类号：S858.293 文献标志码：B\n"
        "[[左栏]] 1 病例介绍\n未绝育年轻母猫，乳腺对称性肿大。\n"
        "[[右栏]] 4 小结\n上篇文章结尾。\n"
        "参考文献：\n"
        "[1] Smith A. Previous article ref[J]. 2010.\n"
        "文章编号：0529-6005（2015）10-0070-02\n"
        "2.2实验室检验\n血常规未见明显异常，白细胞8.4×10⁹/L。",
    )
    p1 = _make_page(
        doc.document_id,
        1,
        "[[通栏]] 中国兽医杂志2015年（第51卷）第10期\n"
        "3 治疗与转归\n实施卵巢子宫摘除术后乳腺逐渐消退。\n"
        "参考文献：\n"
        "[1] Jones B. Target article ref[J]. 2014.",
    )
    p2 = _make_page(
        doc.document_id,
        2,
        "[[通栏]] 中国兽医杂志2015年（第51卷）第10期\n"
        "《诊治犬腹腔肠系膜妊娠病例》图版\n"
        "（作者黄庆洲等，正文见第65～66页）\n"
        "图3 取除的子宫及卵巢\n"
        "《猫乳腺纤维腺瘤样增生的病理诊断和治疗》图版\n"
        "（作者朱国，师福山，正文见第70～71页）\n"
        "图1 病理学观察\n"
        "A：低倍镜下可见乳腺间质细胞增生明显。\n"
        "《菌毒清口服液治疗人工感染鸡传染性鼻炎试验》图版\n"
        "图2 鸡肺脏切片",
    )

    segs = segment_journal_document(doc, [p0, p1, p2])
    assert len(segs) == 1
    seg = segs[0]
    assert seg.boundary_uncertain is False
    assert seg.article_title == "猫乳腺纤维腺瘤样增生的病理诊断和治疗"
    assert seg.page_indices == [0, 1, 2]
    assert "2.2实验室检验" in seg.raw_text
    assert "实施卵巢子宫摘除术" in seg.raw_text
    assert "低倍镜下可见乳腺间质细胞增生明显" in seg.raw_text
    assert "Previous article ref" not in seg.raw_text
    assert "Target article ref" not in seg.raw_text
    assert "取除的子宫及卵巢" not in seg.raw_text
    assert "鸡肺脏切片" not in seg.raw_text


def test_journal_synthetic_omnibus_with_received_date_and_sec1_excludes_pig_article() -> None:
    doc = _make_doc(
        filename="合成_猫角膜穿孔合订本病例.pdf",
        doc_id="doc_syn_omni",
        target_query="猫角膜穿孔结膜瓣移植术病例报告",
    )
    p0 = _make_page(
        doc.document_id,
        0,
        "【SYNTHETIC TEST FIXTURE / 合成测试数据 — 非真实临床病例】\n"
        "中国兽医杂志  2023年第59卷第2期\n"
        "规模化猪场猪流行性腹泻的防控体会\n"
        "2023年3月某规模化猪场发生仔猪急性水样腹泻，发病率达45%。诊断：猪流行性腹泻。",
    )
    p1 = _make_page(
        doc.document_id,
        1,
        "【SYNTHETIC TEST FIXTURE / 合成测试数据 — 非真实临床病例】\n"
        "中国兽医杂志  2023年第59卷第2期\n"
        "收稿日期：2023-02-14\n"
        "猫角膜穿孔结膜瓣移植术病例报告\n"
        "1 病史与眼科检查\n"
        "患猫，加菲猫，1岁，雌性。因右眼被抓伤流泪2天就诊。",
    )

    segs = segment_journal_document(doc, [p0, p1])
    assert len(segs) == 1
    seg = segs[0]
    assert seg.boundary_uncertain is False
    assert seg.article_title == "猫角膜穿孔结膜瓣移植术病例报告"
    assert seg.received_year == "2023"
    assert seg.page_indices == [1]
    assert "猪流行性腹泻" not in seg.raw_text
    assert "加菲猫" in seg.raw_text


