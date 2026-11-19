# 病例报告三模块结构化抽取

从中文兽医期刊临床病例报告 PDF、以及扫描图版型兽医书籍/译著中，抽取结构化病例数据，
用于兽医 AI（如 vet_agent）测试集 / 评测集构建。

核心纪律：**所有内容完全取自原文，缺失留空，绝不编造、不改写、不推理、不翻译。**

## 包内容

| 文件 | 说明 |
|---|---|
| `SKILL.md` | 技能本体。含触发词、字段口径、Dx 书写五则、书籍适配、OCR 管线、QA 清单 |
| `requirements.txt` | 运行时依赖清单（PDF 抽取 + OCR + xlsx 写入） |
| `README.md` | 本文件：安装与使用说明 |

## 安装

1. 将整个 `病例报告三模块结构化抽取/` 目录放到技能目录下：

```
~/.workbuddy/skills/病例报告三模块结构化抽取/
├── SKILL.md
├── requirements.txt
└── README.md
```

Windows 实际路径：`C:\Users\<用户名>\.workbuddy\skills\`

2. 安装依赖到 managed python venv（**不要全局安装**）：

```powershell
C:\Users\work\.workbuddy\binaries\python\envs\default\Scripts\python.exe -m pip install -r requirements.txt
```

## 使用

在对话里给出触发词或直接描述任务即可，例如：

- 「把这些病例报告 PDF 抽成结构化表格」
- 「病例报告抽取」「抽取病例」「测试集病例」
- 「全书病例抽取」「扫描书抽病例」

## 输出 schema

**期刊病例（9 列，顺序固定）**

`source | year | title | medical_history | medical_test | medical_treatment | Dx | follow-up | 备注`

**扫描书籍 / 译著（7 列，删除 source / year）**

`title | medical_history | medical_test | medical_treatment | Dx | follow-up | 备注`

## 依赖说明

| 依赖 | 用途 | 备注 |
|---|---|---|
| PyMuPDF (`fitz`) | PDF 文本层抽取 | **首选引擎**。实测 pdfplumber 失败的中文兽医 PDF，PyMuPDF 可干净抽出 |
| pdfplumber | PDF 文本层抽取 | 备选；部分中文 PDF 返回空文本，勿默认全量使用 |
| rapidocr-onnxruntime | OCR | 仅用于无文字层 / 字体损坏乱码页。250 dpi 渲染后识别 |
| openpyxl | xlsx 读写 | 产物表格与合并脚本 |

> OCR 实测性能标定（8 核机器）：渲染 0.4~0.5 s/页，识别 8~11 s/页（单进程），
> **并发 3 进程最优**，4~6 进程因内存带宽瓶颈反而无收益。

## 注意事项

- 中间产物建议保留以便返工：`pages_json/`、`pages_ocr/`、`pages_img/`、`index.json`、`cases/`、`rows/`、`EXTRACTION_SPEC.md`。
- 分批抽取每批 6~10 例，并**强制增量落盘**，否则大批次易被中途截断。
