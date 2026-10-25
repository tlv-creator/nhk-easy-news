# tadoku/books56d — 教材 12 本「有原文、缺官方朗読」补音批次

> 复核方：Mavis（ASR + 对齐方）｜日期：2026-10-26
> 平台 ASR 走 0 额度通道，**未消耗用户 token**。

## 一、这个批次是什么

线上（v1.3.0，87 本 / 11481 句 / 66 本有音）里，有 **12 本书的原文（句 / 逐字注音 / 译文）已经在库里**，
但 `has_audio = 0` —— 因为它们的**官方朗読音频我们之前没接**。

我用冻结的 `tadoku/jgr86_snapshot.json`（86 本有官方朗読）逐本对线上索引重算得出：

- 66 本已有音频
- **12 本：有原文、没音频** ← 本批次
- 8 本：线上完全没有（Start 8 + L2 等，等新做）

**关键点：这 12 本不用等新书、不用 OCR、不用注音** —— 原文已经在库里，只差「下载官方音频 → ASR 真值 → 对齐 → 重建」。

## 二、官方音频来源

教材系列按 **ISBN 卷**打包，每个包 `storage.ask-books.com/uploads/books/<ISBN13>/audios/audios.zip`：

| ISBN | 教材巻 | 轨数 | 字节 | CRC |
|---|---|---|---|---|
| 9784866397306 | レベル3 | 7 | 125,690,877 | 全段通过 ✅ |
| 9784866396583 | レベル1 | 6 | 58,349,129 | 全段通过 ✅ |
| 9784866396569 | レベル4 | 5 | 169,753,159 | 全段通过 ✅ |

🔴 **下载门槛**：必须**同时带 UA + Referer**（`Referer: https://ask-books.com/book-details/?slug=<ISBN13>`）；
只带 UA ⇒ **403**。

## 三、目录

```
tadoku/books56d/
├── books/          12 本书文件（2026-10-26 从线上拉的**快照**；如线上更新请同步）
├── out/            对齐+重建后的时间轴（与线上 v1.3.0 的「无音频」版本对应）
├── asr_truth/      18 条轨的 ASR 真值 + index.json（轨↔书对照）
├── align_report.json / rebuild_report.json / e2e_result.json
├── units.json      轨↔书↔绝对偏移
└── VERIFY.md       验收报告
```

**文件所有权**：`books/` 是线上数据（构建会话拥有），`out/` + `asr_truth/` 是本方产物。
两边若不一致，以 `books/` 的 sha 为准重跑对齐。

## 四、轨 ↔ 书 对照

| 轨 | 时长 | 书 |
|---|---|---|
| 9784866397306-track-001 | 403.2s | むじな → `level3-koizumi-yakumo-s-scary-tales-mujina-and-ghost-falls` |
| 9784866397306-track-002 | 573.3s | 幽霊滝 → 同上（第 2 话，同一册） |
| 9784866397306-track-003 | 983.4s | 注文の多い料理店 → `level3-the-restaurant-of-many-orders` |
| 9784866397306-track-004 | 1272.5s | お札の話 → `level3-who-are-these-people-the-story-of-japanese-banknotes` |
| 9784866397306-track-005 | 1056.2s | かぐや姫 → `level3-princess-kaguya` |
| 9784866397306-track-006 | 704.6s | 蜘蛛の糸 → `level3-the-spider-s-thread-and-the-nose` |
| 9784866397306-track-007 | 798.7s | 鼻 → 同上 |
| 9784866396569-NogikunoHaka | 2561.8s | 野菊の墓 → `level4-the-grave-of-the-wild-chrysanthemums` |
| 9784866396569-Toshishun | 1683.0s | 杜子春 → `level4-toshishun` |
| 9784866396569-Yukionna | 1269.1s | 雪女 → `level4-the-snow-woman` |
| 9784866396569-Hashire_Merosu | 1723.7s | 走れメロス → `level4-run-melos` |
| 9784866396569-NagaiTakashi | 1228.9s | 永井隆 → `level4-takashi-nagai-living-in-nagasaki-the-atomic-bomb-site` |
| 9784866396583-l1-00N | 6 轨 | 着物 / エビ・ねこ年 等（见 asr_truth/index.json） |

🔴 **L4 巻的文件名直接带书名** ⇒ 轨↔书零歧义；**L3 巻是数字名** ⇒ 用「各轨开头 ASR vs 書名」内容级佐证
（`mapping_status = content_supported`）。

## 五、口径纪律

- **ASR**：40 秒切段、批 5 / 并发 2；失败结果**不缓存**（判 `success` + description 非空）；`[No speech]` 是真实答案（尾静音）。
- **时间戳正则**必须穷举 `【0】` 与 `【0秒】` 两种写法 ⇒ `【\s*((?:\d+:)?\d+(?:\.\d+)?)\s*秒?\s*】`。
- **对齐**：字符流全局对齐（最长公共子串覆盖率）+ 汉字骨架双通道（`--dual`）+ 顺序检查（容差 1.5 秒）。
- **末行结束时间**：语速中位数估算 + **实测 speech_end**（`silencedetect` 能量分析）当硬上限。
  🔴 **夹取顺序有坑**（2026-10-26 端到端逮到）：实测 speech_end 可能比 ASR 末行起点**还早 0.4 秒**
  ⇒ 旧写法 `est = min(est, se)` 会把末句时间窗压成 **0.1 秒** ⇒ 已修（见 VERIFY.md 第三节）。
- **长音符「ー」(U+30FC)**：在**注音闸门**里必须保留，在**对齐覆盖率**里要剥（音节口径）⇒ 两个场景口径不同，详见 `align_audio.py` 注释。

## 六、怎么接上

本目录的 `out/<tag>.json` 是**与线上同结构**的完整书文件（`cues[].s/e` 是音频里的真实秒），
可直接替换线上 `data/eps/<tag>.json` 的对应字段；音频文件由构建会话按上面的 ISBN 轨对应关系上传到音频站。
