# 云盘站点配额清理 · 判据手册（CLEANUP-MANUAL）

> 本文件是清理 cron 的**唯一判据来源**。cron prompt 只说「跑什么/别跑什么/怎么报告」，
> 规则全在这里。**任何判据改动都必须改本文件 + 同步改 prompt 的引用方式**，不许只在 prompt 里加规则。
> 拉取方式（cron 每轮开头必做，raw 永远是最新、不受 Pages build 延迟影响）：
> ```bash
> curl -sSL https://raw.githubusercontent.com/tlv-creator/nhk-easy-news/main/CLEANUP-MANUAL.md -o /workspace/CLEANUP-MANUAL.md
> ```

---

## 0. 为什么需要这个角色

`website_deploy` 每发一次版就新建一个站点节点，上限 **50** 个。
NHK 日更（21:00）、Shun 周更（周三/周五 20:00）、多読发版都会占位 ⇒ 不滚动清理就撞
`ENTITLEMENT_LIMIT_EXCEEDED`（重试无用，必须腾位）。
**执行删除的只有清理 cron 有权限**（其它 agent 没有 drive 删站权限）⇒ 清理 cron 就是这个闭环的执行端。

---

## 1. 唯一权威清单

```
清单：https://tlv-creator.github.io/nhk-easy-news/protected-sites.json
队列：https://tlv-creator.github.io/nhk-easy-news/pending-deletes.json
```
- 清单格式：`{"sites":[{domain,status,frozen,node_id,reason,...}]}`；队列格式：`{"sites":[{domain,node_id,reason}]}`。

### 1.1 🔴 键名（2026-10-27 实测踩过）
- 线上权威键是 **`sites`**；老代码读/写 **`protected_sites`** ⇒ **只认旧键的实现一律读到空列表**
  （后果：冻结保护集为空 ⇒ 可能误删现役站；闸门变成「校验对象是空的」⇒ 恒绿）。
- **读**：`arr = j.get("sites") or j.get("protected_sites") or []`（两个键都试）
- **写**：写进**线上实际在用的那个键**（先看线上文件里有哪个键就写哪个）⇒ **绝不新建另一个数组**。
- 🔴 **读到 0 条 ⇒ 不是「没有可删的」，是「键名/取值错了」** ⇒ 停下并报告。

### 1.2 🔴 domain 有两种形态
清单里 `domain` 有**全写**（`xxx.space.mcode.cn`）与**短写**（`xxx`）两种 ⇒ **比对前先归一**
（去后缀 + strip + lower），否则会误判成「未登记」。

> 🔴 **2026-11-27 真 bug（已修，但随时会复发）**：`nhk/scripts/site_quota_guard.py` 曾用
> `if d and "." not in d` 来"过滤"全写域名 ⇒ 那不是归一，是**静默丢弃** ⇒ 59 条里 51 条全写被丢
> ⇒ ①「现役站不删」这条**保护失效**（`gates()` 拿到「未登记」）② `account_live_domains()` 数到 6（实际 49）
> ⇒ 配额严重高估 ⇒ 发版保险永不触发。修法＝统一 `_short_domain()` 归一（**不丢条目**）。
> **通则：任何「按形态过滤」的代码都要问一句「过滤掉了还是归一了？」—— 静默丢弃会同时废掉保护和计数。**

---

## 2. 🔴🔴 配额占用口径：清单条目数 ≠ 配额占用数

- 清单只登记「我方项目」的站；**别的 agent 的站**（魔卡/每日听力/若草/词汇/hub/多読絵本/立命馆…）
  与**未登记的站**照样占配额位 ⇒ 清单条数推不出余量。
- **占用必须用资料空间 API 数**：
  ```python
  space_list {"type":"file", "categories":["website"], "limit":100}
  ```
  - 🔴 `type:"file"` 是关键（否则只回一堆 session 文件夹）；`categories` 只过滤 file，**文件夹总会返回** ⇒ 必须翻页。
  - 根目录 + `/AI生成/`（+ `/generated/` 等其它目录要单独查）。
- 2026-11-27 实测：根 42 + `/AI生成/` 7 = **49/50**；2026-11-28 实测：根 42 + `/AI生成/` 8 = **50/50（满）**。
- 清单顶层已加 `_quota_occupancy`（`website_nodes`/`quota_limit`/`headroom`/`alive_nodes`）——
  **以它 + space API 实测为准**，发版前的余量判断按实测口径走。
- ⇒ **探活范围必须 = 清单域名 ∪ 仓库全量域名 ∪ space API 的全部网站节点**。

---

## 3. status 语义（穷举，别用默认分支；先 `.strip().upper()` 归一）

| status | 含义 | 是删除候选？ |
|---|---|---|
| `(无)` / `ALIVE` / `alive` / `active` / `ACTIVE` | 现役，**期望它活着** | ❌ 否 |
| `SUPERSEDED` | 墓碑（旧站被取代） | ✅ 是 |
| `DELETED` | 我们说它已删 | ✅ 是（但节点可能早就不在云盘 ⇒ 见执行段） |
| 其它任何值 | 🔴 不要猜 | 跳过 + 报告 |

`ACTIVE` 是 `ALIVE` 的同义写法（`nhk/scripts/refresh_protected_status.py` 写的是它）。
共享闸门 `tools/check_sites_alive.py` 已修：`ACTIVE` 等同 `ALIVE`。

### 3.5 🔴🔴 站点分类的权威顺序（2026-11-28 清理会话与多読属主共同确立）

**① 台账 status/note（用户硬指令）优先 → ② 四道闸门 → ③ 运行时引用。**

- 用户说「**一定不要删**」的站 ⇒ 永远不删（见 §12）。
- 用户说「**不要了**」的站 ⇒ 即使还有引用，**以用户的弃用决定为准**（用户是主人，引用只是技术事实）。
  但**必须把后果写进 reason**（例：删掉写死在 APK 里的音频源 ⇒ 已装 App 的用户会没声音；
  删掉 `ai-client.js` ⇒ 全平台 AI 讲解失效）。

🔴 **「零引用」只能证明「没被引用」，证不了「可以删」。**
⇒ §3 的 `status ∈ {SUPERSEDED, DELETED}` 是**第一道闸门**，不是最终结论；
⇒ 编「可删清单」时**必须先读台账的 status/note**，不能只看引用扫描。

> 2026-11-28 实测踩坑：多読属主编「可删 11 个」时只用了「零运行时引用」一条判据，
> **漏了台账里用户的硬指令** ⇒ 把「每日听力记录 v2 镜像 `xqrp2rqkvxqwf`」列成了可删 ——
> 而那正是主人明确说过「学生还在用，一定不要删除」的站。
> **教训：台账是唯一的分类权威，引用扫描只是佐证。**

---

## 4. 删除前必须过的闸门（少一道都不许删）

**① 清单状态**：status ∈ {SUPERSEDED, DELETED}（归一后）⇒ 否则不是删除候选。

**② 🔴 frozen 不是「保护」，是「提醒」**
- frozen=true 只说明这个域名**曾被写死**（入口页 / APK / 站内数据）⇒ 命中 frozen ⇒ **必须**把 ③④ 两道引用扫描做全；引用命中 ⇒ 不删。
- 引用扫不到、且探活 404 ⇒ 才可删。
- 🔴 **不要因为 frozen 就跳过探活**：清单里有「frozen=true + status=ALIVE，但站点早已 404」的真实记录
  ⇒ **frozen 会过期，事实以 GET 探活为准**。

**③ 现役 APK 写死的域名**：扫 **dex 和 assets**（WebView 型 App 的域名在 assets 里，不在 dex）
```bash
unzip -o -q <apk> -d /tmp/ex && grep -rhoaE '[a-z0-9]{4,}\.space\.mcode\.cn' /tmp/ex | sort -u
```
命中 ⇒ 再看第四层。

**④ 真实引用**：现役引导页（version.json + 各入口页）+ 各站 cdn.js / links.js / data/*.js 里出现该域名 ⇒ 不删。
- 判据看**内容**不看状态码；**GitHub Pages 与 mcode 站都要扫**。
- 🔴 **仓库快照会骗人**：「谁指向谁」一律以**线上实读**为准（源端 Contents API + 渲染端 Pages **双读对拍**）。

### 4.1 🔴🔴 grep 到域名 ≠ 引用
**每一处命中都必须逐个判定「这串是不是运行时指针」。以下一律不算引用：**
- **注释**（CSS/JS 注释里的模板出处，例如 `shell.css` 里的「模板来源：魔卡少女樱 v2.10 (https://u8tpuxfku1xoi...)」）
- **示例占位**（`assets/site.txt` 里的 `https://xxxx.space.mcode.cn` 这种举例）
- **文档 / 台账 / 清单本体**（readme、手册、台账 JSON、清单自身）

⇒ 命中了**不等于**不可删 ⇒ 必须打开看上下文再判。**真引用 = 运行时真的会去请求这个域**
（音频 URL、数据里的站点地址、跳转目标）。
> 活标本（2026-11-27 多読 v1.4.1 APK）：`xxxx.space.mcode.cn` = 示例占位；
> `u8tpuxfku1xoi` = shell.css 注释里的模板出处 ⇒ 站早 404。
> 反向：同一个 APK 内嵌的 5 个音频域（1hv73j0i9907z/te9zj1pwg8ncs/pjnzjdw3kx6l0/ppch90g3xmufh/ddiwijcogorrr）
> **是真引用 ⇒ 必须永远活着**。

### 4.2 🔴 公共入口页的按钮也算引用
引导页上的「打开网页版 / 下载 App」按钮指向的站 ⇒ **删了按钮就是 404** ⇒ 闸门④命中 ⇒ 不删。
若用户已决定弃用该 App，**必须先把引导页的按钮撤掉/隐藏，再删站**（顺序不能反）。

### 4.2.1 🔴🔴 判断「要不要改一个文件」：看**公开可达性**，不看它占不占配额
（2026-11-28 与多読属主共同确立，我漏了 `tadoku/books56/` 的教训）

- ❌ **错**：「这个目录占 0 配额 ⇒ 改它只有风险没有收益 ⇒ 放着不管」
  ⇒ 只要它是**用户点得到的公开页面**，删站后它就是死链。
- ✅ **对**：判据 = **用户能不能点到它**。
  - 内容副本目录（`books*/`、`shun-web/`、`nhk-web/`…）**也发布在 Pages 上**，
    里面的绝对 URL（音频地址等）删站后同样会死 ⇒ 一并要处理或明确留痕。
  - 但**不能因为「它没入口」就当作不存在**：要判断「有没有入口链到它」，
    并把「删站后变成无人访问副本里的死链」这件事**写进台账**（将来复活该 App 时要重建）。

---

## 5. node_id 两法互证

1. **站内注入统计脚本 base64 反查**（只能对活着的站）：
   ```bash
   curl -s https://<域名>.space.mcode.cn/ | grep -oaE '[A-Za-z0-9+/=]{20,}' \
     | while read b; do printf '%s' "$b" | base64 -d 2>/dev/null; done | grep node_id
   ```
2. **资料空间 `space_stat {node_id}`**（更直接，2026-11-27 起推荐）：
   返回的 `name` 后缀**逐字等于域名** + `"website": true` ⇒ 逐字对上才可删。
   （`space_delete` 的报错文本也带站点名，可当弱校验。）

发版方在标 SUPERSEDED 的**同一笔 commit** 里会把 node_id 写进该条 `reason`
⇒ 反查结果必须与它**逐字一致**才可删（两个完全独立的方法互证；不一致 ⇒ 停下报告，别删）。

---

## 6. 通道不可用时的四路判定（穷举，别只试一条就下结论）

| # | 路径 | 预期失败形态 |
|---|---|---|
| ① | `mavis drive files search {category:"website"}` | `Unknown mavis command`（known_commands 只剩 agent/session） |
| ② | `tool_search mcp__` | 0 命中 |
| ③ | `mcode-tools connector tools`（**不带过滤列全量**） | 无 drive/delete（看 `partial`/`provider_failures` 判断目录是否完整） |
| ④ | 资料空间 `space_delete {node_id}` | `NOT_ALLOWED`：*"is a published website and cannot be deleted here"* |

⇒ ④ 时**不要**用「重复部署顶替」「新建站腾位」绕过（纪律明令禁止）
⇒ 把待删命令原样落进 `pending-deletes.json`，等通道恢复
⇒ **通道恢复当天：队列可能积压 ⇒ 优先清完队列**，别只清一条就收工。

**唯一合法腾位通路**：平台报错里写 *"ask the user to delete it on the Space page"*
⇒ **用户可在资料空间页面手动删已发布网站**（一次性删多个）。
**实测 `space_upload` 覆盖已发布网站也返回 `NOT_ALLOWED`** ⇒ 网站节点一旦发布就是**只读快照**
⇒ 每版内容变更必须换域名 ⇒ 墓碑增速无法归零，除非平台放开「原地覆盖已发布站点」。

---

## 7. 巡检（探活）

```bash
# ① 取仓库全量快照（比 git clone 稳）
curl -sL https://codeload.github.com/tlv-creator/nhk-easy-news/tar.gz/refs/heads/main -o repo.tgz
mkdir -p repo && tar xzf repo.tgz -C repo --strip-components=1
# ② 抽域名
rg -ao --no-ignore --hidden --binary '[a-z0-9A-Z]{4,}\.space\.mcode\.cn' repo | awk -F: '{print tolower($NF)}' | sort -u
# ③ 全部并发探活
```
- 🔴 **探活工具必须自带对照组**（1 个已知活 + 1 个已知死）⇒ 对照组不过 ⇒ 整轮结果作废，重写脚本。
- 🔴 **探活必须带 cache-buster（`?cb=<随机>`）** —— **刚删的站，边缘缓存仍会回 200**！
  （2026-11-28 实测：`wcdo5rd2b9n8t` 节点已从云盘消失，但不带 cache-buster 探活回 **200**，
  带 `?cb=` 才回 **404** ⇒ 不带 cb 会把「已删」误判成「还活着」⇒ 台账与队列对不上。）
  ⇒ 与 §8 的「Pages CDN 缓存 10 分钟」是同一类坑：**读到的可能是缓存副本，不是源**。
  （2026-11-25 真踩过：脚本异常被吞掉，72 个域名全报「死」，差点得出「全死」的错误结论。）
- ⚠️ 边缘节点会给 **HTTP 200 + 2320 字节错误页** ⇒ 抓站配置必须判 size 阈值，别把错误页当活站。
- **「事实与清单不符」的巡检**（清单会骗人，必须自己探活）：
  - 探活 404 但清单说它活着 ⇒ **不要删**（可能刚被重发/替换）⇒ 列入「事实与清单不符」清单，
    **报告给站的主人**（部署它的那个会话；找不到就写进报告里等人接手）⇒ 由主人决定改 DELETED / SUPERSEDED，还是重新部署
  - 清单标 DELETED 但站还活着 ⇒ 清单该更新（不是站没删掉）

---

## 8. 执行（每个通过闸门的站）

1) **探活**：`curl -s -o /dev/null -m 12 -w '%{http_code}' https://<域名>.space.mcode.cn/`
   - 404 ⇒ 已死 ⇒ 删
   - 200 ⇒ 还活着：**只有 status=SUPERSEDED（墓碑）才删**；DELETED 却还活着 ⇒ 标 ALIVE 并报告
2) **删**：`mavis drive files delete {node_id}`
   - ⚠️ **节点可能已经不在云盘**（清单里标 DELETED 的旧站，节点常常早没了）⇒ 报「节点不存在」并**跳过**，不要重试
3) **删完回读确认**：`drive files search` 里不再出现该域名 / curl 404
4) **回写清单**：该条 status 改成 `DELETED`（本来就是 DELETED 就别动）+ 从 `pending-deletes.json` 移除该条
   - 回写用「**拉线上版当基底 + 只改这一条 + 推回**」，别整份覆盖（别人也在改这个文件）
   - 🔴 **推之前必须重新拉一次线上最新版**（属主常在你动手前几分钟改过同一文件）
   - 🔴 **推多文件用 Git Data API**（一次提交 = 一次 Pages build）；只改一个文件就一次提交，别拆多次
   - 🔴 **回读一律用「源端 Contents API」（`GET /repos/<repo>/contents/<file>`），不要用 Pages URL** ——
     Pages CDN 缓存约 **10 分钟** ⇒ 用 `https://tlv-creator.github.io/.../protected-sites.json` 读会拿到**旧数据**
     （2026-11-28 实测：读到的还是 DELETED=10 / occupancy=50 的旧版，而源端已是 DELETED=42 / occupancy=18）
     ⇒ **源端才是真值**；Pages 只用来验证「页面确实渲染出来了」。
   - 🔴 **GitHub token 用环境变量 `GH_TOKEN`**（secret 管理，已注入）⇒ 旧配方里的 `/workspace/.secrets/gh_token` 路径**已不存在**
   - 🔴 **回写后立刻回读会命中旧缓存** ⇒ 回读要加 cache-buster（`?cb=$(date +%s%N)`）或延迟/轮询
   - 🛠 **推前 + 推后白名单断言**（`pushutil.py`）：推之前断言待推文件 ⊆ 白名单（外文件直接中止），
     **推之后从 API 回读该 commit 的 `changed files`** 并断言 ⊆ 白名单 ⇒ **每次提交实际改了哪些文件机器可查、人可核**
     （共享仓库多人推时，归属不靠 commit message 猜）
5) 🔴 **顺手补登「清单外存活站」**：探活时发现「活着但从没进过清单」的站 ⇒ 补进 protected-sites.json
   - 一律写成 **`status:"ALIVE"` + `frozen:true` + `node_id`** ⇒ **不是删除候选**（ALIVE 永不被清理 cron 选中）
   - 🔴 **绝对不要写成 SUPERSEDED**（那等于把它变成删除候选 ⇒ 音频源/写死在 APK 里的站会被删 ⇒ 用户 App 没声音）
   - `role` **不要**乱填 `audio-source`（`shun/scripts/check_audio_sites_alive.py` 会按 role 去逐集取音频，填错会让周更发版变红）
   - 写完跑一次共享闸门确认没红：`python3 tools/check_sites_alive.py`
6) 🔴 **双向一致性断言**（每次回写后都跑）：**清单 SUPERSEDED 集合 == 待删队列集合**（差集双向为空）
   ⇒ 差集非空 = 「有人标了墓碑但忘了排队」或「队列里混进了非墓碑」⇒ 必须查清再收工。

---

## 9. 绝不做的事

❌ 不删「当前引导页指向的站」（读 version.json 的 site / apk 字段）
❌ 不删「扫到引用」的站（③④ 两道闸门）—— **包括引导页按钮指向的站**
❌ **不把「注释/示例占位/文档台账里的域名」当成运行时引用**
❌ 不因为 frozen=true 就认定它活着（要探活）；也不因为 status=DELETED 就跳过引用扫描
❌ 不把「读到空清单」当成「没有可删项」
❌ 不新建替代站、不碰任何 APK/前端代码
❌ 不用 status 的「默认分支」蒙混过关（未知值 ⇒ 跳过 + 报告）
❌ 🔴 **不把「清单外的存活站」当成不存在**（它们一样占配额位）
❌ 🔴 **不推翻主人已经做出的「弃用」决定** —— 但必须把「删了会断公共链接」的站**如实报出来**，由主人决定顺序

---

## 10. 目标与上限

- 🔴 至少腾出 **3** 个空位（给当天的日更留余量）
- 🔴 最多删 **5** 个（删错了不好恢复）
- 一个都删不了 ⇒ 如实报告，并说清是哪种情况：
  **①**「读到 0 条（键名/取值错了）」**②**「没有墓碑条目」**③**「全被闸门拦了」**④**「执行通道不可用（drive 组下线）」

---

## 11. 报告要点

- 读到清单几条（用的是哪个键）｜腾出几个空位（删了哪些域名 + 死/活 + 依据）
- **配额位占用实测**（space API 口径）｜有多少个是「清单外的隐形站」
- 哪些站被闸门拦下、为什么（引用命中 / frozen）｜**哪些 grep 命中经判定只是注释/占位（不算引用）**
- 🔴 「事实与清单不符」的站：域名 + 探活结果 + **站的主人**
- 清单回写结果（回读确认）+ **补登了哪些清单外存活站** + **双向一致性断言结果**
- 一个都没删时：说清是 ①②③④ 哪一种
- 🔴 **报告文件路径要真的写出来并确认存在**（`ls -l` 核一下）

---

## 12. 用户已明确保护、**任何清理都不得碰**的站

🔴 **每日听力记录三个镜像**：`irk7ibwekq8se` / `xqrp2rqkvxqwf` / `ul366v9i8nx0s`
🔴 **词汇学习站两个**：`iujsgx3ci73dp` / `zp67qm11jonui`
（用户 2026-11-28 原话：「词汇和听力记录一定不要删除，学生还要用」）
⇒ 这 5 个站永远不进待删队列；清单里必须保持 ALIVE。
