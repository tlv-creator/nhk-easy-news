# 四级词汇分级（`cet4` 字段）· 数据说明

给每一期的生词卡打一个标签：**这个词是不是四级词汇？**

- 词表：`data/cet4_words.json`
- 打标签脚本：`tools/tag_cet4_vocab.py`
- 重建词表：`tools/build_cet4_words.py`　自检：`tools/test_cet4_words.py`
- 共享库（解析 / 归一 / 判定）：`tools/cet4_vocab.py`

---

## 判据（只有两条依据，全是公开词表）

| 来源 | 作用 |
|---|---|
| 四级大纲词表 `CET4_edited.txt`（4615 条） | 决定**是不是四级词**：在大纲里 = 四级词，不在 = 超纲 |
| 词频表 `COCA_20000.txt`（20200 行，频次降序） | 取**前 4000 名**当「高频核心带」 |

```
core      = 大纲 ∩ COCA 前 4000      高频 ⇒ 核心
secondary = 大纲 − COCA 前 4000      在纲但不算高频 ⇒ 次核心
beyond    = 不在大纲                 超纲（运行时判断，不入库）
```

- 「前 4000 名」按**首次出现行号**算 —— COCA 表里有重复词（20200 行只有 17634 个不同的词），
  按行号算才不会同一个词同时落进两档。
- 当前词表：**core 2858 / secondary 1686 / 大纲词 4544**（`built_at` 2026-10-24）。
- 词表里 `levels.beyond` 恒为空数组：不在大纲的就是超纲，运行时判断就行。

## `vocab[]` 里的新字段

```json
{ "w": "habitat", "pos": "n.", "cn": "栖息地", "ex": "…", "note": "…", "cet4": "core" }
```

`cet4` ∈ `core` | `secondary` | `beyond`，**可选字段**，加不加都不影响旧内容渲染。

## 词形归一（复数 / 过去式 / 进行时）

生词卡里可能有 `habitats` / `protected` / `doubling` 这类变形，用**后缀剥离启发式**还原原形。
🔴 **只有剥离结果能在词表里查到才认，查不到就判 beyond** —— 宁可漏标也不错标（错标会教错学生）。

- `-es` 只在词干以 s/x/z/ch/sh 收尾时允许 ⇒ `habitats` 不会被错剥成 `habit`（大纲里是 habitat 不是 habit）
- 剥出来的词干 ≥ 3 字母（`-ing` 放宽到 2：go / do / see）⇒ `bed` 不会被剥成 `b`
- 英美拼写（favor/favour、color/colour、centre/center）按规则变体试查，变体不在词表里就当没这条
- **短语按整条判**（`solar term` / `cultural heritage` / `micro-drama`）：短语在大纲里才算四级词

## 写回命令

```
python3 tools/tag_cet4_vocab.py            # 给 data/issues/*.json 的 vocab[] 写回 cet4 字段（幂等，可反复跑）
```

先看结果不落盘：`python3 tools/tag_cet4_vocab.py --dry-run`

## 自检

```
python3 tools/test_cet4_words.py && python3 tools/tag_cet4_vocab.py --dry-run
```

自检里含**反向验证**：造一份假的分级词表（把一个常见四级词从 levels 里删掉），
判定器必须把它判成 beyond —— 证明判据真的在查表，不是恒绿。

## 已知的大纲缺口（词表本身的问题，不是脚本的 bug）

`CET4_edited.txt` 覆盖 COCA 前 500 名的 98.4%、前 1000 名的 97.8%，但只覆盖前 4000 名的 77.9%。
中频段缺了一批常见词，例如：`species` `budget` `sector` `decline` `revenue` `habitat` `heritage`
`virtual` `innovation` `script` `click` `delegate` `e-mail`。
这些词会被判成 `beyond` —— 属词表缺口，已单独列出，不在脚本里偷偷兜底。
